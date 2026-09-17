"""Tests for `runner.run_agent_over`'s JSONL logging: `view` and the
ground-truth `expected_decision` must appear in the log, but only as a
post-hoc annotation - never inside what the LLM was actually sent."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from prompt.agent import AgentConfig, EmailAgent
from prompt.provider import MockProvider
from prompt.runner import run_agent_over
from prompt.tests.test_agent import json_response, make_action


def test_log_records_view_and_expected_decision(tmp_path):
    action = make_action(record_id="T1-0002", label="ATTACK", expected_decision="BLOCK")
    agent = EmailAgent(MockProvider(json_response("ALLOW")), AgentConfig(prompt_version="v7_baseline"))
    log_path = tmp_path / "run.jsonl"

    run_agent_over(agent, [action], "destination_blind", log_path)

    entry = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert entry["scenario_id"] == "T1-0002"
    assert entry["view"] == "destination_blind"
    assert entry["expected_decision"] == "BLOCK"
    assert entry["decision"] == "ALLOW"  # the LLM's own decision, unaffected by the ground truth


def test_ground_truth_never_reaches_the_prompt_sent_to_the_provider():
    """Spy on exactly what text the provider receives - `expected_decision`
    and `label` must not appear in it, even though the JSONL log (a
    different, later consumer) now records `expected_decision`."""
    captured_prompts = []

    def spy(system_prompt: str, user_prompt: str) -> str:
        captured_prompts.append((system_prompt, user_prompt))
        return json_response("BLOCK")

    # "BLOCK"/"ALLOW" are deliberately excluded from this check: they are
    # legitimate values in the response-format schema the prompt teaches the
    # model ("ALLOW" | "FLAG" | "BLOCK"), so their presence is not a leak.
    # Distinctive ground-truth-only values are what would prove a leak.
    action = make_action(
        record_id="T1-0002",
        label="ATTACK",
        expected_decision="BLOCK",
        attack_category="UNAUTHORIZED_DATA_FLOW",
        attack_technique="hidden_bcc",
        difficulty="HARD",
    )
    agent = EmailAgent(MockProvider(spy), AgentConfig(prompt_version="v7_baseline"))

    run_agent_over(agent, [action], "full", log_path=None)

    assert len(captured_prompts) == 1
    system_prompt, user_prompt = captured_prompts[0]
    for leak in ("ATTACK", "UNAUTHORIZED_DATA_FLOW", "hidden_bcc", "HARD"):
        assert leak not in system_prompt
        assert leak not in user_prompt
