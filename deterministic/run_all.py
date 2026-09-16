"""Run Tasks 10-13 end to end.

    python deterministic/run_all.py            # headline view (full) + comparison
    python deterministic/run_all.py --view all # every task against every view
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import VIEWS, allowlist_for, apply_view, evaluate, format_metrics_table, load_actions

from deterministic import task10_basic_rules as basic
from deterministic import task11_provenance_rules as provenance
from deterministic import task12_ci_norm as ci_norm
from deterministic import task13_compare

TASKS = [
    ("Task 10", "Basic rule-based guardrail", basic),
    ("Task 11", "Provenance-based guardrail", provenance),
    ("Task 12", "Contextual-integrity norm guardrail", ci_norm),
]


def main() -> None:
    every_view = "--view" in sys.argv and "all" in sys.argv
    views = list(VIEWS) if every_view else ["full"]

    actions = load_actions()
    print(f"loaded {len(actions)} records from the corpus\n")

    for label, description, module in TASKS:
        print("=" * 97)
        print(f"{label} - {description}")
        print("=" * 97)
        rows = []
        for view in views:
            viewed = apply_view(actions, view)
            run = evaluate(module.make_guardrail(allowlist_for(view)), viewed, module.NAME, view)
            rows.append(run.metrics)
        print(format_metrics_table(rows))
        print()

    print()
    task13_compare.main()


if __name__ == "__main__":
    main()
