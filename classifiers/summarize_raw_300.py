"""One metrics table for every guardrail on the 300-email raw CSV.

    python classifiers/summarize_raw_300.py

Every number comes from the per-row prediction files the other
`eval_raw_csv_*.py` scripts wrote for `email_guardrail_raw_email_3class.csv`,
so all techniques and combinations are scored on the same 300 emails with the
same metric code. Nothing is re-run except the RF/XGBoost feature models,
which have no saved per-row output and are cheap to re-predict.

Label mapping onto SAFE / REVISE / ATTACK:
  * three-class techniques (deterministic, prompt agents, Gemini, RF/XGBoost)
    use their own label;
  * binary techniques (Prompt Guard 1/2, Qwen LoRA) map "attack" -> ATTACK and
    anything else -> SAFE. They have no REVISE output, so their three-class
    accuracy can never exceed 0.667 on this file (100 of 300 rows are REVISE).

Binary metrics treat ATTACK as the positive class and SAFE + REVISE as
negatives. FPR is therefore the share of non-attack emails called ATTACK.

Combinations use most-restrictive-wins (SAFE < REVISE < ATTACK), the same
fusion as combination/deterministic+ml/ and Task 23. Tasks 25-28 are read from
the per-row columns eval_raw_csv_combinations.py already computed.

Writes results/raw_email_300_overall_metrics.csv and .md.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Callable, Dict, List

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from classifiers.eval_raw_csv_combinations import (
    CONFIG, CSV_STEM, DATA_CSV, RESULTS_DIR, load_jsonl, most_restrictive,
)
from classifiers.features import FeatureExtractor, parse

OUT_DIR = ROOT / "results"
LABELS = ("SAFE", "REVISE", "ATTACK")


def feature_model_predictions(names: List[str]) -> Dict[str, Dict[str, str]]:
    import joblib
    import pandas as pd

    rows = pd.read_csv(DATA_CSV).to_dict("records")
    extractor = FeatureExtractor(CONFIG)
    vectors = [extractor.extract(parse(str(r["raw_email"]))).to_dict() for r in rows]
    out = {}
    for name in names:
        bundle = joblib.load(ROOT / "classifiers" / "models" / f"{name}.joblib")
        matrix = np.array([[v.get(f, 0.0) for f in bundle["features"]] for v in vectors])
        predicted = bundle["encoder"].inverse_transform(bundle["model"].predict(matrix))
        out[name] = {str(r["id"]): str(p) for r, p in zip(rows, predicted)}
    return out


def binary_file(stem: str, attack: Callable[[dict], bool]) -> Dict[str, str]:
    rows = load_jsonl(RESULTS_DIR / f"{stem}_raw_eval_{CSV_STEM}.jsonl")
    return {k: ("ATTACK" if attack(r) else "SAFE") for k, r in rows.items()}


def metrics(truth: Dict[str, str], pred: Dict[str, str]) -> dict:
    ids = [i for i in truth if i in pred]
    n = len(ids)
    exact = sum(pred[i] == truth[i] for i in ids)
    f1s = []
    per_class_recall = {}
    for label in LABELS:
        tp = sum(pred[i] == label and truth[i] == label for i in ids)
        fp = sum(pred[i] == label and truth[i] != label for i in ids)
        fn = sum(pred[i] != label and truth[i] == label for i in ids)
        p = tp / (tp + fp) if tp + fp else 0.0
        r = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * p * r / (p + r) if p + r else 0.0)
        per_class_recall[label] = r
    tp = sum(truth[i] == "ATTACK" and pred[i] == "ATTACK" for i in ids)
    fn = sum(truth[i] == "ATTACK" and pred[i] != "ATTACK" for i in ids)
    fp = sum(truth[i] != "ATTACK" and pred[i] == "ATTACK" for i in ids)
    tn = n - tp - fn - fp
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "n": n,
        "acc_3class": exact / n,
        "macro_f1": sum(f1s) / 3,
        "acc_binary": (tp + tn) / n,
        "precision": precision,
        "attack_recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "fpr": fp / (fp + tn) if fp + tn else 0.0,
        "safe_recall": per_class_recall["SAFE"],
        "revise_recall": per_class_recall["REVISE"],
    }


def main() -> List[dict]:
    det = load_jsonl(RESULTS_DIR / f"deterministic_raw_eval_{CSV_STEM}.jsonl")
    truth = {k: r["label"] for k, r in det.items()}

    rules = {
        "Basic rules": {k: r["basic"] for k, r in det.items()},
        "Provenance rules": {k: r["provenance"] for k, r in det.items()},
        "CI-norm": {k: r["ci_norm"] for k, r in det.items()},
    }
    rules["All deterministic rules"] = {
        k: most_restrictive(r["basic"], r["provenance"], r["ci_norm"]) for k, r in det.items()}

    feats = feature_model_predictions([
        "task17_generated_random_forest", "task17_generated_xgboost",
        "task17_training_set_with_revise_random_forest", "task17_training_set_with_revise_xgboost",
    ])
    ml = {
        "Random Forest": feats["task17_generated_random_forest"],
        "XGBoost": feats["task17_generated_xgboost"],
        "Prompt Guard 1 86M": binary_file("promptguard_pg1-86m", lambda r: r["pred_is_attack"]),
        "Prompt Guard 2 22M": binary_file("promptguard_pg2-22m", lambda r: r["pred_is_attack"]),
        "Prompt Guard 2 86M": binary_file("promptguard_pg2-86m", lambda r: r["pred_is_attack"]),
        "Qwen LoRA 0.5B": binary_file("qwen", lambda r: r["pred_is_attack"]),
    }
    extra_ml = {
        "Random Forest (trained with REVISE)": feats["task17_training_set_with_revise_random_forest"],
        "XGBoost (trained with REVISE)": feats["task17_training_set_with_revise_xgboost"],
    }
    prompts = {}
    for version, label in [("v7_baseline", "v7 baseline"), ("v8_basic_security", "v8 basic security"),
                           ("v9_context_aware", "v9 context-aware")]:
        rows = load_jsonl(RESULTS_DIR / f"prompt_agent_{version}_raw_eval_{CSV_STEM}.jsonl")
        prompts[label] = {k: r["predicted_label"] for k, r in rows.items()}
    gemini = {k: r["prediction"] for k, r in load_jsonl(RESULTS_DIR / f"gemini_raw_eval_{CSV_STEM}.jsonl").items()}

    table: List[dict] = []

    def add(section: str, family: str, name: str, pred: Dict[str, str], output: str) -> None:
        table.append({"section": section, "family": family, "method": name, "output": output,
                      **metrics(truth, pred)})

    for name, pred in rules.items():
        add("Individual", "Deterministic", name, pred, "3-class")
    for name, pred in {**ml, **extra_ml}.items():
        binary = name.startswith(("Prompt Guard", "Qwen"))
        add("Individual", "ML", name, pred, "binary" if binary else "3-class")
    for name, pred in prompts.items():
        add("Individual", "Prompt defense", name, pred, "3-class")
    add("Individual", "LLM judge", "Gemini 3.5 Flash Lite", gemini, "3-class")

    from classifiers.eval_raw_csv_gemini_tasks20_22 import revision_outcome, triad_outcome
    v7_rows = load_jsonl(RESULTS_DIR / f"prompt_agent_v7_baseline_raw_eval_{CSV_STEM}.jsonl")
    binary_judge = load_jsonl(RESULTS_DIR / f"gemini_t21_binary_raw_eval_{CSV_STEM}.jsonl")
    revision = load_jsonl(RESULTS_DIR / f"gemini_t22_revision_raw_eval_{CSV_STEM}.jsonl")
    triad = load_jsonl(RESULTS_DIR / f"gemini_t20_triad_raw_eval_{CSV_STEM}.jsonl")
    judge_rows = [
        ("Task 19: basic judge, 3-way (SAFE/REVISE/ATTACK)", gemini, "3-class"),
        ("Task 21: binary judge (ALLOW/BLOCK)", {k: r["prediction"] for k, r in binary_judge.items()}, "binary"),
        ("Task 20: TRIAD-style judge (strict)", {k: r["prediction"] for k, r in triad.items()}, "3-class"),
        ("Task 20: TRIAD-style judge (outcome-based)",
         {k: triad_outcome(r, v7_rows.get(k)) for k, r in triad.items()}, "3-class"),
        ("Task 22: revision loop K=0", {k: r["prediction_k0"] for k, r in revision.items()}, "binary"),
        ("Task 22: revision loop K=1 (strict)", {k: r["prediction"] for k, r in revision.items()}, "3-class"),
        ("Task 22: revision loop K=1 (outcome-based)",
         {k: revision_outcome(r) for k, r in revision.items()}, "3-class"),
    ]
    for name, pred, output in judge_rows:
        if pred:
            add("LLM judge (Tasks 19-22)", "LLM judge", name, pred, output)

    for p_name, p_pred in prompts.items():
        for r_name in ("Basic rules", "Provenance rules", "CI-norm"):
            r_pred = rules[r_name]
            add("Prompt + Deterministic", "Hybrid", f"{p_name} + {r_name}",
                {k: most_restrictive(p_pred[k], r_pred[k]) for k in p_pred if k in r_pred}, "3-class")

    for r_name, r_pred in rules.items():
        for m_name, m_pred in ml.items():
            add("Deterministic + ML", "Hybrid", f"{r_name} + {m_name}",
                {k: most_restrictive(r_pred[k], m_pred[k]) for k in r_pred if k in m_pred}, "3-class")

    combos = load_jsonl(RESULTS_DIR / f"combinations_raw_eval_{CSV_STEM}.jsonl")
    for key, name in [("t25", "Task 25: RF -> Gemini on RF/provenance disagreement"),
                      ("t26", "Task 26: Provenance -> Gemini on REVISE"),
                      ("t27", "Task 27: Rules vote -> RF -> Gemini (full cascade)"),
                      ("t28", "Task 28: Parallel vote (provenance, RF, Gemini)")]:
        add("Cascades / voting", "Hybrid", name,
            {k: r[key] for k, r in combos.items() if key in r}, "3-class")

    OUT_DIR.mkdir(exist_ok=True)
    cols = ["n", "acc_3class", "macro_f1", "acc_binary", "precision", "attack_recall", "f1", "fpr",
            "safe_recall", "revise_recall"]
    with (OUT_DIR / "raw_email_300_overall_metrics.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["section", "family", "method", "output", *cols])
        writer.writeheader()
        for row in table:
            writer.writerow({**row, **{c: (round(row[c], 4) if c != "n" else row[c]) for c in cols}})

    heads = ["Method", "Output", "n", "3-class acc", "Macro-F1", "Binary acc", "Precision",
             "Attack recall", "F1", "FPR", "SAFE recall", "REVISE recall"]
    lines = ["# Overall metrics on the 300-email raw dataset", "",
             "Generated by `python classifiers/summarize_raw_300.py`. Binary metrics: ATTACK is the "
             "positive class, SAFE + REVISE are negatives. Binary-output models (Prompt Guard, Qwen) "
             "have no REVISE label, so their 3-class accuracy is capped at 66.7%.", ""]
    for section in dict.fromkeys(r["section"] for r in table):
        lines += [f"## {section}", "", "| " + " | ".join(heads) + " |",
                  "|" + "|".join(["---"] * 2 + ["---:"] * (len(heads) - 2)) + "|"]
        for r in (r for r in table if r["section"] == section):
            pct = [f"{r[c] * 100:.2f}%" for c in cols[1:]]
            lines.append(f"| {r['method']} | {r['output']} | {r['n']} | " + " | ".join(pct) + " |")
        lines.append("")
    (OUT_DIR / "raw_email_300_overall_metrics.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    return table


if __name__ == "__main__":
    main()
