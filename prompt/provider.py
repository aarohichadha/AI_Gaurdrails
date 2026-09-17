"""LLM provider abstraction.

`EmailAgent` (agent.py) depends only on `LLMProvider`, so the model backend
can be swapped - or replaced with a scripted mock for tests - without
touching the agent, the prompts, or the evaluation wiring.

    EmailAgent
        |
        +-- LLMProvider
              |
              +-- OpenAIProvider   (real model, needs OPENAI_API_KEY)
              +-- OllamaProvider   (local model via a running `ollama serve`)
              +-- MockProvider     (fixed/scripted text, for tests and dry runs)
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Optional, Union


@dataclass
class CompletionResult:
    """One model call, plus the bookkeeping the runner logs."""

    text: str
    model: str
    latency_s: float


class LLMProvider(ABC):
    """A chat-completion backend. Implementations must never log the API key."""

    @abstractmethod
    def complete(
        self, system_prompt: str, user_prompt: str, *, model: str, temperature: float
    ) -> CompletionResult:
        ...


class OpenAIProvider(LLMProvider):
    """Wraps the OpenAI chat-completions API.

    The API key is read from the `OPENAI_API_KEY` environment variable (or
    passed explicitly, e.g. from a secrets manager in a future integration).
    It is never hardcoded and never included in logs or exceptions.
    """

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self._api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Export it before running an "
                "OpenAI-backed experiment, e.g.:\n"
                "  export OPENAI_API_KEY=sk-...            (bash)\n"
                "  $env:OPENAI_API_KEY = 'sk-...'          (PowerShell)"
            )
        self._client = None  # lazy import: openai is only needed for this path

    def _client_or_create(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - environment issue
                raise RuntimeError(
                    "the 'openai' package is required for --provider openai "
                    "(pip install openai)"
                ) from exc
            self._client = OpenAI(api_key=self._api_key)
        return self._client

    def complete(
        self, system_prompt: str, user_prompt: str, *, model: str, temperature: float
    ) -> CompletionResult:
        client = self._client_or_create()
        start = time.monotonic()
        response = client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        latency = time.monotonic() - start
        text = response.choices[0].message.content or ""
        return CompletionResult(text=text, model=model, latency_s=latency)


#: Fixed default so runs are reproducible unless the caller opts out. Verified
#: empirically against a live `ollama serve` (llama3.2:1b): the same seed
#: reproduces the same output even at temperature=1.0, and a different seed
#: changes it - so `options.seed` genuinely controls generation, not just a
#: no-op accepted-and-ignored field.
DEFAULT_OLLAMA_SEED = 0


class OllamaProvider(LLMProvider):
    """Talks to a local Ollama server over its native REST API.

    No API key and no extra dependency - just `urllib` against a running
    `ollama serve` (default `http://localhost:11434`, or `OLLAMA_HOST`).
    Pull the model first, e.g. `ollama pull llama3.1`, and pass its name via
    `--model`.
    """

    def __init__(self, host: Optional[str] = None, seed: Optional[int] = DEFAULT_OLLAMA_SEED):
        self._host = (host or os.environ.get("OLLAMA_HOST") or "http://localhost:11434").rstrip("/")
        self._seed = seed

    def complete(
        self, system_prompt: str, user_prompt: str, *, model: str, temperature: float
    ) -> CompletionResult:
        options = {"temperature": temperature}
        if self._seed is not None:
            options["seed"] = self._seed
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "options": options,
            "stream": False,
        }
        request = urllib.request.Request(
            f"{self._host}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        start = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"could not reach Ollama at {self._host} (is `ollama serve` running? "
                f"is the model pulled?): {exc}"
            ) from exc
        latency = time.monotonic() - start
        text = body.get("message", {}).get("content", "")
        return CompletionResult(text=text, model=model, latency_s=latency)


ScriptedResponse = Union[str, Callable[[str, str], str]]


class MockProvider(LLMProvider):
    """Fixed or scripted responses - no network call, no API key.

    Used by unit tests and by `--provider mock` dry runs that only check the
    wiring. Pass a string to return it for every call, or a callable that
    receives `(system_prompt, user_prompt)` and returns the response text.
    """

    def __init__(self, response: ScriptedResponse):
        self._response = response

    def complete(
        self, system_prompt: str, user_prompt: str, *, model: str, temperature: float
    ) -> CompletionResult:
        start = time.monotonic()
        text = self._response(system_prompt, user_prompt) if callable(self._response) else self._response
        return CompletionResult(text=text, model=model, latency_s=time.monotonic() - start)
