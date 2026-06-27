"""
roi_subagent.py
In-film integration ROI calculator.

Parallel node at the orchestrator level. Runs the proprietary formula
against each title independently of the rubric scoring chain. Output is the
client-deliverable dollar figure, not a rubric score.

Integration feasibility gate (hard):
  - title["integration_feasible"] == False -> ROI sub-agent does not run.
    Output: integration_not_feasible flag, reason from slate, no dollar figure.
  - D5 score below threshold (soft signal) -> low_confidence flag added to output,
    but formula still runs. Human gate reviewer weighs both signals.

Formula (proprietary -- rates loaded from config.ini, not hardcoded here):
  Step 1: Combined viewership = sum of available market viewerships (audience data)
           + theatrical box office where applicable
           + other territories estimate (20% of named market sum)
  Step 2: 30-sec ad rate = (combined_viewership / 1000) * CPM
           CPM loaded from config.ini per channel (theatrical, streaming, social).
  Step 3: 15-sec ad rate = 30-sec rate * 0.5
  Step 4: Tier multiplier: fair=0.75, good=1.0, great=1.5, excellent=2.0
  Step 5: Media value = (viewership / 1000) * CPM * (spot_seconds / 30) * quantity * tier_multiplier
  Step 6: ROI = (media_value - integration_fee) / integration_fee

Hero scenario matrix (all feasible titles):
  All hero exposures are projected at excellent tier, 15 seconds each (standard:
  exposures under 15 seconds round up to 15). Three fixed scenarios:
    1 hero: $300K fee, quantity=1, excellent, 15s
    2 hero: $600K fee, quantity=2, excellent, 15s
    3 hero: $1M fee,   quantity=3, excellent, 15s
  Highest ROI scenario surfaces as the recommended scenario.
"""

import configparser
import os
from dataclasses import dataclass, field
from typing import Optional

from google.adk.agents.llm_agent import LlmAgent

# ---------------------------------------------------------------------------
# Config -- CPM rates and tier multipliers loaded from config.ini at runtime.
# Rates are not hardcoded here; config.ini references internal methodology.
# ---------------------------------------------------------------------------
_config = configparser.ConfigParser()
_config.read(os.path.join(os.path.dirname(__file__), "..", "config.ini"))

THEATRICAL_CPM = float(_config["cpm_rates"]["theatrical_cpm"])
STREAMING_CPM = float(_config["cpm_rates"]["streaming_cpm"])
SOCIAL_CPMS = {
    "instagram_feed":  float(_config["cpm_rates"]["social_cpm_instagram_feed"]),
    "instagram_story": float(_config["cpm_rates"]["social_cpm_instagram_story"]),
    "twitter":         float(_config["cpm_rates"]["social_cpm_twitter"]),
    "facebook":        float(_config["cpm_rates"]["social_cpm_facebook"]),
    "youtube":         float(_config["cpm_rates"]["social_cpm_youtube"]),
    "tiktok":          float(_config["cpm_rates"]["social_cpm_tiktok"]),
}
SOCIAL_CPM_AVG = sum(SOCIAL_CPMS.values()) / len(SOCIAL_CPMS)

TIER_MULTIPLIERS = {
    "fair":      float(_config["tier_multipliers"]["fair"]),
    "good":      float(_config["tier_multipliers"]["good"]),
    "great":     float(_config["tier_multipliers"]["great"]),
    "excellent": float(_config["tier_multipliers"]["excellent"]),
}

# Hero scenario matrix: fixed fee, quantity, tier, and duration per internal methodology.
# All hero exposures are excellent tier at 15 seconds (rounds up if under 15s).
HERO_SCENARIOS = [
    {"label": "1 hero",  "fee": 300_000,   "quantity": 1, "narrative_integration": False},
    {"label": "2 hero",  "fee": 600_000,   "quantity": 2, "narrative_integration": False},
    # 3-hero fee includes filmmaker incentive for narrative integration -- brand becomes
    # part of the story, not a placement (e.g. a brand used as prop across heist planning
    # scenes). ROI multiplier is lower than 1-2 hero but the value proposition is
    # qualitatively different -- earned narrative presence beyond media value.
    {"label": "3 hero",  "fee": 1_000_000, "quantity": 3, "narrative_integration": True},
]
HERO_TIER = "excellent"
HERO_SECONDS = 15

# D5 soft-signal threshold: if D5 scores at or below this, add low_confidence
# note even if integration_feasible is True.
D5_SOFT_SIGNAL_FLOOR = 3


# ---------------------------------------------------------------------------
# Formula engine (deterministic, no LLM involved)
# ---------------------------------------------------------------------------

@dataclass
class ROIResult:
    title_id: str
    title: str
    integration_feasible: bool
    feasibility_note: str = ""
    combined_viewership: float = 0.0
    scenarios: list[dict] = field(default_factory=list)
    low_confidence: bool = False
    confidence_note: str = ""


def _cpm_for_channel(channel: str) -> float:
    if channel == "theatrical":
        return THEATRICAL_CPM
    elif channel == "streaming":
        return STREAMING_CPM
    elif channel == "social":
        return SOCIAL_CPM_AVG
    else:
        raise ValueError(f"Unknown channel: {channel}. Use theatrical, streaming, or social.")


def calc_media_value(
    viewership: float,
    channel: str,
    tier: str,
    spot_seconds: int,
    quantity: int,
) -> float:
    """
    Core media value formula:
    media_value = (viewership / 1000) * CPM * (spot_seconds / 30) * quantity * tier_multiplier
    CPM and tier_multiplier loaded from config.ini -- not hardcoded.
    """
    cpm = _cpm_for_channel(channel)
    multiplier = TIER_MULTIPLIERS[tier]
    return (viewership / 1000) * cpm * (spot_seconds / 30) * quantity * multiplier


def calc_roi(media_value: float, integration_fee: float) -> float:
    """
    ROI = (media_value - integration_fee) / integration_fee
    Returns a multiplier (e.g. 10.0 means media value is 10x the fee).
    """
    if integration_fee <= 0:
        raise ValueError("Integration fee must be greater than zero.")
    return (media_value - integration_fee) / integration_fee


def run_roi_calculation(title: dict, brand_profile: dict, d5_score: int = 10) -> ROIResult:
    """
    Runs the ROI formula for a single title.

    Feasibility gate (hard): if integration_feasible is False in the slate,
    returns an ROIResult with integration_feasible=False and no scenarios.

    D5 soft signal: if d5_score is at or below D5_SOFT_SIGNAL_FLOOR, adds a
    low_confidence flag even though the formula still runs. Human gate reviewer
    sees both the numbers and the flag.

    Hero scenarios: all feasible titles run three fixed scenarios at excellent
    tier, 15 seconds, with fees of $300K / $600K / $1M for 1 / 2 / 3 heroes.
    Streaming channel used for all hero scenarios (primary distribution window).
    """
    title_id = title["id"]
    title_name = title["title"]
    feasible = title.get("integration_feasible", True)
    feasibility_note = title.get("integration_feasibility_note", "")

    if not feasible:
        return ROIResult(
            title_id=title_id,
            title=title_name,
            integration_feasible=False,
            feasibility_note=feasibility_note,
        )

    combined_viewership = _estimate_viewership_from_notes(title)
    low_confidence = False
    confidence_notes = []

    if combined_viewership is None:
        low_confidence = True
        confidence_notes.append(
            "No comparable viewership data available. ROI figures are illustrative only."
        )
        combined_viewership = 5_000_000  # conservative floor for illustration

    if d5_score <= D5_SOFT_SIGNAL_FLOOR:
        low_confidence = True
        confidence_notes.append(
            f"D5 integration potential scored {d5_score}/10 -- rubric flagged limited "
            "in-film integration path despite real-world setting. Human reviewer should "
            "weigh whether hero placement is realistically achievable."
        )

    scenario_results = []
    for s in HERO_SCENARIOS:
        mv = calc_media_value(
            combined_viewership, "streaming", HERO_TIER, HERO_SECONDS, s["quantity"]
        )
        roi = calc_roi(mv, s["fee"])
        scenario_results.append({
            "label": s["label"],
            "heroes": s["quantity"],
            "integration_fee": s["fee"],
            "media_value": round(mv, 2),
            "roi_multiplier": round(roi, 4),
            "tier": HERO_TIER,
            "spot_seconds": HERO_SECONDS,
            "channel": "streaming",
            "narrative_integration": s["narrative_integration"],
        })

    # Surface recommended scenario: highest ROI above 1.0x; flag if none exceed 1.0x
    above_threshold = [s for s in scenario_results if s["roi_multiplier"] > 1.0]
    if above_threshold:
        recommended = max(above_threshold, key=lambda s: s["roi_multiplier"])
    else:
        recommended = None

    return ROIResult(
        title_id=title_id,
        title=title_name,
        integration_feasible=True,
        feasibility_note=feasibility_note,
        combined_viewership=combined_viewership,
        scenarios=scenario_results,
        low_confidence=low_confidence,
        confidence_note=" | ".join(confidence_notes) if confidence_notes else "",
    )


def _estimate_viewership_from_notes(title: dict) -> Optional[float]:
    """
    Derives a combined viewership estimate from slate metadata.
    Public repo uses comp-based estimates. Production: first-party audience data.
    Returns None if confidence is too low to estimate.
    """
    notes = title.get("notes", "").lower()
    title_name = title["title"].lower()
    role = title.get("scoring_role", "edge_case")

    if "avengers: secret wars" in title_name:
        return 250_000_000
    if "avengers: doomsday" in title_name:
        return 200_000_000
    if "spider-man" in title_name:
        return 150_000_000
    if "frozen" in title_name:
        return 120_000_000
    if "minecraft" in title_name:
        return 90_000_000
    if "lord of the rings" in title_name or "gollum" in title_name:
        return 80_000_000
    if "superman" in title_name:
        return 80_000_000
    if "batman" in title_name:
        return 70_000_000
    if "cat in the hat" in title_name:
        return 60_000_000
    if "narnia" in title_name:
        return 50_000_000
    if "focker" in title_name:
        return 40_000_000
    if "k-pop" in title_name:
        return 30_000_000
    if "thomas crown" in title_name:
        return 25_000_000
    if "cocomelon" in title_name:
        return 15_000_000
    if "apatow" in notes or "powell" in notes or "apatow" in title_name or "powell" in title_name:
        # Comp: Glen Powell's Anyone But You (~$220M global theatrical / avg $10 ticket = ~22M
        # admissions + streaming tail est. 13M). Universal rom-com overperformer anchor.
        return 35_000_000

    role_defaults = {
        "obvious_great_fit": 180_000_000,
        "high_scorer":        80_000_000,
        "mid_scorer":         40_000_000,
        "edge_case":          None,
        "obvious_low_scorer": 15_000_000,
    }
    return role_defaults.get(role, None)


# ---------------------------------------------------------------------------
# ADK agent wrapper
# Deterministic formula engine above; LLM formats the narrative output only.
# ---------------------------------------------------------------------------

def create_roi_subagent(model: str) -> LlmAgent:
    """
    Creates the ROI sub-agent. Calculation is deterministic (run_roi_calculation).
    LLM formats output and surfaces flags in plain language.
    """
    return LlmAgent(
        name="roi_subagent",
        model=model,
        instruction="""
You are the in-film integration ROI calculator.

You receive a JSON object with pre-calculated ROI results from the deterministic
formula engine. Format these as a clean, client-ready summary.
Do not recalculate. Do not invent numbers. Report exactly what the formula produced.

TWO POSSIBLE INPUTS:

1. integration_feasible = false:
   Output exactly:
   INTEGRATION FEASIBILITY: NOT FEASIBLE
   REASON: [feasibility_note from input]
   ROI PROJECTION: None -- integration fee investment not applicable.

2. integration_feasible = true:
   Output exactly:
   INTEGRATION FEASIBILITY: FEASIBLE
   ESTIMATED COMBINED VIEWERSHIP: [viewership] (comp-based; production uses first-party audience data)
   LOW CONFIDENCE: [YES -- reason / NO]

   HERO SCENARIO PROJECTIONS (excellent tier, 15s, streaming):
   [For each scenario:]
     [label] ([heroes] hero, $[fee] fee): Media value $[amount] | ROI [multiplier]x
     [If narrative_integration is true, add on the same line:]
       -- Narrative integration tier: filmmaker incentive for brand-as-story placement.
         ROI multiplier reflects fee premium; value proposition includes earned narrative
         presence beyond media value (e.g. brand used as prop or story element across scenes).

   RECOMMENDED SCENARIO:
   - If 1 or 2 hero has the highest ROI: name it, state the multiplier.
   - If 3 hero is selected despite lower ROI: explain the narrative integration value.
   - If no scenario exceeds 1.0x: flag that integration fee investment is not justified
     at current viewership estimate and note what viewership would be required.

If LOW CONFIDENCE is YES, state that prominently before the scenario table.
Do not add commentary beyond what the numbers support.
""",
    )
