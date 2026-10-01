# AI Guardrails — email agent security

Guardrail techniques evaluated against a 7,200-record corpus of benign and
attacking instructions aimed at an email agent, plus a second, harder
300-record corpus of raw `.eml`-style messages with no structured fields
(see [Raw-email dataset](#raw-email-dataset-300-rows) below).

## Layout

One folder per **technique**, so variants can be compared on identical data:

```
AI_Gaurdrails/
├── data/                  email_agent_security_dataset.xlsx  (7,200 records
│                          + a 3,000-row Generated Three-Class sheet, opt-in)
├── common/                shared across every technique
│   ├── schema.py          Action, Decision, provenance channels, GuardrailResult
│   ├── policy.py          domains, sensitivity tiers, action classes, markers
│   ├── dataset.py         corpus loader + evaluation views (feature ablations)
│   └── evaluation.py      confusion matrix, breakdowns, formatting
├── deterministic/         🟨 C — rule-based guardrails (Tasks 10–13)   [done]
├── prompt/                🟦 B — prompt-defense agent (Tasks 7–9)      [foundation]
├── classifiers/           🟧 D — ML classifiers (Tasks 14-18)     [14-18 done]
├── llm_judge/             🟩 E — Gemini LLM judge (Tasks 19-22)
├── combination/           🟪 F — layered defences (Tasks 24-25)   [rules+ML, ML→LLM]
└── results/               per-technique metrics land in <technique>/results/
```

A new technique (LLM judge, classifier, hybrid) becomes a sibling folder of
`deterministic/` and reuses `common/` — same `Action` objects, same views, same
metrics, so the numbers stay comparable.

## Setup

```bash
pip install -r requirements.txt
python deterministic/run_all.py
```

## The corpus

`data/email_agent_security_dataset.xlsx` — six scenario sheets of 1,200 records
plus a taxonomy and a validation sheet.

| Sheet | Focus |
|---|---|
| Base Dataset | Balanced benign/attack baseline |
| Paired Benign-Attack | Matched twins differing only in the attack |
| Contextual Manipulation | False authorization context (all attacks) |
| Implement Flow Separation | Two flows in one message, one legitimate |
| Attack Taxonomy | Even coverage of all 10 attack categories |
| Adaptive Attack Generation | Mutations designed to evade named defenses |

Ground truth is `expected_decision` (ALLOW / BLOCK), balanced 1,800 benign to
5,400 attack overall. Ten attack categories run from `DIRECT_INJECTION` to
`CREDENTIAL_EXFILTRATION`.

**One property shapes every evaluation here:** in the corpus as given, the
requested destination is a perfect oracle — every attack targets an
off-allowlist address, every benign action targets its authorized one. A
one-line allowlist check therefore scores 100%. `common/dataset.py` defines
*views* that withhold that oracle, without modifying any label, so the
techniques can be compared on their reasoning instead. See
[deterministic/README.md](deterministic/README.md) for the results and caveats.

## Techniques

| Status | Technique | Tasks |
|---|---|---|
| ✅ | [Deterministic guardrails](deterministic/README.md) | 10–13 |
| 🟦 | [Prompt-defense agent](prompt/README.md) | 7–9 |
| 🟧 | [ML classifiers — Prompt Guard, features, RF/XGBoost, Qwen](classifiers/README.md) | 14–18 |
| 🟩 | [LLM judge — Gemini](llm_judge/README.md) | 19–22 |
| 🟪 | [Combinations — rules+ML, ML→LLM cascade](combination/README.md) | 24–25 |

Prompt Guard 1 and 2 are both Hugging Face-gated models. The repository includes the Task 14 scaffold and the Task 15 baseline, but a valid HF token is required before the real model run can execute.

## Raw-email dataset (300 rows)

`data/email_guardrail_raw_email_3class.csv` — 300 full raw email messages,
100 each of SAFE / REVISE / ATTACK, with only an `id`, the raw message, and
the label. No authorized-destination field, no case record, no sensitivity
tier — a harder, more realistic surface than the structured 7,200-row
corpus, and a test of whether each technique's actual reasoning (not the
destination oracle) carries over.

Every technique that normally decides over those structured fields
(deterministic rules, the prompt-defense agent) was re-implemented against a
documented default policy instead of literally rerun — see the module
docstrings in `classifiers/eval_raw_csv_deterministic.py` and
`classifiers/eval_raw_csv_prompt_agent.py` for exactly what was assumed.

```bash
python classifiers/eval_raw_csv_deterministic.py
python classifiers/eval_raw_csv_models.py
python classifiers/eval_raw_csv_promptguard.py          # needs HF_TOKEN
python classifiers/qwen/qwen_eval_raw_csv.py
python classifiers/eval_raw_csv_gemini.py                # needs GEMINI_API_KEY
python classifiers/eval_raw_csv_prompt_agent.py           # local Ollama, one call/row
python classifiers/eval_raw_csv_combinations.py            # joins everything above
python classifiers/generate_raw_eval_report.py              # results/raw_email_300_evaluation_report.pdf
```

Full write-up, including what was *not* run (Prompt Guard 1 — blocked on an
unaccepted gated-repo license; the structured/4-way/revision-loop judge
variants — not yet built) and why the v9 prompt agent blocks nearly
everything on this file: **[results/raw_email_300_evaluation_report.pdf](results/raw_email_300_evaluation_report.pdf)**.
Headline numbers are in the Overall metrics table below, rows marked
`raw_email_300`.

<!-- OVERALL-METRICS:START -->
## Overall metrics

Generated by `python combination/collect_metrics.py --write` from the result
files each task writes, so the numbers cannot drift from the runs.

**Read the corpus rows with care.** In the corpus as given, the requested
destination is a perfect oracle, so any technique that can see it scores
1.000 - that measures the dataset, not the defence. The rows that mean
something are the ones on withheld-oracle views, the generated corpus, and
the held-out stress sheets.

| Technique | Variant | Data | n | Accuracy | Attack recall | FPR | Note |
|---|---|---|---|---|---|---|---|
| Deterministic (10-13) | `basic_rules [full]` | corpus 7,200 | 7200 | 1.000 | 1.000 | 0.000 | oracle view |
| Deterministic (10-13) | `provenance_rules [full]` | corpus 7,200 | 7200 | 1.000 | 1.000 | 0.000 | oracle view |
| Deterministic (10-13) | `ci_norm [full]` | corpus 7,200 | 7200 | 1.000 | 1.000 | 0.000 | oracle view |
| Deterministic (10-13) | `basic_rules [destination_blind]` | corpus 7,200 | 7200 | 0.278 | 0.037 | 0.000 | oracle withheld |
| Deterministic (10-13) | `provenance_rules [destination_blind]` | corpus 7,200 | 7200 | 0.833 | 0.778 | 0.000 | oracle withheld |
| Deterministic (10-13) | `ci_norm [destination_blind]` | corpus 7,200 | 7200 | 1.000 | 1.000 | 0.000 | oracle withheld |
| Deterministic (10-13) | `basic_rules [stale_allowlist]` | corpus 7,200 | 7200 | 0.278 | 0.037 | 0.000 | oracle withheld |
| Deterministic (10-13) | `provenance_rules [stale_allowlist]` | corpus 7,200 | 7200 | 1.000 | 1.000 | 0.000 | oracle withheld |
| Deterministic (10-13) | `ci_norm [stale_allowlist]` | corpus 7,200 | 7200 | 1.000 | 1.000 | 0.000 | oracle withheld |
| Prompt Guard (14-15) | `Prompt-Guard-86M [untrusted]` | corpus 7,200 | 7200 | 0.616 | 0.820 | 0.997 | zero-shot |
| Prompt Guard (14-15) | `Llama-Prompt-Guard-2-22M [untrusted]` | corpus 7,200 | 7200 | 0.252 | 0.003 | 0.000 | zero-shot |
| Prompt Guard (14-15) | `Llama-Prompt-Guard-2-86M [full]` | corpus 7,200 | 7200 | 0.287 | 0.121 | 0.214 | zero-shot |
| Prompt Guard (14-15) | `Llama-Prompt-Guard-2-86M [untrusted]` | corpus 7,200 | 7200 | 0.256 | 0.008 | 0.000 | zero-shot |
| Feature ML (17) | `random_forest [corpus]` | 7,200 rows | 480 | 1.000 | 1.000 | 0.000 | macro-F1 1.000 |
| Feature ML (17) | `xgboost [corpus]` | 7,200 rows | 480 | 1.000 | 1.000 | 0.000 | macro-F1 1.000 |
| Feature ML (17) | `random_forest [corpus_no_oracle]` | 7,200 rows | 480 | 1.000 | 1.000 | 0.000 | macro-F1 1.000 |
| Feature ML (17) | `xgboost [corpus_no_oracle]` | 7,200 rows | 480 | 1.000 | 1.000 | 0.000 | macro-F1 1.000 |
| Feature ML (17) | `random_forest [generated]` | 3,000 rows | 750 | 0.852 | 0.951 | 0.092 | macro-F1 0.842 |
| Feature ML (17) | `xgboost [generated]` | 3,000 rows | 750 | 0.856 | 0.956 | 0.110 | macro-F1 0.846 |
| Feature ML (17) | `random_forest [generated_no_oracle]` | 3,000 rows | 750 | 0.812 | 0.924 | 0.142 | macro-F1 0.803 |
| Feature ML (17) | `xgboost [generated_no_oracle]` | 3,000 rows | 750 | 0.827 | 0.933 | 0.145 | macro-F1 0.819 |
| Feature ML (17) | `random_forest [training_set_with_revise]` | 10,200 rows | 780 | 0.921 | 0.983 | 0.122 | macro-F1 0.846 |
| Feature ML (17) | `xgboost [training_set_with_revise]` | 10,200 rows | 780 | 0.942 | 0.986 | 0.080 | macro-F1 0.887 |
| Qwen LoRA (18) | `Qwen/Qwen2.5-0.5B-Instruct` | corpus 7,200 | 720 | 1.000 | n/a | n/a | validation split |
| Rules + ML (24) | `all_deterministic__promptguard2_22m` | corpus 7,200 | 7200 | 1.000 | 1.000 | 0.000 | fusion OR_BLOCK |
| Rules + ML (24) | `all_deterministic__promptguard2_86m` | corpus 7,200 | 7200 | 1.000 | 1.000 | 0.000 | fusion OR_BLOCK |
| Rules + ML (24) | `provenance_rules__promptguard1_86m` | corpus 7,200 | 7200 | 0.751 | 1.000 | 0.997 | fusion OR_BLOCK |
| ML + LLM cascade (25) | `xgb -> none @ 0%` | held-out 2,880 | 2880 | 0.583 | 0.730 | 0.909 | escalated 0.0% |
| ML + LLM cascade (25) | `xgb -> gemini @ 5%` | held-out 2,880 | 2880 | 0.627 | 0.730 | 0.718 | escalated 5.0% |
| ML + LLM cascade (25) | `xgb -> gemini @ 10%` | held-out 2,880 | 2880 | 0.670 | 0.730 | 0.529 | escalated 10.0% |
| ML + LLM cascade (25) | `rf -> none @ 0%` | held-out 2,880 | 2880 | 0.913 | 1.000 | 0.379 | escalated 0.0% |
| ML + LLM cascade (25) | `rf -> gemini @ 5%` | held-out 2,880 | 2880 | 0.954 | 1.000 | 0.200 | escalated 5.0% |
| ML + LLM cascade (25) | `rf -> gemini @ 10%` | held-out 2,880 | 2880 | 0.990 | 1.000 | 0.042 | escalated 10.0% |
| ML + LLM cascade (25) | `pg2_86m -> none @ 0%` | held-out 2,880 | 2880 | 0.229 | 0.000 | 0.000 | escalated 0.0% |
| ML + LLM cascade (25) | `xgb -> oracle @ 5%` | held-out 2,880 | 2880 | 0.633 | 0.730 | 0.691 | escalated 5.0% |
| ML + LLM cascade (25) | `xgb -> oracle @ 10%` | held-out 2,880 | 2880 | 0.683 | 0.730 | 0.473 | escalated 10.0% |
| ML + LLM cascade (25) | `rf -> oracle @ 5%` | held-out 2,880 | 2880 | 0.963 | 1.000 | 0.161 | escalated 5.0% |
| ML + LLM cascade (25) | `rf -> oracle @ 10%` | held-out 2,880 | 2880 | 1.000 | 1.000 | 0.000 | escalated 10.0% |
| ML + LLM cascade (25) | `pg2_86m -> oracle @ 5%` | held-out 2,880 | 2880 | 0.279 | 0.065 | 0.000 | escalated 5.0% |
| ML + LLM cascade (25) | `pg2_86m -> oracle @ 10%` | held-out 2,880 | 2880 | 0.329 | 0.130 | 0.000 | escalated 10.0% |
| Deterministic, raw-email (10-12 adapted) | `basic [raw_email_300]` | raw-email 300 | 300 | 0.333 | 0.000 | 0.000 | 3-class acc.; no destination field in this file |
| Deterministic, raw-email (10-12 adapted) | `provenance [raw_email_300]` | raw-email 300 | 300 | 0.960 | 0.880 | 0.000 | 3-class acc.; no destination field in this file |
| Deterministic, raw-email (10-12 adapted) | `ci_norm [raw_email_300]` | raw-email 300 | 300 | 0.667 | 1.000 | 0.500 | 3-class acc.; no destination field in this file |
| Feature ML (17), raw-email | `task17_generated_random_forest [raw_email_300]` | raw-email 300 | 300 | 0.650 | 1.000 | n/a | macro-F1 0.543; generated-corpus model, out-of-distribution |
| Feature ML (17), raw-email | `task17_generated_xgboost [raw_email_300]` | raw-email 300 | 300 | 0.617 | 1.000 | n/a | macro-F1 0.521; generated-corpus model, out-of-distribution |
| Qwen LoRA (18), raw-email | `Qwen2.5-0.5B-Instruct [raw_email_300]` | raw-email 300 | 300 | 0.667 | 1.000 | 0.500 | binary vocab (no REVISE token) |
| Prompt Guard (14-15), raw-email | `Llama-Prompt-Guard-2-86M [raw_email_300]` | raw-email 300 | 300 | 0.867 | 0.600 | 0.000 | zero-shot, untrusted text only; PG1 blocked (HF license) |
| Prompt agent (7-9), raw-email | `v9_context_aware [raw_email_300]` | raw-email 300 | 300 | 0.333 | 1.000 | 1.000 | default policy has no authorization fields -> blocks everything |
| LLM judge (19), raw-email | `gemini-3.5-flash-lite plain [raw_email_300]` | raw-email 300 | 300 | 0.917 | 1.000 | 0.095 | best single technique on this file |
| Combinations (23-28), raw-email | `23: agent v9 OR deterministic [raw_email_300]` | raw-email 300 | 300 | 0.333 | 1.000 | 1.000 |  |
| Combinations (23-28), raw-email | `24: deterministic OR random forest [raw_email_300]` | raw-email 300 | 300 | 0.650 | 1.000 | 0.525 |  |
| Combinations (23-28), raw-email | `24: deterministic OR prompt guard 2 [raw_email_300]` | raw-email 300 | 300 | 1.000 | 1.000 | 0.000 |  |
| Combinations (23-28), raw-email | `24: deterministic OR qwen lora [raw_email_300]` | raw-email 300 | 300 | 0.667 | 1.000 | 0.500 |  |
| Combinations (23-28), raw-email | `25: RF -> Gemini (on disagreement) [raw_email_300]` | raw-email 300 | 300 | 0.937 | 1.000 | 0.095 | escalated 39.0% |
| Combinations (23-28), raw-email | `26: deterministic -> Gemini (on REVISE) [raw_email_300]` | raw-email 300 | 300 | 0.937 | 1.000 | 0.095 | escalated 37.3% |
| Combinations (23-28), raw-email | `27: full cascade (det vote -> RF -> Gemini) [raw_email_300]` | raw-email 300 | 300 | 0.747 | 1.000 | 0.380 |  |
| Combinations (23-28), raw-email | `28: parallel vote (det, RF, Gemini) [raw_email_300]` | raw-email 300 | 300 | 0.937 | 1.000 | 0.095 |  |

Attack recall counts FLAG as a catch: escalating to a human is a safe
outcome, not a miss.
<!-- OVERALL-METRICS:END -->
