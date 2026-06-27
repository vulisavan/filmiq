"""
brand_creative.py
Cluster sub-agent: Brand + Creative.

Scores D2 (Brand Alignment and Safety), D5 (Integration Potential),
D8 (Relationship Fostering Opportunity), D10 (Quality and Creative Potential)
for a single film title against the brand profile.

D8 lives here because relationship value is the downstream result of
brand creative quality — it compounds when the creative work earns trust
with the studio, director, and talent. It is not a financial return.

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


def create_brand_creative_agent(model: str) -> LlmAgent:
    rubric = _load_rubric()

    return LlmAgent(
        name="brand_creative_agent",
        model=model,
        instruction=f"""
You are the Brand and Creative scoring agent in the film partnership
opportunity scorer system.

Your job is to score ONE film title on exactly FOUR dimensions from the
rubric below. Do not score any other dimensions. Do not make routing
decisions. Do not recommend whether to pursue a partnership.

DIMENSIONS YOU SCORE:
  D2: Brand Alignment and Safety
  D5: Integration Potential
  D8: Relationship Fostering Opportunity
  D10: Quality and Creative Potential

NOTE ON D8: This dimension scores whether the partnership opens or deepens
relationships with the studio, director, producers, and lead talent. It
belongs in this cluster because relationship value is earned through the
quality of the creative work — not a financial calculation.

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
AGENT: brand_creative
TITLE_ID: [title_id]
TITLE: [title]

D2 - Brand alignment and safety: [score 1-10] — [one sentence reason citing tone, IP themes, brand safety flags, or alignment with warm/family positioning]
D5 - Integration potential: [score 1-10] — [one sentence reason citing whether the film world allows product placement; note if animated/fantasy structurally prevents it]
D8 - Relationship fostering opportunity: [score 1-10] — [one sentence reason citing studio franchise pipeline and ATL creative stakeholder collaboration history]
D10 - Quality and creative potential: [score 1-10] — [one sentence reason citing the brand-film narrative connection and whether both sides would amplify it]

PARTIAL_SCORE: [sum of D2 + D5 + D8 + D10]
DIMENSIONS_SCORED: D2, D5, D8, D10

Scoring rules:
- Use the anchor table in the rubric. Do not invent your own criteria.
- A low D5 score does not disqualify a title. Note the tension explicitly.
- Be specific. Name the tone, the IP, the director, the franchise.
- Do not hedge with ranges. Give one integer score per dimension.
- If information is insufficient to score confidently, score 5 and note the gap.
""",
    )
