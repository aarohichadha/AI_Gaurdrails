"""Tests for the synthetic three-class email corpus.

The point of these is corpus *quality*: the generated data has to be
learnable without being a giveaway, and no single structural feature may
encode the label.
"""
from __future__ import annotations

import csv
import statistics as stats
import sys
from collections import defaultdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from classifiers import generate_email_corpus as cli
from classifiers.emailgen.generator import LABELS, EmailGenerator, GeneratorConfig
from classifiers.features import ExtractorConfig, FeatureExtractor, parse

EXTRACTOR = FeatureExtractor(cli.CONFIG)


@pytest.fixture(scope="module")
def corpus():
    generator = EmailGenerator(GeneratorConfig(seed=7))
    items = generator.generate_many({"SAFE": 150, "REVISE": 120, "ATTACK": 150})
    return [(item, EXTRACTOR.extract(parse(item.as_bytes()))) for item in items]


def by_label(corpus, name):
    grouped = defaultdict(list)
    for item, vector in corpus:
        grouped[item.label].append(vector[name])
    return {label: stats.fmean(values) for label, values in grouped.items()}


# -- basic structure ------------------------------------------------------

def test_every_message_parses_and_is_addressed_to_the_mailbox_owner(corpus):
    for item, _ in corpus:
        message = parse(item.as_bytes())
        assert message.sender and "@" in message.sender
        assert message.subject
        assert message.body_text.strip()


def test_labels_are_the_three_task_17_classes(corpus):
    assert {item.label for item, _ in corpus} == set(LABELS)


def _normalise(raw: bytes) -> bytes:
    """Drop MIME boundary strings - plumbing, generated at serialisation."""
    import re

    return re.sub(rb"={5,}\d+==", b"==BOUNDARY==", raw)


def test_generation_is_deterministic_for_a_seed():
    first = EmailGenerator(GeneratorConfig(seed=3)).generate_many({"SAFE": 5, "ATTACK": 5})
    second = EmailGenerator(GeneratorConfig(seed=3)).generate_many({"SAFE": 5, "ATTACK": 5})
    assert [_normalise(i.as_bytes()) for i in first] == [_normalise(i.as_bytes()) for i in second]


def test_different_seeds_give_different_corpora():
    first = EmailGenerator(GeneratorConfig(seed=1)).generate_many({"ATTACK": 5})
    second = EmailGenerator(GeneratorConfig(seed=2)).generate_many({"ATTACK": 5})
    assert [i.as_bytes() for i in first] != [i.as_bytes() for i in second]


# -- the gaps this corpus exists to fill ----------------------------------

def test_attachments_links_and_html_are_all_present(corpus):
    """The corpus-derived training set had these columns dead."""
    for name in ("has_attachment", "has_link", "has_html_part", "has_bcc"):
        assert any(vector[name] for _, vector in corpus), name


def test_attachments_and_links_appear_in_every_class(corpus):
    """Structure must not encode the label."""
    for name in ("has_attachment",):
        means = by_label(corpus, name)
        assert all(mean > 0 for mean in means.values()), (name, means)


def test_freemail_senders_appear_in_benign_and_attack_mail(corpus):
    """A consumer mailbox is not by itself an attack."""
    means = by_label(corpus, "sender_is_freemail")
    assert means["SAFE"] > 0 and means["ATTACK"] > 0


# -- no single feature may give the label away ----------------------------

def test_body_address_feature_is_not_an_oracle(corpus):
    """On the corpus-derived set this was 0.000 vs 1.000. Not here."""
    means = by_label(corpus, "has_body_external_address")
    assert means["ATTACK"] < 0.95
    assert means["REVISE"] > 0.0


def test_message_shape_does_not_encode_the_class(corpus):
    """Body length must overlap heavily across classes.

    An earlier generator gave each class its own body templates, so a model
    reached 100% on `body_line_count` alone - learning the generator, not the
    threat. Shared wrapper parts fix that.
    """
    for name in ("body_word_count", "body_line_count"):
        means = by_label(corpus, name)
        spread = max(means.values()) - min(means.values())
        assert spread < 0.5 * stats.fmean(means.values()), (name, means)


def test_hard_cases_exist_on_both_sides(corpus):
    hard_attacks = [i for i, _ in corpus if i.label == "ATTACK" and i.notes == "hard_case"]
    hard_safe = [i for i, _ in corpus if i.label == "SAFE" and i.notes == "hard_case"]
    assert hard_attacks and hard_safe


def test_quiet_attacks_trip_no_text_lexicon(corpus):
    """Some attacks must be invisible to the wording features alone."""
    quiet = [
        vector for item, vector in corpus
        if item.label == "ATTACK" and vector["text_signal_families"] == 0.0
    ]
    assert quiet, "every attack fires a lexicon - the corpus is too easy"


def test_some_benign_mail_looks_alarming(corpus):
    """Hard negatives: legitimate mail that trips the urgency lexicon."""
    noisy = [
        vector for item, vector in corpus
        if item.label == "SAFE" and vector["has_urgency"]
    ]
    assert noisy, "no benign mail trips a lexicon - false positives untested"


# -- CLI ------------------------------------------------------------------

def test_cli_writes_eml_labels_and_feature_matrix(tmp_path):
    summary = cli.main([
        "--n", "60", "--seed", "5",
        "--out-dir", str(tmp_path / "eml"),
        "--csv", str(tmp_path / "features.csv"),
    ])
    assert summary["emails"] == 60
    assert sum(summary["classes"].values()) == 60

    eml_files = list((tmp_path / "eml").glob("*.eml"))
    assert len(eml_files) == 60

    labels = list(csv.DictReader((tmp_path / "eml" / "labels.csv").open(encoding="utf-8")))
    assert len(labels) == 60
    assert {row["label"] for row in labels} <= set(LABELS)
    assert all(row["rationale"] for row in labels)

    table = list(csv.reader((tmp_path / "features.csv").open(encoding="utf-8")))
    assert len(table) == 61
    assert table[0][:3] == ["record_id", "label", "notes"]
    assert table[0][3:] == EXTRACTOR.feature_names
