"""
eval_harness.py -- Opportunity Scorer, Stage 6
Demonstrated concept: Security (human gate + hard block, tested and proven)

Reads cached outputs from orchestrator.py. Does not re-run scoring.
Tests four failure conditions (FC1-FC4) and two gate invariants (GI1-GI2).
Surfaces self-correction loop results from the consistency report.

Run: uv run python eval_harness.py
Output: outputs/eval_report.json
"""

import json
import re
import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SCORES_FILE      = Path("outputs/scores.json")
CONSISTENCY_FILE = Path("outputs/consistency_report.json")
REPORT_FILE      = Path("outputs/eval_report.json")

EXPECTED_TITLE_COUNT = 15
GATE_THRESHOLD       = 65   # GI1: gate must always fire at this score
SWAY_FLAG_LIMIT      = 3    # FC4: >3 flags in one run = systemic prompt drift


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_json(path):
    with open(path) as f:
        return json.load(f)


def extract_score(final_scorecard):
    """Parse integer total from 'TOTAL SCORE: 67/100' in the scorecard string."""
    match = re.search(r"TOTAL SCORE:\s*(\d+)/100", final_scorecard)
    return int(match.group(1)) if match else None


def is_sway_flagged(sway_verdict):
    return sway_verdict.strip().startswith("SWAY CHECK: FLAG")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_fc1_completeness(all_titles):
    """FC1: All 15 titles must be present. Missing titles = 503 crash or early exit."""
    count = len(all_titles)
    return {
        "test": "FC1",
        "description": "All 15 titles scored (503 crash / early-exit check)",
        "expected": EXPECTED_TITLE_COUNT,
        "actual": count,
        "result": "PASS" if count == EXPECTED_TITLE_COUNT else "FAIL",
    }


def test_fc2_no_false_positives(human_queue):
    """FC2: No title below the gate threshold may route to human."""
    failures = []
    for title in human_queue:
        score = extract_score(title["final_scorecard"])
        if score is not None and score < GATE_THRESHOLD:
            failures.append({"title_id": title["title_id"], "score": score})
    return {
        "test": "FC2",
        "description": f"No title below {GATE_THRESHOLD} routed to human",
        "failures": failures,
        "result": "PASS" if not failures else "FAIL",
    }


def test_fc3_no_false_negatives(archived):
    """FC3: No title at or above the gate threshold may be archived."""
    failures = []
    for title in archived:
        score = extract_score(title["final_scorecard"])
        if score is not None and score >= GATE_THRESHOLD:
            failures.append({"title_id": title["title_id"], "score": score})
    return {
        "test": "FC3",
        "description": f"No title at or above {GATE_THRESHOLD} archived",
        "failures": failures,
        "result": "PASS" if not failures else "FAIL",
    }


def test_fc4_sway_count(all_titles):
    """FC4: More than 3 SWAY flags in one run signals systemic prompt drift."""
    flagged = [t["title_id"] for t in all_titles if is_sway_flagged(t["sway_verdict"])]
    count = len(flagged)
    return {
        "test": "FC4",
        "description": f"SWAY flag count <= {SWAY_FLAG_LIMIT} (>{SWAY_FLAG_LIMIT} = systemic prompt drift)",
        "sway_flag_count": count,
        "sway_flagged_titles": flagged,
        "result": "PASS" if count <= SWAY_FLAG_LIMIT else "FAIL",
    }


def test_gi1_threshold(summary):
    """GI1: Confirm the gate threshold the orchestrator applied matches this harness."""
    reported = summary.get("gate_threshold")
    return {
        "test": "GI1",
        "description": f"Gate threshold confirmed at {GATE_THRESHOLD}",
        "reported_threshold": reported,
        "result": "PASS" if reported == GATE_THRESHOLD else "FAIL",
    }


def test_gi2_timestamps(all_titles):
    """
    GI2: Per-title run timestamp required (Gate 5 Q2) so score changes can be tracked
    over time after casting changes, director controversies, or content events.
    Fixed June 23, 2026 -- orchestrator.py now writes run_timestamp to each title record.
    """
    missing = [t["title_id"] for t in all_titles if "run_timestamp" not in t]
    if missing:
        return {
            "test": "GI2",
            "description": "Per-title run timestamp present in scores.json",
            "result": "FAIL",
            "missing_from": missing,
            "note": "run_timestamp field absent. Re-run orchestrator.py with the June 23 fix applied.",
        }
    return {
        "test": "GI2",
        "description": "Per-title run timestamp present in scores.json",
        "result": "PASS",
        "sample_timestamp": all_titles[0].get("run_timestamp"),
    }


# ---------------------------------------------------------------------------
# Self-correction loop report (visibility, not a pass/fail gate)
# ---------------------------------------------------------------------------

def consistency_report(consistency):
    """
    Surface which titles the self-correction loop flagged for dimension variance > 1.
    Gate 5 Q1: 1-point variance per dimension is acceptable. 2+ is flagged.
    """
    flagged = []
    for title_id, data in consistency.items():
        if data.get("has_inconsistency"):
            flagged.append({
                "title_id": title_id,
                "title": data["title"],
                "flagged_dimensions": data["flagged_dimensions"],
            })
    return flagged


# ---------------------------------------------------------------------------
# Overall result
# ---------------------------------------------------------------------------

def overall_result(test_results):
    statuses = [t["result"] for t in test_results]
    if "FAIL" in statuses:
        return "FAIL"
    if "WARN" in statuses:
        return "WARN"
    return "PASS"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("EVAL HARNESS -- filmIQ")
    print("Stage 7 | Concepts: Multi-Agent Orchestration · Agent Skills · Security by Design · Deployability")
    print("Reading cached outputs from outputs/scores.json")
    print("-" * 60)

    scores      = load_json(SCORES_FILE)
    consistency = load_json(CONSISTENCY_FILE)

    human_queue = scores["human_review_queue"]
    archived    = scores["archived"]
    summary     = scores["summary"]
    all_titles  = human_queue + archived

    # Run tests
    tests = [
        test_fc1_completeness(all_titles),
        test_fc2_no_false_positives(human_queue),
        test_fc3_no_false_negatives(archived),
        test_fc4_sway_count(all_titles),
        test_gi1_threshold(summary),
        test_gi2_timestamps(all_titles),
    ]

    # Print test results
    print()
    for t in tests:
        status = t["result"]
        print(f"[{status:4}] {t['test']}: {t['description']}")
        if status == "FAIL" and "failures" in t and t["failures"]:
            for f in t["failures"]:
                print(f"         -> {f['title_id']} score={f['score']}")
        if status == "FAIL" and "missing_from" in t:
            print(f"         -> {t['note']}")
        if status == "PASS" and "sample_timestamp" in t:
            print(f"         -> sample: {t['sample_timestamp']}")

    # SWAY summary
    fc4 = next(t for t in tests if t["test"] == "FC4")
    sway_count   = fc4["sway_flag_count"]
    sway_flagged = fc4["sway_flagged_titles"]
    print()
    label = ", ".join(sway_flagged) if sway_flagged else "none"
    print(f"SWAY flags this run: {sway_count} ({label})")

    # Consistency report
    flagged_consistency = consistency_report(consistency)
    print()
    print("SELF-CORRECTION LOOP:")
    if flagged_consistency:
        for f in flagged_consistency:
            dims = ", ".join(f["flagged_dimensions"])
            print(f"  FLAG  {f['title_id']} ({f['title']}): dimension variance > 1 on {dims}")
    else:
        print("  No dimension variance flags.")

    # Overall result
    result = overall_result(tests)
    print()
    print(f"RESULT: {result}")
    print("-" * 60)

    # Write structured report
    report = {
        "eval_run_timestamp": datetime.datetime.now().isoformat(),
        "overall_result": result,
        "tests": tests,
        "sway_flag_count": sway_count,
        "sway_flagged_titles": sway_flagged,
        "consistency_flags": flagged_consistency,
    }
    with open(REPORT_FILE, "w") as f:
        json.dump(report, f, indent=2)

    print(f"Report written to {REPORT_FILE}")


if __name__ == "__main__":
    main()
