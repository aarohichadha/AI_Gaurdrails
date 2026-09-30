"""Evaluate the Qwen LoRA classifier on a raw-email CSV.

    python classifiers/qwen/qwen_eval_raw_csv.py
    python classifiers/qwen/qwen_eval_raw_csv.py --csv data/my_test.csv --limit 60

The CSV needs an `id`, a `raw_email` (a full RFC-822 message) and a `label`
column. Raw messages are parsed with the Task 16 parser, so the model sees the
same subject/body an agent would.

TWO CLASSES MEET THREE
----------------------
The LoRA was fine-tuned to emit exactly one word, ATTACK or BENIGN, from the
corpus rule `expected_decision == BLOCK -> ATTACK`. It has no REVISE token and
cannot produce one. A three-class file is therefore scored two ways:

  * **Binary (the fair test).** ATTACK against everything-else. This is the
    question the model was actually trained to answer.
  * **Three-class (the honest ceiling).** Every REVISE row is wrong by
    construction, so accuracy cannot exceed (SAFE + ATTACK) / total - 0.667 on
    a balanced three-way file. Reported so the number is not mistaken for a
    real three-class result.

What REVISE rows do reveal is which way the model leans on genuinely
under-specified requests: calling them BENIGN means it would let them run,
calling them ATTACK means it would block legitimate work.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from classifiers.features import parse

DEFAULT_CSV = ROOT / "data" / "email_guardrail_raw_email_3class.csv"
DEFAULT_ADAPTER = ROOT / "classifiers" / "results" / "qwen_lora_colab"
RESULTS_DIR = ROOT / "classifiers" / "results"

#: Model vocabulary -> "is this an attack?"
ATTACK_LABELS = {"ATTACK"}

#: The model says BENIGN where the file says SAFE. Without this mapping the
#: exact-match score would also mark every correct SAFE row wrong, putting the
#: apparent ceiling at 1/3 instead of the real 2/3.
PREDICTION_TO_FILE_LABEL = {"BENIGN": "SAFE", "ATTACK": "ATTACK"}


def exact_match(rows: Sequence[dict]) -> float:
    """Three-class agreement, after translating the model's vocabulary."""
    if not rows:
        return 0.0
    hits = sum(
        PREDICTION_TO_FILE_LABEL.get(r["prediction"], r["prediction"]) == r["label"]
        for r in rows
    )
    return hits / len(rows)


def load_rows(path: Path, limit: Optional[int]) -> List[dict]:
    import pandas as pd

    frame = pd.read_csv(path)
    missing = {"id", "raw_email", "label"} - set(frame.columns)
    if missing:
        raise SystemExit(f"{path} is missing column(s): {', '.join(sorted(missing))}")
    if limit:
        frame = frame.head(limit)
    return frame.to_dict("records")


def parsed_fields(raw_email: str) -> Dict[str, str]:
    message = parse(str(raw_email))
    return {
        "subject": message.subject,
        # Hidden HTML text is part of what an agent ingests, so include it.
        "body": "\n".join(p for p in (message.body_text, message.hidden_text) if p.strip()),
        "sender": message.sender,
    }


def binary_metrics(rows: Sequence[dict]) -> dict:
    tp = sum(r["truth_is_attack"] and r["pred_is_attack"] for r in rows)
    fn = sum(r["truth_is_attack"] and not r["pred_is_attack"] for r in rows)
    fp = sum(not r["truth_is_attack"] and r["pred_is_attack"] for r in rows)
    tn = sum(not r["truth_is_attack"] and not r["pred_is_attack"] for r in rows)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "tp": tp, "fn": fn, "fp": fp, "tn": tn,
        "accuracy": round((tp + tn) / len(rows), 4) if rows else 0.0,
        "precision": round(precision, 4),
        "attack_recall": round(recall, 4),
        "f1": round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0,
        "false_positive_rate": round(fp / (fp + tn), 4) if fp + tn else 0.0,
    }


def per_label(rows: Sequence[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for label in sorted({r["label"] for r in rows}):
        subset = [r for r in rows if r["label"] == label]
        counts = Counter(r["prediction"] for r in subset)
        out[label] = {
            "n": len(subset),
            "predicted": dict(counts),
            "called_attack": round(counts.get("ATTACK", 0) / len(subset), 4),
        }
    return out


def main(argv: Optional[Sequence[str]] = None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
    parser.add_argument("--adapter", type=Path, default=DEFAULT_ADAPTER)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    rows = load_rows(args.csv, args.limit)
    print(f"loaded {len(rows)} rows from {args.csv.name}")
    print("  labels:", dict(Counter(r["label"] for r in rows)))

    from classifiers.qwen.qwen_lora_infer import classify, load_classifier

    print(f"  loading {args.model} + adapter {args.adapter.name} ...")
    model, tokenizer, device, temporary = load_classifier(args.model, str(args.adapter))
    print(f"  device: {device}")

    scored: List[dict] = []
    started = time.perf_counter()
    for index, row in enumerate(rows, 1):
        fields = parsed_fields(row["raw_email"])
        call_started = time.perf_counter()
        prediction = classify(model, tokenizer, device, fields["subject"], fields["body"])
        latency = time.perf_counter() - call_started

        truth = str(row["label"]).upper()
        scored.append({
            "id": row["id"],
            "label": truth,
            "prediction": prediction,
            "truth_is_attack": truth in ATTACK_LABELS,
            "pred_is_attack": prediction in ATTACK_LABELS,
            "sender": fields["sender"],
            "subject": fields["subject"][:120],
            "latency_s": round(latency, 3),
        })
        if index % 25 == 0 or index == len(rows):
            rate = index / max(time.perf_counter() - started, 1e-9)
            print(f"\r  {index}/{len(rows)}  ({rate:.1f} rows/s)", end="", flush=True)
    print()
    if temporary is not None:
        temporary.cleanup()

    metrics = binary_metrics(scored)
    labels = per_label(scored)
    exact = exact_match(scored)
    latencies = sorted(r["latency_s"] for r in scored)

    print("\n" + "=" * 78)
    print(f"QWEN LoRA ON {args.csv.name}")
    print("=" * 78)
    print(f"  BINARY (ATTACK vs rest) - what the model was trained for")
    print(f"    accuracy        {metrics['accuracy']:.3f}")
    print(f"    attack recall   {metrics['attack_recall']:.3f}   ({metrics['tp']}/{metrics['tp'] + metrics['fn']})")
    print(f"    precision       {metrics['precision']:.3f}")
    print(f"    F1              {metrics['f1']:.3f}")
    print(f"    false positives {metrics['fp']} of {metrics['fp'] + metrics['tn']}  (FPR {metrics['false_positive_rate']:.3f})")

    print(f"\n  BY TRUE LABEL")
    print(f"    {'label':<10}{'n':>6}{'called ATTACK':>16}   predictions")
    for label, stats in labels.items():
        print(f"    {label:<10}{stats['n']:>6}{stats['called_attack']:>15.1%}   {stats['predicted']}")

    print(f"\n  THREE-CLASS exact match: {exact:.3f}")
    if "REVISE" in labels:
        share = labels["REVISE"]["n"] / len(scored)
        print(f"    ceiling is {1 - share:.3f}: the model has no REVISE token, so all"
              f" {labels['REVISE']['n']} REVISE rows are wrong by construction")

    print(f"\n  latency/row  mean {sum(latencies)/len(latencies):.2f}s  "
          f"p50 {latencies[len(latencies)//2]:.2f}s  p95 {latencies[int(len(latencies)*0.95)-1]:.2f}s")

    stem = args.out or (RESULTS_DIR / f"qwen_raw_eval_{args.csv.stem}")
    Path(f"{stem}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in scored) + "\n", encoding="utf-8")
    summary = {
        "csv": str(args.csv), "model": args.model, "adapter": str(args.adapter),
        "device": device, "n": len(scored),
        "binary": metrics, "by_label": labels,
        "three_class_exact_match": round(exact, 4),
        "latency_s": {
            "mean": round(sum(latencies) / len(latencies), 3),
            "p50": round(latencies[len(latencies) // 2], 3),
            "p95": round(latencies[int(len(latencies) * 0.95) - 1], 3),
        },
    }
    Path(f"{stem}_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nwrote {stem}.jsonl\nwrote {stem}_summary.json")
    return summary


if __name__ == "__main__":
    main()
