"""
ranking_agent.py
Ranking agent — aggregates the four cluster partial scorecards and the
ROI figure, applies the 65-point human gate, runs the consistency
check, and flags low confidence.

Model: gemini-2.5-flash (higher capability; aggregation requires synthesis)
"""

import configparser
import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

from google.adk.agents.llm_agent import LlmAgent

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_config = configparser.ConfigParser()
_config.read(os.path.join(os.path.dirname(__file__), "..", "config.ini"))

HUMAN_GATE = int(_config["thresholds"]["human_gate"])
LC_FLOOR = int(_config["thresholds"]["low_confidence_dimension_floor"])
LC_COUNT = int(_config["thresholds"]["low_confidence_count_trigger"])
CONSISTENCY_LIMIT = int(_config["thresholds"]["consistency_variance_limit"])


# ---------------------------------------------------------------------------
# Scorecard parser (deterministic — extracts structured data from agent output)
# ---------------------------------------------------------------------------

def parse_partial_scorecard(agent_output: str) -> dict:
    """
    Parses structured output from a cluster sub-agent into a dict of
    {dimension_code: score} pairs.

    Expects lines like:
      D1 - Audience reach and demographic fit: 8 — reason text
    """
    scores = {}
    reasons = {}
    pattern = re.compile(
        r"(D\d+)\s*-\s*[^:]+:\s*(\d+)(?:/\d+)?\s*[\u2014\u2013\-]+\s*(.+)",
        re.IGNORECASE,
    )
    for line in agent_output.splitlines():
        m = pattern.match(line.strip())
        if m:
            dim = m.group(1).upper()
            score = min(int(m.group(2)), 10)
            reason = m.group(3).strip()
            scores[dim] = score
            reasons[dim] = reason
    return {"scores": scores, "reasons": reasons}


def aggregate_scorecards(partial_outputs: list[str]) -> dict:
    """
    Merges all partial scorecards into a single dict of {dimension: score}.
    Expects one output string per cluster agent.
    """
    all_scores = {}
    all_reasons = {}
    for output in partial_outputs:
        parsed = parse_partial_scorecard(output)
        all_scores.update(parsed["scores"])
        all_reasons.update(parsed["reasons"])
    return {"scores": all_scores, "reasons": all_reasons}


def apply_gate(total_score: int) -> bool:
    """Returns True if the title routes to human review."""
    return total_score >= HUMAN_GATE


def check_low_confidence(scores: dict, d6_lc_flag: bool = False) -> tuple[bool, str]:
    """
    Flags low confidence if:
      - Two or more dimensions score at or below LC_FLOOR (default 4), OR
      - D6 business_roi agent returned a low-confidence flag
    Returns (is_low_confidence, reason_string).
    """
    low_dims = [dim for dim, score in scores.items() if score <= LC_FLOOR]
    reasons = []
    if len(low_dims) >= LC_COUNT:
        reasons.append(
            f"{len(low_dims)} dimensions scored at or below {LC_FLOOR}: {', '.join(sorted(low_dims))}"
        )
    if d6_lc_flag:
        reasons.append("D6 comp data unavailable or release at risk")
    is_lc = bool(reasons)
    return is_lc, "; ".join(reasons) if reasons else ""


def check_consistency(
    scores_run1: dict, scores_run2: Optional[dict]
) -> tuple[bool, list[str]]:
    """
    Compares two scoring runs. Flags any dimension where variance > CONSISTENCY_LIMIT.
    Returns (has_inconsistency, list_of_flagged_dimensions).
    If scores_run2 is None (single-run mode), returns (False, []).
    """
    if scores_run2 is None:
        return False, []
    flagged = []
    for dim in scores_run1:
        if dim in scores_run2:
            variance = abs(scores_run1[dim] - scores_run2[dim])
            if variance > CONSISTENCY_LIMIT:
                flagged.append(
                    f"{dim}: run1={scores_run1[dim]}, run2={scores_run2[dim]}, variance={variance}"
                )
    return bool(flagged), flagged


# ---------------------------------------------------------------------------
# Final output formatter
# ---------------------------------------------------------------------------

def format_scorecard_for_ranking_agent(
    title: dict,
    partial_outputs: list[str],
    roi_result_json: str,
    d6_low_confidence: bool = False,
    second_run_scores: Optional[dict] = None,
) -> str:
    """
    Builds the structured input the ranking agent LLM receives.
    Aggregation and gate logic are deterministic (above).
    The LLM formats the final narrative output.
    """
    aggregated = aggregate_scorecards(partial_outputs)
    scores = aggregated["scores"]
    reasons = aggregated["reasons"]

    # Ensure all 10 dimensions present; fill missing with 5/unknown
    expected = [f"D{i}" for i in range(1, 11)]
    for dim in expected:
        if dim not in scores:
            scores[dim] = 5
            reasons[dim] = "Dimension not scored by any cluster agent — defaulting to 5."

    total = sum(scores.values())
    route_to_human = apply_gate(total)
    is_lc, lc_reason = check_low_confidence(scores, d6_low_confidence)
    has_inconsistency, inconsistent_dims = check_consistency(scores, second_run_scores)

    summary = {
        "title_id": title["id"],
        "title": title["title"],
        "total_score": total,
        "route_to_human": route_to_human,
        "low_confidence": is_lc,
        "low_confidence_reason": lc_reason,
        "consistency_flag": has_inconsistency,
        "inconsistent_dimensions": inconsistent_dims,
        "dimension_scores": scores,
        "dimension_reasons": reasons,
        "roi_calculation": roi_result_json,
    }
    return json.dumps(summary, indent=2)


# ---------------------------------------------------------------------------
# ADK agent
# ---------------------------------------------------------------------------

def create_ranking_agent(model: str) -> LlmAgent:
    return LlmAgent(
        name="ranking_agent",
        model=model,
        instruction=f"""
You are the Ranking Agent in the film partnership opportunity scorer system.

You receive a pre-computed JSON scorecard for a single film title. The
scores, gate decision, and flags are already calculated deterministically.
Your job is to format this into the final structured output and write
clear, plain-language scoring notes where flags are present.

Do not change any scores. Do not override the gate decision. Do not
recalculate. Report what the JSON contains, formatted as shown below.

Human gate threshold: {HUMAN_GATE} points.
Low-confidence flag: fires when 2+ dimensions score {LC_FLOOR} or below,
or when D6 comp data is unavailable.
Consistency flag: fires when the same title scored twice shows variance
greater than {CONSISTENCY_LIMIT} point on any dimension.

OUTPUT FORMAT (return exactly this, no preamble):

TITLE: [title]
TOTAL SCORE: [X/100]
ROUTE TO HUMAN: [YES / NO]
LOW CONFIDENCE FLAG: [YES — reason / NO]
CONSISTENCY FLAG: [YES — affected dimensions / NO]

DIMENSION SCORES:
1. Audience reach and demographic fit: [D1 score/10] — [D1 reason]
2. Brand alignment and safety: [D2 score/10] — [D2 reason]
3. Cultural moment and earned media potential: [D3 score/10] — [D3 reason]
4. Activation runway and timing: [D4 score/10] — [D4 reason]
5. Integration potential: [D5 score/10] — [D5 reason]
6. Franchise activation precedent: [D6 score/10] — [D6 reason]
7. Talent alignment and promotional commitment: [D7 score/10] — [D7 reason]
8. Relationship fostering opportunity: [D8 score/10] — [D8 reason]
9. Key market activation fit: [D9 score/10] — [D9 reason]
10. Quality and creative potential: [D10 score/10] — [D10 reason]

IN-FILM ROI PROJECTION:
[Extract and format the roi_calculation field from the input JSON.
List integration fee, viewership estimate, and the top 2-3 scenarios
by ROI multiplier. If low confidence, state that prominently.]

SCORING NOTES:
[Write 1-3 plain-language sentences covering:
  - Any low-confidence flags and what would resolve them
  - Any consistency flags and which dimensions drifted
  - Any edge cases the human gate reviewer should weigh
  - If no flags: one sentence confirming clean score]
""",
    )
