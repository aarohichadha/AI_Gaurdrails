# 🟦 B. Prompt-Defense Experiments (Tasks 7–9)

An LLM-backed email agent for the same corpus `deterministic/` scores. One
agent implementation (`agent.py`) is shared by all three experiments; the
*only* thing that is meant to change between them is which system prompt
`prompts.py` supplies.

```
                 SAME EmailAgent
                       |
        +--------------+--------------+
        |              |              |
        v              v              v
    Task 7          Task 8          Task 9
  No Defense      Basic Prompt    Context-Aware
    Prompt          Defense          Defense
```

| File | Task | What it does |
|---|---|---|
| [agent.py](agent.py) | — | `EmailAgent`: builds the scenario prompt, calls the provider, parses/validates the structured reply |
| [provider.py](provider.py) | — | `LLMProvider` abstraction (`OpenAIProvider`, `OllamaProvider`, `MockProvider`) |
| [prompts.py](prompts.py) | 7–9 | The security-prompt variants; this is the only file that differs between tasks |
| [runner.py](runner.py) | — | Shared CLI: dataset loading, view selection, logging, evaluation |
| [task7_baseline.py](task7_baseline.py) | 7 | Baseline — no security-defense prompt |
| `task8_basic_defense.py` | 8 | *(not yet implemented)* |
| `task9_context_aware.py` | 9 | *(not yet implemented)* |

This folder currently implements the **Task 7 foundation**: the agent,
provider abstraction, prompt scaffold and evaluation wiring that Tasks 8 and
9 will reuse unchanged.

## Why this is not a fourth deterministic guardrail

`deterministic/` decides ALLOW/BLOCK from rules over `Action` attributes.
This agent instead asks a model to interpret the scenario and propose a
decision — it contains **no hardcoded security rule** (no "external
recipient → BLOCK", no domain allowlist check). That is deliberate: the
point of Tasks 7–9 is to measure what the *prompt* buys you, holding the
agent, the dataset, and the scoring identical to the deterministic runs. If
the agent computed its own allow/block logic, the comparison would be
between two different guardrails, not between prompts.

## Input/output contract

**In:** one `Action` from `common/dataset.py` (post-view). The agent reads
only fields a real email agent would legitimately observe — sender, subject,
body, user instruction, requested action/destination, data
sensitivity, and whatever authorization context the active view leaves
visible. It never reads `label`, `expected_decision`, `attack_category`,
`attack_technique` or `difficulty` (see `agent.FORBIDDEN_FIELDS`, enforced by
`prompt/tests/test_agent.py`).

**Out:** `AgentDecision` — `decision` (`ALLOW`/`FLAG`/`BLOCK`, using
`common.schema.Decision`), `action`, `recipient`, `reason`, plus `model`,
`prompt_version`, `latency_s` and `parse_error` for logging.
`AgentDecision.to_guardrail_result()` adapts it to `common.schema.GuardrailResult`
so it drops straight into `common.evaluation.evaluate()`.

Malformed model output is never treated as ALLOW: it is scored `FLAG` with
`parse_error` set, so it is both visible in the logs and counted as "not
autonomously allowed" by the scoring harness.

## Running

```bash
pip install -r ../requirements.txt

# wiring check, no API key (always-ALLOW mock provider)
python prompt/task7_baseline.py --record-id T1-0002 --provider mock

# one real scenario, OpenAI
export OPENAI_API_KEY=sk-...
python prompt/task7_baseline.py --record-id T1-0002 --provider openai

# one real scenario, local model via Ollama (no API key; `ollama serve` must be running)
ollama pull llama3.1
python prompt/task7_baseline.py --record-id T1-0002 --provider ollama --model llama3.1

# a slice of the corpus, on an ablation view
python prompt/task7_baseline.py --view destination_blind --limit 50 --provider openai
```

Per-record results are logged to `results/task7_baseline.jsonl`
(`scenario_id`, `model`, `prompt_version`, `decision`, `action`, `latency_s`,
`error` — never the raw API key).

## Tests

```bash
python -m pytest prompt/tests -v
```

All tests run against `MockProvider` (no network, no API key) and cover:
dataset-row → prompt conversion, JSON parsing (including fenced and
malformed output), missing optional fields, benign/attack end-to-end runs,
that ground-truth fields never reach the prompt, and that the agent's
decision tracks the model's output rather than a hardcoded destination rule.

## How Tasks 8 and 9 will reuse this

Both add a new entry to `prompts.PROMPT_VERSIONS` (`v8_basic_defense`,
`v9_context_aware`) and a `task8_...py` / `task9_...py` that calls
`runner.main()` with that version — the same three lines as
`task7_baseline.py`. `agent.py`, `provider.py` and `runner.py` do not change.
