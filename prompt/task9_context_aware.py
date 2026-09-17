"""Task 9 - context-aware prompt defense.

Wires the shared `EmailAgent` (agent.py) to `prompts.CONTEXT_AWARE_PROMPT` -
the same role prompt Tasks 7-8 use, but with the security content replaced
by a structured procedure that names contextual dimensions (user intent,
email content trust, instruction authority, requested action, data
sensitivity, authorization context, destination consistency, contextual
conflicts) for the model to reason over before deciding. Same agent, same
dataset loader/views, same evaluation harness, same provider abstraction as
`task7_baseline.py` / `task8_basic.py` - the ONLY intended change is the
prompt.

Usage:
    python prompt/task9_context_aware.py --view destination_blind --limit 30 --provider ollama --model llama3.2:1b
    python prompt/task9_context_aware.py --record-id T1-0002 --provider openai
    python prompt/task9_context_aware.py                       # --provider mock wiring check, no API key
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prompt import runner

NAME = "task9_context_aware"


def main() -> None:
    runner.main(
        prompt_version="v9_context_aware",
        results_name=NAME,
        description="Task 9 - context-aware prompt defense (structured reasoning procedure)",
    )


if __name__ == "__main__":
    main()
