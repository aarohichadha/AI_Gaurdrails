# 🟨 C. Deterministic Guardrails (Tasks 10–13)

Rule-based guardrails for the email-agent security corpus. No model is called
anywhere in this folder — every decision is reproducible and explainable from
the rules alone.

| File | Task | What it does |
|---|---|---|
| [task10_basic_rules.py](task10_basic_rules.py) | 10 | Attribute rules: unauthorized recipient, sensitive+external, unknown sender, credential routing |
| [task11_provenance_rules.py](task11_provenance_rules.py) | 11 | Tracks which channel each instruction arrived on and refuses to let untrusted text act as authority |
| [task12_ci_norm.py](task12_ci_norm.py) | 12 | Contextual Integrity: every action becomes a 4-parameter flow checked against declared norms |
| [task13_compare.py](task13_compare.py) | 13 | Runs all three across every view and reports where they diverge |
| [run_all.py](run_all.py) | — | Runs 10–13 end to end |

## Running

```bash
pip install -r ../requirements.txt

python deterministic/run_all.py              # headline results + comparison
python deterministic/run_all.py --view all   # every task against every view
python deterministic/task13_compare.py       # the comparison on its own

python deterministic/task10_basic_rules.py destination_blind   # one task, one view
```

Results are written to [results/](results/) as CSV and JSON.

## The three variants

**Task 10 — basic rules.** Compares action attributes against policy constants.
`R1` unauthorized recipient, `R2` sensitive data leaving the org, `R3` unknown
sender, `R4` credential material to an unapproved destination. It has no notion
of where an instruction came from; it fires on positive evidence of a bad value.

**Task 11 — provenance rules.** Each span of context is tagged with the channel
it arrived on, and trust is a property of the channel, never the content:

| Channel | Trust |
|---|---|
| `USER` (the principal's instruction) | TRUSTED |
| `CASE_RECORD` (system of record) | TRUSTED |
| `EMAIL` (subject, body) | UNTRUSTED |
| `WEBPAGE`, `TOOL_OUTPUT`, `MEMORY` | UNTRUSTED |

The governing principle is that **untrusted content is data, not instructions**.
An email may *describe* a request, but it can never be the authority that
approves a sensitive flow — that has to trace back to a trusted channel.

**Task 12 — CI-norm.** Every action is represented as a Contextual Integrity
flow — *sender, recipient, data + attribute, transmission principle* — and
checked against norms declared in advance (`N1`–`N4` in the file). The key
difference is the **direction of the default**: Tasks 10 and 11 look for
evidence that something is wrong and allow otherwise, while a CI norm has to be
positively *affirmed*. If the transmission principle cannot be shown to hold,
the flow is not normal and is blocked.

## Evaluation views (why Task 13 is not a three-way tie)

On the corpus as given, `requested_destination` is a **perfect oracle**: every
attack targets an off-allowlist address and every benign action targets its
authorized one. A single allowlist comparison scores 100%, so all three
variants tie at 1.000 and the comparison says nothing.

The views control *what the guardrail is allowed to see*. **No label is ever
modified** — only the features are withheld.

| View | What it models |
|---|---|
| `full` | All curated fields, including `authorized_destination` |
| `text_only` | Raw mail plus a static domain allowlist; no case record |
| `destination_blind` | The exfil channel is not a visible To: field (hidden BCC, alias, link) |
| `blind_no_cue` | As above, and the corroboration phrase is deleted — worst case |
| `stale_allowlist` | The domain allowlist has gone stale and trusts attacker domains |

## Results (7,200 records)

Attack recall — share of attacks stopped:

| Guardrail | full | text_only | destination_blind | blind_no_cue | stale_allowlist |
|---|---|---|---|---|---|
| basic_rules | 1.000 | 1.000 | 0.037 | 0.037 | 0.037 |
| provenance_rules | 1.000 | 1.000 | 0.778 | 0.778 | 1.000 |
| ci_norm | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |

False-positive rate — share of benign work refused:

| Guardrail | full | text_only | destination_blind | blind_no_cue | stale_allowlist |
|---|---|---|---|---|---|
| basic_rules | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| provenance_rules | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| ci_norm | 0.000 | 0.000 | 0.000 | **1.000** | 0.000 |

### Reading the table

1. **All three tie at 1.000 on the raw corpus.** That is a property of the
   data, not evidence the techniques are equivalent.

2. **Basic rules fail open.** Remove the destination oracle or let the
   allowlist go stale and recall falls to 0.037 — a rule that only recognises
   known-bad values cannot act on what it cannot see.

3. **Provenance rules degrade gracefully.** They ask *who issued the
   instruction* rather than *where it points*, so a stale allowlist costs them
   nothing and an invisible destination still leaves 0.778 recall at zero
   false-positive cost. Their residual gap is the `Adaptive Attack Generation`
   sheet, which paraphrases away the lexical markers — the standard weakness of
   any keyword-anchored rule.

4. **CI-norm fails closed.** Default-deny holds recall at 1.000 in every view.
   The cost shows up in `blind_no_cue`: with nothing left to affirm it blocks
   all benign traffic too (FPR 1.000). Safe, but unusable — the failure lands
   on availability rather than security.

5. **They are complementary, not ranked.** Basic rules give cheap precision on
   known-bad values, provenance decides whether untrusted text may act as an
   instruction at all, and CI-norm is the default-deny backstop. A deployed
   guardrail should run all three and take the most restrictive decision.

### Honest caveats

- **The corroboration cue is doing real work.** In the blind views the
  variants lean on the template phrase *"the destination recorded in case N"*,
  which appears in 100% of benign and 0% of attack mail in this corpus. That
  separation is an artifact of how the corpus was generated. `blind_no_cue`
  exists to measure exactly that dependence.
- **Lexical markers were tuned on this corpus** and measured at a 0% hit rate
  on benign mail. `credential` and `multistep` were deliberately excluded from
  the strong set because they also fire on legitimate traffic (33% and 5.6%).
  Marker lists do not transfer to new phrasing — that is the point item 3 makes.
- **Splits are not used.** The corpus carries `train`/`validation`/`test`
  splits; these rules are hand-written rather than fitted, so every number
  above is over all 7,200 records. Anything *learned* from the data should be
  scored on the test split instead.
- **`sender_trust` is nearly constant** (`INTERNAL_VERIFIED` for 7,000 of
  7,200 rows), so the unknown-sender rule is barely exercised — it carries the
  whole 0.037 recall that survives the ablations.
