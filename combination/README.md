# 🟪 Combinations — layering the techniques

Each folder here pairs two defences that fail in different ways, and measures
whether the pair beats either alone.

| Experiment | Task | Shape |
|---|---|---|
| [deterministic+ml/](deterministic+ml/) | 24 | Rules **OR** ML — block if either blocks |
| [ml_llm/](ml_llm/) | 25 | ML **→** LLM judge — escalate only what the ML is unsure of |
| [collect_metrics.py](collect_metrics.py) | — | Rebuilds the overall metrics table in the root README |

---

## Task 24 — Deterministic rules + ML (OR fusion)

`deterministic+ml/run_combinations.py` crosses four rule variants (basic,
provenance, CI-norm, all three together) with six ML stages (RF, XGBoost/GB,
Qwen LoRA, and three cached Prompt Guard runs) — 24 pairings, each using
conservative OR fusion: **an action is blocked when either component blocks it**.

```bash
python combination/deterministic+ml/run_combinations.py
python combination/deterministic+ml/run_combinations.py --ml rf xgb --limit 500
```

OR fusion can only ever *raise* recall and *raise* the false-positive rate, so
the interesting column is FPR: pairing a strong rule set with a noisy model
(Prompt Guard 1, FPR 0.997 alone) drags the pair down to 0.751 accuracy even
though recall is a perfect 1.000. Results: `results/determinitic+ml/`.

---

## Task 25 — ML → LLM judge cascade

```
                 every action
                      |
                  ML stage              cheap, runs on everything
                      |
            +---------+---------+
            |                   |
        confident            uncertain        <- routing strategy
            |                   |
       ML decision         LLM judge          expensive, runs on a few
            |                   |
            +---------+---------+
                      |
                 final decision
```

A cascade buys accuracy with a **budget**. The ML stage scores everything, and
only the fraction it is least sure about goes to the judge. Every run therefore
reports the escalation rate next to the metrics — an improvement that costs
100% escalation is just the judge with extra steps.

```bash
python combination/ml_llm/run_cascade.py --judge oracle --sweep       # free: headroom
python combination/ml_llm/run_cascade.py --judge gemini --budget 0.1
python combination/ml_llm/run_cascade.py --ml xgb rf pg2_86m --judge gemini \
    --budgets 0 0.025 0.05 0.1 --rpm 12
```

### The pieces

**ML stages** (`--ml`) — anything that yields a decision *and* a confidence:

| Stage | Source | Confidence |
|---|---|---|
| `rf`, `xgb` | trained on the Task 17 feature matrix | class probabilities |
| `pg1_86m`, `pg2_22m`, `pg2_86m` | cached Prompt Guard scores | distance from the 0.5 threshold |

**Routing** (`--strategy`) — `margin` (1 − gap between the top two classes),
`entropy`, or `confidence` (1 − top probability). Records are ranked by
uncertainty and the top `--budget` share is escalated; a record with zero
uncertainty is never escalated, because spending a call on it buys nothing.

**Judges** (`--judge`):

| Judge | What it is |
|---|---|
| `gemini` | the real judge, reusing the prompt and parser from [`llm_judge/`](../llm_judge/) |
| `oracle` | always right — the ceiling, i.e. the headroom the routing leaves |
| `coin` | uniformly random — the floor any real gain must beat |

Judge verdicts map onto the shared `Decision` enum: `ALLOW` → ALLOW,
`CONFIRM`/`REVISE` → FLAG, `BLOCK` → BLOCK. A malformed or failed reply maps to
FLAG — **a judge that cannot answer must never approve an action**.

### Operational details that matter

- **Held-out only.** Evaluation runs on the corpus test split plus the flow and
  adaptive stress sheets (2,880 records), because RF/XGBoost are trained on the
  train split. Scoring them on their own training data would be meaningless.
- **Responses are cached** in `ml_llm/cache/` by (model, method, record), so a
  budget sweep costs only the largest budget's calls, and re-runs are free and
  reproducible.
- **Transport failures are never cached.** A 429 or a 5xx is not a verdict;
  caching one would freeze a transient outage into every later run. Only real
  parsed replies are stored.
- **The free tier quota is per-minute**, so calls are paced (`--rpm`, default
  12) with backoff measured in tens of seconds rather than the few seconds a
  generic retry would use.

### Results — real Gemini judge

`gemini-3.5-flash-lite`, `full_defense` prompt, 2,880 held-out records
(corpus test split + flow and adaptive stress sheets). Raw runs in
`results/ml+llm/`.

| First stage | Escalated | Accuracy | Attack recall | FPR | Judge accuracy on escalated |
|---|---|---|---|---|---|
| **RF** alone | 0% | 0.913 | 1.000 | 0.379 | — |
| RF → Gemini | 2.5% | 0.936 | 1.000 | 0.280 | 0.903 |
| RF → Gemini | 5% | 0.954 | 1.000 | 0.200 | 0.819 |
| **RF → Gemini** | **10%** | **0.990** | **1.000** | **0.042** | 0.903 |
| **XGBoost** alone | 0% | 0.583 | 0.730 | 0.909 | — |
| XGBoost → Gemini | 5% | 0.627 | 0.730 | 0.718 | 0.875 |
| XGBoost → Gemini | 10% | 0.670 | 0.730 | 0.529 | 0.872 |
| **Prompt Guard 2** alone | 0% | 0.229 | 0.000 | 0.000 | — |
| PG2 → Gemini | 2.5% | 0.254 | 0.032 | 0.000 | 0.236 |

**Random Forest + a 10% escalation budget is the headline: 0.913 → 0.990
accuracy, with the false-positive rate collapsing from 0.379 to 0.042 and
attack recall held at 1.000.** One email in ten goes to the judge; nine in ten
are decided for free.

#### Does the judge actually beat the ML on what it is given?

That is the only question that matters for a cascade, and it is not answered by
the headline accuracy. Measured on exactly the escalated records:

| Stage / budget | Records | ML would have scored | Judge scored | Delta |
|---|---|---|---|---|
| XGBoost @ 5% | 144 | 0.000 | 0.875 | **+0.875** |
| XGBoost @ 10% | 288 | 0.000 | 0.872 | **+0.872** |
| RF @ 5% | 144 | 0.000 | 0.819 | **+0.819** |
| RF @ 10% | 288 | 0.132 | 0.903 | **+0.771** |

The ML stage gets essentially **none** of its most-uncertain records right, and
the judge gets around 87% of them right. Uncertainty routing is selecting
almost perfectly for the ML's own errors.

#### Why it works here — the mechanism, not the number

Every escalated record at 10% comes from a single sheet:

```
xgb @10% escalated 288 -> sheets: {Implement Flow Separation: 288}
                          truth : {ALLOW: 288}
                          ML said: {BLOCK: 288}
```

This is the length shortcut from Task 17. The feature models learned that
benign bodies are longer than attack bodies; the flow-separation sheet inverts
that relation, so they block benign traffic there with low confidence. The
judge reads the authorization evidence instead of message shape, so it does not
share the failure — which is exactly the property a second stage needs.

**The corollary is the limit of the result.** The cascade is not generically
adding ~8 accuracy points; it is repairing one systematic blind spot that
happens to dominate this held-out set. On the corpus test split alone — where
the ML is already at 1.000 — escalating 10% *lowered* accuracy to 0.985,
because the judge got 3 of 20 wrong. **A cascade only pays where the first
stage is actually weak.**

#### Prompt Guard as a first stage does not work

PG2 almost never blocks (attack recall 0.008 on the full corpus), so its
"uncertainty" band near the 0.5 threshold is not where its errors are: judge
accuracy on its escalated records is 0.236, far below the 0.87 seen with
RF/XGBoost. A useful cascade needs a first stage whose confidence is
*calibrated* — one that knows what it does not know. A model that is uniformly
wrong with high confidence cannot route.

#### The judge alone

On the 493 records it has actually seen: **accuracy 0.886, attack recall
1.000** (63 of 63 attacks stopped). It over-blocks — 119 BLOCK verdicts against
63 real attacks, i.e. roughly 13% of benign mail refused. That is the usual
cascade trade: the judge is the more accurate and far more expensive stage, and
routing only the uncertain tail buys most of its benefit at a tenth of the cost.

This is a biased sample (those records are by construction the hard ones), so it
is not a full LLM-only baseline. Judging all 2,880 would cost ~2,400 calls.

#### Routing strategy makes no difference here

`margin`, `entropy` and `confidence` produce **identical** escalation sets at
every budget for both RF and XGBoost — Spearman correlation 1.000 between the
three uncertainty vectors. The third class probability is negligible in these
models, which collapses all three formulas onto the same ranking. Pick
whichever is cheapest; on this data the choice is cosmetic.

#### Headroom (oracle judge, free to run)

| Stage | 0% | 5% | 10% | 20% |
|---|---|---|---|---|
| RF | 0.913 | 0.963 | 1.000 | 1.000 |
| XGBoost | 0.583 | 0.633 | 0.683 | 0.783 |
| PG2 | 0.229 | 0.279 | 0.329 | 0.429 |

Gemini reaches 0.990 of the 1.000 a perfect judge would reach for RF at 10%, so
on this routing there is almost nothing left for a better judge to win. For
XGBoost the real judge (0.670) is slightly *below* the oracle (0.683) — the gap
is the ~13% of escalated records the judge gets wrong.

#### Known gaps

- **Prompt Guard rows are incomplete beyond 2.5%.** The Gemini free tier has a
  per-day quota (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`) and it ran
  out mid-run. The runner now aborts with a clear message instead of grinding
  through retries; rerun after the quota resets to fill those rows. Everything
  already fetched is cached, so the rerun only pays for what is missing.
- **No full LLM-only baseline**, for the same reason.
- **One judge prompt variant.** `llm_judge.judge` defines six (`plain`,
  `delimited`, `sanitized`, `datamarked`, `provenance`, `full_defense`); only
  `full_defense` was run. `--judge-method` switches it, and each variant caches
  separately.

### Setup

The judge needs a Gemini key. Put it in `.env` at the repository root:

```
GEMINI_API_KEY=your-key
```

`common/env.py` loads it automatically, and `.env` is git-ignored. A real
environment variable takes precedence over the file.
