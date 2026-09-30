"""Score a raw-email CSV with the saved Task 17 feature models.

    python classifiers/eval_raw_csv_models.py
    python classifiers/eval_raw_csv_models.py --csv data/my_test.csv --models task17_generated_xgboost

Companion to `qwen/qwen_eval_raw_csv.py`: same file, same labels, but these
models were trained with three classes, so unlike the two-class Qwen LoRA they
can actually emit REVISE. That makes them the fair reference point on a
three-class test file.

Each `.joblib` bundle carries its own feature order, so a model trained on a
different feature set still lines up correctly.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from classifiers.features import ExtractorConfig, FeatureExtractor, parse

DEFAULT_CSV = ROOT / "data" / "email_guardrail_raw_email_3class.csv"
MODELS_DIR = ROOT / "classifiers" / "models"
RESULTS_DIR = ROOT / "classifiers" / "results"

#: Domains this test file treats as the organisation and its approved partners.
CONFIG = ExtractorConfig(
    internal_domains={"corp.example"},
    trusted_domains={"partner-a.example", "counsel.example", "audit-approved.example"},
)

CLASS_ORDER = ["SAFE", "REVISE", "ATTACK"]


def load_rows(path: Path, limit: Optional[int]) -> List[dict]:
    import pandas as pd

    frame = pd.read_csv(path)
    if limit:
        frame = frame.head(limit)
    return frame.to_dict("records")


def report(name: str, truth: Sequence[str], predicted: Sequence[str], elapsed: float) -> dict:
    from sklearn.metrics import classification_report, confusion_matrix

    labels = [c for c in CLASS_ORDER if c in set(truth) | set(predicted)]
    detail = classification_report(truth, predicted, labels=labels,
                                   output_dict=True, zero_division=0)
    matrix = confusion_matrix(truth, predicted, labels=labels)
    accuracy = float(np.mean([t == p for t, p in zip(truth, predicted)]))

    # Security view: an attack not called SAFE was contained.
    attacks = [(t, p) for t, p in zip(truth, predicted) if t == "ATTACK"]
    contained = sum(1 for _, p in attacks if p != "SAFE")

    print(f"\n{'=' * 78}\n{name}\n{'=' * 78}")
    print(f"  accuracy {accuracy:.3f}   macro-F1 {detail['macro avg']['f1-score']:.3f}"
          f"   {len(truth) / elapsed:,.0f} rows/s")
    print(f"  {'class':<10}{'precision':>11}{'recall':>9}{'F1':>9}{'support':>9}")
    for label in labels:
        stats = detail[label]
        print(f"  {label:<10}{stats['precision']:>11.3f}{stats['recall']:>9.3f}"
              f"{stats['f1-score']:>9.3f}{int(stats['support']):>9}")
    print(f"  confusion (rows = true {' / '.join(labels)}):")
    for label, line in zip(labels, matrix):
        print(f"    {label:<8}" + "".join(f"{value:>7}" for value in line))
    if attacks:
        print(f"  attack containment (not called SAFE): {contained}/{len(attacks)}"
              f" = {contained / len(attacks):.3f}")

    return {
        "model": name,
        "accuracy": round(accuracy, 4),
        "macro_f1": round(detail["macro avg"]["f1-score"], 4),
        "per_class": {
            label: {k: round(v, 4) for k, v in detail[label].items()} for label in labels
        },
        "labels": labels,
        "confusion_matrix": matrix.tolist(),
        "attack_containment": round(contained / len(attacks), 4) if attacks else None,
        "rows_per_second": round(len(truth) / elapsed, 1),
    }


def main(argv: Optional[Sequence[str]] = None) -> dict:
    import joblib

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--models", nargs="+", default=[
        "task17_training_set_with_revise_xgboost",
        "task17_training_set_with_revise_random_forest",
        "task17_generated_xgboost",
        "task17_generated_random_forest",
    ])
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    rows = load_rows(args.csv, args.limit)
    truth = [str(r["label"]).upper() for r in rows]
    print(f"{len(rows)} rows from {args.csv.name}: {dict(Counter(truth))}")

    extractor = FeatureExtractor(CONFIG)
    started = time.perf_counter()
    vectors = [extractor.extract(parse(str(r["raw_email"]))) for r in rows]
    extract_seconds = time.perf_counter() - started
    print(f"feature extraction: {extract_seconds:.2f}s "
          f"({len(rows) / extract_seconds:,.0f} rows/s)")

    summaries = []
    for name in args.models:
        path = MODELS_DIR / f"{name}.joblib"
        if not path.exists():
            print(f"\nskipping {name}: {path} not found")
            continue
        bundle = joblib.load(path)
        features = bundle["features"]
        matrix = np.array([[v.to_dict().get(f, 0.0) for f in features] for v in vectors])

        started = time.perf_counter()
        predicted = bundle["encoder"].inverse_transform(bundle["model"].predict(matrix))
        summaries.append(report(name, truth, list(predicted), time.perf_counter() - started))

    out = RESULTS_DIR / f"feature_models_raw_eval_{args.csv.stem}.json"
    out.write_text(json.dumps({
        "csv": str(args.csv), "n": len(rows),
        "labels": dict(Counter(truth)),
        "feature_extraction_rows_per_second": round(len(rows) / extract_seconds, 1),
        "models": summaries,
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return {"models": summaries}


if __name__ == "__main__":
    main()
