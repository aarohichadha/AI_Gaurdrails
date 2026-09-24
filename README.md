# AI Guardrails — email agent security

Guardrail techniques evaluated against a 7,200-record corpus of benign and
attacking instructions aimed at an email agent.

## Layout

One folder per **technique**, so variants can be compared on identical data:

```
AI_Gaurdrails/
├── data/                  email_agent_security_dataset.xlsx  (7,200 records)
├── common/                shared across every technique
│   ├── schema.py          Action, Decision, provenance channels, GuardrailResult
│   ├── policy.py          domains, sensitivity tiers, action classes, markers
│   ├── dataset.py         corpus loader + evaluation views (feature ablations)
│   └── evaluation.py      confusion matrix, breakdowns, formatting
├── deterministic/         🟨 C — rule-based guardrails (Tasks 10–13)   [done]
├── prompt/                🟦 B — prompt-defense agent (Tasks 7–9)      [foundation]
├── classifiers/           🟧 D — ML classifiers (Tasks 14-16)     [15 run; 16 features done]
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
| 🟧 | [ML classifiers — Prompt Guard 1 / 2, feature engineering](classifiers/README.md) | 14–16 |

Prompt Guard 1 and 2 are both Hugging Face-gated models. The repository includes the Task 14 scaffold and the Task 15 baseline, but a valid HF token is required before the real model run can execute.
