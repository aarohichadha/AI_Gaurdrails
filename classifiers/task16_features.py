"""Task 16 - Feature engineering for raw email.

    RAW EMAIL
        |
    Email Parser
        |
    Feature Extractor
        |
        +----------------------+----------------------+
        |                                             |
   Metadata features                            Text features
   sender domain                                urgency
   recipient domain                             override language
   attachments                                  authorization claim
   links                                        sensitive-data indicators
        |                                             |
        +----------------------+----------------------+
                               |
                        FEATURE VECTOR      <-- this file stops here
                               |
                     Random Forest / XGBoost          (Task 17)
                               |
                    SAFE / REVISE / ATTACK

Nothing here reads the project dataset. The input is an ordinary `.eml`
message, so the same code runs against a live mailbox.

    python classifiers/task16_features.py                      # bundled samples
    python classifiers/task16_features.py path/to/mail.eml     # one message
    python classifiers/task16_features.py inbox/ --csv out.csv # a folder
    python classifiers/task16_features.py --list-features
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from classifiers.features import ExtractorConfig, FeatureExtractor, FeatureVector, RawEmail, parse

SAMPLES_DIR = Path(__file__).resolve().parent / "features" / "samples"

#: Deployment facts for the bundled samples. Override with the CLI flags.
DEFAULT_CONFIG = ExtractorConfig(
    internal_domains={"corp.example"},
    trusted_domains={"partner-a.example", "counsel.example", "audit-approved.example"},
)

#: Which features belong to which branch of the diagram, for reporting.
TEXT_PREFIXES = (
    "urgency", "has_urgency", "instruction_override", "has_instruction_override",
    "authorization_claim", "has_authorization_claim", "suspicious_", "has_suspicious",
    "sensitive_", "has_sensitive", "instruction_verb", "instruction_density",
    "text_signal",
)


def is_text_feature(name: str) -> bool:
    return name.startswith(TEXT_PREFIXES)


def collect_emails(paths: Sequence[str]) -> List[Tuple[str, RawEmail]]:
    """Gather `.eml` files from the given files/folders (or the samples)."""
    targets: List[Path] = []
    for raw in paths or [str(SAMPLES_DIR)]:
        path = Path(raw)
        if path.is_dir():
            targets.extend(sorted(path.rglob("*.eml")))
        elif path.exists():
            targets.append(path)
        else:
            raise SystemExit(f"no such file or folder: {path}")
    if not targets:
        raise SystemExit("no .eml files found")
    return [(p.name, parse(p.read_bytes())) for p in targets]


def summarise(name: str, message: RawEmail, vector: FeatureVector) -> None:
    print("=" * 79)
    print(f"{name}")
    print("=" * 79)
    print(f"  From:        {message.sender_display_name or '-'} <{message.sender or '-'}>")
    print(f"  To/Cc/Bcc:   {len(message.to)}/{len(message.cc)}/{len(message.bcc)}")
    print(f"  Subject:     {message.subject[:60] or '-'}")
    print(f"  Attachments: {len(message.attachments)}   Links: {len(message.links)}"
          f"   Hidden text: {'yes' if message.hidden_text.strip() else 'no'}")

    active = vector.nonzero()
    metadata = {k: v for k, v in active.items() if not is_text_feature(k)}
    text = {k: v for k, v in active.items() if is_text_feature(k)}

    print(f"\n  Metadata features ({len(metadata)} non-zero)")
    for key, value in metadata.items():
        print(f"    {key:<34}{value:>10.3f}")
    print(f"\n  Text features ({len(text)} non-zero)")
    for key, value in text.items():
        print(f"    {key:<34}{value:>10.3f}")

    if vector.evidence:
        print("\n  Evidence (what fired each text feature)")
        for line in vector.explain():
            print(f"    - {line}")
    print()


def write_csv(path: Path, rows: List[Tuple[str, FeatureVector]], names: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source", *names])
        for source, vector in rows:
            writer.writerow([source, *vector.to_list(names)])


def write_json(path: Path, rows: List[Tuple[str, FeatureVector]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {"source": source, "features": vector.to_dict(), "evidence": vector.evidence}
        for source, vector in rows
    ]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def parse_args(argv: Optional[Sequence[str]] = None):
    parser = argparse.ArgumentParser(description="Extract Task 16 features from raw email")
    parser.add_argument("paths", nargs="*", help=".eml files or folders (default: bundled samples)")
    parser.add_argument("--internal", default="corp.example",
                        help="comma-separated internal domains")
    parser.add_argument("--trusted", default="partner-a.example,counsel.example,audit-approved.example",
                        help="comma-separated approved external domains")
    parser.add_argument("--csv", type=Path, help="write the feature matrix to this CSV")
    parser.add_argument("--json", type=Path, help="write features + evidence to this JSON")
    parser.add_argument("--quiet", action="store_true", help="suppress the per-email report")
    parser.add_argument("--list-features", action="store_true", help="print the feature names and exit")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> List[Tuple[str, FeatureVector]]:
    args = parse_args(argv)

    config = ExtractorConfig(
        internal_domains={d.strip() for d in args.internal.split(",") if d.strip()},
        trusted_domains={d.strip() for d in args.trusted.split(",") if d.strip()},
    )
    extractor = FeatureExtractor(config)
    names = extractor.feature_names

    if args.list_features:
        metadata = [n for n in names if not is_text_feature(n)]
        text = [n for n in names if is_text_feature(n)]
        print(f"{len(names)} features = {len(metadata)} metadata + {len(text)} text\n")
        print("METADATA FEATURES")
        for name in metadata:
            print(f"  {name}")
        print("\nTEXT FEATURES")
        for name in text:
            print(f"  {name}")
        return []

    emails = collect_emails(args.paths)
    rows = [(name, extractor.extract(message)) for name, message in emails]

    if not args.quiet:
        for (name, message), (_, vector) in zip(emails, rows):
            summarise(name, message, vector)

    print(f"{len(rows)} email(s) -> feature vectors of {len(names)} features each")
    if args.csv:
        write_csv(args.csv, rows, names)
        print(f"wrote {args.csv}")
    if args.json:
        write_json(args.json, rows)
        print(f"wrote {args.json}")
    return rows


if __name__ == "__main__":
    main()
