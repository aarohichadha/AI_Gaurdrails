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
| [task7_baseline.py](task7_baseline.py) | 7 | Baseline — no security-defense prompt (`v7_baseline`) |
| [task8_basic.py](task8_basic.py) | 8 | Basic prompt defense — general, dataset-agnostic security instruction (`v8_basic_security`) |
| [task9_context_aware.py](task9_context_aware.py) | 9 | Context-aware defense — structured 10-dimension reasoning procedure (`v9_context_aware`) |

All three tasks (7, 8, 9) are implemented: same `EmailAgent`, same dataset
loader/views, same evaluation harness, same provider abstraction, same
response schema. The only file that differs between them is `prompts.py`.

**Evaluated so far on a 30-record pilot only** (`T1-0001`–`T1-0030`, the
first 30 rows of the "Base Dataset" sheet) — not the full 7,200-record
corpus `deterministic/` uses. This was a deliberate scope choice for fast
local iteration with a small Ollama model (`llama3.2:1b` takes ~9-13s/record
on CPU; the full corpus would take roughly a day per prompt version). Treat
the results below as a pilot signal, not a benchmark.

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

## Prompt design per task

| Task | Prompt version | Security content |
|---|---|---|
| 7 | `v7_baseline` | None — plain "you are an email assistant" role prompt only |
| 8 | `v8_basic_security` | 8 general principles (treat email as untrusted, distinguish user instruction from embedded ones, watch for manipulation, check authorization consistency, caution with sensitive data, refuse when suspicious, reject role/instruction overrides, always give a reason) |
| 9 | `v9_context_aware` | A structured 10-dimension procedure the model works through before deciding: user intent, email content trust, instruction authority, requested action, data/sensitivity, authorization context, destination consistency, contextual conflicts, final decision, conservative-default principle |

Neither `v8_basic_security` nor `v9_context_aware` names any corpus-specific
domain, attack category, or example — verified by
`prompt/tests/test_prompts.py::test_v{8,9}_prompt_has_no_dataset_specific_signatures`.
The security *decision* comes entirely from the model's own reasoning; no
prompt version, and no other file in this folder, contains a hardcoded
allow/block rule.

## Results (pilot: 30 records, `destination_blind` view, `llama3.2:1b`, temperature 0, seed 0)

| Metric | Task 7 | Task 8 | Task 9 |
|---|---|---|---|
| Accuracy | 0.500 | 0.500 | 0.767 |
| Attack recall | 0.000 | 0.000 | 0.933 |
| Precision | 0.000 | 0.000 | 0.700 |
| F1 | 0.000 | 0.000 | 0.800 |
| False positive rate | 0.000 | 0.000 | 0.400 |
| Attacks blocked | 0/15 | 0/15 | 14/15 |
| Benign blocked | 0/15 | 0/15 | 6/15 |
| Avg latency | 9.1s | 11.4s | 13.1s |

Tasks 7 and 8 allowed every one of the 30 records, including all 15 attacks
- a general security *principle* prompt did not change a single decision
from the undefended baseline on this small model/sample. Task 9's
structured procedure did change behavior substantially: it caught nearly
all attacks (14/15) but at the cost of blocking 6 of 15 legitimate requests
(FPR 0.400) - a large recall gain traded for a real usability cost, not an
unambiguous improvement. See the Task 9 experiment notes in the repository
history for the per-category breakdown and which specific records flipped.

**This is one pilot run on one small model** - it says nothing about
`gpt-4o-mini`, a larger Ollama model, or the full corpus, and reason strings
are not currently persisted in the JSONL log, so *why* Task 9 blocked those
6 benign records isn't captured without an additional run.

## Running

```bash
pip install -r ../requirements.txt

# wiring check, no API key (always-ALLOW mock provider)
python prompt/task7_baseline.py --record-id T1-0002 --provider mock

# one real scenario, OpenAI
export OPENAI_API_KEY=sk-...
python prompt/task7_baseline.py --record-id T1-0002 --provider openai

# one real scenario, local model via Ollama (no API key; `ollama serve` must be running)
ollama pull llama3.2:1b
python prompt/task7_baseline.py --record-id T1-0002 --provider ollama --model llama3.2:1b

# the same 30-record pilot used for the results above, any of the three tasks
python prompt/task7_baseline.py --view destination_blind --limit 30 --provider ollama --model llama3.2:1b
python prompt/task8_basic.py --view destination_blind --limit 30 --provider ollama --model llama3.2:1b
python prompt/task9_context_aware.py --view destination_blind --limit 30 --provider ollama --model llama3.2:1b
```

Per-record results are logged to `results/task{7,8,9}_*.jsonl`
(`scenario_id`, `view`, `model`, `prompt_version`, `decision`, `action`,
`recipient`, `latency_s`, `error`, `expected_decision` — the last one is
written only *after* the model has already responded, purely for scoring;
API keys are never logged).

## Tests

```bash
python -m pytest prompt/tests -v    # 39 tests
```

All tests run against `MockProvider` (no network, no API key) and cover:
dataset-row → prompt conversion, JSON parsing (including fenced and
malformed output), missing optional fields, benign/attack end-to-end runs,
that ground-truth fields never reach the prompt (for every prompt version),
that the agent's decision tracks the model's output rather than a hardcoded
destination rule, and - for `v7_baseline`/`v8_basic_security` - a SHA-256
hash check that fails loudly if a later task's implementation ever edits an
earlier task's prompt text.

## How Tasks 8 and 9 reuse the Task 7 foundation

Both added one entry to `prompts.PROMPT_VERSIONS` (`v8_basic_security`,
`v9_context_aware`) and a `task8_basic.py` / `task9_context_aware.py` that
calls `runner.main()` with that version — the same three lines as
`task7_baseline.py`. `agent.py`, `provider.py`, and `runner.py` were not
changed for either task.
