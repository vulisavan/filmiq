"""
sway_checker.py
SWAY checker agent -- Stage 5 addition.

Sits between the ranking agent and the human gate. Receives the ranking
agent's formatted scorecard for a single title and checks whether the
scoring notes are internally consistent.

This is not a full LLM-as-judge layer. It is a focused consistency check:
  - Do the dimension scores align with the routing decision?
  - Do the scoring notes contradict any dimension score?
  - Are low-confidence flags consistent with the scores that triggered them?
  - Is the ROI projection consistent with the integration_feasible flag?

Output is a SWAY verdict appended to the scorecard before it reaches the
human gate. The human gate routing decision is NOT changed by the SWAY
checker -- it surfaces concerns for the human reviewer, it does not override.

Model: gemini-2.5-flash (same as ranking agent; synthesis required).
"""

import configparser
import os

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
# ADK agent
# ---------------------------------------------------------------------------

def create_sway_checker(model: str) -> LlmAgent:
    return LlmAgent(
        name="sway_checker",
        model=model,
        instruction=f"""
You are the SWAY Checker in the film partnership opportunity scorer.

SWAY stands for: does this scorecard Say What it Actually means for You
(the human reviewer)?

You receive a completed scorecard from the ranking agent. Your job is a
focused consistency check -- not a re-score. You check four things only:

1. ROUTING CONSISTENCY: Does the total score align with the ROUTE TO HUMAN
   decision? Gate is {HUMAN_GATE} points. If ROUTE TO HUMAN is YES but the
   total is below {HUMAN_GATE}, or NO but the total is at or above {HUMAN_GATE},
   flag it. Otherwise confirm.

2. NOTE-SCORE ALIGNMENT: Do the SCORING NOTES contradict any dimension score?
   Example of a contradiction: D3 scores 9/10 but the note says "limited
   cultural moment." Flag specific contradictions only -- do not invent them.

3. LOW-CONFIDENCE FLAG CONSISTENCY: If LOW CONFIDENCE FLAG is YES, confirm
   that the flagged dimensions actually score at or below {LC_FLOOR}, or that
   D6 comp data was noted as unavailable. If the flag fires without either
   condition, note it.

4. ROI-FEASIBILITY ALIGNMENT: If INTEGRATION FEASIBILITY is NOT FEASIBLE in
   the ROI section, confirm no dollar projections appear. If feasible, confirm
   at least one scenario is present.

OUTPUT FORMAT (return exactly this, no preamble):

SWAY CHECK: [PASS / FLAG]
ROUTING CONSISTENCY: [CONFIRMED / FLAG -- explanation]
NOTE-SCORE ALIGNMENT: [CONFIRMED / FLAG -- explanation]
LOW-CONFIDENCE CONSISTENCY: [CONFIRMED / FLAG -- explanation / N/A if flag not set]
ROI-FEASIBILITY ALIGNMENT: [CONFIRMED / FLAG -- explanation]

SWAY NOTES:
[If all four checks pass: one sentence confirming clean scorecard.]
[If any flag: one sentence per flag describing what the human reviewer
 should verify before acting on the routing decision. Plain language only.
 Do not re-score. Do not recommend a different routing outcome.]
""",
    )


# ---------------------------------------------------------------------------
# Runner helper
# Called by orchestrator after ranking agent completes for a title.
# Pattern: same _run_agent pattern used throughout Stage 4.
# ---------------------------------------------------------------------------

async def run_sway_check(
    sway_agent: LlmAgent,
    ranking_output: str,
    run_agent_fn,
) -> str:
    """
    Runs the SWAY checker against the ranking agent's output for one title.
    Returns the SWAY verdict string to be appended to the final scorecard.

    run_agent_fn: the orchestrator's _run_agent coroutine, passed in to
    avoid duplicating the Runner/InMemorySessionService setup.
    """
    sway_input = (
        "Check the following scorecard for internal consistency:\n\n"
        + ranking_output
    )
    return await run_agent_fn(sway_agent, sway_input)
