"""Build a labelled training set for Task 17 by rendering the corpus as email.

    dataset record -> .eml message -> Task 16 feature extractor -> labelled row

The Task 16 feature code stays dataset-agnostic; this script is the bridge, so
the pipeline itself never learns anything about the corpus.

    python classifiers/build_training_set.py
    python classifiers/build_training_set.py --write-eml classifiers/data/eml
    python classifiers/build_training_set.py --drop-oracle-features
    python classifiers/build_training_set.py --split test --limit 200

IMPORTANT - two honesty caveats, both reported by every run:

1. **The corpus is plain text.** Its records have no attachments, links, HTML
   or hidden text, so those features are constant here. A model trained on
   this alone is a text-and-address model, not a full email model. Mix in real
   `.eml` corpora (SpamAssassin, Nazario phishing, Enron) before believing any
   number that involves attachments or links.

2. **`has_body_external_address` is an oracle on this corpus.** Every attack
   record names an off-allowlist address in its body and every benign record
   names an approved one, so that single feature separates the classes almost
   perfectly. Train with `--drop-oracle-features` as well, and report both:
   the honest number is the one without it.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.dataset import DATA_SHEETS, DATASET, load_actions
from common.schema import Action

from classifiers.features import ExtractorConfig, FeatureExtractor, FeatureVector, RawEmail

RESULTS_DIR = Path(__file__).resolve().parent / "results"

#: The corpus's own deployment facts.
CORPUS_CONFIG = ExtractorConfig(
    internal_domains={"corp.example"},
    trusted_domains={"partner-a.example", "counsel.example", "audit-approved.example"},
)

#: Corpus labels -> Task 17 classes. The six original sheets are two-class;
#: `REVISE` rows come only from the exported synthetic sheet, which
#: `--include-generated` pulls in.
LABEL_MAP = {"BENIGN": "SAFE", "ATTACK": "ATTACK", "REVISE": "REVISE"}

#: Features that are near-perfect predictors *on this corpus only*, because of
#: how it was generated. Not dropped by default - surfaced so a run can
#: measure with and without them.
ORACLE_FEATURES = (
    "n_body_external_addresses",
    "has_body_external_address",
    "has_body_lookalike_address",
    "has_body_freemail_address",
)


def _display_name(address: str) -> str:
    local = address.split("@", 1)[0]
    return " ".join(part.capitalize() for part in local.replace(".", " ").split())


def action_to_email(action: Action) -> RawEmail:
    """Render one corpus record as the email an agent would actually receive.

    The requested destination is deliberately **not** added as a header
    recipient: in the scenario the message is addressed to the agent and only
    *asks* for a forward, so putting it in `To:` would invent a header the real
    scenario does not have (and would hand the classifier the label).
    """
    sender = action.sender or "unknown@corp.example"
    mailbox = action.mailbox_owner or "agent.user@corp.example"
    return RawEmail.from_parts(
        sender=f"{_display_name(sender)} <{sender}>",
        to=[mailbox],
        subject=action.subject,
        body=action.email_body,
    )


def build_rows(
    actions: Sequence[Action],
    extractor: FeatureExtractor,
    eml_dir: Optional[Path] = None,
) -> List[Tuple[Action, RawEmail, FeatureVector]]:
    rows = []
    if eml_dir:
        eml_dir.mkdir(parents=True, exist_ok=True)
    for action in actions:
        message = action_to_email(action)
        rows.append((action, message, extractor.extract(message)))
        if eml_dir:
            _write_eml(eml_dir / f"{action.record_id}.eml", action, message)
    return rows


def _write_eml(path: Path, action: Action, message: RawEmail) -> None:
    """Serialise the rendered message as a real `.eml` file."""
    lines = [
        f"From: {_display_name(action.sender)} <{action.sender}>",
        f"To: {action.mailbox_owner}",
        f"Subject: {action.subject}",
        f"Message-ID: <{action.record_id}@corp.example>",
        "MIME-Version: 1.0",
        'Content-Type: text/plain; charset="utf-8"',
        "",
        action.email_body,
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def split_lookup(include_generated: bool = False) -> Dict[str, str]:
    import pandas as pd

    from common.dataset import GENERATED_SHEET

    workbook = pd.ExcelFile(DATASET)
    sheets = list(DATA_SHEETS)
    if include_generated and GENERATED_SHEET in workbook.sheet_names:
        sheets.append(GENERATED_SHEET)
    lookup: Dict[str, str] = {}
    for sheet in sheets:
        frame = workbook.parse(sheet, usecols=["record_id", "split"])
        lookup.update(dict(zip(frame["record_id"].astype(str), frame["split"].astype(str))))
    return lookup


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def report_coverage(rows, names: Sequence[str]) -> Dict[str, List[str]]:
    """Which features actually vary on this data, and which are dead."""
    varying, constant = [], []
    for name in names:
        values = {vector[name] for _, _, vector in rows}
        (varying if len(values) > 1 else constant).append(name)
    return {"varying": varying, "constant": constant}


def report_separation(rows, names: Sequence[str], top: int = 12) -> List[Tuple[str, float, float, float]]:
    """Mean per class, so leakage is visible before any model is trained."""
    safe = [v for a, _, v in rows if LABEL_MAP.get(a.label) == "SAFE"]
    attack = [v for a, _, v in rows if LABEL_MAP.get(a.label) == "ATTACK"]
    if not safe or not attack:
        return []

    scored = []
    for name in names:
        s = sum(v[name] for v in safe) / len(safe)
        a = sum(v[name] for v in attack) / len(attack)
        spread = max(abs(x[name]) for x in (*safe[:1], *attack[:1])) or 1.0
        scored.append((name, s, a, abs(a - s) / (spread or 1.0)))
    scored.sort(key=lambda row: -row[3])
    return scored[:top]


def print_report(rows, names, coverage, dropped) -> None:
    labels = Counter(LABEL_MAP.get(a.label, a.label) for a, _, _ in rows)
    print("=" * 79)
    print("TRAINING SET FROM THE CORPUS")
    print("=" * 79)
    print(f"  rows            {len(rows)}")
    print(f"  classes         " + ", ".join(f"{k}={v}" for k, v in sorted(labels.items())))
    if len(labels) > 1:
        ratio = max(labels.values()) / min(labels.values())
        if ratio >= 2:
            print(f"                  imbalanced {ratio:.1f}:1 - use class weights or resampling in Task 17")
    print(f"  features        {len(names)}" + (f"  ({len(dropped)} oracle features dropped)" if dropped else ""))
    print(f"  varying         {len(coverage['varying'])}")
    print(f"  constant (dead) {len(coverage['constant'])}")

    if coverage["constant"]:
        print("\n  Dead columns - the corpus has no attachments, links, HTML or hidden text:")
        for index in range(0, min(len(coverage["constant"]), 24), 4):
            print("    " + "  ".join(f"{n:<28}" for n in coverage["constant"][index:index + 4]))
        if len(coverage["constant"]) > 24:
            print(f"    ... and {len(coverage['constant']) - 24} more")

    separation = report_separation(rows, coverage["varying"])
    if separation:
        print("\n  Strongest single features (mean per class) - check these for leakage:")
        print(f"    {'feature':<34}{'SAFE':>10}{'ATTACK':>10}")
        for name, safe_mean, attack_mean, _ in separation:
            flag = "  <-- oracle on this corpus" if name in ORACLE_FEATURES else ""
            print(f"    {name:<34}{safe_mean:>10.3f}{attack_mean:>10.3f}{flag}")


def write_csv(path: Path, rows, names: Sequence[str], splits: Dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["record_id", "sheet", "split", "label", "attack_category", *names])
        for action, _, vector in rows:
            writer.writerow([
                action.record_id,
                action.sheet,
                splits.get(action.record_id, ""),
                LABEL_MAP.get(action.label, action.label),
                action.attack_category,
                *vector.to_list(names),
            ])


def parse_args(argv: Optional[Sequence[str]] = None):
    parser = argparse.ArgumentParser(description="Render the corpus as email and extract Task 16 features")
    parser.add_argument("--csv", type=Path, default=RESULTS_DIR / "task17_training_set.csv")
    parser.add_argument("--write-eml", type=Path, default=None,
                        help="also save each rendered message as a real .eml file")
    parser.add_argument("--split", default=None,
                        help="train | validation | test | flow_test | adaptive_test")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--drop-oracle-features", action="store_true",
                        help="exclude the body-address features that leak the label on this corpus")
    parser.add_argument("--include-generated", action="store_true",
                        help="also read the synthetic three-class sheet (adds REVISE rows)")
    parser.add_argument("--summary", type=Path, default=None, help="write a JSON summary here")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> dict:
    args = parse_args(argv)

    actions = load_actions(include_generated=args.include_generated)
    splits = split_lookup(include_generated=args.include_generated)
    if args.split:
        actions = [a for a in actions if splits.get(a.record_id) == args.split]
    if args.limit:
        actions = actions[: args.limit]

    extractor = FeatureExtractor(CORPUS_CONFIG)
    names = list(extractor.feature_names)
    dropped: List[str] = []
    if args.drop_oracle_features:
        dropped = [n for n in names if n in ORACLE_FEATURES]
        names = [n for n in names if n not in ORACLE_FEATURES]

    print(f"rendering {len(actions)} records as email ...")
    rows = build_rows(actions, extractor, args.write_eml)

    coverage = report_coverage(rows, names)
    print_report(rows, names, coverage, dropped)

    write_csv(args.csv, rows, names, splits)
    print(f"\nwrote {args.csv}")
    if args.write_eml:
        print(f"wrote {len(rows)} .eml files to {args.write_eml}")

    summary = {
        "rows": len(rows),
        "features": len(names),
        "dropped_oracle_features": dropped,
        "classes": dict(Counter(LABEL_MAP.get(a.label, a.label) for a, _, _ in rows)),
        "varying_features": coverage["varying"],
        "constant_features": coverage["constant"],
        "csv": str(args.csv),
    }
    if args.summary:
        args.summary.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"wrote {args.summary}")
    return summary


if __name__ == "__main__":
    main()
