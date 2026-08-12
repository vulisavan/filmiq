"""
Unit tests for the deterministic logic in agents/ranking_agent.py.

These cover the pure functions only: no network, no API key, no cost.
They describe behaviour that is already correct, so that the fixes in
F05, F06 and F30 can be made without silently breaking it.
"""

from agents.ranking_agent import (
    HUMAN_GATE,
    LC_COUNT,
    LC_FLOOR,
    NEUTRAL_D5_FLOOR,
    apply_gate,
    build_summary,
    check_low_confidence,
    parse_partial_scorecard,
)

# ---------------------------------------------------------------------------
# parse_partial_scorecard
# ---------------------------------------------------------------------------


def test_parses_well_formed_dimension_lines() -> None:
    """Scores and reasons are both extracted from conforming output."""
    output = (
        "D1 - Audience reach and demographic fit: 8 — Strong overlap with core demo\n"
        "D2 - Brand alignment and safety: 7 — No conflicts identified"
    )
    parsed = parse_partial_scorecard(output)
    assert parsed["scores"] == {"D1": 8, "D2": 7}
    assert parsed["reasons"]["D1"] == "Strong overlap with core demo"


def test_caps_scores_above_ten() -> None:
    """A score above the 0-10 range is clamped rather than accepted."""
    parsed = parse_partial_scorecard("D3 - Cultural moment: 47 — Extremely high buzz")
    assert parsed["scores"]["D3"] == 10


def test_malformed_line_yields_no_dimension() -> None:
    """
    A line missing the dash separator does not parse.

    The dimension is absent from the result — not zero, not defaulted.
    Whatever fills that gap is decided downstream, not here.
    """
    parsed = parse_partial_scorecard("D1 - Audience reach: 8. Strong overlap.")
    assert parsed["scores"] == {}


def test_prose_yields_no_dimensions() -> None:
    """Narrative text with no dimension lines parses to nothing."""
    parsed = parse_partial_scorecard("The film scores well across the board.")
    assert parsed["scores"] == {}
    assert parsed["reasons"] == {}


# ---------------------------------------------------------------------------
# apply_gate
# ---------------------------------------------------------------------------


def test_gate_is_inclusive_at_the_threshold() -> None:
    """
    The boundary, not values far from it, is where off-by-one lives.

    Written relative to HUMAN_GATE so this asserts the shape of the
    comparison (>=), independent of what the threshold is set to.
    """
    assert apply_gate(HUMAN_GATE - 1) is False
    assert apply_gate(HUMAN_GATE) is True
    assert apply_gate(HUMAN_GATE + 1) is True


def test_configured_thresholds_match_the_documented_values() -> None:
    """
    Pins the config values the README and the rubric describe.

    Separate from the test above on purpose: if someone edits config.ini,
    this fails and names the real cause instead of making the gate logic
    look broken.
    """
    assert HUMAN_GATE == 65
    assert LC_FLOOR == 4
    assert LC_COUNT == 2


# ---------------------------------------------------------------------------
# check_low_confidence
# ---------------------------------------------------------------------------


def test_two_low_dimensions_trigger_the_flag() -> None:
    """Two dimensions at or below the floor is the documented trigger."""
    is_lc, reason = check_low_confidence({"D1": 4, "D2": 3, "D3": 9})
    assert is_lc is True
    assert "D1" in reason and "D2" in reason


def test_one_low_dimension_does_not_trigger_the_flag() -> None:
    """One is below the count trigger, so the flag stays down."""
    is_lc, reason = check_low_confidence({"D1": 4, "D2": 8})
    assert is_lc is False
    assert reason == ""


def test_d6_flag_alone_triggers_low_confidence() -> None:
    """The ROI agent's own flag fires independently of the score floor."""
    is_lc, reason = check_low_confidence({"D1": 9, "D2": 8}, d6_lc_flag=True)
    assert is_lc is True
    assert "D6" in reason


def test_both_triggers_report_both_reasons() -> None:
    """Two independent causes produce two reasons, not one."""
    is_lc, reason = check_low_confidence({"D1": 2, "D2": 4}, d6_lc_flag=True)
    assert is_lc is True
    assert "dimensions scored" in reason
    assert "D6" in reason

    # ---------------------------------------------------------------------------
# build_summary — defaulted dimensions (F06)
# ---------------------------------------------------------------------------

TITLE = {"id": "T001", "title": "Test Title"}
ROI_JSON = "{}"
NON_FEASIBLE_TITLE = {
    "id": "T002",
    "title": "Animated Test Title",
    "integration_feasible": False,
    "integration_feasibility_note": "Animated; no in-world product placement path.",
}


def scorecard(scores: dict[str, int]) -> str:
    """Renders a cluster-agent output containing exactly the given dimensions."""
    return "\n".join(
        f"{dim} - Dimension {dim}: {score} — scored by a cluster agent"
        for dim, score in scores.items()
    )


def all_ten(score: int = 5) -> dict[str, int]:
    """A complete scorecard. 5 is above LC_FLOOR, so no dimension is low."""
    return {f"D{i}": score for i in range(1, 11)}


def test_defaulted_dimensions_lists_only_the_unscored_ones() -> None:
    scores = all_ten()
    del scores["D3"]
    del scores["D7"]
    result = build_summary(TITLE, [scorecard(scores)], ROI_JSON)
    assert result["defaulted_dimensions"] == ["D3", "D7"]


def test_a_defaulted_dimension_forces_routing_below_the_gate() -> None:
    scores = all_ten()
    del scores["D3"]
    result = build_summary(TITLE, [scorecard(scores)], ROI_JSON)
    # The gate on its own would archive this title.
    assert apply_gate(result["total_score"]) is False
    assert result["route_to_human"] is True


def test_a_single_defaulted_dimension_forces_low_confidence() -> None:
    scores = all_ten()
    del scores["D3"]
    result = build_summary(TITLE, [scorecard(scores)], ROI_JSON)
    # One defaulted dimension is fewer than LC_COUNT, so check_low_confidence
    # would not fire on its own. Only the F06 escalation sets this.
    assert LC_COUNT > 1
    assert result["low_confidence"] is True
    assert "D3" in result["low_confidence_reason"]


def test_a_defaulted_dimension_scores_zero_and_says_why() -> None:
    scores = all_ten()
    del scores["D3"]
    result = build_summary(TITLE, [scorecard(scores)], ROI_JSON)
    assert result["dimension_scores"]["D3"] == 0
    assert len(result["dimension_scores"]) == 10
    assert "not earned" in result["dimension_reasons"]["D3"].lower()


def test_a_clean_scorecard_leaves_routing_to_the_gate() -> None:
    result = build_summary(TITLE, [scorecard(all_ten())], ROI_JSON)
    assert result["defaulted_dimensions"] == []
    assert result["route_to_human"] is apply_gate(result["total_score"])


# ---------------------------------------------------------------------------
# F30 — neutral D5 floor for non-feasible titles
# ---------------------------------------------------------------------------


def test_non_feasible_title_gets_the_neutral_floor() -> None:
    """A low D5 is replaced by the neutral floor and the total rises by the gap."""
    scores = all_ten()
    scores["D5"] = 2
    raw_total = sum(scores.values())
    result = build_summary(NON_FEASIBLE_TITLE, [scorecard(scores)], ROI_JSON)
    assert result["dimension_scores"]["D5"] == NEUTRAL_D5_FLOOR
    assert result["d5_pre_floor"] == 2
    # INV-1 shape: removing the floor must reproduce the lower total.
    assert result["total_score"] == raw_total - 2 + NEUTRAL_D5_FLOOR


def test_feasible_title_keeps_its_scored_d5() -> None:
    """The floor must not touch a feasible title, however low D5 scored."""
    scores = all_ten()
    scores["D5"] = 2
    raw_total = sum(scores.values())
    result = build_summary(TITLE, [scorecard(scores)], ROI_JSON)
    assert result["dimension_scores"]["D5"] == 2
    assert result["d5_pre_floor"] is None
    assert result["total_score"] == raw_total


def test_non_feasible_title_surfaces_its_feasibility() -> None:
    """INV-2 groundwork: the flag and note reach the payload unnested."""
    result = build_summary(NON_FEASIBLE_TITLE, [scorecard(all_ten())], ROI_JSON)
    assert result["integration_feasible"] is False
    assert result["feasibility_note"] != ""


def test_an_unscored_d5_on_a_non_feasible_title_still_escalates() -> None:
    """The floor runs after the fill, so the fill cannot overwrite it with 0."""
    scores = all_ten()
    del scores["D5"]
    result = build_summary(NON_FEASIBLE_TITLE, [scorecard(scores)], ROI_JSON)
    # defaulted_dimensions is computed above both the fill and the floor,
    # so escalation fires regardless of their order.
    assert "D5" in result["defaulted_dimensions"]
    assert result["route_to_human"] is True
    assert result["low_confidence"] is True
    # d5_pre_floor captures what the fill wrote, not "no value" -- the fill
    # runs first, so an unscored D5 is already 0 by the time the floor reads it.
    assert result["d5_pre_floor"] == 0
    assert result["dimension_scores"]["D5"] == NEUTRAL_D5_FLOOR

