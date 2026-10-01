"""Evaluate Prompt Guard (Tasks 14/15) on a raw-email CSV.

    python classifiers/eval_raw_csv_promptguard.py
    python classifiers/eval_raw_csv_promptguard.py --model pg2-86m --limit 60

Companion to `eval_raw_csv_models.py` (Task 17), `qwen/qwen_eval_raw_csv.py`
(Task 18), and `eval_raw_csv_gemini.py` (Task 19): same CSV, same labels.
Prompt Guard is a pure text classifier with no notion of sender, recipient,
or authorization, so it is scored on the email body/subject alone (the
"untrusted" input, matching the headline metric used for the structured
corpus in Task 15).

Requires an HF token with accepted access to the gated meta-llama models
(`HF_TOKEN` in the environment or a gitignored `.env`; see common/env.py).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from classifiers.features import parse
from classifiers.promptguard import MODELS, PromptGuardScorer
from common.env import load_env

DEFAULT_CSV = ROOT / "data" / "email_guardrail_raw_email_3class.csv"
RESULTS_DIR = ROOT / "classifiers" / "results"
ATTACK_LABELS = {"ATTACK"}


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
        mean_score = sum(r["score"] for r in subset) / len(subset)
        called = sum(r["pred_is_attack"] for r in subset)
        out[label] = {"n": len(subset), "called_attack": round(called / len(subset), 4),
                       "mean_score": round(mean_score, 4)}
    return out


def main(argv: Optional[Sequence[str]] = None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--model", choices=sorted(MODELS), default="pg2-86m")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    load_env()
    rows = load_rows(args.csv, args.limit)
    print(f"loaded {len(rows)} rows from {args.csv.name}")
    print("  labels:", dict(Counter(r["label"] for r in rows)))
    print(f"  loading {MODELS[args.model]} ...")
    scorer = PromptGuardScorer(MODELS[args.model], device=args.device)

    fields = [parsed_fields(r["raw_email"]) for r in rows]
    texts = [f"{f['subject']}\n\n{f['body']}" for f in fields]

    started = time.perf_counter()
    scores = scorer.score(texts, progress=lambda n: print(f"\r  scored {n}/{len(texts)} windows", end="", flush=True))
    elapsed = time.perf_counter() - started
    print(f"\n  scored {len(rows)} rows in {elapsed:.1f}s ({len(rows) / elapsed:.1f} rows/s)")

    latency = scorer.measure_latency(texts[: min(50, len(texts))])

    scored: List[dict] = []
    for row, f, score in zip(rows, fields, scores):
        truth = str(row["label"]).upper()
        pred_is_attack = score >= args.threshold
        scored.append({
            "id": row["id"], "label": truth, "score": round(float(score), 4),
            "pred_is_attack": pred_is_attack, "truth_is_attack": truth in ATTACK_LABELS,
            "sender": f["sender"], "subject": f["subject"][:120],
        })

    metrics = binary_metrics(scored)
    labels = per_label(scored)

    print("\n" + "=" * 78)
    print(f"{args.model.upper()} ON {args.csv.name}  (threshold {args.threshold})")
    print("=" * 78)
    print(f"  accuracy        {metrics['accuracy']:.3f}")
    print(f"  attack recall   {metrics['attack_recall']:.3f}   ({metrics['tp']}/{metrics['tp'] + metrics['fn']})")
    print(f"  precision       {metrics['precision']:.3f}")
    print(f"  F1              {metrics['f1']:.3f}")
    print(f"  false positives {metrics['fp']} of {metrics['fp'] + metrics['tn']}  (FPR {metrics['false_positive_rate']:.3f})")
    print(f"\n  BY TRUE LABEL")
    for label, stats in labels.items():
        print(f"    {label:<8}{stats['n']:>6}{stats['called_attack']:>15.1%} called ATTACK   mean score {stats['mean_score']:.3f}")
    print(f"\n  latency (batch=1, n={latency.samples})  mean {latency.mean_ms:.0f}ms  p50 {latency.p50_ms:.0f}ms  p95 {latency.p95_ms:.0f}ms")

    stem = args.out or (RESULTS_DIR / f"promptguard_{args.model}_raw_eval_{args.csv.stem}")
    Path(f"{stem}.jsonl").write_text("\n".join(json.dumps(r) for r in scored) + "\n", encoding="utf-8")
    summary = {
        "csv": str(args.csv), "model": MODELS[args.model], "threshold": args.threshold,
        "n": len(scored), "metrics": metrics, "by_label": labels,
        "latency_ms": latency.as_dict(),
        "throughput_rows_per_s": round(len(rows) / elapsed, 2),
    }
    Path(f"{stem}_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nwrote {stem}.jsonl\nwrote {stem}_summary.json")
    return summary


if __name__ == "__main__":
    main()
