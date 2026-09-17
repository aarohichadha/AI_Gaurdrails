"""Tests for `prompt/prompts.py` - the only file meant to differ between
Tasks 7, 8 and 9. These guard every addition made after Task 7: `v7_baseline`
and `v8_basic_security` must stay byte-identical once a later task's prompt
is added, and each new prompt (`v8_basic_security`, `v9_context_aware`) must
be general (no dataset-specific attack signatures) and must still respect
the same ground-truth isolation the agent enforces for every prompt version.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from prompt.agent import AgentConfig, EmailAgent
from prompt.provider import MockProvider
from prompt.prompts import (
    BASELINE_ROLE_PROMPT,
    BASIC_SECURITY_PROMPT,
    CONTEXT_AWARE_PROMPT,
    PROMPT_VERSIONS,
    get_system_prompt,
)
from prompt.tests.test_agent import json_response, make_action

#: Corpus-specific strings that must never appear in a general-purpose
#: security prompt - if they did, the "defense" would only work on this
#: dataset rather than generalizing to unseen attacks.
DATASET_SPECIFIC_LEAKS = (
    "free-check.example", "temp-transfer.example", "personal-box.example",
    "quick-share.example", "outside-audit.example", "open-review.example",
    "mateo", "hidden_bcc", "hidden-bcc",
    "CREDENTIAL_EXFILTRATION", "UNAUTHORIZED_DATA_FLOW", "ROLE_IMPERSONATION",
    "INDIRECT_INJECTION", "MEMORY_POISONING", "corp.example",
)

#: Frozen content hashes, captured when Task 9 was implemented (v7's hash
#: also matches the last commit exactly). Any future edit to these prompts'
#: *text* - accidental or "just a tweak while working on the next task" -
#: will change the hash and fail loudly here, which is the point: v7/v8 must
#: never drift once later tasks are built on top of them.
_V7_SHA256 = "71903b0447d2c45e4bac5c8576603b567921818e8aa798f89be7b6af9895c2ed"
_V8_SHA256 = "ea0a0540b2f37f87bf85bff3ce7c666c6884859316cdf9b100457e2b366f9623"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 1. v7_baseline remains unchanged
# ---------------------------------------------------------------------------

def test_v7_baseline_is_unchanged():
    assert PROMPT_VERSIONS["v7_baseline"] == BASELINE_ROLE_PROMPT
    assert _sha256(BASELINE_ROLE_PROMPT) == _V7_SHA256
    for term in ("untrusted", "authoriz", "override", "manipulat", "refuse"):
        assert term not in BASELINE_ROLE_PROMPT.lower(), (
            f"v7_baseline must stay defense-free, but now mentions {term!r}"
        )


# ---------------------------------------------------------------------------
# v8_basic_security remains unchanged (Task 8 is complete; Task 9 must not
# touch it)
# ---------------------------------------------------------------------------

def test_v8_basic_security_is_unchanged():
    assert PROMPT_VERSIONS["v8_basic_security"] == BASIC_SECURITY_PROMPT
    assert _sha256(BASIC_SECURITY_PROMPT) == _V8_SHA256


# ---------------------------------------------------------------------------
# 2. v8_basic_security exists
# ---------------------------------------------------------------------------

def test_v8_basic_security_exists():
    assert "v8_basic_security" in PROMPT_VERSIONS
    assert get_system_prompt("v8_basic_security") == BASIC_SECURITY_PROMPT
    assert BASIC_SECURITY_PROMPT != BASELINE_ROLE_PROMPT


# ---------------------------------------------------------------------------
# 3. The Task 8 prompt contains the intended general security instructions,
#    and only general ones - no dataset-specific attack signatures.
# ---------------------------------------------------------------------------

def test_v8_prompt_contains_general_security_instructions():
    prompt = BASIC_SECURITY_PROMPT.lower()
    expected_themes = [
        "untrusted",   # (1) treat email content as potentially untrusted
        "instruction", # (2) distinguish the user's instruction from embedded ones
        "manipulat",   # (3) watch for attempts to manipulate the agent
        "authoriz",    # (4) check consistency with authorization/context
        "sensitive",   # (5) caution around sensitive/confidential information
        "refuse",      # (6) refuse/flag suspicious or unauthorized actions
        "override",    # (7) don't follow instructions that override role/security
        "reason",      # (8) always give a reason
    ]
    for theme in expected_themes:
        assert theme in prompt, f"expected the v8 prompt to mention {theme!r}"


def test_v8_prompt_has_no_dataset_specific_signatures():
    lowered = BASIC_SECURITY_PROMPT.lower()
    for leak in DATASET_SPECIFIC_LEAKS:
        assert leak.lower() not in lowered, f"v8 prompt leaks corpus-specific term {leak!r}"


# ---------------------------------------------------------------------------
# 4. Ground-truth fields never enter the Task 8 prompt
# ---------------------------------------------------------------------------

def test_v8_ground_truth_never_reaches_the_prompt():
    captured = []

    def spy(system_prompt: str, user_prompt: str) -> str:
        captured.append((system_prompt, user_prompt))
        return json_response("BLOCK")

    action = make_action(
        record_id="T1-0002",
        label="ATTACK",
        expected_decision="BLOCK",
        attack_category="UNAUTHORIZED_DATA_FLOW",
        attack_technique="hidden_bcc",
        difficulty="HARD",
    )
    agent = EmailAgent(MockProvider(spy), AgentConfig(prompt_version="v8_basic_security"))
    agent.run(action)

    assert len(captured) == 1
    system_prompt, user_prompt = captured[0]
    for leak in ("ATTACK", "UNAUTHORIZED_DATA_FLOW", "hidden_bcc", "HARD"):
        assert leak not in system_prompt
        assert leak not in user_prompt


# ---------------------------------------------------------------------------
# 5. The output parser still works under v8
# ---------------------------------------------------------------------------

def test_v8_output_parser_still_works():
    agent = EmailAgent(
        MockProvider(json_response("BLOCK", reason="looks suspicious")),
        AgentConfig(prompt_version="v8_basic_security"),
    )
    result = agent.run(make_action())
    assert result.ok
    assert result.decision.name == "BLOCK"


# ---------------------------------------------------------------------------
# 6. The provider receives the correct (v8) prompt version and system prompt
# ---------------------------------------------------------------------------

def test_provider_receives_the_v8_prompt_version_and_system_prompt():
    captured = {}

    def spy(system_prompt: str, user_prompt: str) -> str:
        captured["system_prompt"] = system_prompt
        return json_response("ALLOW")

    agent = EmailAgent(MockProvider(spy), AgentConfig(prompt_version="v8_basic_security"))
    result = agent.run(make_action())

    assert result.prompt_version == "v8_basic_security"
    assert captured["system_prompt"] == BASIC_SECURITY_PROMPT
    assert captured["system_prompt"] != BASELINE_ROLE_PROMPT


# ---------------------------------------------------------------------------
# Task 9 - v9_context_aware
# ---------------------------------------------------------------------------

def test_v9_context_aware_exists():
    assert "v9_context_aware" in PROMPT_VERSIONS
    assert get_system_prompt("v9_context_aware") == CONTEXT_AWARE_PROMPT
    assert CONTEXT_AWARE_PROMPT not in (BASELINE_ROLE_PROMPT, BASIC_SECURITY_PROMPT)


def test_v9_prompt_contains_the_intended_contextual_reasoning_dimensions():
    prompt = CONTEXT_AWARE_PROMPT.lower()
    expected_themes = [
        "user intent",             # (1)
        "untrusted",               # (2) email content treated as untrusted
        "instruction authority",   # (3)
        "requested action",        # (4)
        "sensitivity",             # (5)
        "authorization context",   # (6)
        "destination consistency", # (7)
        "contextual conflicts",    # (8)
        "final decision",          # (9)
        "conservative security",   # (10)
    ]
    for theme in expected_themes:
        assert theme in prompt, f"expected the v9 prompt to mention {theme!r}"


def test_v9_prompt_has_no_dataset_specific_signatures():
    lowered = CONTEXT_AWARE_PROMPT.lower()
    for leak in DATASET_SPECIFIC_LEAKS:
        assert leak.lower() not in lowered, f"v9 prompt leaks corpus-specific term {leak!r}"


def test_v9_ground_truth_never_reaches_the_prompt():
    captured = []

    def spy(system_prompt: str, user_prompt: str) -> str:
        captured.append((system_prompt, user_prompt))
        return json_response("BLOCK")

    action = make_action(
        record_id="T1-0002",
        label="ATTACK",
        expected_decision="BLOCK",
        attack_category="UNAUTHORIZED_DATA_FLOW",
        attack_technique="hidden_bcc",
        difficulty="HARD",
    )
    agent = EmailAgent(MockProvider(spy), AgentConfig(prompt_version="v9_context_aware"))
    agent.run(action)

    assert len(captured) == 1
    system_prompt, user_prompt = captured[0]
    for leak in ("ATTACK", "UNAUTHORIZED_DATA_FLOW", "hidden_bcc", "HARD"):
        assert leak not in system_prompt
        assert leak not in user_prompt


def test_v9_output_parser_still_works():
    agent = EmailAgent(
        MockProvider(json_response("FLAG", reason="unresolved authorization conflict")),
        AgentConfig(prompt_version="v9_context_aware"),
    )
    result = agent.run(make_action())
    assert result.ok
    assert result.decision.name == "FLAG"


def test_provider_receives_the_v9_prompt_version_and_system_prompt():
    captured = {}

    def spy(system_prompt: str, user_prompt: str) -> str:
        captured["system_prompt"] = system_prompt
        return json_response("ALLOW")

    agent = EmailAgent(MockProvider(spy), AgentConfig(prompt_version="v9_context_aware"))
    result = agent.run(make_action())

    assert result.prompt_version == "v9_context_aware"
    assert captured["system_prompt"] == CONTEXT_AWARE_PROMPT
    assert captured["system_prompt"] not in (BASELINE_ROLE_PROMPT, BASIC_SECURITY_PROMPT)
