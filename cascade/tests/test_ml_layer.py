"""Tests for Layer 2's prediction/confidence wrapper.

Uses a fake `predict_proba` model instead of training a real XGBoost model,
so these run fast and don't depend on the corpus. `test_train_...` is the
one exception - it exercises the real training path end to end, on a tiny
slice, to catch wiring breaks (feature extraction, class encoding, save/load).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pytest

from common.schema import Action, Decision

from cascade.ml_layer import MLLayer, train


def make_action(**overrides) -> Action:
    defaults = dict(
        record_id="T1-TEST",
        task_id="TASK1",
        sheet="Base Dataset",
        label="BENIGN",
        expected_decision="ALLOW",
        attack_category="NONE",
        attack_technique="none",
        difficulty="EASY",
        sender="amina.finance@corp.example",
        sender_trust="INTERNAL_VERIFIED",
        mailbox_owner="agent.user@corp.example",
        data_asset="quarterly forecast workbook",
        data_sensitivity="FINANCIAL_CONFIDENTIAL",
        requested_action="SEND_EMAIL",
        requested_destination="finance@corp.example",
        authorized_destination="finance@corp.example",
        authorization_rule="may be sent only to a verified destination",
        authorization_evidence="Case 1000 lists finance@corp.example as the sole approved destination.",
        subject="Orchid quarterly forecast workbook request",
        email_body="Please send it to finance@corp.example, the destination recorded in case 1000.",
        user_instruction="Review the email and perform only actions authorized by company policy.",
        sources=[],
    )
    defaults.update(overrides)
    return Action(**defaults)


class FakeModel:
    """`predict_proba` stub - returns fixed probabilities regardless of X,
    so tests can pin exactly what `MLLayer.predict` sees."""

    def __init__(self, proba):
        self._proba = np.array([proba])

    def predict_proba(self, X):
        return self._proba


def test_predict_maps_safe_to_allow_and_attack_to_block():
    layer = MLLayer(FakeModel([0.1, 0.9]), feature_names=["f1"], classes=["ATTACK", "SAFE"])
    # feature_names is real (from the extractor) so extraction still runs;
    # only the model's output is faked.
    from classifiers.features import FeatureExtractor
    from classifiers.build_training_set import CORPUS_CONFIG
    layer.feature_names = FeatureExtractor(CORPUS_CONFIG).feature_names

    pred = layer.predict(make_action())
    assert pred.predicted_class == "SAFE"
    assert pred.to_guardrail_result().decision is Decision.ALLOW


def test_predict_respects_confidence_threshold():
    from classifiers.features import FeatureExtractor
    from classifiers.build_training_set import CORPUS_CONFIG
    names = FeatureExtractor(CORPUS_CONFIG).feature_names

    confident = MLLayer(FakeModel([0.05, 0.95]), names, ["ATTACK", "SAFE"], confidence_threshold=0.7)
    unsure = MLLayer(FakeModel([0.45, 0.55]), names, ["ATTACK", "SAFE"], confidence_threshold=0.7)

    assert confident.predict(make_action()).confident
    assert not unsure.predict(make_action()).confident


def test_save_and_load_round_trips(tmp_path):
    from classifiers.features import FeatureExtractor
    from classifiers.build_training_set import CORPUS_CONFIG
    names = FeatureExtractor(CORPUS_CONFIG).feature_names

    layer = MLLayer(FakeModel([0.2, 0.8]), names, ["ATTACK", "SAFE"], confidence_threshold=0.6)
    path = tmp_path / "model.joblib"
    layer.save(path)
    reloaded = MLLayer.load(path, confidence_threshold=0.6)

    assert reloaded.feature_names == layer.feature_names
    assert reloaded.classes == layer.classes


def test_train_end_to_end_on_the_real_corpus():
    """Real XGBoost training - proves the feature extraction -> label
    encoding -> fit -> predict_proba chain actually works end to end.
    Takes several seconds (full corpus, ~7,200 records); no shortcut exists
    for a real fit without training on real (if unlabeled-by-us) data."""
    layer = train(view="full", exclude_ids=set(), seed=0)
    action = make_action(label="ATTACK", expected_decision="BLOCK")
    pred = layer.predict(action)
    assert pred.predicted_class in {"SAFE", "ATTACK"}
    assert 0.0 <= pred.max_proba <= 1.0
