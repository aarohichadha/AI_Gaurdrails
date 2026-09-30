"""Stage 1: generate (and cache) real prompt-agent proposals for the pilot.

One JSONL cache file per (prompt_version, view). The 9-combination matrix in
`run_matrix.py` needs the agent's output 3 times (once per prompt version),
not 9 times - the deterministic side of each combination is free to
recompute, but an LLM call is not, so it is cached and resumed exactly like
`task7_to_llm_judge.py` does for the Gemini judge.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.schema import Action, Decision
from prompt.agent import AgentConfig, AgentDecision, EmailAgent
from prompt.provider import LLMProvider


def _load_cached(path: Path) -> Dict[str, AgentDecision]:
    if not path.exists():
        return {}
    cached: Dict[str, AgentDecision] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            cached[row["record_id"]] = AgentDecision(
                record_id=row["record_id"],
                decision=Decision[row["decision"]],
                action=row["action"],
                recipient=row["recipient"],
                reason=row["reason"],
                model=row["model"],
                prompt_version=row["prompt_version"],
                latency_s=row["latency_s"],
                raw_response="",
                parse_error=row["parse_error"],
            )
    return cached


def generate_traces(
    actions: List[Action],
    prompt_version: str,
    provider: LLMProvider,
    *,
    model: str,
    view: str,
    cache_path: Path,
) -> Dict[str, AgentDecision]:
    """Run `EmailAgent` under `prompt_version` over `actions`, resuming from
    `cache_path` for any record already logged there."""
    cached = _load_cached(cache_path)
    todo = [a for a in actions if a.record_id not in cached]
    if not todo:
        print(f"    [{prompt_version}] all {len(actions)} traces already cached in {cache_path}")
        return cached

    agent = EmailAgent(provider, AgentConfig(model=model, prompt_version=prompt_version))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("a", encoding="utf-8") as handle:
        for i, action in enumerate(todo, start=1):
            decision = agent.run(action)
            cached[action.record_id] = decision
            handle.write(
                json.dumps(
                    {
                        "record_id": decision.record_id,
                        "view": view,
                        "model": decision.model,
                        "prompt_version": decision.prompt_version,
                        "decision": decision.decision.name,
                        "action": decision.action,
                        "recipient": decision.recipient,
                        "reason": decision.reason,
                        "latency_s": round(decision.latency_s, 4),
                        "parse_error": decision.parse_error,
                    }
                )
                + "\n"
            )
            handle.flush()
            if i % 10 == 0 or i == len(todo):
                print(f"    [{prompt_version}] {i}/{len(todo)} new traces this run", flush=True)
    return cached
