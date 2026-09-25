"""Tests for the corpus -> .eml -> features training-set builder."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from classifiers import build_training_set as builder
from classifiers.features import FeatureExtractor, parse
from common import load_actions


def _action(record_id: str):
    return next(a for a in load_actions() if a.record_id == record_id)


def test_rendered_email_keeps_sender_subject_and_body():
    action = _action("T1-0002")
    message = builder.action_to_email(action)
    assert message.sender == action.sender
    assert message.subject == action.subject
    assert action.email_body.strip()[:40] in message.body_text


def test_requested_destination_is_not_added_as_a_header_recipient():
    """Putting it in To: would invent a header and hand over the label."""
    action = _action("T1-0002")
    message = builder.action_to_email(action)
    assert action.requested_destination not in message.recipients
    assert message.recipients == [action.mailbox_owner]


def test_no_ground_truth_reaches_the_rendered_message():
    action = _action("T1-0002")
    message = builder.action_to_email(action)
    blob = (message.text + " ".join(message.recipients) + message.sender).lower()
    for leaked in (action.label, action.expected_decision, action.attack_category, action.attack_technique):
        assert leaked.lower() not in blob


def test_written_eml_round_trips_through_the_parser(tmp_path):
    action = _action("T1-0002")
    extractor = FeatureExtractor(builder.CORPUS_CONFIG)
    rows = builder.build_rows([action], extractor, tmp_path)

    written = tmp_path / f"{action.record_id}.eml"
    assert written.exists()

    reparsed = parse(written.read_bytes())
    assert reparsed.sender == action.sender
    assert reparsed.subject == action.subject
    # The features from the in-memory render and the round-tripped file agree.
    assert extractor.extract(reparsed).to_dict() == rows[0][2].to_dict()


def test_labels_map_to_the_task_17_classes():
    assert builder.LABEL_MAP["BENIGN"] == "SAFE"
    assert builder.LABEL_MAP["ATTACK"] == "ATTACK"
    # REVISE rows exist only in the exported synthetic sheet, which is read
    # solely when --include-generated is passed.
    assert builder.LABEL_MAP["REVISE"] == "REVISE"


def test_generated_rows_are_excluded_unless_asked_for(tmp_path):
    summary = builder.main(["--limit", "40", "--csv", str(tmp_path / "t.csv")])
    assert "REVISE" not in summary["classes"]


def test_csv_has_one_row_per_record_and_stable_columns(tmp_path):
    out = tmp_path / "train.csv"
    summary = builder.main(["--limit", "40", "--csv", str(out)])

    with out.open(encoding="utf-8") as handle:
        table = list(csv.reader(handle))
    header, body = table[0], table[1:]

    assert len(body) == 40 == summary["rows"]
    assert header[:5] == ["record_id", "sheet", "split", "label", "attack_category"]
    assert header[5:] == FeatureExtractor(builder.CORPUS_CONFIG).feature_names
    assert all(len(row) == len(header) for row in body)
    assert {row[3] for row in body} <= {"SAFE", "ATTACK"}


def test_drop_oracle_features_removes_the_leaking_columns(tmp_path):
    out = tmp_path / "train.csv"
    summary = builder.main(["--limit", "20", "--csv", str(out), "--drop-oracle-features"])

    header = next(csv.reader(out.open(encoding="utf-8")))
    for name in builder.ORACLE_FEATURES:
        assert name not in header
    assert set(summary["dropped_oracle_features"]) == set(builder.ORACLE_FEATURES)


def test_coverage_report_flags_dead_columns(tmp_path):
    """The corpus has no attachments or links, so those must be reported dead."""
    summary = builder.main(["--limit", "60", "--csv", str(tmp_path / "t.csv")])
    assert "has_attachment" in summary["constant_features"]
    assert "has_link" in summary["constant_features"]
    # ... while the text features do vary.
    assert "text_signal_families" in summary["varying_features"]
