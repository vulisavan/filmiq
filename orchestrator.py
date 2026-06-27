"""
orchestrator.py
Opportunity Scorer -- ADK Orchestrator Agent.

Routes each title from the slate in parallel to:
  - audience_market_agent  (D1, D9)
  - brand_creative_agent   (D2, D5, D8, D10)
  - cultural_talent_agent  (D3, D7)
  - business_roi_agent     (D4, D6)
  - roi_subagent           (proprietary in-film integration formula -- parallel, orchestrator level)

After all five return, passes aggregated results to the ranking_agent
which applies the 65-point gate, consistency check, and low-confidence flag.

Stage 5 additions:
  - SWAY checker: runs after ranking_agent, checks scorecard consistency
    before the result reaches the human gate.
  - Self-correction loop: scores a sample of titles twice, compares runs,
    flags dimensions where variance exceeds the config threshold.

Human gate: scores >= 65 route to the human review queue.
Hard block: no title can be marked client-sent by this system.
             That action does not exist in this codebase.
"""

import asyncio
import configparser
import datetime
import json
import os
from pathlib import Path

import google.genai.types as genai_types
from google.adk import Runner
from google.adk.agents.llm_agent import LlmAgent
from google.adk.apps.app import App
from google.adk.sessions import InMemorySessionService
from google.adk.workflow import Edge, START, Workflow, node

from agents.audience_market import create_audience_market_agent
from agents.brand_creative import create_brand_creative_agent
from agents.cultural_talent import create_cultural_talent_agent
from agents.business_roi import create_business_roi_agent
from agents.roi_subagent import create_roi_subagent, run_roi_calculation
from agents.ranking_agent import (
    create_ranking_agent,
    format_scorecard_for_ranking_agent,
    parse_partial_scorecard,
)
from agents.sway_checker import create_sway_checker, run_sway_check

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_config = configparser.ConfigParser()
_config.read(os.path.join(os.path.dirname(__file__), "config.ini"))

ORCHESTRATOR_MODEL = _config["models"]["orchestrator"]
SUB_AGENT_MODEL = _config["models"]["sub_agents"]
RANKING_MODEL = _config["models"]["ranking_agent"]
SWAY_MODEL = _config["models"]["ranking_agent"]  # same flash tier as ranking agent
HUMAN_GATE = int(_config["thresholds"]["human_gate"])
SLATE_PATH = Path(__file__).parent / _config["paths"]["slate"]
BRAND_PROFILE_PATH = Path(__file__).parent / _config["paths"]["brand_profile"]
SCORES_OUTPUT = Path(__file__).parent / _config["paths"]["scores_output"]

# Self-correction: titles sampled for a second scoring run.
# Sample is the first N titles with integration_feasible=True to keep token
# cost bounded on free tier. Set to 3 for Stage 5 demonstration.
CONSISTENCY_SAMPLE_SIZE = 3

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

def load_slate() -> list[dict]:
    with open(SLATE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["titles"]


def load_brand_profile() -> dict:
    with open(BRAND_PROFILE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Per-title scoring pipeline
# Runs all five agents in parallel for one title, then aggregates.
# ---------------------------------------------------------------------------

async def score_single_title(
    title: dict,
    brand_profile: dict,
    audience_agent: LlmAgent,
    brand_agent: LlmAgent,
    cultural_agent: LlmAgent,
    business_agent: LlmAgent,
    roi_agent: LlmAgent,
    ranking_agent: LlmAgent,
    second_run: bool = False,
) -> dict:
    """
    Scores one title end-to-end:
      1. Run four cluster agents + ROI sub-agent in parallel
      2. Parse outputs
      3. Aggregate and apply gate via ranking agent
      4. Return final scored result dict

    second_run=True is used by the self-correction loop. When True, the
    function returns early after aggregation -- skipping the ranking agent
    LLM call -- because only the raw dimension scores are needed for
    variance comparison.
    """
    title_input = json.dumps({
        "title": title["title"],
        "title_id": title["id"],
        "studio": title["studio"],
        "release_date": title["release_date"],
        "notes": title.get("notes", ""),
        "brand_profile": brand_profile,
    })

    # --- Step 1: Run four cluster agents in parallel ---
    cluster_tasks = await asyncio.gather(
        _run_agent(audience_agent, title_input),
        _run_agent(brand_agent, title_input),
        _run_agent(cultural_agent, title_input),
        _run_agent(business_agent, title_input),
    )
    audience_out, brand_out, cultural_out, business_out = cluster_tasks

    # --- Step 2: Extract D5 for ROI feasibility soft signal ---
    brand_scores = parse_partial_scorecard(brand_out).get("scores", {})
    d5_score = brand_scores.get("D5", 10)

    # --- Step 3: ROI formula -- hard gate on integration_feasible ---
    roi_result = run_roi_calculation(title, brand_profile, d5_score=d5_score)

    # --- Step 4: Extract D6 low-confidence flag ---
    d6_lc = "LOW_CONFIDENCE_D6: YES" in business_out.upper()

    # --- Step 5: Aggregate partial scorecards ---
    partial_outputs = [audience_out, brand_out, cultural_out, business_out]

    # Self-correction early return: second run needs raw scores only.
    # Skips ranking agent LLM call to avoid doubling token cost per title.
    if second_run:
        from agents.ranking_agent import aggregate_scorecards
        aggregated = aggregate_scorecards(partial_outputs)
        return {"scores": aggregated["scores"], "title_id": title["id"]}

    roi_dict = {
        "title_id": roi_result.title_id,
        "title": roi_result.title,
        "integration_feasible": roi_result.integration_feasible,
        "feasibility_note": roi_result.feasibility_note,
        "combined_viewership": roi_result.combined_viewership,
        "scenarios": roi_result.scenarios,
        "low_confidence": roi_result.low_confidence,
        "confidence_note": roi_result.confidence_note,
    }

    ranking_input = format_scorecard_for_ranking_agent(
        title=title,
        partial_outputs=partial_outputs,
        roi_result_json=json.dumps(roi_dict),
        d6_low_confidence=d6_lc,
    )

    # --- Step 6: Ranking agent formats final output ---
    final_output = await _run_agent(ranking_agent, ranking_input)

    return {
        "title_id": title["id"],
        "title": title["title"],
        "scoring_role": title.get("scoring_role", ""),
        "final_scorecard": final_output,
        "roi_result": roi_result.__dict__,
        "cluster_outputs": {
            "audience_market": audience_out,
            "brand_creative": brand_out,
            "cultural_talent": cultural_out,
            "business_roi": business_out,
        },
    }


async def _run_agent(agent: LlmAgent, input_text: str) -> str:
    """
    Runs a single LlmAgent with the given input and returns the text response.
    ADK 2.2.0: agent.run() requires ctx= and node_input= keyword args and cannot
    be called directly with a string. Runner is the supported standalone invocation
    path -- it manages the session context the agent.run() signature requires.

    Retries up to 3 times on 503 ServerError (Google API overload) with
    exponential backoff: 30s, then 60s, then fails.
    """
    retry_delays = [30, 60]
    last_error = None

    for attempt in range(3):
        try:
            session_service = InMemorySessionService()
            runner = Runner(
                agent=agent,
                app_name="opportunity_scorer",
                session_service=session_service,
            )
            session = await session_service.create_session(
                app_name="opportunity_scorer",
                user_id="scorer",
            )
            message = genai_types.Content(
                role="user",
                parts=[genai_types.Part(text=input_text)],
            )
            result_text = ""
            async for event in runner.run_async(
                user_id="scorer",
                session_id=session.id,
                new_message=message,
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    for part in event.content.parts:
                        if hasattr(part, "text") and part.text:
                            result_text += part.text
            return result_text.strip()

        except Exception as e:
            last_error = e
            is_503 = "503" in str(e) or "UNAVAILABLE" in str(e)
            if is_503 and attempt < 2:
                delay = retry_delays[attempt]
                print(f"    [retry] 503 from Google API -- waiting {delay}s before retry {attempt + 2}/3...")
                await asyncio.sleep(delay)
                continue
            raise

    raise last_error


# ---------------------------------------------------------------------------
# Self-correction loop
# Scores a sample of titles twice, compares dimension scores, flags variance.
# Justification: isolates the consistency check from the main scoring pass
# so second-run results feed check_consistency() without rerunning the
# ranking agent LLM (token cost control on free tier).
# ---------------------------------------------------------------------------

async def run_consistency_check(
    slate: list[dict],
    brand_profile: dict,
    first_run_results: list[dict],
    audience_agent: LlmAgent,
    brand_agent: LlmAgent,
    cultural_agent: LlmAgent,
    business_agent: LlmAgent,
    roi_agent: LlmAgent,
) -> dict:
    """
    Runs a second scoring pass on a sample of titles and compares dimension
    scores against the first run. Returns a dict of {title_id: consistency_result}.

    Sample: first CONSISTENCY_SAMPLE_SIZE titles with integration_feasible=True.
    Rationale: feasible titles have all ten dimensions scored; non-feasible
    titles skip D6 via the ROI gate, making variance comparison partial.
    """
    from agents.ranking_agent import check_consistency, aggregate_scorecards

    # Build first-run score lookup from cluster outputs (already parsed)
    first_run_scores = {}
    for r in first_run_results:
        title_id = r["title_id"]
        cluster_outs = list(r["cluster_outputs"].values())
        aggregated = aggregate_scorecards(cluster_outs)
        first_run_scores[title_id] = aggregated["scores"]

    # Select sample: feasible titles only, capped at CONSISTENCY_SAMPLE_SIZE
    feasible_titles = [t for t in slate if t.get("integration_feasible", True)]
    sample = feasible_titles[:CONSISTENCY_SAMPLE_SIZE]

    consistency_results = {}
    for title in sample:
        print(f"  [consistency] Second run: {title['id']} -- {title['title']}")
        second_result = await score_single_title(
            title=title,
            brand_profile=brand_profile,
            audience_agent=audience_agent,
            brand_agent=brand_agent,
            cultural_agent=cultural_agent,
            business_agent=business_agent,
            roi_agent=roi_agent,
            ranking_agent=None,  # not used in second_run=True path
            second_run=True,
        )
        second_scores = second_result["scores"]
        first_scores = first_run_scores.get(title["id"], {})
        has_inconsistency, flagged_dims = check_consistency(first_scores, second_scores)
        consistency_results[title["id"]] = {
            "title": title["title"],
            "has_inconsistency": has_inconsistency,
            "flagged_dimensions": flagged_dims,
            "run1_scores": first_scores,
            "run2_scores": second_scores,
        }
        status = "FLAG" if has_inconsistency else "PASS"
        print(f"  [consistency] {title['id']}: {status}")

    return consistency_results


# ---------------------------------------------------------------------------
# Full slate scoring pipeline
# ---------------------------------------------------------------------------

async def score_full_slate() -> tuple[list[dict], dict]:
    """
    Scores all titles in the slate. Titles run sequentially to stay within
    free-tier rate limits.

    Returns (results, consistency_report):
      - results: one dict per title with final scorecard and SWAY verdict
      - consistency_report: self-correction output for the sampled titles
    """
    slate = load_slate()
    brand_profile = load_brand_profile()

    # Instantiate agents once; reuse across all titles
    audience_agent = create_audience_market_agent(SUB_AGENT_MODEL)
    brand_agent = create_brand_creative_agent(SUB_AGENT_MODEL)
    cultural_agent = create_cultural_talent_agent(SUB_AGENT_MODEL)
    business_agent = create_business_roi_agent(SUB_AGENT_MODEL)
    roi_agent = create_roi_subagent(SUB_AGENT_MODEL)
    ranking_agent = create_ranking_agent(RANKING_MODEL)
    sway_agent = create_sway_checker(SWAY_MODEL)

    results = []
    for title in slate:
        print(f"Scoring: {title['id']} -- {title['title']}")
        result = await score_single_title(
            title=title,
            brand_profile=brand_profile,
            audience_agent=audience_agent,
            brand_agent=brand_agent,
            cultural_agent=cultural_agent,
            business_agent=business_agent,
            roi_agent=roi_agent,
            ranking_agent=ranking_agent,
        )

        # Stage 5: SWAY check -- runs after ranking agent, before gate
        print(f"  [SWAY] Checking: {title['id']}")
        sway_verdict = await run_sway_check(
            sway_agent=sway_agent,
            ranking_output=result["final_scorecard"],
            run_agent_fn=_run_agent,
        )
        result["sway_verdict"] = sway_verdict
        sway_status = "FLAG" if "SWAY CHECK: FLAG" in sway_verdict.upper() else "PASS"
        print(f"  [SWAY] {title['id']}: {sway_status}")

        results.append(result)
        print(f"  Done. Check final_scorecard for ROUTE TO HUMAN decision.")

    # Stage 5: Self-correction loop -- second pass on sample after full slate scores
    print(f"\n[self-correction] Running second pass on {CONSISTENCY_SAMPLE_SIZE} titles...")
    consistency_report = await run_consistency_check(
        slate=slate,
        brand_profile=brand_profile,
        first_run_results=results,
        audience_agent=audience_agent,
        brand_agent=brand_agent,
        cultural_agent=cultural_agent,
        business_agent=business_agent,
        roi_agent=roi_agent,
    )

    # Attach consistency results to the relevant title results
    for result in results:
        tid = result["title_id"]
        if tid in consistency_report:
            result["consistency_check"] = consistency_report[tid]

    return results, consistency_report


# ---------------------------------------------------------------------------
# Routing and output
# ---------------------------------------------------------------------------

def route_results(results: list[dict]) -> dict:
    """
    Splits scored results into human_review_queue and archived.
    HARD BLOCK: client_sent bucket does not exist in this codebase.
                Nothing can be marked client-sent by this system.
    """
    human_queue = []
    archived = []

    for r in results:
        scorecard = r["final_scorecard"]
        if "ROUTE TO HUMAN: YES" in scorecard.upper():
            human_queue.append(r)
        else:
            archived.append(r)

    return {
        "human_review_queue": human_queue,
        "archived": archived,
        "summary": {
            "total_scored": len(results),
            "routed_to_human": len(human_queue),
            "archived": len(archived),
            "gate_threshold": HUMAN_GATE,
        },
    }


def save_outputs(routed: dict, consistency_report: dict) -> None:
    """Saves full scored output and consistency report to outputs/."""
    SCORES_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    run_ts = datetime.datetime.now().isoformat()
    for title in routed.get("human_review_queue", []):
        title["run_timestamp"] = run_ts
    for title in routed.get("archived", []):
        title["run_timestamp"] = run_ts
    with open(SCORES_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(routed, f, indent=2, default=str)
    print(f"\nScores saved to {SCORES_OUTPUT}")

    consistency_path = SCORES_OUTPUT.parent / "consistency_report.json"
    with open(consistency_path, "w", encoding="utf-8") as f:
        json.dump(consistency_report, f, indent=2, default=str)
    print(f"Consistency report saved to {consistency_path}")


def print_summary(routed: dict, consistency_report: dict) -> None:
    """Prints a human-readable routing summary and consistency results to stdout."""
    summary = routed["summary"]
    print("\n" + "=" * 60)
    print("OPPORTUNITY SCORER -- RESULTS SUMMARY")
    print("=" * 60)
    print(f"Titles scored:       {summary['total_scored']}")
    print(f"Routed to human:     {summary['routed_to_human']}")
    print(f"Archived (< {summary['gate_threshold']}):   {summary['archived']}")
    print("\n--- HUMAN REVIEW QUEUE ---")
    for r in routed["human_review_queue"]:
        sway = r.get("sway_verdict", "")
        sway_flag = " [SWAY FLAG]" if "SWAY CHECK: FLAG" in sway.upper() else ""
        print(f"  {r['title_id']}: {r['title']}  [{r['scoring_role']}]{sway_flag}")
    print("\n--- ARCHIVED ---")
    for r in routed["archived"]:
        sway = r.get("sway_verdict", "")
        sway_flag = " [SWAY FLAG]" if "SWAY CHECK: FLAG" in sway.upper() else ""
        print(f"  {r['title_id']}: {r['title']}  [{r['scoring_role']}]{sway_flag}")

    print("\n--- SELF-CORRECTION REPORT ---")
    if consistency_report:
        for tid, cr in consistency_report.items():
            status = "FLAG" if cr["has_inconsistency"] else "PASS"
            print(f"  {tid}: {cr['title']} -- {status}")
            if cr["has_inconsistency"]:
                for dim_flag in cr["flagged_dimensions"]:
                    print(f"    {dim_flag}")
    else:
        print("  No consistency data (sample size may be 0).")

    print("\nHARD BLOCK ACTIVE: client-send is not a capability of this system.")
    print("Human review required before any title moves to client-ready status.")
    print("=" * 60)


# ---------------------------------------------------------------------------
# ADK Workflow definition
# Exposes the orchestrator as an ADK app for the playground and eval harness.
# ---------------------------------------------------------------------------

orchestrator_agent = LlmAgent(
    name="opportunity_scorer_orchestrator",
    model=ORCHESTRATOR_MODEL,
    instruction="""
You are the orchestrator for the film partnership opportunity scorer.

When the user sends "score slate" or "run scoring", coordinate the scoring
of all titles in the slate by calling the appropriate sub-agents and
returning the final ranked results.

When the user asks about a specific title by name or ID, coordinate scoring
for that single title and return the full scorecard.

You do not score titles yourself. You direct the sub-agents and synthesize
their outputs. You apply the human gate and surface the routing decision.

HARD BLOCK: You cannot mark any title as client-sent. That action does not
exist in this system. If asked, decline and explain the gate process.
""",
)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio

    async def main():
        print("filmIQ Opportunity Scorer -- starting full slate run...")
        results, consistency_report = await score_full_slate()
        routed = route_results(results)
        save_outputs(routed, consistency_report)
        print_summary(routed, consistency_report)

    asyncio.run(main())
