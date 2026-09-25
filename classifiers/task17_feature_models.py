"""Task 17 - Feature-based ML: Random Forest and XGBoost.

    feature vector (Task 16)  ->  Random Forest / XGBoost  ->  SAFE / REVISE / ATTACK

Two datasets, both built by Task 16:

    generated   classifiers/results/task17_generated_features.csv
                3,000 synthetic emails, three classes, attachments/links/HTML
    corpus      classifiers/results/task17_training_set.csv
                7,200 records rendered from the project dataset, two classes

Run:
    python classifiers/task17_feature_models.py                      # generated, both models
    python classifiers/task17_feature_models.py --dataset corpus
    python classifiers/task17_feature_models.py --drop-oracle-features
    python classifiers/task17_feature_models.py --model rf --no-permutation

Reports accuracy, macro F1, per-class precision/recall/F1, the confusion
matrix, cross-validated stability, accuracy split by hard vs easy case,
feature importance (impurity and permutation), and prediction latency.
Models are saved to `models/`, metrics to `results/`.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

RESULTS_DIR = Path(__file__).resolve().parent / "results"
MODELS_DIR = Path(__file__).resolve().parent / "models"

DATASETS = {
    "generated": RESULTS_DIR / "task17_generated_features.csv",
    "corpus": RESULTS_DIR / "task17_training_set.csv",
}

#: Metadata columns that are never features.
META_COLUMNS = {"record_id", "label", "notes", "sheet", "split", "attack_category"}

#: Near-perfect predictors on the *corpus* dataset only - see the Task 16
#: README. Kept available so a run can measure with and without them.
ORACLE_FEATURES = (
    "n_body_external_addresses",
    "has_body_external_address",
    "has_body_lookalike_address",
    "has_body_freemail_address",
)

CLASS_ORDER = ["SAFE", "REVISE", "ATTACK"]


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def load_dataset(path: Path, drop_oracle: bool = False):
    """Read a Task 16 feature matrix into X, y and its metadata."""
    with path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise SystemExit(f"{path} is empty")

    names = [c for c in rows[0] if c not in META_COLUMNS]
    if drop_oracle:
        names = [c for c in names if c not in ORACLE_FEATURES]

    X = np.array([[float(row[c]) for c in names] for row in rows], dtype=np.float64)
    y = np.array([row["label"] for row in rows])
    meta = {
        "record_id": [row["record_id"] for row in rows],
        "hard": np.array([row.get("notes", "") == "hard_case" for row in rows]),
        "split": [row.get("split", "") for row in rows],
        "attack_category": [row.get("attack_category", "") for row in rows],
    }
    return X, y, names, meta


def split_data(X, y, meta, seed: int, test_size: float = 0.25):
    """Use the dataset's own splits when it has them, else stratify.

    The corpus carries train/validation/test plus two extra held-out sets
    (flow_test, adaptive_test); those are reported separately as stress sets
    rather than folded into the headline test score.
    """
    from sklearn.model_selection import train_test_split

    splits = meta["split"]
    if any(splits):
        index = np.arange(len(y))
        train = index[[s in ("train", "validation") for s in splits]]
        test = index[[s == "test" for s in splits]]
        extra = {
            name: index[[s == name for s in splits]]
            for name in ("flow_test", "adaptive_test")
            if any(s == name for s in splits)
        }
        return train, test, extra

    train, test = train_test_split(
        np.arange(len(y)), test_size=test_size, random_state=seed, stratify=y
    )
    return train, test, {}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

def build_models(which: str, seed: int, n_classes: int) -> Dict:
    from sklearn.ensemble import RandomForestClassifier

    models = {}
    if which in ("rf", "both"):
        models["random_forest"] = RandomForestClassifier(
            n_estimators=400,
            min_samples_leaf=2,
            class_weight="balanced",
            n_jobs=-1,
            random_state=seed,
        )
    if which in ("xgb", "both"):
        from xgboost import XGBClassifier

        models["xgboost"] = XGBClassifier(
            n_estimators=400,
            max_depth=5,
            learning_rate=0.08,
            subsample=0.9,
            colsample_bytree=0.8,
            reg_lambda=1.0,
            objective="multi:softprob" if n_classes > 2 else "binary:logistic",
            num_class=n_classes if n_classes > 2 else None,
            eval_metric="mlogloss" if n_classes > 2 else "logloss",
            tree_method="hist",
            n_jobs=-1,
            random_state=seed,
        )
    return models


def sample_weights(y: np.ndarray) -> np.ndarray:
    """Balance classes for XGBoost, which has no `class_weight`."""
    counts = Counter(y.tolist())
    total = len(y)
    return np.array([total / (len(counts) * counts[label]) for label in y])


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate(model, encoder, X, y_true, labels: Sequence[str]) -> dict:
    from sklearn.metrics import (accuracy_score, classification_report,
                                 confusion_matrix, f1_score)

    predicted = encoder.inverse_transform(model.predict(X))
    report = classification_report(
        y_true, predicted, labels=labels, output_dict=True, zero_division=0
    )
    return {
        "n": int(len(y_true)),
        "accuracy": float(accuracy_score(y_true, predicted)),
        "macro_f1": float(f1_score(y_true, predicted, average="macro", zero_division=0)),
        "weighted_f1": float(f1_score(y_true, predicted, average="weighted", zero_division=0)),
        "per_class": {
            label: {
                "precision": round(report[label]["precision"], 4),
                "recall": round(report[label]["recall"], 4),
                "f1": round(report[label]["f1-score"], 4),
                "support": int(report[label]["support"]),
            }
            for label in labels if label in report
        },
        "confusion_matrix": confusion_matrix(y_true, predicted, labels=labels).tolist(),
        "labels": list(labels),
        "predictions": predicted.tolist(),
    }


def attack_recall(result: dict) -> Optional[float]:
    """The security number: share of ATTACK caught (as ATTACK or REVISE)."""
    labels = result["labels"]
    if "ATTACK" not in labels:
        return None
    matrix = np.array(result["confusion_matrix"])
    row = matrix[labels.index("ATTACK")]
    total = row.sum()
    if not total:
        return None
    caught = total - (row[labels.index("SAFE")] if "SAFE" in labels else 0)
    return float(caught / total)


def cross_validate(model, X, y, seed: int) -> Tuple[float, float]:
    from sklearn.base import clone
    from sklearn.metrics import f1_score
    from sklearn.model_selection import StratifiedKFold
    from sklearn.preprocessing import LabelEncoder

    scores = []
    folds = StratifiedKFold(n_splits=5, shuffle=True, random_state=seed)
    for train_index, test_index in folds.split(X, y):
        encoder = LabelEncoder().fit(y[train_index])
        fitted = clone(model)
        if "XGB" in type(model).__name__:
            fitted.fit(X[train_index], encoder.transform(y[train_index]),
                       sample_weight=sample_weights(y[train_index]))
        else:
            fitted.fit(X[train_index], encoder.transform(y[train_index]))
        predicted = encoder.inverse_transform(fitted.predict(X[test_index]))
        scores.append(f1_score(y[test_index], predicted, average="macro", zero_division=0))
    return float(np.mean(scores)), float(np.std(scores))


def measure_latency(model, X, repeats: int = 200) -> dict:
    """Per-email prediction time, batch size 1 - excludes feature extraction."""
    sample = X[:repeats] if len(X) >= repeats else X
    for row in sample[:5]:
        model.predict(row.reshape(1, -1))
    timings = []
    for row in sample:
        start = time.perf_counter()
        model.predict(row.reshape(1, -1))
        timings.append((time.perf_counter() - start) * 1000)
    timings.sort()
    return {
        "samples": len(timings),
        "mean_ms": round(float(np.mean(timings)), 3),
        "p50_ms": round(float(timings[len(timings) // 2]), 3),
        "p95_ms": round(float(timings[int(len(timings) * 0.95) - 1]), 3),
    }


def importances(model, names: Sequence[str], X_test, y_test, encoder, seed: int,
                permutation: bool = True, top: int = 15) -> dict:
    out = {"impurity": [], "permutation": []}
    if hasattr(model, "feature_importances_"):
        ranked = sorted(zip(names, model.feature_importances_), key=lambda kv: -kv[1])
        out["impurity"] = [(name, round(float(value), 5)) for name, value in ranked[:top]]

    if permutation:
        from sklearn.inspection import permutation_importance

        result = permutation_importance(
            model, X_test, encoder.transform(y_test),
            n_repeats=5, random_state=seed, n_jobs=-1, scoring="f1_macro",
        )
        ranked = sorted(zip(names, result.importances_mean), key=lambda kv: -kv[1])
        out["permutation"] = [(name, round(float(value), 5)) for name, value in ranked[:top]]
    return out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_result(title: str, result: dict) -> None:
    print(f"\n  {title}   n={result['n']}  accuracy={result['accuracy']:.3f}  "
          f"macro-F1={result['macro_f1']:.3f}")
    print(f"    {'class':<10}{'precision':>11}{'recall':>9}{'F1':>9}{'support':>9}")
    for label, stats in result["per_class"].items():
        print(f"    {label:<10}{stats['precision']:>11.3f}{stats['recall']:>9.3f}"
              f"{stats['f1']:>9.3f}{stats['support']:>9}")
    labels = result["labels"]
    print(f"    confusion (rows = true {' / '.join(labels)}):")
    for label, row in zip(labels, result["confusion_matrix"]):
        print(f"      {label:<8}" + "".join(f"{value:>7}" for value in row))
    recall = attack_recall(result)
    if recall is not None:
        print(f"    attack recall (ATTACK not called SAFE): {recall:.3f}")


def print_importances(name: str, data: dict) -> None:
    if not data["impurity"]:
        return
    print(f"\n  Top features - {name}")
    print(f"    {'impurity':<34}{'permutation (macro-F1 drop)':<34}")
    permutation = data["permutation"] or [("-", 0.0)] * len(data["impurity"])
    for (a_name, a_value), (b_name, b_value) in zip(data["impurity"], permutation):
        left = f"{a_name} {a_value:.4f}"
        right = f"{b_name} {b_value:.4f}" if b_name != "-" else ""
        print(f"    {left:<34}{right:<34}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[Sequence[str]] = None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dataset", choices=sorted(DATASETS), default="generated")
    parser.add_argument("--csv", type=Path, default=None, help="use this feature matrix instead")
    parser.add_argument("--model", choices=["rf", "xgb", "both"], default="both")
    parser.add_argument("--drop-oracle-features", action="store_true")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--test-size", type=float, default=0.25)
    parser.add_argument("--no-permutation", action="store_true", help="skip permutation importance")
    parser.add_argument("--no-save", action="store_true", help="do not write models to disk")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> dict:
    from sklearn.preprocessing import LabelEncoder

    args = parse_args(argv)
    path = args.csv or DATASETS[args.dataset]
    if not path.exists():
        raise SystemExit(
            f"{path} not found - build it first:\n"
            "  python classifiers/generate_email_corpus.py     (generated)\n"
            "  python classifiers/build_training_set.py        (corpus)"
        )

    X, y, names, meta = load_dataset(path, args.drop_oracle_features)
    train_index, test_index, extra_sets = split_data(X, y, meta, args.seed, args.test_size)
    labels = [c for c in CLASS_ORDER if c in set(y)]

    print("=" * 79)
    print(f"TASK 17 - FEATURE-BASED ML   ({args.dataset})")
    print("=" * 79)
    print(f"  data            {path.name}")
    print(f"  rows            {len(y)}   train {len(train_index)}   test {len(test_index)}")
    print("  classes         " + ", ".join(f"{k}={v}" for k, v in sorted(Counter(y.tolist()).items())))
    print(f"  features        {len(names)}"
          + ("  (oracle features dropped)" if args.drop_oracle_features else ""))
    for name, index in extra_sets.items():
        print(f"  held-out set    {name}: {len(index)} rows")

    encoder = LabelEncoder().fit(y[train_index])
    models = build_models(args.model, args.seed, len(labels))
    summary = {
        "dataset": args.dataset,
        "csv": str(path),
        "rows": len(y),
        "features": len(names),
        "dropped_oracle_features": args.drop_oracle_features,
        "classes": {k: int(v) for k, v in Counter(y.tolist()).items()},
        "models": {},
    }

    for name, model in models.items():
        print("\n" + "-" * 79)
        print(f"{name.upper()}")
        print("-" * 79)

        started = time.perf_counter()
        if name == "xgboost":
            model.fit(X[train_index], encoder.transform(y[train_index]),
                      sample_weight=sample_weights(y[train_index]))
        else:
            model.fit(X[train_index], encoder.transform(y[train_index]))
        train_seconds = time.perf_counter() - started
        print(f"  trained in {train_seconds:.1f}s")

        mean_f1, std_f1 = cross_validate(model, X[train_index], y[train_index], args.seed)
        print(f"  5-fold CV on train: macro-F1 {mean_f1:.3f} +/- {std_f1:.3f}")

        test_result = evaluate(model, encoder, X[test_index], y[test_index], labels)
        print_result("TEST", test_result)

        hard_mask = meta["hard"][test_index]
        hard_scores = {}
        if hard_mask.any() and (~hard_mask).any():
            predicted = np.array(test_result["predictions"])
            truth = y[test_index]
            hard_scores = {
                "hard": float((predicted[hard_mask] == truth[hard_mask]).mean()),
                "easy": float((predicted[~hard_mask] == truth[~hard_mask]).mean()),
            }
            print(f"    accuracy on hard cases {hard_scores['hard']:.3f} "
                  f"vs easy {hard_scores['easy']:.3f}")

        stress = {}
        for set_name, index in extra_sets.items():
            if len(index):
                result = evaluate(model, encoder, X[index], y[index], labels)
                print_result(f"STRESS SET: {set_name}", result)
                result.pop("predictions")
                stress[set_name] = result

        ranked = importances(model, names, X[test_index], y[test_index], encoder,
                             args.seed, permutation=not args.no_permutation)
        print_importances(name, ranked)

        latency = measure_latency(model, X[test_index])
        print(f"\n  prediction latency (batch=1): mean {latency['mean_ms']:.3f} ms  "
              f"p50 {latency['p50_ms']:.3f} ms  p95 {latency['p95_ms']:.3f} ms")

        test_result.pop("predictions")
        summary["models"][name] = {
            "train_seconds": round(train_seconds, 2),
            "cv_macro_f1_mean": round(mean_f1, 4),
            "cv_macro_f1_std": round(std_f1, 4),
            "test": test_result,
            "attack_recall": attack_recall(test_result),
            "hard_vs_easy": hard_scores,
            "stress_sets": stress,
            "importances": ranked,
            "latency_batch1": latency,
        }

        if not args.no_save:
            import joblib

            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            suffix = "_no_oracle" if args.drop_oracle_features else ""
            target = MODELS_DIR / f"task17_{args.dataset}_{name}{suffix}.joblib"
            joblib.dump({"model": model, "encoder": encoder, "features": names}, target)
            print(f"  saved {target}")

    if len(summary["models"]) > 1:
        print("\n" + "=" * 79)
        print("RANDOM FOREST vs XGBOOST")
        print("=" * 79)
        print(f"  {'model':<16}{'accuracy':>10}{'macro-F1':>10}{'CV macro-F1':>14}"
              f"{'attack recall':>15}{'ms/email':>10}")
        for name, data in summary["models"].items():
            recall = data["attack_recall"]
            print(f"  {name:<16}{data['test']['accuracy']:>10.3f}{data['test']['macro_f1']:>10.3f}"
                  f"{data['cv_macro_f1_mean']:>9.3f} +/-{data['cv_macro_f1_std']:<4.3f}"
                  f"{('-' if recall is None else f'{recall:.3f}'):>15}"
                  f"{data['latency_batch1']['mean_ms']:>10.3f}")

    suffix = "_no_oracle" if args.drop_oracle_features else ""
    out = RESULTS_DIR / f"task17_models_{args.dataset}{suffix}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return summary


if __name__ == "__main__":
    main()
