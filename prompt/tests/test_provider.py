"""Tests for the LLM provider abstraction. No real network call is made:
`OllamaProvider` is exercised against a fake `urlopen`."""
from __future__ import annotations

import json
import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from prompt.provider import DEFAULT_OLLAMA_SEED, MockProvider, OllamaProvider


def test_mock_provider_returns_fixed_string():
    provider = MockProvider('{"decision": "ALLOW"}')
    result = provider.complete("sys", "user", model="mock", temperature=0.0)
    assert result.text == '{"decision": "ALLOW"}'
    assert result.model == "mock"


def test_mock_provider_supports_a_scripted_callable():
    provider = MockProvider(lambda system, user: f"echo:{user}")
    result = provider.complete("sys", "hello", model="mock", temperature=0.0)
    assert result.text == "echo:hello"


class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return BytesIO(self._body)

    def __exit__(self, *exc_info):
        return False


def test_ollama_provider_posts_chat_payload_and_parses_reply():
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse({"message": {"role": "assistant", "content": '{"decision": "BLOCK"}'}})

    provider = OllamaProvider(host="http://localhost:11434")
    with patch("prompt.provider.urllib.request.urlopen", side_effect=fake_urlopen):
        result = provider.complete("system prompt", "user prompt", model="llama3.1", temperature=0.0)

    assert result.text == '{"decision": "BLOCK"}'
    assert captured["url"] == "http://localhost:11434/api/chat"
    assert captured["body"]["model"] == "llama3.1"
    assert captured["body"]["messages"] == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "user prompt"},
    ]
    assert captured["body"]["stream"] is False
    assert captured["body"]["options"]["temperature"] == 0.0
    assert captured["body"]["options"]["seed"] == DEFAULT_OLLAMA_SEED


def test_ollama_provider_seed_is_configurable():
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse({"message": {"content": "{}"}})

    provider = OllamaProvider(host="http://localhost:11434", seed=1234)
    with patch("prompt.provider.urllib.request.urlopen", side_effect=fake_urlopen):
        provider.complete("sys", "user", model="llama3.1", temperature=0.0)

    assert captured["body"]["options"]["seed"] == 1234


def test_ollama_provider_seed_can_be_disabled():
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse({"message": {"content": "{}"}})

    provider = OllamaProvider(host="http://localhost:11434", seed=None)
    with patch("prompt.provider.urllib.request.urlopen", side_effect=fake_urlopen):
        provider.complete("sys", "user", model="llama3.1", temperature=0.0)

    assert "seed" not in captured["body"]["options"]


def test_ollama_provider_raises_a_clear_error_when_unreachable():
    import urllib.error

    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    provider = OllamaProvider(host="http://localhost:11434")
    with patch("prompt.provider.urllib.request.urlopen", side_effect=fake_urlopen):
        try:
            provider.complete("sys", "user", model="llama3.1", temperature=0.0)
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert "ollama serve" in str(exc).lower()
