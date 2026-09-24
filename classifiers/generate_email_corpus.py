"""Generate a three-class `.eml` corpus (SAFE / REVISE / ATTACK) for Task 17.

Fills the three gaps the corpus-derived training set has: no attachments,
links or HTML; a body-address oracle; and no `REVISE` examples.

    python classifiers/generate_email_corpus.py                       # 3,000 emails
    python classifiers/generate_email_corpus.py --n 6000 --seed 1
    python classifiers/generate_email_corpus.py --out-dir classifiers/data/generated

Each run writes:
    <out-dir>/*.eml                      the messages themselves
    <out-dir>/labels.csv                 record_id, label, scenario, notes
    results/task17_generated_features.csv   labels + the Task 16 feature matrix

The label comes from the scenario the generator chose, never from the feature
values, so the features remain an honest measurement of the message.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from classifiers.emailgen.generator import LABELS, EmailGenerator, GeneratorConfig
from classifiers.features import ExtractorConfig, FeatureExtractor, parse

RESULTS_DIR = Path(__file__).resolve().parent / "results"
DEFAULT_OUT = Path(__file__).resolve().parent / "data" / "generated"

CONFIG = ExtractorConfig(
    internal_domains={"corp.example"},
    trusted_domains={"partner-a.example", "counsel.example", "audit-approved.example"},
)

#: Default class mix. SAFE is the majority class in any real mailbox.
DEFAULT_MIX = {"SAFE": 0.45, "REVISE": 0.25, "ATTACK": 0.30}


def class_counts(total: int, mix: Dict[str, float]) -> Dict[str, int]:
    counts = {label: int(total * share) for label, share in mix.items()}
    counts["SAFE"] += total - sum(counts.values())  # absorb rounding
    return counts


def report(rows, names: Sequence[str]) -> dict:
    """Class balance, feature coverage and per-class means."""
    labels = Counter(label for _, label, _, _ in rows)
    by_label: Dict[str, List] = defaultdict(list)
    for _, label, _, vector in rows:
        by_label[label].append(vector)

    varying = [n for n in names if len({v[n] for _, _, _, v in rows}) > 1]
    constant = [n for n in names if n not in varying]

    print("=" * 79)
    print("GENERATED EMAIL CORPUS")
    print("=" * 79)
    print(f"  emails          {len(rows)}")
    print("  classes         " + ", ".join(f"{k}={v}" for k, v in sorted(labels.items())))
    print(f"  features        {len(names)}   varying {len(varying)}   constant {len(constant)}")

    interesting = [
        "has_attachment", "has_executable_attachment", "has_macro_attachment",
        "has_link", "has_shortened_link", "has_ip_literal_link", "has_lookalike_link",
        "has_html_part", "has_hidden_text", "has_bcc", "sender_domain_is_lookalike",
        "sender_is_freemail", "has_body_external_address", "has_urgency",
        "has_instruction_override", "has_authorization_claim", "has_suspicious_language",
        "has_sensitive_data", "text_signal_families",
    ]
    print(f"\n  Mean per class (is any single feature giving the label away?)")
    header = f"    {'feature':<32}" + "".join(f"{label:>10}" for label in LABELS)
    print(header)
    print("    " + "-" * (len(header) - 4))
    summary_means: Dict[str, Dict[str, float]] = {}
    for name in interesting:
        if name not in names:
            continue
        means = {
            label: sum(v[name] for v in by_label[label]) / len(by_label[label])
            if by_label[label] else 0.0
            for label in LABELS
        }
        summary_means[name] = {k: round(v, 3) for k, v in means.items()}
        print(f"    {name:<32}" + "".join(f"{means[label]:>10.3f}" for label in LABELS))

    hard = sum(1 for _, _, notes, _ in rows if notes == "hard_case")
    print(f"\n  hard cases      {hard} ({hard / max(len(rows), 1):.0%}) "
          f"- benign mail that looks alarming, attacks with no trigger wording")

    if constant:
        print(f"\n  still constant  {', '.join(constant[:8])}"
              + (f" (+{len(constant) - 8} more)" if len(constant) > 8 else ""))

    return {
        "emails": len(rows),
        "classes": dict(labels),
        "varying_features": len(varying),
        "constant_features": constant,
        "hard_cases": hard,
        "class_means": summary_means,
    }


def parse_args(argv: Optional[Sequence[str]] = None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--n", type=int, default=3000, help="how many emails to generate")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--csv", type=Path, default=RESULTS_DIR / "task17_generated_features.csv")
    parser.add_argument("--summary", type=Path, default=None)
    parser.add_argument("--hard-case-rate", type=float, default=0.35)
    parser.add_argument("--no-eml", action="store_true", help="skip writing the .eml files")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> dict:
    args = parse_args(argv)

    generator = EmailGenerator(GeneratorConfig(seed=args.seed, hard_case_rate=args.hard_case_rate))
    counts = class_counts(args.n, DEFAULT_MIX)
    print(f"generating {args.n} emails: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    generated = generator.generate_many(counts)

    extractor = FeatureExtractor(CONFIG)
    names = extractor.feature_names

    if not args.no_eml:
        args.out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for item in generated:
        raw = item.as_bytes()
        if not args.no_eml:
            (args.out_dir / f"{item.record_id}.eml").write_bytes(raw)
        rows.append((item.record_id, item.label, item.notes, extractor.extract(parse(raw))))

    if not args.no_eml:
        with (args.out_dir / "labels.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["record_id", "label", "scenario", "notes", "rationale"])
            for item in generated:
                writer.writerow([item.record_id, item.label, item.scenario, item.notes, item.rationale])

    args.csv.parent.mkdir(parents=True, exist_ok=True)
    with args.csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["record_id", "label", "notes", *names])
        for record_id, label, notes, vector in rows:
            writer.writerow([record_id, label, notes, *vector.to_list(names)])

    summary = report(rows, names)
    summary["csv"] = str(args.csv)
    print(f"\nwrote {args.csv}")
    if not args.no_eml:
        print(f"wrote {len(rows)} .eml files + labels.csv to {args.out_dir}")
    if args.summary:
        args.summary.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"wrote {args.summary}")
    return summary


if __name__ == "__main__":
    main()
