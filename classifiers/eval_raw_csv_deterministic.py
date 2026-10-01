"""Deterministic guardrails (Tasks 10-12), adapted for a raw-email-only CSV.

    python classifiers/eval_raw_csv_deterministic.py

WHY THIS IS A SEPARATE MODULE, NOT A REUSE OF `deterministic/`
----------------------------------------------------------------
`deterministic/task10-12_*.py` decide over a `common.schema.Action` built
from the structured 7,200-record corpus: an authorized-destination field, a
case record, a sensitivity tier. This raw-email file
(`email_guardrail_raw_email_3class.csv`) has none of that -- just an id, a
full raw message, and a SAFE/REVISE/ATTACK label. There is no destination to
compare against an allowlist and no case record to corroborate against, so a
literal reuse of those three rule sets would see nothing and default-allow
everything (exactly the "deliberate blind spot" `deterministic/README.md`
describes for `basic_rules` under the `destination_blind` view).

This module is a from-scratch DEFAULT POLICY built for what this file
actually has: sender identity (via Task 16's `FeatureExtractor`) and lexical
markers in the email body. It keeps the same three-way philosophy Tasks
10-12 established -- see `deterministic/README.md` -- ported onto the only
two signals available here:

  basic       sender identity only, no content reasoning at all
  provenance  sender identity + explicit attack markers, escalates to
              BLOCK only on markers that are hard to produce by accident
  ci_norm     default-deny: ALLOW requires an affirmatively trusted sender
              AND a completely clean marker read; anything else is BLOCK

ALLOW/FLAG/BLOCK map onto this file's own SAFE/REVISE/ATTACK labels, so
accuracy is directly comparable to the ML/LLM results scored on the same
CSV (`eval_raw_csv_models.py`, `qwen/qwen_eval_raw_csv.py`,
`eval_raw_csv_gemini.py`, `eval_raw_csv_promptguard.py`).

This is new design work, not a rerun of existing logic: the specific
thresholds below (which markers are "strong", which sender states count as
trusted) are a judgment call documented here so it can be argued with.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from classifiers.features import ExtractorConfig, FeatureExtractor, parse

DEFAULT_CSV = ROOT / "data" / "email_guardrail_raw_email_3class.csv"
RESULTS_DIR = ROOT / "classifiers" / "results"

CONFIG = ExtractorConfig(
    internal_domains={"corp.example"},
    trusted_domains={"partner-a.example", "counsel.example", "audit-approved.example"},
)

#: Markers that are hard to produce by an ordinary legitimate email -
#: mirrors `common/policy.py`'s STRONG_MARKER_NAMES split (override,
#: fake-authority, secrecy, obfuscation, impersonation, payment-redirect,
#: credential-request, exfiltration all measured at ~0% benign hit rate on
#: the structured corpus; urgency alone is excluded because ordinary
#: business mail is often genuinely urgent).
STRONG_MARKERS = [
    "has_instruction_override", "has_authorization_claim", "has_suspicious_secrecy",
    "has_suspicious_obfuscation", "has_suspicious_impersonation",
    "has_suspicious_payment_redirect", "has_suspicious_credential_request",
    "has_suspicious_exfiltration", "display_name_claims_authority",
]
#: Weaker signals: real, but also produced by ordinary business mail, so
#: alone they escalate to FLAG (review) rather than BLOCK.
WEAK_MARKERS = [
    "has_urgency", "has_external_link", "has_lookalike_link", "has_punycode_link",
    "has_shortened_link", "has_credential_path_link", "has_external_recipient",
    "has_body_external_address", "sender_domain_is_lookalike",
]

DECISION_TO_LABEL = {"ALLOW": "SAFE", "FLAG": "REVISE", "BLOCK": "ATTACK"}


def sender_trusted(fv: Dict[str, float]) -> bool:
    return bool(fv.get("sender_is_internal") or fv.get("sender_is_trusted_domain"))


def has_any(fv: Dict[str, float], names: List[str]) -> bool:
    return any(fv.get(n, 0.0) for n in names)


def rule_basic(fv: Dict[str, float]) -> str:
    """Task 10 analogue: sender identity only, no content reasoning."""
    return "ALLOW" if sender_trusted(fv) else "FLAG"


def rule_provenance(fv: Dict[str, float]) -> str:
    """Task 11 analogue: content is data, not instruction - untrusted text
    carrying a strong marker is blocked regardless of who sent it."""
    if has_any(fv, STRONG_MARKERS):
        return "BLOCK"
    if sender_trusted(fv) and not has_any(fv, WEAK_MARKERS):
        return "ALLOW"
    return "FLAG"


def rule_ci_norm(fv: Dict[str, float]) -> str:
    """Task 12 analogue: default-deny. ALLOW must be positively affirmed by
    a trusted sender AND a fully clean marker read; anything else denies."""
    if sender_trusted(fv) and not has_any(fv, STRONG_MARKERS) and not has_any(fv, WEAK_MARKERS):
        return "ALLOW"
    if has_any(fv, STRONG_MARKERS):
        return "BLOCK"
    return "BLOCK"


VARIANTS = {"basic": rule_basic, "provenance": rule_provenance, "ci_norm": rule_ci_norm}


def load_rows(path: Path, limit: Optional[int]) -> List[dict]:
    import pandas as pd
    frame = pd.read_csv(path)
    if limit:
        frame = frame.head(limit)
    return frame.to_dict("records")


def three_class_metrics(rows: Sequence[dict], pred_key: str) -> dict:
    labels = ["SAFE", "REVISE", "ATTACK"]
    hits = sum(r[pred_key] == r["label"] for r in rows)
    per_class = {}
    for label in labels:
        tp = sum(r["label"] == label and r[pred_key] == label for r in rows)
        fp = sum(r["label"] != label and r[pred_key] == label for r in rows)
        fn = sum(r["label"] == label and r[pred_key] != label for r in rows)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}
    macro_f1 = sum(c["f1"] for c in per_class.values()) / len(per_class)
    confusion = {t: Counter(r[pred_key] for r in rows if r["label"] == t) for t in labels}
    return {
        "accuracy": round(hits / len(rows), 4) if rows else 0.0,
        "macro_f1": round(macro_f1, 4),
        "per_class": per_class,
        "confusion": {t: dict(c) for t, c in confusion.items()},
    }


def binary_metrics(rows: Sequence[dict], pred_key: str) -> dict:
    tp = sum(r["label"] == "ATTACK" and r[pred_key] == "ATTACK" for r in rows)
    fn = sum(r["label"] == "ATTACK" and r[pred_key] != "ATTACK" for r in rows)
    fp = sum(r["label"] != "ATTACK" and r[pred_key] == "ATTACK" for r in rows)
    tn = sum(r["label"] != "ATTACK" and r[pred_key] != "ATTACK" for r in rows)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "tp": tp, "fn": fn, "fp": fp, "tn": tn,
        "attack_recall": round(recall, 4), "precision": round(precision, 4),
        "f1": round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0,
        "false_positive_rate": round(fp / (fp + tn), 4) if fp + tn else 0.0,
    }


def main(argv: Optional[Sequence[str]] = None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    rows = load_rows(args.csv, args.limit)
    print(f"loaded {len(rows)} rows from {args.csv.name}")
    print("  labels:", dict(Counter(r["label"] for r in rows)))

    extractor = FeatureExtractor(CONFIG)
    scored: List[dict] = []
    for row in rows:
        message = parse(str(row["raw_email"]))
        fv = extractor.extract(message).values
        record = {"id": row["id"], "label": str(row["label"]).upper(), "sender": message.sender,
                   "subject": message.subject[:120]}
        for name, fn in VARIANTS.items():
            record[name] = DECISION_TO_LABEL[fn(fv)]
        scored.append(record)

    summary = {"csv": str(args.csv), "n": len(scored), "variants": {}}
    print("\n" + "=" * 78)
    print(f"DETERMINISTIC (raw-email adaptation) ON {args.csv.name}")
    print("=" * 78)
    for name in VARIANTS:
        tc = three_class_metrics(scored, name)
        bm = binary_metrics(scored, name)
        summary["variants"][name] = {"three_class": tc, "binary": bm}
        print(f"\n  {name}")
        print(f"    3-class accuracy {tc['accuracy']:.3f}  macro-F1 {tc['macro_f1']:.3f}")
        print(f"    attack recall {bm['attack_recall']:.3f}  precision {bm['precision']:.3f}  "
              f"F1 {bm['f1']:.3f}  FPR {bm['false_positive_rate']:.3f}")
        dist = Counter(r[name] for r in scored)
        print(f"    decisions: {dict(dist)}")

    stem = args.out or (RESULTS_DIR / f"deterministic_raw_eval_{args.csv.stem}")
    Path(f"{stem}.jsonl").write_text("\n".join(json.dumps(r) for r in scored) + "\n", encoding="utf-8")
    Path(f"{stem}_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nwrote {stem}.jsonl\nwrote {stem}_summary.json")
    return summary


if __name__ == "__main__":
    main()
