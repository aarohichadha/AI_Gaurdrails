"""Hybrid guardrail matrix - every prompt-defense agent (Tasks 7-9) crossed
with every deterministic rule set (Tasks 10-12), on the same 100-pair
(200-record) pilot slice `llm_judge`/`task7_to_llm_judge.py` already use.

For each of the 3 prompt versions the agent is run once (an LLM call per
record, cached in `results/traces/`); the 3 deterministic rule sets are then
applied to the same records for free, and `hybrid.combine.combine()` merges
each (prompt_version, rule_set) pair by taking the most restrictive decision.
That gives 9 combined rows, plus 3 prompt-alone and 3 rule-alone rows as a
baseline for whether combining buys anything over either alone.

Usage:
    # wiring check, no network call, no API key
    python hybrid/run_matrix.py --view destination_blind --provider mock --limit 4

    # the real 100-pair pilot, local Ollama
    python hybrid/run_matrix.py --view destination_blind --provider ollama --model llama3.2:1b
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import allowlist_for, evaluate, format_breakdown
from common.schema import Action, GuardrailResult

from hybrid.combine import combine
from hybrid.pilot import pilot_actions
from hybrid.traces import generate_traces

from prompt.runner import DEFAULT_MODELS, build_provider

from deterministic import task10_basic_rules as basic
from deterministic import task11_provenance_rules as provenance
from deterministic import task12_ci_norm as ci_norm

RESULTS_DIR = Path(__file__).resolve().parent / "results"
TRACES_DIR = RESULTS_DIR / "traces"

PROMPT_VERSIONS = ["v7_baseline", "v8_basic_security", "v9_context_aware"]
RULE_TASKS = [
    (basic.NAME, basic.make_guardrail),
    (provenance.NAME, provenance.make_guardrail),
    (ci_norm.NAME, ci_norm.make_guardrail),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--view", default="destination_blind",
        help="evaluation view (see common.dataset.VIEWS); destination_blind is where the "
        "deterministic rule sets actually diverge (see deterministic/README.md)",
    )
    parser.add_argument("--provider", default="mock", choices=["mock", "openai", "ollama"])
    parser.add_argument("--model", default=None)
    parser.add_argument("--n-pairs", type=int, default=100, help="paired pilot size = 2x this")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--limit", type=int, default=None, help="cap records processed (wiring checks)")
    args = parser.parse_args()
    model = args.model or DEFAULT_MODELS[args.provider]

    actions = pilot_actions(view=args.view, n_pairs=args.n_pairs, seed=args.seed)
    if args.limit:
        actions = actions[: args.limit]
    print(
        f"pilot: {len(actions)} records ({args.n_pairs} paired benign/attack), "
        f"view={args.view!r}, provider={args.provider}, model={model}\n"
    )

    provider = build_provider(args.provider)
    allowlist = allowlist_for(args.view)

    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    agent_traces: Dict[str, Dict[str, "AgentDecision"]] = {}
    start = time.monotonic()
    for prompt_version in PROMPT_VERSIONS:
        cache_path = TRACES_DIR / f"{prompt_version}_{args.view}_{model.replace(':', '-')}.jsonl"
        print(f"generating/loading traces for {prompt_version} ({args.provider}:{model})...")
        agent_traces[prompt_version] = generate_traces(
            actions, prompt_version, provider, model=model, view=args.view, cache_path=cache_path,
        )
    elapsed = time.monotonic() - start
    print(f"\nagent traces ready in {elapsed:.1f}s\n")

    rule_results: Dict[str, Dict[str, GuardrailResult]] = {}
    for rule_name, factory in RULE_TASKS:
        guardrail = factory(allowlist)
        rule_results[rule_name] = {a.record_id: guardrail(a) for a in actions}

    rows = []

    for prompt_version in PROMPT_VERSIONS:
        guardrail = lambda action, pv=prompt_version: agent_traces[pv][action.record_id].to_guardrail_result()
        run = evaluate(guardrail, actions, f"{prompt_version} (alone)", args.view)
        rows.append(run.metrics)

    for rule_name, _ in RULE_TASKS:
        guardrail = lambda action, rn=rule_name: rule_results[rn][action.record_id]
        run = evaluate(guardrail, actions, f"{rule_name} (alone)", args.view)
        rows.append(run.metrics)

    combo_runs = {}
    for prompt_version in PROMPT_VERSIONS:
        for rule_name, _ in RULE_TASKS:
            def guardrail(action, pv=prompt_version, rn=rule_name):
                return combine(
                    agent_traces[pv][action.record_id].to_guardrail_result(),
                    rule_results[rn][action.record_id],
                )
            name = f"{prompt_version}+{rule_name}"
            run = evaluate(guardrail, actions, name, args.view)
            combo_runs[name] = run
            rows.append(run.metrics)

    print_matrix(rows)
    save_results(rows, args.view)

    print()
    print(format_breakdown(
        "Recall by attack category (v9_context_aware+ci_norm)",
        combo_runs["v9_context_aware+ci_norm"].breakdown("attack_category"),
    ))


def print_matrix(rows) -> None:
    """A `format_metrics_table`-style printout, but sized to the longer
    `promptversion+rule_set` names this matrix produces (the shared helper's
    fixed 22-char column is tuned for the deterministic variants' shorter
    names and truncates/overlaps here)."""
    name_width = max(len(m.guardrail) for m in rows) + 2
    header = (
        f"{'guardrail':<{name_width}}{'n':>6}{'acc':>8}{'recall':>9}"
        f"{'prec':>8}{'F1':>8}{'FPR':>8}{'flag%':>8}"
    )
    print(header)
    print("-" * len(header))
    for m in rows:
        print(
            f"{m.guardrail:<{name_width}}{m.n:>6}"
            f"{m.accuracy:>8.3f}{m.attack_recall:>9.3f}"
            f"{m.precision:>8.3f}{m.f1:>8.3f}"
            f"{m.false_positive_rate:>8.3f}{m.escalation_rate:>8.3f}"
        )


def save_results(rows, view: str) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_csv = RESULTS_DIR / f"hybrid_matrix_{view}.csv"
    dicts = [m.as_dict() for m in rows]
    with out_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(dicts[0].keys()))
        writer.writeheader()
        writer.writerows(dicts)
    print(f"\nwrote {out_csv}")


if __name__ == "__main__":
    main()
