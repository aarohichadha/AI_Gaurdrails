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


def _binary_accuracy(m: dict) -> Optional[float]:
    total = m.get("tp", 0) + m.get("fn", 0) + m.get("fp", 0) + m.get("tn", 0)
    return (m["tp"] + m["tn"]) / total if total else None


def raw_email_300_rows() -> List[dict]:
    """The second corpus: 300 raw .eml-style messages, 100 each of
    SAFE/REVISE/ATTACK, no structured fields (no authorized-destination, no
    case record). Every technique here that normally reads those fields was
    re-implemented against a documented default policy instead of a literal
    rerun - see classifiers/eval_raw_csv_*.py docstrings for exactly what was
    assumed. Full report: results/raw_email_300_evaluation_report.pdf."""
    out = []
    results = ROOT / "classifiers" / "results"
    dataset = "raw-email 300"

    det = _load_json(results / "deterministic_raw_eval_email_guardrail_raw_email_3class_summary.json")
    if det:
        for name in ("basic", "provenance", "ci_norm"):
            v = det.get("variants", {}).get(name, {})
            tc, bm = v.get("three_class", {}), v.get("binary", {})
            out.append(row(
                "Deterministic, raw-email (10-12 adapted)", f"{name} [raw_email_300]",
                dataset, det.get("n"), tc.get("accuracy"), bm.get("attack_recall"),
                bm.get("false_positive_rate"), "3-class acc.; no destination field in this file",
            ))

    fm = _load_json(results / "feature_models_raw_eval_email_guardrail_raw_email_3class.json")
    if fm:
        for m in fm.get("models", []):
            out.append(row(
                "Feature ML (17), raw-email", f"{m['model']} [raw_email_300]",
                dataset, fm.get("n"), m.get("accuracy"),
                m.get("per_class", {}).get("ATTACK", {}).get("recall"), None,
                f"macro-F1 {m.get('macro_f1', 0):.3f}; generated-corpus model, out-of-distribution",
            ))

    qwen = _load_json(results / "qwen_raw_eval_email_guardrail_raw_email_3class_summary.json")
    if qwen:
        bm = qwen.get("binary", {})
        out.append(row(
            "Qwen LoRA (18), raw-email", "Qwen2.5-0.5B-Instruct [raw_email_300]",
            dataset, qwen.get("n"), _binary_accuracy(bm), bm.get("attack_recall"),
            bm.get("false_positive_rate"), "binary vocab (no REVISE token)",
        ))

    pg2 = _load_json(results / "promptguard_pg2-86m_raw_eval_email_guardrail_raw_email_3class_summary.json")
    if pg2:
        m = pg2.get("metrics", {})
        out.append(row(
            "Prompt Guard (14-15), raw-email", "Llama-Prompt-Guard-2-86M [raw_email_300]",
            dataset, pg2.get("n"), _binary_accuracy(m), m.get("attack_recall"),
            m.get("false_positive_rate"), "zero-shot, untrusted text only; PG1 blocked (HF license)",
        ))

    agent = _load_json(results / "prompt_agent_v9_context_aware_raw_eval_email_guardrail_raw_email_3class_summary.json")
    if agent:
        tc, bm = agent.get("three_class", {}), agent.get("binary", {})
        out.append(row(
            "Prompt agent (7-9), raw-email", "v9_context_aware [raw_email_300]",
            dataset, agent.get("n"), tc.get("accuracy"), bm.get("attack_recall"),
            bm.get("false_positive_rate"), "default policy has no authorization fields -> blocks everything",
        ))

    gemini = _load_json(results / "gemini_raw_eval_email_guardrail_raw_email_3class_summary.json")
    if gemini:
        tc, bm = gemini.get("three_class", {}), gemini.get("binary", {})
        out.append(row(
            "LLM judge (19), raw-email", "gemini-3.5-flash-lite plain [raw_email_300]",
            dataset, gemini.get("n"), tc.get("exact_match"), bm.get("attack_recall"),
            bm.get("false_positive_rate"), "best single technique on this file",
        ))

    combos = _load_json(results / "combinations_raw_eval_email_guardrail_raw_email_3class.json")
    if combos:
        labels = {
            "task23_prompt_or_deterministic": "23: agent v9 OR deterministic",
            "task24_provenance_or_random_forest": "24: deterministic OR random forest",
            "task24_provenance_or_promptguard2_86m": "24: deterministic OR prompt guard 2",
            "task24_provenance_or_qwen_lora": "24: deterministic OR qwen lora",
            "task25_ml_to_llm_on_disagreement": "25: RF -> Gemini (on disagreement)",
            "task26_deterministic_to_llm_on_revise": "26: deterministic -> Gemini (on REVISE)",
            "task27_full_cascade": "27: full cascade (det vote -> RF -> Gemini)",
            "task28_parallel_vote": "28: parallel vote (det, RF, Gemini)",
        }
        for key, label in labels.items():
            r = combos.get("results", {}).get(key, {})
            if "three_class" not in r:
                continue
            tc, bm = r["three_class"], r["binary"]
            note = f"escalated {r['escalation_rate']:.1%}" if "escalation_rate" in r else ""
            out.append(row(
                "Combinations (23-28), raw-email", f"{label} [raw_email_300]",
                dataset, tc.get("n"), tc.get("accuracy"), bm.get("attack_recall"),
                bm.get("false_positive_rate"), note,
            ))

    return out


def build_table() -> str:
    groups = [
        deterministic_rows(), promptguard_rows(), feature_model_rows(),
        qwen_rows(), deterministic_ml_rows(), cascade_rows(),
        raw_email_300_rows(),
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
