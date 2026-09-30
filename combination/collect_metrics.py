"""Collect every technique's headline metrics into one markdown table.

Reads the result files each task already writes and prints (or injects) an
"Overall metrics" section for the root README, so the table cannot drift away
from the runs that produced it.

    python combination/collect_metrics.py            # print the markdown
    python combination/collect_metrics.py --write    # replace the section in README.md
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"

START = "<!-- OVERALL-METRICS:START -->"
END = "<!-- OVERALL-METRICS:END -->"


def _load_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _fmt(value) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def row(technique: str, variant: str, dataset: str, n, accuracy, recall, fpr, note: str = "") -> dict:
    return {
        "technique": technique, "variant": variant, "dataset": dataset,
        "n": n, "accuracy": accuracy, "recall": recall, "fpr": fpr, "note": note,
    }


def _false_positive_rate(test: dict) -> Optional[float]:
    """Share of SAFE rows not predicted SAFE, read off the confusion matrix."""
    labels = test.get("labels") or []
    matrix = test.get("confusion_matrix") or []
    if "SAFE" not in labels or not matrix:
        return None
    index = labels.index("SAFE")
    total = sum(matrix[index])
    if not total:
        return None
    return (total - matrix[index][index]) / total


def deterministic_rows() -> List[dict]:
    path = ROOT / "deterministic" / "results" / "comparison_metrics.csv"
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as handle:
        for entry in csv.DictReader(handle):
            if entry["view"] not in ("full", "destination_blind", "stale_allowlist"):
                continue
            out.append(row(
                "Deterministic (10-13)", f"{entry['guardrail']} [{entry['view']}]",
                f"corpus {int(entry['n']):,}", int(entry["n"]),
                float(entry["accuracy"]), float(entry["attack_recall"]),
                float(entry["false_positive_rate"]),
                "oracle view" if entry["view"] == "full" else "oracle withheld",
            ))
    return out


def promptguard_rows() -> List[dict]:
    out = []
    for path in sorted((ROOT / "classifiers" / "results").glob("task1[45]_*_summary.json")):
        data = _load_json(path)
        if not data or "mock" in path.stem:
            continue
        metrics = data["metrics"]
        out.append(row(
            "Prompt Guard (14-15)",
            f"{data.get('model_id', path.stem).split('/')[-1]} [{data.get('input_mode', '?')}]",
            "corpus 7,200", metrics["n"], metrics["accuracy"],
            metrics["attack_recall"], metrics["false_positive_rate"], "zero-shot",
        ))
    return out


def feature_model_rows() -> List[dict]:
    out = []
    for path in sorted((ROOT / "classifiers" / "results").glob("task17_models_*.json")):
        data = _load_json(path)
        if not data:
            continue
        dataset = path.stem.replace("task17_models_", "")
        for name, model in data["models"].items():
            test = model["test"]
            out.append(row(
                "Feature ML (17)", f"{name} [{dataset}]",
                f"{data['rows']:,} rows", test["n"], test["accuracy"],
                model.get("attack_recall"), _false_positive_rate(test),
                f"macro-F1 {test['macro_f1']:.3f}",
            ))
    return out


def qwen_rows() -> List[dict]:
    data = _load_json(ROOT / "classifiers" / "results" / "qwen_lora_colab_evaluation.json")
    if not data:
        return []
    return [row(
        "Qwen LoRA (18)", data.get("model", "qwen"), "corpus 7,200",
        data.get("n_validation"), data.get("accuracy"), None, None,
        "validation split",
    )]


def deterministic_ml_rows() -> List[dict]:
    path = ROOT / "combination" / "results" / "determinitic+ml" / "combination_summary.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        entries = list(csv.DictReader(handle))
    entries.sort(key=lambda e: -float(e["accuracy"]))
    picked = entries[:2] + entries[-1:]
    return [
        row("Rules + ML (24)", e["combination"], "corpus 7,200", int(e["n"]),
            float(e["accuracy"]), float(e["attack_recall"]),
            float(e["false_positive_rate"]), f"fusion {e['fusion']}")
        for e in picked
    ]


def cascade_rows() -> List[dict]:
    out = []
    # Only the canonical runs; smoke/probe/strategy files would duplicate rows.
    canonical = ("cascade_gemini_full.json", "cascade_gemini_pg2.json",
                 "cascade_oracle_headroom.json")
    seen = set()
    for name in canonical:
        data = _load_json(ROOT / "combination" / "results" / "ml+llm" / name)
        if not data:
            continue
        for entry in data["rows"]:
            if entry["budget"] not in (0.0, 0.05, 0.1):
                continue
            label = f"{entry['ml']} -> {entry['judge']} @ {entry['budget']:.0%}"
            if label in seen:
                continue
            seen.add(label)
            out.append(row(
                "ML + LLM cascade (25)", label, f"held-out {data['records']:,}",
                entry["n"], entry["accuracy"], entry["attack_recall"],
                entry["false_positive_rate"],
                f"escalated {entry.get('routed_rate', 0):.1%}",
            ))
    return out


def build_table() -> str:
    groups = [
        deterministic_rows(), promptguard_rows(), feature_model_rows(),
        qwen_rows(), deterministic_ml_rows(), cascade_rows(),
    ]
    lines = [
        "| Technique | Variant | Data | n | Accuracy | Attack recall | FPR | Note |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for group in groups:
        for entry in group:
            lines.append(
                f"| {entry['technique']} | `{entry['variant']}` | {entry['dataset']} | "
                f"{entry['n']} | {_fmt(entry['accuracy'])} | {_fmt(entry['recall'])} | "
                f"{_fmt(entry['fpr'])} | {entry['note']} |"
            )
    return "\n".join(lines)


def build_section() -> str:
    return f"""{START}
## Overall metrics

Generated by `python combination/collect_metrics.py --write` from the result
files each task writes, so the numbers cannot drift from the runs.

**Read the corpus rows with care.** In the corpus as given, the requested
destination is a perfect oracle, so any technique that can see it scores
1.000 - that measures the dataset, not the defence. The rows that mean
something are the ones on withheld-oracle views, the generated corpus, and
the held-out stress sheets.

{build_table()}

Attack recall counts FLAG as a catch: escalating to a human is a safe
outcome, not a miss.
{END}"""


def main(argv: Optional[Sequence[str]] = None) -> str:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--write", action="store_true", help="inject into README.md")
    args = parser.parse_args(argv)

    section = build_section()
    if not args.write:
        print(section)
        return section

    text = README.read_text(encoding="utf-8")
    if START in text and END in text:
        before = text[: text.index(START)]
        after = text[text.index(END) + len(END):]
        text = before + section + after
    else:
        text = text.rstrip("\n") + "\n\n" + section + "\n"
    README.write_text(text, encoding="utf-8")
    print(f"wrote the Overall metrics section into {README}")
    return section


if __name__ == "__main__":
    main()
