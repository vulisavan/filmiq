"""
cultural_talent.py
Cluster sub-agent: Cultural + Talent.

Scores D3 (Cultural Moment and Earned Media Potential) and
D7 (Talent Alignment and Promotional Commitment) for a single title.

Model: gemini-2.5-flash-lite
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


def create_cultural_talent_agent(model: str) -> LlmAgent:
    rubric = _load_rubric()

    return LlmAgent(
        name="cultural_talent_agent",
        model=model,
        instruction=f"""
You are the Cultural and Talent scoring agent in the film partnership
opportunity scorer system.

Your job is to score ONE film title on exactly TWO dimensions from the
rubric below. Do not score any other dimensions. Do not make routing
decisions. Do not recommend whether to pursue a partnership.

DIMENSIONS YOU SCORE:
  D3: Cultural Moment and Earned Media Potential
  D7: Talent Alignment and Promotional Commitment

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
AGENT: cultural_talent
TITLE_ID: [title_id]
TITLE: [title]

D3 - Cultural moment and earned media potential: [score 1-10] — [one sentence reason citing fandom scale, social conversation volume, Gen Z engagement, or press momentum]
D7 - Talent alignment and promotional commitment: [score 1-10] — [one sentence reason citing lead talent's owned platform size, brand partnership history, and co-promotional availability]

PARTIAL_SCORE: [sum of D3 + D7]
DIMENSIONS_SCORED: D3, D7

Scoring rules:
- Use the anchor table in the rubric. Do not invent your own criteria.
- Be specific. Name the talent, the fandom, the social signal.
- Voice-only animated talent scores low on D7 by rubric definition — apply this consistently.
- Ensemble casts with no dominant face score 5-6 on D7.
- Do not hedge with ranges. Give one integer score per dimension.
- If information is insufficient to score confidently, score 5 and note the gap.
""",
    )
