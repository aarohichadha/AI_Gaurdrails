"""Evaluate every deterministic-rule and ML-classifier pairing.

The experiment evaluates four deterministic techniques against three ML
classifiers, producing 12 pairings. A pairing uses conservative OR fusion:
an action is blocked when either component blocks it.

Run from the repository root:

    .venv/bin/python combination/deterministic+ml/run_combinations.py

The default ML set is Qwen LoRA, Random Forest, and XGBoost. If the macOS
OpenMP runtime required by XGBoost is unavailable, the ``xgb`` slot falls back
to scikit-learn gradient boosting and is labelled ``gradient_boosting``.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Callable, Dict, List

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from classifiers import task17_feature_models as task17
from common import allowlist_for, apply_view, evaluate, load_actions
from common.schema import Action, Decision, GuardrailResult
from deterministic import task10_basic_rules as basic
from deterministic import task11_provenance_rules as provenance
from deterministic import task12_ci_norm as ci_norm

RESULTS_DIR = ROOT / "combination" / "results" / "determinitic+ml"
CORPUS_FEATURES = ROOT / "classifiers" / "results" / "task17_training_set.csv"
ADAPTER_DIR = ROOT / "classifiers" / "results" / "qwen_lora_colab"
PROMPTGUARD_LOGS = {
    "promptguard1_86m": ROOT / "classifiers" / "results" / "task14_pg1-86m_untrusted_full.jsonl",
    "promptguard2_22m": ROOT / "classifiers" / "results" / "task15_pg2-22m_untrusted_full.jsonl",
    "promptguard2_86m": ROOT / "classifiers" / "results" / "task15_pg2-86m_untrusted_full.jsonl",
}


def deterministic_variants() -> Dict[str, Callable[[Action], GuardrailResult]]:
    allowlist = allowlist_for("full")
    basic_guardrail = basic.make_guardrail(allowlist)
    provenance_guardrail = provenance.make_guardrail(allowlist)
    ci_guardrail = ci_norm.make_guardrail(allowlist)

    def all_rules(action: Action) -> GuardrailResult:
        results = [basic_guardrail(action), provenance_guardrail(action), ci_guardrail(action)]
        result = GuardrailResult(record_id=action.record_id)
        for item in results:
            result.decision = max(result.decision, item.decision)
            result.rules_fired.extend(item.rules_fired)
            result.reasons.extend(item.reasons)
        return result

    return {
        "basic_rules": basic_guardrail,
        "provenance_rules": provenance_guardrail,
        "ci_norm": ci_guardrail,
        "all_deterministic": all_rules,
    }


def train_classical_models() -> Dict[str, Callable[[Action], bool]]:
    """Train RF/XGBoost on the Task 17 corpus train split and return predictors."""
    if not CORPUS_FEATURES.exists():
        raise FileNotFoundError(f"Missing feature matrix: {CORPUS_FEATURES}")
    X, labels, names, meta = task17.load_dataset(CORPUS_FEATURES, drop_oracle=False)
    train_index, _, _ = task17.split_data(X, labels, meta, seed=0)
    from sklearn.preprocessing import LabelEncoder

    encoder = LabelEncoder().fit(labels[train_index])
    try:
        models = task17.build_models("both", seed=0, n_classes=len(encoder.classes_))
    except Exception as exc:
        if "xgboost" not in str(exc).lower() and "libomp" not in str(exc).lower():
            raise
        from sklearn.ensemble import HistGradientBoostingClassifier

        print(f"XGBoost unavailable ({exc}); using gradient_boosting fallback.")
        models = {
            "random_forest": task17.build_models("rf", seed=0, n_classes=len(encoder.classes_))["random_forest"],
            "gradient_boosting": HistGradientBoostingClassifier(
                max_iter=400, learning_rate=0.08, max_depth=5, random_state=0
            ),
        }
    predictors = {}
    for name, model in models.items():
        encoded = encoder.transform(labels[train_index])
        if name == "xgboost":
            model.fit(X[train_index], encoded, sample_weight=task17.sample_weights(labels[train_index]))
        else:
            model.fit(X[train_index], encoded)
        predicted_by_id = {
            record_id: encoder.inverse_transform(model.predict(row.reshape(1, -1)))[0]
            for record_id, row in zip(meta["record_id"], X)
        }
        predictors[name] = lambda action, values=predicted_by_id: values[action.record_id] == "ATTACK"
    return predictors


def qwen_predictor(model_id: str, adapter: Path, actions: List[Action], limit: int | None):
    from classifiers.qwen.qwen_finetune import build_training_example
    from classifiers.qwen.qwen_lora_infer import load_classifier

    model, tokenizer, device, temporary_dir = load_classifier(model_id, str(adapter))
    selected = actions if limit is None else actions[:limit]
    predictions: Dict[str, bool] = {}
    for index, action in enumerate(selected, 1):
        item = build_training_example(action)
        inputs = tokenizer(item["text"] + "\nAnswer:", return_tensors="pt").to(device)
        import torch

        with torch.no_grad():
            output = model.generate(
                **inputs,
                max_new_tokens=4,
                do_sample=False,
                pad_token_id=tokenizer.pad_token_id,
            )
        raw = tokenizer.decode(output[0][inputs["input_ids"].shape[1] :], skip_special_tokens=True).upper()
        predictions[action.record_id] = "ATTACK" in raw
        if index % 100 == 0:
            print(f"  Qwen predictions: {index}/{len(selected)}")
    if temporary_dir is not None:
        temporary_dir.cleanup()
    return lambda action: predictions[action.record_id]


def cached_promptguard_predictor(path: Path, actions: List[Action]):
    """Reuse a previously scored real Prompt Guard run without gated downloads."""
    if not path.exists():
        raise FileNotFoundError(f"Missing cached Prompt Guard scores: {path}")
    scores = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            scores[str(row["record_id"])] = float(row["score"])
    missing = [action.record_id for action in actions if action.record_id not in scores]
    if missing:
        raise ValueError(f"Prompt Guard cache is missing {len(missing)} action records")
    return lambda action: scores[action.record_id] >= 0.5


def metric_dict(name: str, metrics) -> dict:
    result = metrics.as_dict()
    result["combination"] = name
    return result


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
    parser.add_argument("--adapter", type=Path, default=ADAPTER_DIR)
    parser.add_argument(
        "--ml", nargs="+",
        choices=["qwen", "rf", "xgb", *PROMPTGUARD_LOGS],
        default=["qwen", "rf", "xgb", *PROMPTGUARD_LOGS],
    )
    parser.add_argument("--limit", type=int, default=None, help="limit records; default is the full corpus")
    args = parser.parse_args(argv)

    actions = apply_view(load_actions(), "full")
    if args.limit is not None:
        actions = actions[: args.limit]
    print(f"Records: {len(actions)}")

    ml_predictors: Dict[str, Callable[[Action], bool]] = {}
    if "rf" in args.ml or "xgb" in args.ml:
        classical = train_classical_models()
        if "rf" in args.ml:
            ml_predictors["random_forest"] = classical["random_forest"]
        if "xgb" in args.ml:
            xgb_name = "xgboost" if "xgboost" in classical else "gradient_boosting"
            ml_predictors[xgb_name] = classical[xgb_name]
    if "qwen" in args.ml:
        ml_predictors["qwen_lora"] = qwen_predictor(args.model, args.adapter, actions, args.limit)
    for promptguard_name, cache_path in PROMPTGUARD_LOGS.items():
        if promptguard_name in args.ml:
            ml_predictors[promptguard_name] = cached_promptguard_predictor(cache_path, actions)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    summaries = []
    for deterministic_name, deterministic in deterministic_variants().items():
        for ml_name, ml_predict in ml_predictors.items():
            combination_name = f"{deterministic_name}__{ml_name}"

            def combined(action, deterministic=deterministic, ml_predict=ml_predict):
                result = deterministic(action)
                if ml_predict(action):
                    result.apply(Decision.BLOCK, "ML_BLOCK", f"{ml_name} predicted ATTACK")
                return result

            run_result = evaluate(combined, actions, combination_name, "full")
            summary = metric_dict(combination_name, run_result.metrics)
            summary["deterministic"] = deterministic_name
            summary["ml"] = ml_name
            summary["fusion"] = "OR_BLOCK"
            summaries.append(summary)
            (RESULTS_DIR / f"{combination_name}.json").write_text(
                json.dumps({"configuration": summary, "results": [
                    {"record_id": action.record_id, "expected_decision": action.expected_decision,
                     "blocked": result.blocked, "decision": str(result.decision),
                     "rules_fired": result.rules_fired}
                    for action, result in zip(run_result.actions, run_result.results)
                ]}, indent=2), encoding="utf-8"
            )
            print(f"{combination_name}: accuracy={summary['accuracy']:.4f} attack_recall={summary['attack_recall']:.4f} f1={summary['f1']:.4f}")

    # Merge with combinations produced by earlier runs, so running one ML
    # family later cannot erase the other families from the aggregate report.
    merged: Dict[str, dict] = {}
    for result_path in RESULTS_DIR.glob("*.json"):
        if result_path.name in {"combination_summary.json"}:
            continue
        try:
            existing = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        configuration = existing.get("configuration")
        if configuration and configuration.get("combination"):
            merged[configuration["combination"]] = configuration
    merged.update({row["combination"]: row for row in summaries})
    all_summaries = [merged[name] for name in sorted(merged)]

    summary_path = RESULTS_DIR / "combination_summary.csv"
    fields = ["combination", "deterministic", "ml", "fusion", "n", "accuracy", "attack_recall", "precision", "f1", "false_positive_rate"]
    with summary_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field) for field in fields} for row in all_summaries)
    (RESULTS_DIR / "combination_summary.json").write_text(json.dumps(all_summaries, indent=2), encoding="utf-8")
    print(f"Wrote {len(all_summaries)} combinations to {RESULTS_DIR}")
    return all_summaries


if __name__ == "__main__":
    run()