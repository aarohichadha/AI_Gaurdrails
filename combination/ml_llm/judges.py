"""Judge backends for the Task 25 ML -> LLM cascade.

Every judge takes one `Action` and returns a `JudgeVerdict`. The real one is
Gemini, reusing the prompt and parser already written in `llm_judge/judge.py`
so the cascade judges exactly what Tasks 19-22 judged.

Two synthetic judges exist to bound the experiment without spending calls:

    oracle   always right - the ceiling a perfect judge could reach, i.e. how
             much headroom the routing strategy leaves on the table
    coin     uniformly random - the floor, so any real gain has to beat noise

Responses are cached on disk by (model, method, record), so a repeated run
costs nothing and produces identical numbers.
"""
from __future__ import annotations

import json
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from common.schema import Action, Decision

CACHE_DIR = Path(__file__).resolve().parent / "cache"

#: Judge vocabulary -> the shared Decision enum. CONFIRM/REVISE both mean
#: "do not run this unsupervised", which is exactly FLAG.
DECISION_MAP = {
    "ALLOW": Decision.ALLOW,
    "CONFIRM": Decision.FLAG,
    "REVISE": Decision.FLAG,
    "BLOCK": Decision.BLOCK,
    # A malformed reply must never be read as approval.
    "INVALID": Decision.FLAG,
}


class QuotaExhausted(RuntimeError):
    """The daily quota is gone - retrying today cannot help."""


@dataclass
class JudgeVerdict:
    decision: Decision
    raw_decision: str
    reason: str = ""
    latency_s: float = 0.0
    cached: bool = False
    error: str = ""


def action_to_row(action: Action) -> Dict[str, str]:
    """The dict shape `llm_judge.judge.prepare_input` expects."""
    return {
        "email_body": action.email_body,
        "subject": action.subject,
        "sender": action.sender,
        "user_instruction": action.user_instruction,
        "authorization_evidence": action.authorization_evidence,
        "authorization_rule": action.authorization_rule,
        "authorized_destination": action.authorized_destination,
        "requested_action": action.requested_action,
        "requested_destination": action.requested_destination,
        "data_asset": action.data_asset,
        "data_sensitivity": action.data_sensitivity,
    }


class OracleJudge:
    """Always returns the ground-truth answer. Upper bound, not a guardrail."""

    name = "oracle"

    def __call__(self, action: Action) -> JudgeVerdict:
        raw = {"ALLOW": "ALLOW", "BLOCK": "BLOCK", "REVISE": "REVISE"}.get(
            action.expected_decision, "BLOCK"
        )
        return JudgeVerdict(DECISION_MAP[raw], raw, "oracle: ground truth")


class CoinJudge:
    """Uniformly random over the judge vocabulary. Lower bound."""

    name = "coin"

    def __init__(self, seed: int = 0, choices=("ALLOW", "CONFIRM", "BLOCK")):
        self._random = random.Random(seed)
        self._choices = choices

    def __call__(self, action: Action) -> JudgeVerdict:
        raw = self._random.choice(self._choices)
        return JudgeVerdict(DECISION_MAP[raw], raw, "coin: random")


class GeminiJudge:
    """The real judge: Gemini, with an on-disk cache and 429 backoff."""

    def __init__(
        self,
        model: str = "gemini-3.5-flash-lite",
        method: str = "full_defense",
        cache_path: Optional[Path] = None,
        max_retries: int = 6,
        include_destination: bool = True,
        requests_per_minute: float = 12.0,
    ):
        from common.env import require

        self.model = model
        self.method = method
        self.name = f"gemini:{model}:{method}"
        self.include_destination = include_destination
        self.max_retries = max_retries
        self._api_key = require("GEMINI_API_KEY", "GOOGLE_API_KEY")
        self._client = None
        self._lock = threading.Lock()
        self.calls = 0
        # The free tier enforces a per-minute quota. Pace calls rather than
        # discovering the limit through 429s, which cost a retry each.
        self._min_interval = 60.0 / requests_per_minute if requests_per_minute else 0.0
        self._next_slot = 0.0

        self.cache_path = cache_path or (
            CACHE_DIR / f"{model.replace('/', '_')}__{method}.jsonl"
        )
        self._cache: Dict[str, dict] = {}
        if self.cache_path.exists():
            with self.cache_path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self._cache[entry["record_id"]] = entry

    # ------------------------------------------------------------------
    def _client_or_create(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def _throttle(self) -> None:
        with self._lock:
            wait = self._next_slot - time.monotonic()
            self._next_slot = max(self._next_slot, time.monotonic()) + self._min_interval
        if wait > 0:
            time.sleep(wait)

    def _remember(self, record_id: str, verdict: JudgeVerdict) -> None:
        entry = {
            "record_id": record_id,
            "decision": verdict.raw_decision,
            "reason": verdict.reason,
            "latency_s": round(verdict.latency_s, 3),
            "model": self.model,
            "method": self.method,
        }
        with self._lock:
            self._cache[record_id] = entry
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with self.cache_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")

    # ------------------------------------------------------------------
    def __call__(self, action: Action) -> JudgeVerdict:
        cached = self._cache.get(action.record_id)
        if cached:
            raw = cached["decision"]
            return JudgeVerdict(
                DECISION_MAP.get(raw, Decision.FLAG), raw, cached.get("reason", ""),
                cached.get("latency_s", 0.0), cached=True,
            )

        from google.genai import types

        from llm_judge.judge import SYSTEM_PROMPT, parse_response, prepare_input

        prompt = prepare_input(
            action_to_row(action), self.method,
            include_destination=self.include_destination,
        )

        last_error = ""
        for attempt in range(self.max_retries):
            self._throttle()
            started = time.perf_counter()
            try:
                response = self._client_or_create().models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        temperature=0,
                        max_output_tokens=2000,
                    ),
                )
                with self._lock:
                    self.calls += 1
                parsed = parse_response(response.text or "")
                verdict = JudgeVerdict(
                    DECISION_MAP.get(parsed["decision"], Decision.FLAG),
                    parsed["decision"],
                    parsed["reason"],
                    time.perf_counter() - started,
                )
                self._remember(action.record_id, verdict)
                return verdict
            except Exception as exc:  # rate limits, transient 5xx
                last_error = f"{type(exc).__name__}: {exc}"
                message = str(exc).lower()
                # A *daily* quota is not a transient condition: every retry
                # burns minutes and cannot succeed until the quota resets.
                # Stop the run instead of grinding through the backoff.
                if "perday" in message.replace("_", "").replace("-", ""):
                    raise QuotaExhausted(
                        "Gemini daily free-tier quota is exhausted for this model. "
                        "Cached verdicts are kept; rerun after the quota resets "
                        "(midnight Pacific) or pass a different --judge-model."
                    ) from exc
                retriable = any(
                    token in message
                    for token in ("429", "resource_exhausted", "503", "unavailable", "500", "timeout")
                )
                if not retriable or attempt == self.max_retries - 1:
                    break
                # A per-minute quota needs a wait measured in tens of seconds,
                # not the few seconds a generic backoff would give.
                time.sleep(min(60.0, 5 * (2 ** attempt)) + random.random())

        # A judge that cannot answer must not silently approve the action.
        # Deliberately NOT cached: a rate limit or a 5xx is a transport
        # failure, and caching it would freeze a transient outage into the
        # results of every later run.
        return JudgeVerdict(Decision.FLAG, "INVALID", last_error[:200], error=last_error)


def build_judge(name: str, **kwargs):
    if name == "oracle":
        return OracleJudge()
    if name == "coin":
        return CoinJudge(seed=kwargs.get("seed", 0))
    if name == "gemini":
        return GeminiJudge(
            model=kwargs.get("model", "gemini-3.5-flash-lite"),
            method=kwargs.get("method", "full_defense"),
            include_destination=kwargs.get("include_destination", True),
            requests_per_minute=kwargs.get("requests_per_minute", 12.0),
        )
    raise KeyError(f"unknown judge {name!r}")
