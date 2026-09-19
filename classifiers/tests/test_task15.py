"""Tests for the Task 15 Prompt Guard 2 pipeline.

None of these download the gated model: the scorer's windowing/aggregation
is exercised with stubbed tokenizer/model calls, and the CLI runs on the mock
backend.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from classifiers import task15_promptguard2 as task15
from classifiers.promptguard import PromptGuardScorer, find_benign_index, summarise_latency
from common import load_actions


def _action(expected: str, category: str = "NONE"):
    base = load_actions()[0]
    from dataclasses import replace

    return replace(base, expected_decision=expected, attack_category=category)


# -- label handling --------------------------------------------------------

def test_benign_index_prompt_guard_2():
    assert find_benign_index({0: "LABEL_0", 1: "LABEL_1"}) == 0


def test_benign_index_prompt_guard_1_three_classes():
    assert find_benign_index({0: "BENIGN", 1: "INJECTION", 2: "JAILBREAK"}) == 0


def test_benign_index_rejects_unknown_label_map():
    with pytest.raises(ValueError):
        find_benign_index({0: "FOO", 1: "BAR"})


# -- scoring --------------------------------------------------------------

def _stub_scorer(windows_by_text, score_by_window):
    scorer = object.__new__(PromptGuardScorer)
    scorer.batch_size = 2
    scorer._windows = lambda text: windows_by_text[text]
    scorer._score_batch = lambda batch: [score_by_window[w] for w in batch]
    return scorer


def test_long_text_takes_the_most_malicious_window():
    """Padding an attack with benign text must not dilute its score."""
    scorer = _stub_scorer(
        {"long": ["pad-1", "attack", "pad-2"], "short": ["short"]},
        {"pad-1": 0.01, "attack": 0.97, "pad-2": 0.02, "short": 0.10},
    )
    assert scorer.score(["long", "short"]) == [0.97, 0.10]


def test_scores_stay_aligned_across_batch_boundaries():
    scorer = _stub_scorer(
        {t: [t] for t in "abcde"},
        {"a": 0.1, "b": 0.2, "c": 0.3, "d": 0.4, "e": 0.5},
    )
    assert scorer.score(list("abcde")) == [0.1, 0.2, 0.3, 0.4, 0.5]


def test_latency_summary_in_milliseconds():
    stats = summarise_latency([0.010, 0.020, 0.030])
    assert stats.samples == 3
    assert stats.mean_ms == pytest.approx(20.0)
    assert stats.p50_ms == pytest.approx(20.0)
    assert stats.max_ms == pytest.approx(30.0)


# -- what the classifier sees ---------------------------------------------

@pytest.mark.parametrize("mode", sorted(task15.INPUT_MODES))
def test_ground_truth_never_reaches_the_classifier(mode):
    action = load_actions()[1]  # an attack
    text = task15.build_text(action, mode)
    for leaked in (action.label, action.expected_decision, action.attack_category, action.attack_technique):
        assert leaked not in text


def test_untrusted_mode_is_subject_and_body_only():
    action = load_actions()[1]
    text = task15.build_text(action, "untrusted")
    assert action.email_body in text and action.subject in text
    assert action.user_instruction not in text


# -- attack grouping ------------------------------------------------------

def test_every_corpus_category_has_exactly_one_group():
    categories = {a.attack_category for a in load_actions()} - {"NONE"}
    for category in categories:
        homes = [g for g, members in task15.ATTACK_GROUPS.items() if category in members]
        assert len(homes) == 1, f"{category} is in {homes}"


# -- threshold analysis ---------------------------------------------------

def test_threshold_sweep_counts():
    actions = [_action("BLOCK"), _action("BLOCK"), _action("ALLOW"), _action("ALLOW")]
    scores = [0.9, 0.2, 0.6, 0.1]
    row = next(r for r in task15.threshold_sweep(actions, scores, thresholds=(0.5,)))
    assert row["recall"] == 0.5
    assert row["fpr"] == 0.5
    assert row["precision"] == 0.5


def test_recall_at_fpr_respects_the_budget():
    actions = [_action("ALLOW")] * 10 + [_action("BLOCK")] * 4
    benign = [0.95, 0.5, 0.4, 0.3, 0.2, 0.1, 0.1, 0.1, 0.1, 0.1]
    attack = [0.99, 0.9, 0.6, 0.05]
    row = task15.recall_at_fpr(actions, benign + attack, budget=0.1)
    benign_flagged = sum(s >= row["threshold"] for s in benign)
    assert benign_flagged <= 1
    assert row["recall"] == 0.75  # 0.99, 0.9, 0.6 clear the 0.5 cutoff


# -- end to end -----------------------------------------------------------

def test_mock_run_writes_log_and_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(task15, "RESULTS_DIR", tmp_path)
    summary = task15.main(["--backend", "mock", "--limit", "60", "--latency-samples", "5"])

    log = tmp_path / f"{summary['run']}.jsonl"
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 60
    assert {"record_id", "score", "prediction", "attack_group"} <= set(entries[0])
    assert (tmp_path / f"{summary['run']}_summary.json").exists()
    assert summary["latency_batch1"]["samples"] == 5

    # --reuse must reproduce the same metrics from the cached scores
    again = task15.main(["--backend", "mock", "--limit", "60", "--reuse", "--latency-samples", "0"])
    assert again["metrics"] == summary["metrics"]
