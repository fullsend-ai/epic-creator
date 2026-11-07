#!/usr/bin/env python3
"""Compute AI implementability scores from individual signal values.

Reads the epic's signal set from frontmatter and writes ai_implementability
+ ai_implementability_score back. Implementation epics use ai_signals (summed
against the thresholds below); Investigation epics use investigation_signals
(a routing model with guards — see classify_investigation).

Implementation thresholds:
    score >= 3  → High
    0 <= score <= 2 → Medium
    score <= -1 → Low

Usage:
    python3 scripts/compute_ai_scores.py RHAISTRAT-1234 RHAISTRAT-1235
    python3 scripts/compute_ai_scores.py --all
"""

import glob
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from artifact_utils import read_frontmatter, update_frontmatter
from workload import get_workload

SIGNAL_FIELDS = [
    "change_specificity",
    "pattern_precedent",
    "adapter_pattern",
    "existing_foundation",
    "open_questions",
    "external_dependency",
    "human_process_gates",
    "repo_access",
    "architecture_claims",
]

# Investigation epics use a separate, purpose-built signal set (see
# artifact_utils epic-task schema). It predicts, at decomposition time,
# whether the AI skill can resolve the unknowns or a person should.
INVESTIGATION_SIGNAL_FIELDS = [
    "question_specificity",
    "source_accessibility",
    "local_runnability",
    "cluster_hardware_dependence",
    "human_judgment_required",
]


def classify(score, planning=None):
    planning = planning or get_workload("rhaistrat-strategy").planning
    if score >= planning.implementation_high:
        return "High"
    elif score >= planning.implementation_medium:
        return "Medium"
    else:
        return "Low"


def classify_investigation(signals, planning=None):
    """Routing classification for Investigation epics.

    Returns (score, classification). The class is a routing decision
    (High = assign to the AI skill, Medium = hybrid: skill does the desk/
    local parts and hands off the rest, Low = assign to a person), NOT a
    pure function of the score — two guards override the sum:

      - Low guard: the skill can't meaningfully proceed — questions are too
        vague (question_specificity < 0), OR there is no AI-reachable oracle
        (nothing readable that *contains* the answer AND nothing runnable),
        OR the blockers drag the net negative.
      - High gate: only when nothing must be handed to a cluster or a human
        (both blockers == 0); any blocker caps the routing at Medium.
    """
    planning = planning or get_workload("rhaistrat-strategy").planning
    s = {f: (signals.get(f) or 0) for f in INVESTIGATION_SIGNAL_FIELDS}
    total = sum(s.values())
    spec = s["question_specificity"]
    src = s["source_accessibility"]
    run = s["local_runnability"]
    cluster = s["cluster_hardware_dependence"]
    human = s["human_judgment_required"]

    if spec < 0 or (src <= 0 and run <= 0) or total <= planning.investigation_low:
        return total, "Low"
    if total >= planning.investigation_high and cluster == 0 and human == 0:
        return total, "High"
    return total, "Medium"


def compute_for_epic(epic_path, planning=None):
    """Compute and write score for a single epic file.

    Returns (epic_id, score, classification) on success, or None if
    the epic has no ai_signals.
    """
    data, _ = read_frontmatter(epic_path)
    if not data:
        return None

    # Dispatch on the epic type, not on which signal block happens to be
    # present — an epic carrying a stale/duplicated block must still be scored
    # by its declared type's rubric. Investigation -> routing model; everything
    # else -> Implementation signal sum.
    if data.get("type") == "Investigation":
        inv = data.get("investigation_signals")
        if not isinstance(inv, dict):
            return None
        total, classification = classify_investigation(inv, planning)
    else:
        signals = data.get("ai_signals")
        if not signals or not isinstance(signals, dict):
            return None

        total = 0
        for field in SIGNAL_FIELDS:
            val = signals.get(field, 0)
            if val is None:
                val = 0
            total += val

        classification = classify(total, planning)

    epic_id = data.get("epic_id", os.path.basename(epic_path))

    old_score = data.get("ai_implementability_score")
    old_class = data.get("ai_implementability")

    if old_score != total or old_class != classification:
        update_frontmatter(epic_path, {
            "ai_implementability_score": total,
            "ai_implementability": classification,
        }, "epic-task")
        changed = " (updated)"
    else:
        changed = ""

    return epic_id, total, classification, changed


def compute_for_strategy(strat_id, planning=None):
    """Compute scores for all epics of a strategy. Returns count of epics processed."""
    # Two explicit patterns: conditional decompositions name their epics
    # {strat_id}-BRANCH-<X>-E00N.md, which {strat_id}-E*.md cannot match — the
    # 2026-08-19 eval's case-007 shipped 7 of 8 epics unscored because of it.
    # (Not {strat_id}*-E*.md: that would cross-match longer strategy ids
    # sharing the prefix.)
    epic_files = sorted(
        glob.glob(f"artifacts/epic-tasks/{strat_id}-E*.md")
        + glob.glob(f"artifacts/epic-tasks/{strat_id}-BRANCH-*-E*.md"))

    if not epic_files:
        print(f"  {strat_id}: no epic files found", file=sys.stderr)
        return 0

    count = 0
    for path in epic_files:
        result = compute_for_epic(path, planning)
        if result:
            epic_id, score, classification, changed = result
            print(f"  {epic_id}: {score} ({classification}){changed}",
                  file=sys.stderr)
            count += 1
        else:
            print(f"  {os.path.basename(path)}: no signals, skipped",
                  file=sys.stderr)

    return count


def main():
    if len(sys.argv) < 2:
        print("Usage: compute_ai_scores.py [--all | STRAT-ID ...]",
              file=sys.stderr)
        sys.exit(1)

    if sys.argv[1] == "--all":
        epic_files = sorted(glob.glob("artifacts/epic-tasks/*-E*.md"))
        strat_ids = set()
        for path in epic_files:
            basename = os.path.basename(path)
            parts = basename.rsplit("-E", 1)
            if len(parts) == 2:
                strat_ids.add(parts[0])
        strat_ids = sorted(strat_ids)
    else:
        strat_ids = sys.argv[1:]

    total = 0
    for strat_id in strat_ids:
        print(f"Computing scores for {strat_id}:", file=sys.stderr)
        total += compute_for_strategy(strat_id)

    print(f"Computed scores for {total} epics across "
          f"{len(strat_ids)} strategies", file=sys.stderr)


if __name__ == "__main__":
    main()
