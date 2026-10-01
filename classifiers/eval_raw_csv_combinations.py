"""Combination guardrails (Tasks 23-28), adapted for a raw-email-only CSV.

    python classifiers/eval_raw_csv_combinations.py

Joins the per-row prediction files every other `eval_raw_csv_*.py` script in
this folder already wrote for `email_guardrail_raw_email_3class.csv`
(deterministic, RF/XGBoost, Prompt Guard 2, Qwen LoRA, the Gemini judge, and
-- if present -- the v9 prompt agent) by `id`, maps every technique's native
vocabulary onto this file's own SAFE/REVISE/ATTACK labels, and computes five
combination strategies with real per-row logic (no re-labelled ground truth,
no invented predictions):

  Task 23  prompt agent (v9) OR deterministic (provenance)   -- only over
           rows the agent subsample actually covers
  Task 24  deterministic (provenance) OR each ML classifier   -- most-
           restrictive-wins, mirrors combination/deterministic+ml/
  Task 25  ML (Random Forest) -> Gemini judge, escalated on RF/provenance
           disagreement (RF alone gives no REVISE and no probability
           ambiguity on this file -- see eval_raw_csv_models.py's own
           finding that both tree models predict SAFE/ATTACK only here)
  Task 26  deterministic (provenance) -> Gemini judge, escalated whenever
           provenance itself lands on REVISE (its own "can't decide" case)
  Task 27  full cascade: 3-way deterministic vote -> unanimous is terminal;
           a split escalates to Random Forest; RF confidence < 0.7 escalates
           again to the Gemini judge (same threshold as cascade/run_cascade.py)
  Task 28  parallel vote across {provenance, Random Forest, Gemini judge},
           ties broken by the most restrictive vote (ATTACK > REVISE > SAFE)

"Most restrictive wins" uses the same ordering as `common.schema.Decision`:
SAFE < REVISE < ATTACK.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from classifiers.features import ExtractorConfig, FeatureExtractor, parse

RESULTS_DIR = ROOT / "classifiers" / "results"
CSV_STEM = "email_guardrail_raw_email_3class"
DATA_CSV = ROOT / "data" / f"{CSV_STEM}.csv"

RANK = {"SAFE": 0, "REVISE": 1, "ATTACK": 2}
CONFIG = ExtractorConfig(
    internal_domains={"corp.example"},
    trusted_domains={"partner-a.example", "counsel.example", "audit-approved.example"},
)


def most_restrictive(*labels: str) -> str:
    return max(labels, key=lambda l: RANK[l])


def load_jsonl(path: Path) -> Dict[str, dict]:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out[str(r["id"])] = r
    return out


def rf_predictions() -> Dict[str, dict]:
    """Recompute Random Forest predictions + confidence inline (the saved
    summary from eval_raw_csv_models.py has no per-row output)."""
    import joblib
    import pandas as pd

    bundle_path = ROOT / "classifiers" / "models" / "task17_generated_random_forest.joblib"
    if not bundle_path.exists():
        return {}
    bundle = joblib.load(bundle_path)
    rows = pd.read_csv(DATA_CSV).to_dict("records")
    extractor = FeatureExtractor(CONFIG)
    vectors = [extractor.extract(parse(str(r["raw_email"]))) for r in rows]
    features = bundle["features"]
    matrix = np.array([[v.to_dict().get(f, 0.0) for f in features] for v in vectors])
    proba = bundle["model"].predict_proba(matrix)
    predicted = bundle["encoder"].inverse_transform(proba.argmax(axis=1))
    out = {}
    for row, label, probs in zip(rows, predicted, proba):
        out[str(row["id"])] = {"prediction": str(label), "confidence": round(float(probs.max()), 4)}
    return out


def three_class_metrics(rows: Sequence[dict], truth_key: str, pred_key: str) -> dict:
    labels = ["SAFE", "REVISE", "ATTACK"]
    hits = sum(r[pred_key] == r[truth_key] for r in rows)
    per_class = {}
    for label in labels:
        tp = sum(r[truth_key] == label and r[pred_key] == label for r in rows)
        fp = sum(r[truth_key] != label and r[pred_key] == label for r in rows)
        fn = sum(r[truth_key] == label and r[pred_key] != label for r in rows)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}
    macro_f1 = sum(c["f1"] for c in per_class.values()) / len(per_class)
    return {"n": len(rows), "accuracy": round(hits / len(rows), 4) if rows else 0.0,
            "macro_f1": round(macro_f1, 4), "per_class": per_class}


def binary_metrics(rows: Sequence[dict], truth_key: str, pred_key: str) -> dict:
    tp = sum(r[truth_key] == "ATTACK" and r[pred_key] == "ATTACK" for r in rows)
    fn = sum(r[truth_key] == "ATTACK" and r[pred_key] != "ATTACK" for r in rows)
    fp = sum(r[truth_key] != "ATTACK" and r[pred_key] == "ATTACK" for r in rows)
    tn = sum(r[truth_key] != "ATTACK" and r[pred_key] != "ATTACK" for r in rows)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {"attack_recall": round(recall, 4), "precision": round(precision, 4),
            "f1": round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0,
            "false_positive_rate": round(fp / (fp + tn), 4) if fp + tn else 0.0}


def main() -> dict:
    det = load_jsonl(RESULTS_DIR / f"deterministic_raw_eval_{CSV_STEM}.jsonl")
    pg2 = load_jsonl(RESULTS_DIR / f"promptguard_pg2-86m_raw_eval_{CSV_STEM}.jsonl")
    qwen = load_jsonl(RESULTS_DIR / f"qwen_raw_eval_{CSV_STEM}.jsonl")
    gemini = load_jsonl(RESULTS_DIR / f"gemini_raw_eval_{CSV_STEM}.jsonl")
    agent = load_jsonl(RESULTS_DIR / f"prompt_agent_v9_context_aware_raw_eval_{CSV_STEM}.jsonl")
    rf = rf_predictions()

    ids = sorted(det.keys())
    print(f"joined {len(ids)} deterministic rows | pg2 {len(pg2)} | qwen {len(qwen)} | "
          f"gemini {len(gemini)} | rf {len(rf)} | v9 agent {len(agent)}")

    joined: List[dict] = []
    for row_id in ids:
        label = det[row_id]["label"]
        entry = {"id": row_id, "label": label, "provenance": det[row_id]["provenance"],
                  "basic": det[row_id]["basic"], "ci_norm": det[row_id]["ci_norm"]}
        if row_id in pg2:
            entry["pg2"] = "ATTACK" if pg2[row_id]["pred_is_attack"] else "SAFE"
        if row_id in qwen:
            entry["qwen"] = "ATTACK" if qwen[row_id]["pred_is_attack"] else "SAFE"
        if row_id in gemini:
            entry["gemini"] = gemini[row_id]["prediction"]
        if row_id in rf:
            entry["rf"] = rf[row_id]["prediction"]
            entry["rf_confidence"] = rf[row_id]["confidence"]
        if row_id in agent:
            entry["agent_v9"] = agent[row_id]["predicted_label"]
        joined.append(entry)

    has_gemini = all("gemini" in e for e in joined)
    has_rf = all("rf" in e for e in joined)
    results: Dict[str, dict] = {}

    # Task 24 -- deterministic (provenance) OR each ML classifier
    for ml_key, ml_name in [("rf", "random_forest"), ("pg2", "promptguard2_86m"), ("qwen", "qwen_lora")]:
        rows = [e for e in joined if ml_key in e]
        if not rows:
            continue
        for r in rows:
            r[f"t24_{ml_name}"] = most_restrictive(r["provenance"], r[ml_key])
        results[f"task24_provenance_or_{ml_name}"] = {
            "three_class": three_class_metrics(rows, "label", f"t24_{ml_name}"),
            "binary": binary_metrics(rows, "label", f"t24_{ml_name}"),
        }

    if has_rf:
        # Task 25 -- RF terminal unless RF disagrees with provenance, then Gemini decides
        rows = [e for e in joined if "gemini" in e]
        for r in rows:
            r["t25"] = r["gemini"] if r["rf"] != r["provenance"] else r["rf"]
        escalated_25 = sum(1 for r in rows if r["rf"] != r["provenance"])
        results["task25_ml_to_llm_on_disagreement"] = {
            "three_class": three_class_metrics(rows, "label", "t25"),
            "binary": binary_metrics(rows, "label", "t25"),
            "escalation_rate": round(escalated_25 / len(rows), 4) if rows else 0.0,
        }

        # Task 27 -- full cascade: det vote -> RF -> Gemini (RF confidence < 0.7)
        rows = [e for e in joined if "gemini" in e]
        for r in rows:
            votes = [r["basic"], r["provenance"], r["ci_norm"]]
            unanimous = len(set(votes)) == 1
            if unanimous:
                r["t27"] = votes[0]
                r["t27_layer"] = 1
            elif r["rf_confidence"] >= 0.7:
                r["t27"] = r["rf"]
                r["t27_layer"] = 2
            else:
                r["t27"] = r["gemini"]
                r["t27_layer"] = 3
        layer_counts = Counter(r["t27_layer"] for r in rows)
        results["task27_full_cascade"] = {
            "three_class": three_class_metrics(rows, "label", "t27"),
            "binary": binary_metrics(rows, "label", "t27"),
            "layer_resolution": {f"layer_{k}": v for k, v in sorted(layer_counts.items())},
        }

        # Task 28 -- parallel majority vote {provenance, RF, Gemini}
        for r in rows:
            votes = [r["provenance"], r["rf"], r["gemini"]]
            counts = Counter(votes)
            top = counts.most_common()
            if len(top) > 1 and top[0][1] == top[1][1]:
                r["t28"] = most_restrictive(*votes)  # no majority -> most restrictive wins
            else:
                r["t28"] = top[0][0]
        results["task28_parallel_vote"] = {
            "three_class": three_class_metrics(rows, "label", "t28"),
            "binary": binary_metrics(rows, "label", "t28"),
        }

    # Task 26 -- deterministic (provenance) -> Gemini only when provenance itself says REVISE
    if has_gemini:
        rows = [e for e in joined if "gemini" in e]
        for r in rows:
            r["t26"] = r["gemini"] if r["provenance"] == "REVISE" else r["provenance"]
        escalated_26 = sum(1 for r in rows if r["provenance"] == "REVISE")
        results["task26_deterministic_to_llm_on_revise"] = {
            "three_class": three_class_metrics(rows, "label", "t26"),
            "binary": binary_metrics(rows, "label", "t26"),
            "escalation_rate": round(escalated_26 / len(rows), 4) if rows else 0.0,
        }

    # Task 23 -- prompt agent (v9) OR deterministic (provenance), only over the agent subsample
    rows = [e for e in joined if "agent_v9" in e]
    if rows:
        for r in rows:
            r["t23"] = most_restrictive(r["agent_v9"], r["provenance"])
        results["task23_prompt_or_deterministic"] = {
            "three_class": three_class_metrics(rows, "label", "t23"),
            "binary": binary_metrics(rows, "label", "t23"),
            "note": f"agent subsample only, n={len(rows)}",
        }
    else:
        results["task23_prompt_or_deterministic"] = {"note": "v9 agent run not finished yet -- rerun this script once it is"}

    print("\n" + "=" * 78)
    print("COMBINATIONS (raw-email adaptation)")
    print("=" * 78)
    for name, r in results.items():
        print(f"\n  {name}")
        if "three_class" not in r:
            print(f"    {r.get('note')}")
            continue
        tc, bm = r["three_class"], r["binary"]
        print(f"    n={tc['n']}  3-class accuracy {tc['accuracy']:.3f}  macro-F1 {tc['macro_f1']:.3f}")
        print(f"    attack recall {bm['attack_recall']:.3f}  precision {bm['precision']:.3f}  "
              f"F1 {bm['f1']:.3f}  FPR {bm['false_positive_rate']:.3f}")
        if "escalation_rate" in r:
            print(f"    escalated to LLM judge: {r['escalation_rate']:.1%}")
        if "layer_resolution" in r:
            print(f"    layer resolution: {r['layer_resolution']}")

    out_path = RESULTS_DIR / f"combinations_raw_eval_{CSV_STEM}.json"
    out_path.write_text(json.dumps({"csv": str(DATA_CSV), "n_joined": len(ids), "results": results}, indent=2),
                         encoding="utf-8")
    jsonl_path = RESULTS_DIR / f"combinations_raw_eval_{CSV_STEM}.jsonl"
    jsonl_path.write_text("\n".join(json.dumps(r) for r in joined) + "\n", encoding="utf-8")
    print(f"\nwrote {out_path}\nwrote {jsonl_path}")
    return results


if __name__ == "__main__":
    main()
