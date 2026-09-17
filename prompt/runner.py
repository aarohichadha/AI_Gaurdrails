"""Shared CLI runner for the prompt-defense experiments (Tasks 7-9).

Each task's entry point (`task7_baseline.py`, and later a `task8_...py` /
`task9_...py`) is a few lines that call `main()` with its own prompt version
and results filename. Dataset loading, view selection, provider selection,
per-record logging and evaluation are identical across all three tasks, so
this is the one place that wiring lives.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import apply_view, evaluate, format_breakdown, format_metrics_table, load_actions
from common.schema import Action

from prompt.agent import AgentConfig, AgentDecision, EmailAgent
from prompt.provider import LLMProvider, MockProvider, OllamaProvider, OpenAIProvider

RESULTS_DIR = Path(__file__).resolve().parent / "results"

#: A trivial always-ALLOW mock so `--provider mock` can exercise the full
#: pipeline (dataset -> agent -> parsing -> evaluation) without an API key.
#: It is NOT a guardrail and produces meaningless metrics - real experiments
#: must use `--provider openai` or `--provider ollama`.
_MOCK_ALLOW_RESPONSE = (
    '{"decision": "ALLOW", "action": "SEND_EMAIL", "recipient": null, '
    '"reason": "mock provider - not a real decision"}'
)

#: Used only when `--model` is not given, so `--provider ollama` doesn't
#: silently try to call a model named "gpt-4o-mini" against a local server.
DEFAULT_MODELS = {
    "mock": "mock",
    "openai": "gpt-4o-mini",
    "ollama": "llama3.1",
}


def build_provider(name: str) -> LLMProvider:
    if name == "openai":
        return OpenAIProvider()
    if name == "ollama":
        return OllamaProvider()
    if name == "mock":
        return MockProvider(_MOCK_ALLOW_RESPONSE)
    raise ValueError(f"unknown provider {name!r}")


def run_agent_over(
    agent: EmailAgent, actions: List[Action], view: str, log_path: Optional[Path] = None
) -> List[AgentDecision]:
    """Run the agent once per action, optionally logging each call as JSONL.

    Logged fields never include API keys or raw model output containing
    secrets. `expected_decision` (ground truth) is read from `action` and
    written to the log only *after* `agent.run(action)` has already returned
    - the agent itself never sees it (see `agent.FORBIDDEN_FIELDS`); this is
    the same "ground truth used only after the LLM responds" rule
    `common.evaluation.evaluate` already follows.
    """
    decisions: List[AgentDecision] = []
    handle = log_path.open("w", encoding="utf-8") if log_path else None
    try:
        for action in actions:
            decision = agent.run(action)  # <-- LLM call happens here, before any ground truth is touched
            decisions.append(decision)
            if handle:
                handle.write(
                    json.dumps(
                        {
                            "scenario_id": decision.record_id,
                            "view": view,
                            "model": decision.model,
                            "prompt_version": decision.prompt_version,
                            "decision": decision.decision.name,
                            "action": decision.action,
                            "recipient": decision.recipient,
                            "latency_s": round(decision.latency_s, 4),
                            "error": decision.parse_error,
                            "expected_decision": action.expected_decision,
                        }
                    )
                    + "\n"
                )
    finally:
        if handle:
            handle.close()
    return decisions


def main(prompt_version: str, results_name: str, description: str) -> None:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--view", default="full", help="evaluation view (see common.dataset.VIEWS)")
    parser.add_argument("--provider", default="mock", choices=["mock", "openai", "ollama"])
    parser.add_argument(
        "--model", default=None,
        help="model name passed to the provider (default depends on --provider, e.g. llama3.1 for ollama)",
    )
    parser.add_argument("--limit", type=int, default=None, help="cap the number of records processed")
    parser.add_argument("--record-id", default=None, help="run a single scenario by record_id")
    args = parser.parse_args()
    model = args.model or DEFAULT_MODELS[args.provider]

    actions = apply_view(load_actions(), args.view)
    if args.record_id:
        actions = [a for a in actions if a.record_id == args.record_id]
        if not actions:
            raise SystemExit(f"no record with id {args.record_id!r} in view {args.view!r}")
    elif args.limit:
        actions = actions[: args.limit]

    provider = build_provider(args.provider)
    config = AgentConfig(model=model, prompt_version=prompt_version)
    agent = EmailAgent(provider, config)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = RESULTS_DIR / f"{results_name}.jsonl"

    start = time.monotonic()
    decisions = run_agent_over(agent, actions, args.view, log_path)
    elapsed = time.monotonic() - start

    by_record: Dict[str, AgentDecision] = {d.record_id: d for d in decisions}
    guardrail = lambda action: by_record[action.record_id].to_guardrail_result()
    run = evaluate(guardrail, actions, prompt_version, args.view)

    print(f"{description}\n(view: {args.view}, provider: {args.provider}, model: {model})\n")
    print(f"{len(actions)} scenario(s) in {elapsed:.1f}s\n")
    print(format_metrics_table([run.metrics]))

    if len(actions) > 1:
        print()
        print(format_breakdown("Recall by attack category", run.breakdown("attack_category")))

    errors = [d for d in decisions if d.parse_error]
    if errors:
        print(f"\n{len(errors)} scenario(s) had unparseable model output (scored FLAG):")
        for d in errors[:5]:
            print(f"  {d.record_id}: {d.parse_error}")

    print(f"\nwrote {log_path}")
