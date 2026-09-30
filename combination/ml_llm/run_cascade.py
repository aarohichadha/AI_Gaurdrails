"""Task 25 - ML -> LLM judge cascade.

                 every action
                      |
                  ML stage            cheap, runs on everything
                      |
            +---------+---------+
            |                   |
        confident            uncertain          <- routing strategy
            |                   |
       ML decision         LLM judge            expensive, runs on a few
            |                   |
            +---------+---------+
                      |
                 final decision

The point of a cascade is buying accuracy with a *budget*: escalate only the
fraction of traffic the ML stage is least sure about, and let the judge decide
those. Every run therefore reports the escalation rate alongside the metrics -
an improvement that costs 100% escalation is just the judge with extra steps.

    python combination/ml_llm/run_cascade.py --judge oracle
    python combination/ml_llm/run_cascade.py --judge gemini --budget 0.1
    python combination/ml_llm/run_cascade.py --judge gemini --sweep
    python combination/ml_llm/run_cascade.py --ml rf xgb pg2_86m --judge oracle --sweep

Evaluation is on held-out rows only (the corpus test split plus the flow and
adaptive stress sheets), because the RF/XGBoost stages are trained on the
train split.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from classifiers import task17_feature_models as task17
from combination.ml_llm.judges import JudgeVerdict, QuotaExhausted, build_judge
from common import apply_view, evaluate, load_actions
from common.schema import Action, Decision, GuardrailResult

RESULTS_DIR = ROOT / "combination" / "results" / "ml+llm"
CORPUS_FEATURES = ROOT / "classifiers" / "results" / "task17_training_set.csv"

PROMPTGUARD_LOGS = {
    "pg1_86m": ROOT / "classifiers" / "results" / "task14_pg1-86m_untrusted_full.jsonl",
    "pg2_22m": ROOT / "classifiers" / "results" / "task15_pg2-22m_untrusted_full.jsonl",
    "pg2_86m": ROOT / "classifiers" / "results" / "task15_pg2-86m_untrusted_full.jsonl",
}

#: Held-out sheets: never seen by the ML stage during training.
HELD_OUT_SPLITS = ("test", "flow_test", "adaptive_test")


# ---------------------------------------------------------------------------
# ML stage
# ---------------------------------------------------------------------------

@dataclass
class MLStage:
    """A first-stage model: a decision plus a confidence, per record."""

    name: str
    decision: Dict[str, Decision]
    #: 0 = fully confident, 1 = maximally uncertain.
    uncertainty: Dict[str, float]

    def covers(self, actions: Sequence[Action]) -> bool:
        return all(a.record_id in self.decision for a in actions)


def _margin_uncertainty(probabilities: np.ndarray) -> np.ndarray:
    """1 - (top probability - runner-up). Small margin = uncertain."""
    ordered = np.sort(probabilities, axis=1)
    return 1.0 - (ordered[:, -1] - ordered[:, -2])


def _entropy_uncertainty(probabilities: np.ndarray) -> np.ndarray:
    clipped = np.clip(probabilities, 1e-12, 1.0)
    entropy = -(clipped * np.log(clipped)).sum(axis=1)
    return entropy / math.log(probabilities.shape[1])


def _confidence_uncertainty(probabilities: np.ndarray) -> np.ndarray:
    return 1.0 - probabilities.max(axis=1)


UNCERTAINTY = {
    "margin": _margin_uncertainty,
    "entropy": _entropy_uncertainty,
    "confidence": _confidence_uncertainty,
}


def train_stage(kind: str, strategy: str, seed: int = 0) -> MLStage:
    """Train RF or XGBoost on the Task 17 train split and score every record."""
    from sklearn.preprocessing import LabelEncoder

    X, y, names, meta = task17.load_dataset(CORPUS_FEATURES)
    train_index, _, _ = task17.split_data(X, y, meta, seed)
    encoder = LabelEncoder().fit(y[train_index])

    model = task17.build_models("rf" if kind == "rf" else "xgb", seed, len(encoder.classes_))
    model = model["random_forest" if kind == "rf" else "xgboost"]
    if kind == "rf":
        model.fit(X[train_index], encoder.transform(y[train_index]))
    else:
        model.fit(X[train_index], encoder.transform(y[train_index]),
                  sample_weight=task17.sample_weights(y[train_index]))

    probabilities = model.predict_proba(X)
    predicted = encoder.inverse_transform(probabilities.argmax(axis=1))
    uncertainty = UNCERTAINTY[strategy](probabilities)

    to_decision = {"SAFE": Decision.ALLOW, "REVISE": Decision.FLAG, "ATTACK": Decision.BLOCK}
    return MLStage(
        name=kind,
        decision={rid: to_decision[label] for rid, label in zip(meta["record_id"], predicted)},
        uncertainty={rid: float(value) for rid, value in zip(meta["record_id"], uncertainty)},
    )


def promptguard_stage(kind: str, threshold: float = 0.5) -> MLStage:
    """A cached Prompt Guard run as the first stage.

    Prompt Guard emits one attack probability, so uncertainty is distance from
    the decision threshold, rescaled so 0 = far from it, 1 = sitting on it.
    """
    path = PROMPTGUARD_LOGS[kind]
    if not path.exists():
        raise SystemExit(f"missing cached Prompt Guard scores: {path}")

    decision: Dict[str, Decision] = {}
    uncertainty: Dict[str, float] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            score = float(row["score"])
            decision[row["record_id"]] = Decision.BLOCK if score >= threshold else Decision.ALLOW
            span = max(threshold, 1 - threshold)
            uncertainty[row["record_id"]] = max(0.0, 1.0 - abs(score - threshold) / span)
    return MLStage(name=kind, decision=decision, uncertainty=uncertainty)


def build_stage(kind: str, strategy: str, seed: int = 0) -> MLStage:
    if kind in ("rf", "xgb"):
        return train_stage(kind, strategy, seed)
    if kind in PROMPTGUARD_LOGS:
        return promptguard_stage(kind)
    raise KeyError(f"unknown ML stage {kind!r}")


# ---------------------------------------------------------------------------
# Cascade
# ---------------------------------------------------------------------------

def select_actions(include_generated: bool, splits: Sequence[str]) -> List[Action]:
    import pandas as pd

    from common.dataset import DATA_SHEETS, DATASET, GENERATED_SHEET

    workbook = pd.ExcelFile(DATASET)
    sheets = list(DATA_SHEETS) + ([GENERATED_SHEET] if include_generated else [])
    lookup: Dict[str, str] = {}
    for sheet in sheets:
        if sheet not in workbook.sheet_names:
            continue
        frame = workbook.parse(sheet, usecols=["record_id", "split"])
        lookup.update(dict(zip(frame["record_id"].astype(str), frame["split"].astype(str))))

    actions = apply_view(load_actions(include_generated=include_generated), "full")
    if "all" in splits:
        return actions
    return [a for a in actions if lookup.get(a.record_id) in splits]


def route(stage: MLStage, actions: Sequence[Action], budget: float) -> List[Action]:
    """The `budget` share of actions the stage is least sure about."""
    if budget <= 0:
        return []
    ranked = sorted(actions, key=lambda a: -stage.uncertainty.get(a.record_id, 0.0))
    count = min(len(ranked), max(1, round(budget * len(ranked))))
    # Do not escalate records the stage is completely certain about, even if
    # the budget would allow it - that spends calls on nothing.
    return [a for a in ranked[:count] if stage.uncertainty.get(a.record_id, 0.0) > 0.0]


def run_cascade(
    stage: MLStage,
    actions: Sequence[Action],
    judge: Optional[Callable[[Action], JudgeVerdict]],
    budget: float,
    progress: bool = True,
) -> Tuple[Dict[str, GuardrailResult], dict]:
    escalated = {a.record_id for a in route(stage, actions, budget)} if judge else set()
    results: Dict[str, GuardrailResult] = {}
    verdicts: Dict[str, JudgeVerdict] = {}

    started = time.perf_counter()
    for index, action in enumerate(actions, 1):
        result = GuardrailResult(record_id=action.record_id)
        ml_decision = stage.decision.get(action.record_id, Decision.FLAG)

        if action.record_id in escalated:
            verdict = judge(action)
            verdicts[action.record_id] = verdict
            if verdict.decision is not Decision.ALLOW:
                result.apply(verdict.decision, "LLM_JUDGE", f"judge said {verdict.raw_decision}")
            else:
                result.reasons.append("judge said ALLOW")
            result.rules_fired.append("ESCALATED")
        else:
            if ml_decision is not Decision.ALLOW:
                result.apply(ml_decision, f"ML:{stage.name}", f"{stage.name} predicted {ml_decision.name}")
            else:
                result.reasons.append(f"{stage.name} predicted ALLOW")

        results[action.record_id] = result
        if progress and escalated and index % 50 == 0:
            done = sum(1 for a in list(actions)[:index] if a.record_id in escalated)
            print(f"\r  {index}/{len(actions)} records, {done} judged", end="", flush=True)

    if progress and escalated:
        print()

    live_calls = sum(1 for v in verdicts.values() if not v.cached)
    stats = {
        "escalated": len(escalated),
        "routed_rate": len(escalated) / len(actions) if actions else 0.0,
        "judge_calls": live_calls,
        "judge_cached": len(verdicts) - live_calls,
        "judge_invalid": sum(1 for v in verdicts.values() if v.raw_decision == "INVALID"),
        "wall_seconds": round(time.perf_counter() - started, 1),
        "judge_verdicts": {k: v.raw_decision for k, v in verdicts.items()},
    }
    return results, stats


def score(results: Dict[str, GuardrailResult], actions: Sequence[Action], name: str):
    return evaluate(lambda action: results[action.record_id], actions, name, "full")


def judged_only_accuracy(results, actions, stats) -> Optional[dict]:
    """Did the judge actually improve the records it was given?"""
    escalated = set(stats["judge_verdicts"])
    if not escalated:
        return None
    subset = [a for a in actions if a.record_id in escalated]
    correct = 0
    for action in subset:
        decision = results[action.record_id].decision
        expected = action.expected_decision
        right = (
            (expected == "ALLOW" and decision is Decision.ALLOW)
            or (expected == "BLOCK" and decision is Decision.BLOCK)
            or (expected == "REVISE" and decision is Decision.FLAG)
        )
        correct += int(right)
    return {"n": len(subset), "accuracy": round(correct / len(subset), 4)}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[Sequence[str]] = None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ml", nargs="+", default=["xgb"],
                        choices=["rf", "xgb", *PROMPTGUARD_LOGS])
    parser.add_argument("--judge", default="oracle", choices=["oracle", "coin", "gemini"])
    parser.add_argument("--judge-model", default="gemini-3.5-flash-lite")
    parser.add_argument("--rpm", type=float, default=12.0,
                        help="judge requests per minute (free-tier quota is per-minute)")
    parser.add_argument("--judge-method", default="full_defense",
                        help="prompt variant from llm_judge.judge.METHODS")
    parser.add_argument("--strategy", nargs="+", default=["margin"],
                        choices=sorted(UNCERTAINTY))
    parser.add_argument("--budget", type=float, default=0.1,
                        help="share of traffic to escalate (0-1)")
    parser.add_argument("--sweep", action="store_true",
                        help="sweep budgets 0, 5, 10, 20, 50, 100%%")
    parser.add_argument("--budgets", nargs="+", type=float, default=None,
                        help="explicit budget list, e.g. 0 0.05 0.1 0.2")
    parser.add_argument("--splits", nargs="+", default=list(HELD_OUT_SPLITS))
    parser.add_argument("--include-generated", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--tag", default="", help="suffix for the results filename")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> dict:
    args = parse_args(argv)
    if args.budgets:
        budgets = sorted(set(args.budgets))
    elif args.sweep:
        budgets = [0.0, 0.05, 0.1, 0.2, 0.5, 1.0]
    else:
        budgets = [0.0, args.budget]

    actions = select_actions(args.include_generated, args.splits)
    if args.limit:
        actions = actions[: args.limit]
    if not actions:
        raise SystemExit(f"no records for splits {args.splits}")

    print("=" * 92)
    print("TASK 25 - ML -> LLM JUDGE CASCADE")
    print("=" * 92)
    print(f"  records      {len(actions)}   splits {', '.join(args.splits)}")
    print(f"  ML stages    {', '.join(args.ml)}")
    print(f"  strategies   {', '.join(args.strategy)}")
    print(f"  judge        {args.judge}"
          + (f" ({args.judge_model}, {args.judge_method})" if args.judge == "gemini" else ""))

    judge = build_judge(args.judge, model=args.judge_model, method=args.judge_method,
                        seed=args.seed, requests_per_minute=args.rpm)
    rows: List[dict] = []

    for ml_name in args.ml:
        for strategy in args.strategy:
            stage = build_stage(ml_name, strategy, args.seed)
            missing = [a for a in actions if a.record_id not in stage.decision]
            if missing:
                print(f"\n  skipping {ml_name}: no score for {len(missing)} records")
                continue

            print("\n" + "-" * 92)
            print(f"{ml_name.upper()}  |  uncertainty = {strategy}")
            print("-" * 92)
            header = (f"  {'budget':>8}{'escalated':>11}{'calls':>7}{'accuracy':>10}"
                      f"{'macro-ish F1':>14}{'attack recall':>15}{'FPR':>8}{'judged acc':>12}")
            print(header)

            for budget in budgets:
                try:
                    results, stats = run_cascade(
                        stage, actions, None if budget == 0 else judge, budget, progress=False,
                    )
                except QuotaExhausted as exc:
                    print(f"\n  stopping: {exc}")
                    break
                run = score(results, actions, f"{ml_name}+{args.judge}@{budget:.2f}")
                metrics = run.metrics
                judged = judged_only_accuracy(results, actions, stats)

                print(f"  {budget:>8.0%}{stats['routed_rate']:>11.1%}"
                      f"{stats['judge_calls']:>7}{metrics.accuracy:>10.3f}{metrics.f1:>14.3f}"
                      f"{metrics.attack_recall:>15.3f}{metrics.false_positive_rate:>8.3f}"
                      f"{(judged['accuracy'] if judged else float('nan')):>12.3f}")

                row = {
                    "ml": ml_name,
                    "strategy": strategy,
                    "judge": args.judge if budget else "none",
                    "judge_model": args.judge_model if args.judge == "gemini" else "",
                    "judge_method": args.judge_method if args.judge == "gemini" else "",
                    "budget": budget,
                    **{k: v for k, v in stats.items() if k != "judge_verdicts"},
                    **metrics.as_dict(),
                    "judged_only_accuracy": judged["accuracy"] if judged else None,
                    "judged_only_n": judged["n"] if judged else 0,
                }
                rows.append(row)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    tag = f"_{args.tag}" if args.tag else ""
    stem = f"cascade_{args.judge}{tag}"
    with (RESULTS_DIR / f"{stem}.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (RESULTS_DIR / f"{stem}.json").write_text(
        json.dumps({"records": len(actions), "splits": args.splits, "rows": rows}, indent=2),
        encoding="utf-8",
    )
    print(f"\nwrote {RESULTS_DIR / (stem + '.csv')}")
    return {"rows": rows, "records": len(actions)}


if __name__ == "__main__":
    main()
