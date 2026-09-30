"""Task 27 - full cascade: Deterministic -> LLM Judge -> ML.

    Layer 1: confidence_layer.score()     unanimous (3/3 rule sets agree) -> terminal
                    | uncertain (2-1 split)
                    v
    Layer 2: batch_judge.BatchGeminiJudge ALLOW/BLOCK -> terminal
                    | uncertain (CONFIRM/REVISE/UNCERTAIN/INVALID)
                    v
    Layer 3: ml_layer.MLLayer             terminal - the cascade's last resort,
                                           whatever it predicts is final

ML is deliberately the *last* layer, not the middle one - see
`cascade/README.md` for why (an earlier ML-in-the-middle ordering never
actually reached Gemini).

Layer 2 judges records in *batches* (one Gemini call scores many records at
once via `cascade.batch_judge.BatchGeminiJudge`, a confidence-aware judge
that can answer `UNCERTAIN` instead of guessing) rather than one call per
record. This key's real constraint turned out to be ~20 requests/*day*/model
(see cascade/README.md), not tokens or per-minute rate, so a single request
covering `--batch-size` (default 300) records costs the same "1" as a call
covering one - the only way the full 7,200-record corpus's Layer 2 workload
fits inside a daily quota that small at all.

Usage:
    # wiring check - no Gemini calls, no ML training
    python cascade/run_cascade.py --view destination_blind --limit 8 --skip-llm

    # the 100-pair pilot (default --source pilot) - now ~1 Gemini call, not ~100
    GEMINI_API_KEY=... python cascade/run_cascade.py --view destination_blind

    # the full 7,200-record corpus - ~18 batched calls, not ~5,200; resumable
    # (Ctrl-C, a daily-quota stop, or a crash all resume from
    # results/llm_cache_<view>_full_<judge_model>.jsonl on the next run)
    GEMINI_API_KEY=... python cascade/run_cascade.py --view destination_blind --source full
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Set

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import allowlist_for, apply_view, evaluate, load_actions
from common.schema import Action, GuardrailResult

from cascade.confidence_layer import make_scorer
from cascade.llm_layer import LLMJudgment, action_to_judge_row
from cascade.ml_layer import MODELS_DIR, MLLayer, train as train_ml_layer
from hybrid.pilot import pilot_actions

RESULTS_DIR = Path(__file__).resolve().parent / "results"

#: Layer 2 batches many records into one Gemini call (cascade/batch_judge.py)
#: because this key's real constraint is ~20 requests/*day*/model, not a
#: per-minute rate - checked empirically, see cascade/README.md. A small gap
#: between batches is still kept as basic courtesy/anti-burst margin, not
#: because it's load-bearing the way per-minute pacing would be.
DEFAULT_SECONDS_BETWEEN_BATCHES = 3.0
DEFAULT_BATCH_SIZE = 300
MAX_RETRIES = 4
_RETRY_DELAY_RE = re.compile(r"retryDelay['\"]?:\s*['\"]?(\d+(?:\.\d+)?)s")


class DailyQuotaExhausted(Exception):
    """Raised when the API itself reports a *per-day* quota violation -
    retrying within this run cannot help; only waiting for the daily reset
    can. Distinguished from ordinary transient errors (429/500/503/504)."""


def _is_daily_quota_error(exc: Exception) -> bool:
    """`exc.details["error"]["details"]` is a list of typed entries; the
    quota one carries its own nested "violations" list, each with a
    `quotaId` - e.g. `{"@type": ".../QuotaFailure", "violations":
    [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier", ...}]}`.
    `quotaId` is never a top-level key on the outer entries themselves."""
    details = getattr(exc, "details", {}) or {}
    entries = details.get("error", {}).get("details", []) if isinstance(details, dict) else []
    violations = [v for entry in entries for v in entry.get("violations", [])]
    return any("PerDay" in str(v.get("quotaId", "")) for v in violations)


def _batch_with_backoff(judge, rows: List[dict], method: str) -> List[dict]:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return judge.judge_batch(rows, method)
        except Exception as exc:  # google.genai raises its own ClientError/ServerError
            if _is_daily_quota_error(exc):
                raise DailyQuotaExhausted(str(exc)[:300]) from exc
            code = getattr(exc, "code", None)
            if attempt == MAX_RETRIES or code not in (429, 500, 503, 504):
                # Treat an unrecoverable API error as "uncertain" for every
                # record in the batch rather than inventing a decision - it
                # falls through to Layer 3 (ML), same as CONFIRM/REVISE/UNCERTAIN.
                return [{"decision": "INVALID", "reason": f"api error: {str(exc)[:300]}", "status": "INVALID"}
                        for _ in rows]
            match = _RETRY_DELAY_RE.search(str(getattr(exc, "details", "") or exc))
            delay = float(match.group(1)) + 1.0 if match else min(60.0, 5.0 * 2 ** (attempt - 1))
            print(f"    retry {attempt}/{MAX_RETRIES} after {delay:.0f}s ({code or 'error'})", flush=True)
            time.sleep(delay)
    raise RuntimeError("unreachable")


def _load_llm_cache(path: Path) -> Dict[str, LLMJudgment]:
    if not path.exists():
        return {}
    cache: Dict[str, LLMJudgment] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            cache[row["record_id"]] = LLMJudgment(row["record_id"], row["raw_decision"], row["reason"], row["confident"])
    return cache


def run_llm_layer(
    stage2_pool: List[Action], view: str, source: str, *, judge_model: str, judge_method: str,
    api_key: str, batch_size: int = DEFAULT_BATCH_SIZE,
    seconds_between_batches: float = DEFAULT_SECONDS_BETWEEN_BATCHES,
) -> Dict[str, LLMJudgment]:
    """Judge every record in `stage2_pool`, `batch_size` at a time in one
    Gemini call each, resuming from a cache file so an interrupted run never
    re-pays for a batch already judged. See cascade/batch_judge.py for why
    batching exists at all - this key's real constraint is total requests
    *per day*, not tokens or per-minute rate, so one call covering 300
    records costs the same "1" as a call covering 1."""
    from cascade.batch_judge import BatchGeminiJudge

    # Model-scoped path: a cache with no model in its name would silently
    # mix judgments from different --judge-model runs (or resurrect stale
    # INVALID entries from a quota-exhausted run against a *different*
    # model) as if they were one consistent judge. Happened once already -
    # see cascade/README.md.
    cache_path = RESULTS_DIR / f"llm_cache_{view}_{source}_{judge_model.replace('.', '-')}.jsonl"
    cache = _load_llm_cache(cache_path)
    todo = [a for a in stage2_pool if a.record_id not in cache]
    already_done = len(stage2_pool) - len(todo)
    if already_done:
        print(f"  resuming: {already_done}/{len(stage2_pool)} already judged in {cache_path}")
    if not todo:
        return {a.record_id: cache[a.record_id] for a in stage2_pool}

    judge = BatchGeminiJudge(api_key, model=judge_model)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    chunks = [todo[i:i + batch_size] for i in range(0, len(todo), batch_size)]
    print(f"  {len(todo)} record(s) to judge in {len(chunks)} batch(es) of up to {batch_size}")
    last_call = 0.0
    with cache_path.open("a", encoding="utf-8") as handle:
        for b, chunk in enumerate(chunks, start=1):
            wait = seconds_between_batches - (time.monotonic() - last_call)
            if wait > 0:
                time.sleep(wait)
            rows = [action_to_judge_row(a) for a in chunk]
            try:
                results = _batch_with_backoff(judge, rows, judge_method)
            except DailyQuotaExhausted as exc:
                print(f"  DAILY QUOTA EXHAUSTED after {b - 1}/{len(chunks)} batch(es) "
                      f"({already_done + (b - 1) * batch_size} record(s) judged this run's worth): {exc}")
                print(f"  resume tomorrow with the same command - {cache_path} already has this progress saved")
                break
            last_call = time.monotonic()
            for a, result in zip(chunk, results):
                judgment = LLMJudgment(a.record_id, result["decision"], result["reason"],
                                        result["decision"] in {"ALLOW", "BLOCK"})
                cache[a.record_id] = judgment
                handle.write(json.dumps({
                    "record_id": judgment.record_id,
                    "raw_decision": judgment.raw_decision,
                    "reason": judgment.reason,
                    "confident": judgment.confident,
                }) + "\n")
            handle.flush()
            print(f"  batch {b}/{len(chunks)}: {len(chunk)} records judged", flush=True)
    return {a.record_id: cache[a.record_id] for a in stage2_pool if a.record_id in cache}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--view", default="destination_blind", help="evaluation view (see common.dataset.VIEWS)")
    parser.add_argument("--source", choices=["pilot", "full"], default="pilot",
                         help="pilot = the 100-pair paired benign/attack slice (matches hybrid/); "
                              "full = every record in the corpus under --view")
    parser.add_argument("--n-pairs", type=int, default=100, help="--source pilot only")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--limit", type=int, default=None, help="cap records processed (wiring checks)")
    parser.add_argument("--skip-llm", action="store_true",
                         help="don't call Gemini; send everything Layer 1 escalates straight to ML instead")
    parser.add_argument("--judge-model", default="gemini-2.5-flash")
    parser.add_argument("--judge-method", default="plain", help="see llm_judge.judge.METHODS")
    parser.add_argument("--api-key", default=None, help="defaults to $GEMINI_API_KEY")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                         help="records judged per Gemini call - see cascade/batch_judge.py; this key's real "
                              "constraint is total requests/day, not tokens, so bigger batches (up to the "
                              "model's output-token budget) mean far fewer requests")
    parser.add_argument("--seconds-between-batches", type=float, default=DEFAULT_SECONDS_BETWEEN_BATCHES)
    args = parser.parse_args()

    if args.source == "pilot":
        actions = pilot_actions(view=args.view, n_pairs=args.n_pairs, seed=args.seed)
    else:
        actions = apply_view(load_actions(), args.view)
    if args.limit:
        actions = actions[: args.limit]
    eval_ids = {a.record_id for a in actions}
    allowlist = allowlist_for(args.view)
    print(f"cascade: {len(actions)} records, view={args.view!r}, source={args.source!r}\n")

    # -- Layer 1: confidence-scored deterministic vote -------------------
    scorer = make_scorer(allowlist)
    layer1 = {a.record_id: scorer(a) for a in actions}
    stage2_pool = [a for a in actions if not layer1[a.record_id].confident]
    print(f"[Layer 1] {len(actions) - len(stage2_pool)}/{len(actions)} resolved (unanimous); "
          f"{len(stage2_pool)} escalated to Layer 2")

    # -- Layer 2: Gemini judge (resumable - see run_llm_layer) -----------
    llm_judgments: Dict[str, LLMJudgment] = {}
    stage3_pool: List[Action] = []
    if stage2_pool and not args.skip_llm:
        api_key = args.api_key or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise SystemExit("GEMINI_API_KEY is not set; export it, pass --api-key, or use --skip-llm")
        print(f"\n[Layer 2] judging {len(stage2_pool)} record(s) with Gemini ({args.judge_model}, "
              f"method={args.judge_method})...")
        llm_judgments = run_llm_layer(stage2_pool, args.view, args.source, judge_model=args.judge_model,
                                       judge_method=args.judge_method, api_key=api_key,
                                       batch_size=args.batch_size,
                                       seconds_between_batches=args.seconds_between_batches)
        # .get(...) rather than [...]: a record can be missing entirely if
        # the daily quota ran out mid-run (run_llm_layer stops early rather
        # than fail every remaining batch) - treat "never judged" the same
        # as "judged but uncertain": escalate to Layer 3.
        stage3_pool = [a for a in stage2_pool
                       if not (llm_judgments.get(a.record_id) and llm_judgments[a.record_id].confident)]
        print(f"[Layer 2] {len(stage2_pool) - len(stage3_pool)}/{len(stage2_pool)} resolved (ALLOW/BLOCK); "
              f"{len(stage3_pool)} escalated to Layer 3")
    elif stage2_pool:
        print(f"\n[Layer 2] skipped (--skip-llm): {len(stage2_pool)} record(s) go straight to Layer 3")
        stage3_pool = list(stage2_pool)

    # -- Layer 3: XGBoost, the cascade's last resort ----------------------
    ml_predictions = {}
    if stage3_pool:
        exclude_ids = _training_exclusions(args.source, eval_ids)
        exclusion_note = "this run's own evaluation set" if args.source == "pilot" else "everything outside the train split"
        print(f"\n[Layer 3] training XGBoost (view={args.view!r}, {len(exclude_ids)} record(s) excluded "
              f"from training: {exclusion_note})...")
        start = time.monotonic()
        ml_layer = train_ml_layer(view=args.view, exclude_ids=exclude_ids, seed=args.seed)
        ml_layer.save(MODELS_DIR / f"xgb_{args.view}.joblib")
        print(f"  trained in {time.monotonic() - start:.1f}s, saved to {MODELS_DIR / f'xgb_{args.view}.joblib'}")
        for a in stage3_pool:
            ml_predictions[a.record_id] = ml_layer.predict(a)
        low_conf = sum(1 for p in ml_predictions.values() if not p.confident)
        print(f"[Layer 3] resolved all {len(stage3_pool)} (terminal - nowhere further to escalate); "
              f"{low_conf} were below the ML layer's own 0.7 self-confidence but used anyway")

    # -- Assemble final per-record result + audit trail -------------------
    final: Dict[str, GuardrailResult] = {}
    stage_of: Dict[str, str] = {}
    for a in actions:
        rid = a.record_id
        l1 = layer1[rid]
        if l1.confident:
            final[rid] = l1.to_guardrail_result()
            stage_of[rid] = "layer1_deterministic"
        elif rid in llm_judgments and llm_judgments[rid].confident:
            final[rid] = llm_judgments[rid].to_guardrail_result()
            stage_of[rid] = "layer2_llm"
        elif rid in ml_predictions:
            final[rid] = ml_predictions[rid].to_guardrail_result()
            stage_of[rid] = "layer3_ml"
        else:
            # Only reachable if --skip-llm was NOT set and stage3_pool ended
            # up empty for this record somehow; fail safe rather than KeyError.
            final[rid] = l1.to_guardrail_result()
            stage_of[rid] = "layer1_deterministic (fallback)"

    guardrail = lambda action: final[action.record_id]
    run = evaluate(guardrail, actions, "cascade", args.view)

    print(f"\n{'=' * 60}\nCASCADE RESULT (view: {args.view}, source: {args.source})\n{'=' * 60}")
    print(f"n={run.metrics.n}  accuracy={run.metrics.accuracy:.3f}  "
          f"recall={run.metrics.attack_recall:.3f}  precision={run.metrics.precision:.3f}  "
          f"F1={run.metrics.f1:.3f}  FPR={run.metrics.false_positive_rate:.3f}")

    from collections import Counter
    funnel = Counter(stage_of.values())
    print("\nFunnel (where each record's final decision came from):")
    for stage in ("layer1_deterministic", "layer2_llm", "layer3_ml", "layer1_deterministic (fallback)"):
        if funnel[stage]:
            print(f"  {stage:<38}{funnel[stage]:>4}  ({funnel[stage] / len(actions):.1%})")

    if any(stage_of[a.record_id] == "layer2_llm" for a in actions):
        raw = Counter(llm_judgments[a.record_id].raw_decision for a in actions if a.record_id in llm_judgments)
        print("\nLayer 2 raw decisions (before confidence mapping):")
        for k, v in raw.most_common():
            print(f"  {k:<12}{v}")

    save_results(actions, final, stage_of, args.view, args.source)


def _training_exclusions(source: str, eval_ids: Set[str]) -> Set[str]:
    """Which record_ids Layer 3's XGBoost must never train on.

    --source pilot: exclude exactly the records being evaluated (small set,
    cheap, fully leak-free).
    --source full: the evaluation set *is* the whole corpus, so excluding it
    entirely would leave nothing to train on. Train only on the corpus's own
    `train` split instead (matches classifiers/task17's convention) - i.e.
    exclude everything that is NOT in the train split. This means records
    that are themselves in the train split are scored in-sample by Layer 3;
    the ~3,840 validation/test/flow_test/adaptive_test records are genuinely
    held out. See cascade/README.md for the caveat.
    """
    if source == "pilot":
        return set(eval_ids)
    from classifiers.build_training_set import split_lookup

    lookup = split_lookup()
    return {rid for rid in eval_ids if lookup.get(rid) != "train"}


def save_results(actions, final, stage_of, view: str, source: str) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"cascade_{view}_{source}.jsonl"
    with out_path.open("w", encoding="utf-8") as handle:
        for a in actions:
            result = final[a.record_id]
            handle.write(json.dumps({
                "record_id": a.record_id,
                "label": a.label,
                "expected_decision": a.expected_decision,
                "stage": stage_of[a.record_id],
                "decision": result.decision.name,
                "rules_fired": result.rules_fired,
                "reasons": result.reasons,
            }) + "\n")
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
