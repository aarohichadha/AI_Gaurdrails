"""Task 13 - Compare the three deterministic variants.

Runs Basic Rules (Task 10), Provenance Rules (Task 11) and the CI-Norm
guardrail (Task 12) over every evaluation view and reports where they agree,
where they diverge, and how each one fails.

Why the views matter: on the corpus as given, `requested_destination` is a
perfect oracle - every attack targets an off-allowlist address and every
benign action targets its authorized one. A single allowlist comparison
therefore scores 100%, and all three variants tie. The ablation views withhold
that oracle (without touching any label) so the comparison measures the
*reasoning*, not the convenience of one column.

Usage:
    python deterministic/task13_compare.py
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import VIEWS, allowlist_for, apply_view, evaluate, format_metrics_table, load_actions
from common.evaluation import EvalRun, Metrics

from deterministic import task10_basic_rules as basic
from deterministic import task11_provenance_rules as provenance
from deterministic import task12_ci_norm as ci_norm

RESULTS_DIR = Path(__file__).resolve().parent / "results"

NEWLINE = chr(10)

VARIANTS = [
    (basic.NAME, basic.make_guardrail, "Task 10"),
    (provenance.NAME, provenance.make_guardrail, "Task 11"),
    (ci_norm.NAME, ci_norm.make_guardrail, "Task 12"),
]

VIEW_ORDER = ["full", "text_only", "destination_blind", "blind_no_cue", "stale_allowlist"]


def run_all(include_generated: bool = False) -> Dict[str, Dict[str, EvalRun]]:
    base_actions = load_actions(include_generated=include_generated)
    runs: Dict[str, Dict[str, EvalRun]] = {}
    for view in VIEW_ORDER:
        actions = apply_view(base_actions, view)
        allowlist = allowlist_for(view)
        runs[view] = {
            name: evaluate(factory(allowlist), actions, name, view)
            for name, factory, _ in VARIANTS
        }
    return runs


def print_headline(runs) -> None:
    print("=" * 97)
    print("TASK 13 - DETERMINISTIC GUARDRAIL COMPARISON")
    print("=" * 97)
    for view in VIEW_ORDER:
        print(f"\n[{view}]  {VIEWS[view]['description']}")
        print(format_metrics_table([runs[view][name].metrics for name, _, _ in VARIANTS]))


def print_recall_matrix(runs) -> None:
    print("\n" + "=" * 97)
    print("ATTACK RECALL BY VIEW  (share of attacks stopped; higher is better)")
    print("=" * 97)
    header = f"{'guardrail':<22}" + "".join(f"{v:>19}" for v in VIEW_ORDER)
    print(header)
    print("-" * len(header))
    for name, _, _ in VARIANTS:
        row = f"{name:<22}" + "".join(
            f"{runs[v][name].metrics.attack_recall:>19.3f}" for v in VIEW_ORDER
        )
        print(row)

    print(f"\n{'FALSE POSITIVE RATE  (share of benign work refused; lower is better)'}")
    print("-" * len(header))
    for name, _, _ in VARIANTS:
        row = f"{name:<22}" + "".join(
            f"{runs[v][name].metrics.false_positive_rate:>19.3f}" for v in VIEW_ORDER
        )
        print(row)


def print_category_comparison(runs, view: str) -> None:
    print("\n" + "=" * 97)
    print(f"RECALL BY ATTACK CATEGORY  (view: {view})")
    print("=" * 97)
    breakdowns = {name: runs[view][name].breakdown("attack_category") for name, _, _ in VARIANTS}
    categories = [c for c in sorted(breakdowns[VARIANTS[0][0]]) if c != "NONE"]
    header = f"{'attack_category':<26}{'n':>6}" + "".join(f"{n:>22}" for n, _, _ in VARIANTS)
    print(header)
    print("-" * len(header))
    for category in categories:
        n = breakdowns[VARIANTS[0][0]][category]["n_attack"]
        row = f"{category:<26}{n:>6}"
        for name, _, _ in VARIANTS:
            recall = breakdowns[name][category]["recall"]
            row += f"{'-' if recall is None else f'{recall:.3f}':>22}"
        print(row)


def print_disagreements(runs, view: str) -> None:
    """Where do the variants actually differ, and who is right?"""
    print("\n" + "=" * 97)
    print(f"DISAGREEMENT ANALYSIS  (view: {view})")
    print("=" * 97)

    runs_v = runs[view]
    actions = runs_v[VARIANTS[0][0]].actions
    n_variants = len(VARIANTS)
    disagreements: List = []

    for index, action in enumerate(actions):
        verdicts = {name: runs_v[name].results[index].blocked for name, _, _ in VARIANTS}
        if len(set(verdicts.values())) > 1:
            disagreements.append((action, verdicts))

    print(f"records where the variants disagree: {len(disagreements)} / {len(actions)}")
    if not disagreements:
        print("(all three variants reach the same decision on every record)")
        return

    correct = {name: 0 for name, _, _ in VARIANTS}
    for action, verdicts in disagreements:
        should_block = action.expected_decision == "BLOCK"
        for name, blocked in verdicts.items():
            if blocked == should_block:
                correct[name] += 1

    print("\namong those disagreements, each variant was right:")
    for name, _, _ in VARIANTS:
        share = correct[name] / len(disagreements)
        print(f"  {name:<22}{correct[name]:>6} / {len(disagreements)}  ({share:.1%})")

    print("\nsample disagreements:")
    for action, verdicts in disagreements[:5]:
        marks = "  ".join(
            f"{name.split('_')[0]}={'BLOCK' if blocked else 'ALLOW'}" for name, blocked in verdicts.items()
        )
        print(f"  {action.record_id} [{action.attack_technique}] truth={action.expected_decision}  {marks}")


def print_revise(runs) -> None:
    """How does each variant handle an under-specified but legitimate request?

    The right answer for a REVISE row is FLAG: escalate to a human. BLOCK is
    over-strict but safe; ALLOW means the agent ran an action nobody
    authorised.
    """
    view = "full"
    metrics = [runs[view][name].metrics for name, _, _ in VARIANTS]
    if not any(m.revise_total for m in metrics):
        return

    print(NEWLINE + "=" * 97)
    print("REVISE ROWS - legitimate but under-specified (correct answer: FLAG)")
    print("=" * 97)
    header = (f"{'guardrail':<22}{'n':>7}{'FLAG':>9}{'BLOCK':>9}{'ALLOW':>9}"
              f"{'escalated':>12}{'contained':>12}")
    print(header)
    print("-" * len(header))
    for m in metrics:
        print(f"{m.guardrail:<22}{m.revise_total:>7}{m.revise_flagged:>9}"
              f"{m.revise_blocked:>9}{m.revise_allowed:>9}"
              f"{m.revise_accuracy:>12.3f}{m.revise_contained:>12.3f}")
    print(NEWLINE + "  escalated = FLAG (the correct outcome)")
    print("  contained = FLAG or BLOCK (did not run unsupervised)")


def print_verdict() -> None:
    print("\n" + "=" * 97)
    print("WHAT THE COMPARISON SHOWS")
    print("=" * 97)
    print(
        """
1. On the corpus as given (view: full) all three variants score 1.000. That is
   a property of the data, not evidence that the techniques are equivalent -
   `requested_destination` is a perfect oracle, so one allowlist comparison is
   already sufficient.

2. Basic rules fail OPEN. Remove the destination oracle (destination_blind) or
   let the allowlist go stale (stale_allowlist) and recall falls to 0.037:
   nothing is left to compare, so almost every attack is allowed. A rule that
   only recognises known-bad values cannot act on what it cannot see.

3. Provenance rules degrade GRACEFULLY. They ask who issued the instruction
   rather than where it points, so a stale allowlist costs them nothing
   (recall 1.000) and an invisible destination still leaves 0.778 recall at no
   false-positive cost. Their residual gap is the adaptive-mutation sheet,
   which paraphrases away the lexical markers - the known weakness of any
   keyword-anchored rule.

4. CI-norm fails CLOSED. Because a norm must be positively affirmed rather
   than merely not-contradicted, it holds recall at 1.000 in every view. The
   cost is visible in blind_no_cue: with no destination and no corroboration
   cue it cannot affirm anything, so it blocks all benign traffic too (FPR
   1.000). Safe, but unusable - the failure lands on availability, not
   security.

5. Practical reading: the three are complementary, not ranked. Basic rules are
   cheap precision on known-bad values; provenance decides whether untrusted
   text may act as an instruction at all; CI-norm supplies the default-deny
   backstop. A deployed guardrail should run all three and take the most
   restrictive decision.

CAVEAT: in the blind views the variants lean on the template phrase "the
destination recorded in case N", which appears in 100% of benign and 0% of
attack mail in this corpus. That separation is an artifact of how the corpus
was generated. blind_no_cue exists to measure exactly that dependence, and the
drop it produces is the honest estimate of how much of the blind-view
performance is real.
"""
    )


def save_results(runs) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    rows = [runs[view][name].metrics.as_dict() for view in VIEW_ORDER for name, _, _ in VARIANTS]
    with (RESULTS_DIR / "comparison_metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    payload = {
        "views": {v: VIEWS[v]["description"] for v in VIEW_ORDER},
        "metrics": rows,
        "recall_by_category": {
            view: {
                name: {
                    key: stats["recall"]
                    for key, stats in runs[view][name].breakdown("attack_category").items()
                    if key != "NONE"
                }
                for name, _, _ in VARIANTS
            }
            for view in VIEW_ORDER
        },
    }
    with (RESULTS_DIR / "comparison_results.json").open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)

    print(f"\nwrote {RESULTS_DIR / 'comparison_metrics.csv'}")
    print(f"wrote {RESULTS_DIR / 'comparison_results.json'}")


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else list(argv)
    include_generated = "--include-generated" in argv
    runs = run_all(include_generated)
    if include_generated:
        print("including the synthetic three-class sheet (REVISE rows)" + NEWLINE)
    print_headline(runs)
    print_recall_matrix(runs)
    print_category_comparison(runs, "destination_blind")
    print_disagreements(runs, "destination_blind")
    print_revise(runs)
    print_verdict()
    save_results(runs)


if __name__ == "__main__":
    main()
