"""Tests for the Task 17 Random Forest / XGBoost trainer."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from classifiers import task17_feature_models as task17

FEATURES = ["has_urgency", "has_body_external_address", "n_body_addresses", "body_word_count"]


def write_csv(path: Path, rows, header) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


@pytest.fixture
def tiny_csv(tmp_path):
    """A small separable three-class matrix, enough for 5-fold CV."""
    rng = np.random.default_rng(0)
    rows = []
    for index in range(90):
        label = ["SAFE", "REVISE", "ATTACK"][index % 3]
        base = {"SAFE": 0.0, "REVISE": 0.5, "ATTACK": 1.0}[label]
        values = [
            round(float(np.clip(base + rng.normal(0, 0.15), 0, 1)), 3),
            round(float(np.clip(base + rng.normal(0, 0.2), 0, 1)), 3),
            round(float(rng.integers(0, 3)), 3),
            round(float(20 + rng.normal(0, 5)), 3),
        ]
        rows.append([f"R{index:03d}", label, "hard_case" if index % 7 == 0 else "", *values])
    return write_csv(tmp_path / "tiny.csv", rows, ["record_id", "label", "notes", *FEATURES])


# -- data loading ---------------------------------------------------------

def test_load_dataset_separates_metadata_from_features(tiny_csv):
    X, y, names, meta = task17.load_dataset(tiny_csv)
    assert names == FEATURES
    assert X.shape == (90, 4)
    assert set(y) == {"SAFE", "REVISE", "ATTACK"}
    assert meta["hard"].sum() > 0


def test_drop_oracle_features_removes_those_columns(tiny_csv):
    _, _, names, _ = task17.load_dataset(tiny_csv, drop_oracle=True)
    assert "has_body_external_address" not in names
    assert "has_urgency" in names


def test_split_uses_the_datasets_own_splits_when_present(tmp_path):
    rows = []
    for index in range(40):
        split = ["train", "test", "flow_test", "adaptive_test"][index % 4]
        rows.append([f"R{index}", "corpusfile", split, "SAFE" if index % 2 else "ATTACK", "NONE", 1.0])
    path = write_csv(tmp_path / "c.csv",
                     rows, ["record_id", "sheet", "split", "label", "attack_category", "has_urgency"])

    X, y, _, meta = task17.load_dataset(path)
    train, test, extra = task17.split_data(X, y, meta, seed=0)

    assert all(meta["split"][i] in ("train", "validation") for i in train)
    assert all(meta["split"][i] == "test" for i in test)
    assert set(extra) == {"flow_test", "adaptive_test"}


def test_split_falls_back_to_a_stratified_split(tiny_csv):
    X, y, _, meta = task17.load_dataset(tiny_csv)
    train, test, extra = task17.split_data(X, y, meta, seed=0, test_size=0.25)
    assert extra == {}
    assert len(set(train) & set(test)) == 0
    assert len(train) + len(test) == len(y)
    # stratified: every class present on both sides
    assert set(y[train]) == set(y[test]) == {"SAFE", "REVISE", "ATTACK"}


# -- helpers --------------------------------------------------------------

def test_sample_weights_balance_the_classes():
    y = np.array(["SAFE"] * 90 + ["ATTACK"] * 10)
    weights = task17.sample_weights(y)
    safe_total = weights[y == "SAFE"].sum()
    attack_total = weights[y == "ATTACK"].sum()
    assert safe_total == pytest.approx(attack_total)


def test_attack_recall_counts_revise_as_caught():
    """Routing an attack to human review is a catch, not a miss."""
    result = {
        "labels": ["SAFE", "REVISE", "ATTACK"],
        # 100 true attacks: 10 called SAFE, 20 REVISE, 70 ATTACK
        "confusion_matrix": [[0, 0, 0], [0, 0, 0], [10, 20, 70]],
    }
    assert task17.attack_recall(result) == pytest.approx(0.9)


def test_attack_recall_is_none_without_an_attack_class():
    assert task17.attack_recall({"labels": ["SAFE"], "confusion_matrix": [[5]]}) is None


# -- end to end -----------------------------------------------------------

def test_training_run_reports_both_models_and_saves_them(tiny_csv, tmp_path, monkeypatch):
    monkeypatch.setattr(task17, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(task17, "MODELS_DIR", tmp_path / "models")

    summary = task17.main([
        "--csv", str(tiny_csv), "--dataset", "generated",
        "--model", "both", "--no-permutation",
    ])

    assert set(summary["models"]) == {"random_forest", "xgboost"}
    for name, data in summary["models"].items():
        assert 0.0 <= data["test"]["accuracy"] <= 1.0
        assert data["test"]["per_class"]
        assert data["latency_batch1"]["samples"] > 0
        # a custom --csv run is named after the file, so it cannot overwrite
        # the artifacts of a standard --dataset run
        assert (tmp_path / "models" / f"task17_tiny_{name}.joblib").exists()

    written = json.loads((tmp_path / "task17_models_tiny.json").read_text(encoding="utf-8"))
    assert written["rows"] == 90


def test_saved_model_carries_its_feature_order(tiny_csv, tmp_path, monkeypatch):
    """Inference must not be able to drift from training column order."""
    import joblib

    monkeypatch.setattr(task17, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(task17, "MODELS_DIR", tmp_path / "models")
    task17.main(["--csv", str(tiny_csv), "--model", "rf", "--no-permutation"])

    bundle = joblib.load(tmp_path / "models" / "task17_tiny_random_forest.joblib")
    assert bundle["features"] == FEATURES
    assert set(bundle["encoder"].classes_) == {"SAFE", "REVISE", "ATTACK"}

    prediction = bundle["encoder"].inverse_transform(
        bundle["model"].predict(np.array([[1.0, 1.0, 2.0, 25.0]]))
    )
    assert prediction[0] in {"SAFE", "REVISE", "ATTACK"}


def test_missing_dataset_fails_with_a_useful_message(tmp_path, monkeypatch):
    monkeypatch.setitem(task17.DATASETS, "generated", tmp_path / "nope.csv")
    with pytest.raises(SystemExit) as error:
        task17.main(["--dataset", "generated"])
    assert "generate_email_corpus" in str(error.value)
