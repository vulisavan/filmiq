"""
Unit tests for the deterministic logic in agents/ranking_agent.py.

These cover the pure functions only: no network, no API key, no cost.
They describe behaviour that is already correct, so that the fixes in
F05 and F06 can be made without silently breaking it.
"""

from agents.ranking_agent import (
    HUMAN_GATE,
    LC_COUNT,
    LC_FLOOR,
    apply_gate,
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
