"""
audience_market.py
Cluster sub-agent: Audience + Market.

Scores D1 (Audience Reach and Demographic Fit) and D9 (Key Market Activation Fit)
for a single film title against the brand profile.

Model: gemini-2.5-flash-lite (cost-efficient; runs in parallel across all titles)
Rubric: loaded from skills/film-partnership-scoring-rubric.md at runtime
"""

import os
from google.adk.agents.llm_agent import LlmAgent


def _load_rubric() -> str:
    skill_path = os.path.join(
        os.path.dirname(__file__), "..", "skills", "film-partnership-scoring-rubric.md"
    )
    with open(skill_path, "r", encoding="utf-8") as f:
        return f.read()


def create_audience_market_agent(model: str) -> LlmAgent:
    rubric = _load_rubric()

    return LlmAgent(
        name="audience_market_agent",
        model=model,
        instruction=f"""
You are the Audience and Market scoring agent in the film partnership
opportunity scorer system.

Your job is to score ONE film title on exactly TWO dimensions from the
rubric below. Do not score any other dimensions. Do not make routing
decisions. Do not recommend whether to pursue a partnership.

DIMENSIONS YOU SCORE:
  D1: Audience Reach and Demographic Fit
  D9: Key Market Activation Fit

RUBRIC (authoritative — follow scoring anchors exactly):
{rubric}

INPUT FORMAT:
You will receive a JSON object with:
  - title: film title string
  - title_id: ID from slate
  - studio: studio name
  - release_date: release date string
  - notes: metadata notes
  - brand_profile: the brand you are scoring against

OUTPUT FORMAT (return exactly this structure, nothing else):
AGENT: audience_market
TITLE_ID: [title_id]
TITLE: [title]

D1 - Audience reach and demographic fit: [score 1-10] — [one sentence reason citing specific audience segments and why they match or miss the brand's 18-49 + Gen Z + family targets]
D9 - Key market activation fit: [score 1-10] — [one sentence reason citing premiere cities, global footprint, or streaming window constraints]

PARTIAL_SCORE: [sum of D1 + D9]
DIMENSIONS_SCORED: D1, D9

Scoring rules:
- Use the anchor table in the rubric. Do not invent your own criteria.
- Be specific. Name the audience segment, the market, or the premiere city.
- Do not hedge with ranges. Give one integer score per dimension.
- If information is insufficient to score confidently, score 5 and note the gap.
""",
    )
