"""Task 7 - baseline agent, no security-defense prompt.

Wires the shared `EmailAgent` (agent.py) to `prompts.BASELINE_ROLE_PROMPT` -
a plain "you are an email assistant" role prompt with none of the
untrusted-content / authorization / anti-injection instructions that Tasks 8
and 9 will add. This measures how the underlying model behaves with no
explicit defense prompt at all.

Usage:
    python prompt/task7_baseline.py --record-id T1-0002 --provider openai
    python prompt/task7_baseline.py --record-id T1-0002 --provider ollama --model llama3.1
    python prompt/task7_baseline.py --view destination_blind --limit 50 --provider openai
    python prompt/task7_baseline.py                       # --provider mock wiring check, no API key
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prompt import runner

NAME = "task7_baseline"


def main() -> None:
    runner.main(
        prompt_version="v7_baseline",
        results_name=NAME,
        description="Task 7 - baseline agent (no security-defense prompt)",
    )


if __name__ == "__main__":
    main()
