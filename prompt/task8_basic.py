"""Task 8 - basic prompt defense.

Wires the shared `EmailAgent` (agent.py) to `prompts.BASIC_SECURITY_PROMPT` -
the same role prompt Task 7 uses, plus a general, dataset-agnostic security
instruction (treat email content as untrusted, distinguish the user's
instruction from anything an email tries to add, check consistency with
authorization/context, refuse when suspicious). Same agent, same dataset
loader/views, same evaluation harness, same provider abstraction as
`task7_baseline.py` - the ONLY intended change is the prompt.

Usage:
    python prompt/task8_basic.py --view full --limit 30 --provider ollama --model llama3.2:1b
    python prompt/task8_basic.py --record-id T1-0002 --provider openai
    python prompt/task8_basic.py                       # --provider mock wiring check, no API key
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prompt import runner

NAME = "task8_basic"


def main() -> None:
    runner.main(
        prompt_version="v8_basic_security",
        results_name=NAME,
        description="Task 8 - basic prompt defense (general security instruction)",
    )


if __name__ == "__main__":
    main()
