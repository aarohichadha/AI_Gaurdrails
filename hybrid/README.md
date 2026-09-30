# 🟩 Hybrid Guardrail (prompt-defense agent x deterministic rules)

Every combination of a Task 7-9 prompt-based agent with a Task 10-12
deterministic rule set, scored on the same 100-pair (200-record) pilot slice
`llm_judge`/`task7_to_llm_judge.py` already use. This is not a fourth
technique competing with the other three - it is what
[deterministic/README.md](../deterministic/README.md) already recommends as
the practical conclusion of Task 13 ("a deployed guardrail should run all
three and take the most restrictive decision"), extended to also fold in the
LLM agent as a fourth vote rather than just the three deterministic ones.

```
        EmailAgent (v7 / v8 / v9)         basic_rules / provenance_rules / ci_norm
                 |                                        |
                 +--------------------+  +----------------+
                                      v  v
                              hybrid.combine.combine()
                              (most restrictive wins)
                                      |
                                      v
                              ALLOW / FLAG / BLOCK
```

| File | What it does |
|---|---|
| [combine.py](combine.py) | `combine(*GuardrailResult)` - merges any number of results, keeping the most restrictive `Decision` and concatenating the audit trail |
| [pilot.py](pilot.py) | The shared 100-pair / 200-record slice (same sheet, split, seed as `llm_judge.evaluation.paired_pilot`) |
| [traces.py](traces.py) | Runs `prompt.agent.EmailAgent` once per prompt version over the pilot, caching each record to `results/traces/*.jsonl` so a crash or a re-run never re-pays for an LLM call already made |
| [run_matrix.py](run_matrix.py) | CLI: builds all 9 `(prompt_version, rule_set)` combinations plus 6 solo baselines, prints and saves the comparison |

## Why only 3 LLM-call budgets, not 9

The prompt-based agent's proposal does not depend on which deterministic
rule set it will later be combined with - only on the prompt version. So the
agent runs exactly 3 times per record (`v7_baseline`, `v8_basic_security`,
`v9_context_aware`), each cached to its own JSONL file; the 3 deterministic
rule sets are then applied to those same records for free (no model call),
and every `(prompt_version, rule_set)` pair is just `combine()` over
already-computed results. 9 combinations therefore cost the same 3 x 200 = 600
LLM calls as running the 3 prompt tasks alone would.

## Running

```bash
pip install -r ../requirements.txt

# wiring check, no network call, no API key
python hybrid/run_matrix.py --view destination_blind --provider mock --limit 6

# the real 100-pair (200-record) pilot, local Ollama
ollama pull llama3.2:1b   # if not already pulled
python hybrid/run_matrix.py --view destination_blind --provider ollama --model llama3.2:1b
```

`--view` defaults to `destination_blind` rather than `full`: on `full`,
`requested_destination` is a perfect oracle and all three deterministic rule
sets tie at 1.000 (see deterministic/README.md), which would make the
9-combination comparison say nothing about the rule sets' actual behavior.
`destination_blind` is where they diverge (`basic_rules` collapses,
`provenance_rules` partially recovers, `ci_norm` holds), and it is the same
view `prompt/README.md` already benchmarks Tasks 7-9 on, so a single view
choice keeps every number in this repo comparable.

Pass `--view all` style sweeps are not built in on purpose - this folder
answers "does combining help", not "which view" (that question is
`deterministic/task13_compare.py`'s). Re-run with a different `--view` to see
the matrix under `full`, `text_only`, `blind_no_cue`, or `stale_allowlist`.

Results:
- `results/traces/<prompt_version>_<view>_<model>.jsonl` - raw per-record
  agent proposals (decision, action, recipient, reason, latency, parse
  errors), one file per prompt version, resumable.
- `results/hybrid_matrix_<view>.csv` - one row per solo baseline (6 rows:
  3 prompt versions alone, 3 rule sets alone) and one row per combination (9
  rows), with the same accuracy/recall/precision/F1/FPR/escalation columns
  `common.evaluation.Metrics` reports everywhere else in this repo.

## Tests

```bash
python -m pytest hybrid/tests -v
```

Covers the combiner (most-restrictive-wins, order-independence, audit-trail
merge, the all-ALLOW case, and the mismatched-record_id guard) and the pilot
slice (200 unique records for 100 pairs, seed-reproducible, and that a view
withholding the destination oracle actually withholds it).

## Results (100-pair pilot, `destination_blind` view, `llama3.2:1b`)

Solo baselines:

| Guardrail | Accuracy | Recall | Precision | FPR |
|---|---|---|---|---|
| v7_baseline (alone) | 0.500 | 0.000 | 0.000 | 0.000 |
| v8_basic_security (alone) | 0.510 | 0.020 | 1.000 | 0.000 |
| v9_context_aware (alone) | 0.650 | 0.870 | 0.604 | 0.570 |
| basic_rules (alone) | 0.500 | 0.000 | 0.000 | 0.000 |
| provenance_rules (alone) | 1.000 | 1.000 | 1.000 | 0.000 |
| ci_norm (alone) | 1.000 | 1.000 | 1.000 | 0.000 |

All 9 combinations (most-restrictive-wins):

| Combination | Accuracy | Recall | Precision | FPR |
|---|---|---|---|---|
| v7+basic_rules | 0.500 | 0.000 | 0.000 | 0.000 |
| v7+provenance_rules | 1.000 | 1.000 | 1.000 | 0.000 |
| v7+ci_norm | 1.000 | 1.000 | 1.000 | 0.000 |
| v8+basic_rules | 0.510 | 0.020 | 1.000 | 0.000 |
| v8+provenance_rules | 1.000 | 1.000 | 1.000 | 0.000 |
| v8+ci_norm | 1.000 | 1.000 | 1.000 | 0.000 |
| v9+basic_rules | 0.650 | 0.870 | 0.604 | 0.570 |
| v9+provenance_rules | 0.715 | 1.000 | 0.637 | 0.570 |
| v9+ci_norm | 0.715 | 1.000 | 0.637 | 0.570 |

**Combining did not help on this view - it only hurt.** Because
most-restrictive-wins can only escalate a decision, never soften it:

- Weak prompt + weak rule stays weak: `v7`/`v8` already miss almost every
  attack alone, and `basic_rules` fires on nothing here either, so pairing
  them changes nothing.
- Weak prompt + strong rule inherits the rule: `provenance_rules` and
  `ci_norm` are already perfect alone (1.000 accuracy, 0 FPR), so `v7`/`v8`
  paired with either just reproduces that same perfect score - the prompt
  contributes nothing, positive or negative.
- **Strong prompt + strong rule is actively worse than the rule alone.**
  `ci_norm` alone is 1.000 accuracy / 0 FPR. Pairing it with `v9_context_aware`
  drags accuracy down to 0.715 and pushes FPR to 0.570 - `v9` alone
  over-blocks 57% of legitimate traffic on this view, and a union-of-blocks
  combiner inherits that cost without buying any extra recall, since the rule
  had already caught every attack by itself.

The best performer in the whole 15-row matrix is a **deterministic rule set
alone** (`provenance_rules` or `ci_norm`), not any hybrid combination -
`combine()`'s only-escalate semantics mean it can never beat the better of
its two inputs on precision/FPR, only on recall, and here the rule side had
already maxed out recall on its own. A hybrid combiner would be worth
revisiting on a view or model where the deterministic side is *not* already
at 1.000 recall by itself (e.g. `basic_rules` on `destination_blind`, or any
rule set on `blind_no_cue`) - there a prompt agent voting BLOCK has actual
recall to add rather than only false positives.

Full data: `results/hybrid_matrix_destination_blind.csv`; raw per-record
agent traces in `results/traces/`.
