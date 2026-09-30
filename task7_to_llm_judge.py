"""Bridge: feed Task 7's undefended agent output into the Gemini judge.

Lives outside `prompt/` and `llm_judge/` on purpose - it only *imports* from
both and never edits either. It closes the gap `llm_judge/README.md` and
`judge.py` already flag: the judge's `agent_action`/`agent_destination`
fallback (see `judge.prepare_input`) has never been fed a real proposal,
only the dataset's own pre-authored `requested_action`/`requested_destination`
("controlled proposals, not genuine upstream agent traces").

Pipeline:

    Task 7 EmailAgent (v7_baseline, no security prompt, local via Ollama)
        |  proposes (action, destination) for real, per record
        v
    Gemini judge (llm_judge.judge.GeminiJudge)
        |  reviews that real proposal as the security barrier
        v
    ALLOW / CONFIRM / REVISE / BLOCK  (scored against expected_decision)

Stage 1 (generating traces) is free/local and safe to run any time. Stage 2
calls the Gemini API and costs money per record per method - see
`--methods` and `--n-pairs` below before running it for real.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from common.dataset import apply_view, load_actions
from prompt.agent import AgentConfig, EmailAgent
from prompt.provider import OllamaProvider

from llm_judge.evaluation import load_dataset, paired_pilot, score
from llm_judge.judge import METHODS, GeminiJudge

RESULTS_DIR = ROOT / "results" / "task7_to_llm_judge"
DEFAULT_DATASET = ROOT / "data" / "email_agent_security_dataset.xlsx"


# ---------------------------------------------------------------------------
# Stage 1: genuine Task 7 (undefended) upstream traces - local, free (Ollama)
# ---------------------------------------------------------------------------

def generate_task7_traces(
    record_ids: Iterable[str], *, model: str = "llama3.2:1b", view: str = "full"
) -> Dict[str, dict]:
    """Run `prompt.agent.EmailAgent` under `v7_baseline` (no security prompt)
    over `record_ids`, keyed by record_id. This is what an *unprotected*
    agent actually proposes - including, on attack records, whatever an
    injected instruction talked it into - not the dataset's canned answer.
    """
    wanted = {str(r) for r in record_ids}
    actions = [a for a in apply_view(load_actions(), view) if a.record_id in wanted]
    missing = wanted - {a.record_id for a in actions}
    if missing:
        raise KeyError(f"record_id(s) not found in view {view!r}: {sorted(missing)}")

    agent = EmailAgent(OllamaProvider(), AgentConfig(model=model, prompt_version="v7_baseline"))
    traces: Dict[str, dict] = {}
    for action in actions:
        decision = agent.run(action)
        traces[action.record_id] = {
            "agent_action": decision.action,
            "agent_destination": decision.recipient,
            "agent_decision": decision.decision.name,
            "agent_reason": decision.reason,
            "agent_parse_error": decision.parse_error,
            "agent_latency_s": round(decision.latency_s, 4),
        }
    return traces


# ---------------------------------------------------------------------------
# Stage 2: judge the real proposal - calls Gemini, costs money
# ---------------------------------------------------------------------------

#: The free tier enforces `generate_content_free_tier_requests` per model per
#: minute (observed limit: 15). Space calls out proactively instead of only
#: reacting to 429s, since a burst just trades an immediate 429 for a slower
#: one.
MIN_SECONDS_BETWEEN_CALLS = 4.5
MAX_RETRIES = 6
_RETRY_DELAY_RE = re.compile(r"retryDelay['\"]?:\s*['\"]?(\d+(?:\.\d+)?)s")


def _judge_with_backoff(judge: GeminiJudge, row: dict, method: str) -> dict:
    """Call `judge.judge`, retrying on rate limits/transient errors instead
    of letting one flaky call crash the whole 1200-call run. Gives up after
    `MAX_RETRIES` and returns an ERROR-tagged result so the record is still
    accounted for in the output, matching this repo's "never silently drop
    a scored record" convention.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return judge.judge(row, method)
        except Exception as exc:  # google.genai raises its own ClientError/ServerError
            code = getattr(exc, "code", None)
            if attempt == MAX_RETRIES or code not in (429, 500, 503):
                # "INVALID" (not a custom "ERROR" label) so `llm_judge.evaluation.score`'s
                # existing valid_output_rate/stopped logic already accounts for this record.
                return {"decision": "INVALID", "reason": f"api error: {str(exc)[:300]}", "status": "ERROR", "raw_output": "", "latency_seconds": 0.0}
            match = _RETRY_DELAY_RE.search(str(getattr(exc, "details", "") or exc))
            delay = float(match.group(1)) + 1.0 if match else min(60.0, 5.0 * 2 ** (attempt - 1))
            print(f"    retry {attempt}/{MAX_RETRIES} after {delay:.0f}s ({code or 'error'}: {str(exc)[:120]})", flush=True)
            time.sleep(delay)
    return {"decision": "INVALID", "reason": "unreachable", "status": "ERROR", "raw_output": "", "latency_seconds": 0.0}


def _already_done(out_path: Path) -> Set[Tuple[str, str]]:
    """(record_id, method) pairs already written, for resuming after a crash
    without re-spending API calls on work that's already been paid for."""
    if not out_path.exists():
        return set()
    done = set()
    with out_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            done.add((row["record_id"], row["method"]))
    return done


def run_judge_on_traces(
    pilot, traces: Dict[str, dict], methods: List[str], api_key: str, out_path: Path, *, judge_model: str
) -> List[dict]:
    judge = GeminiJudge(api_key, model=judge_model)
    done = _already_done(out_path)
    if done:
        print(f"resuming: {len(done)} (record, method) pairs already judged in {out_path}", flush=True)
    all_rows: List[dict] = []
    if out_path.exists():
        with out_path.open(encoding="utf-8") as handle:
            all_rows = [json.loads(line) for line in handle if line.strip()]

    last_call = 0.0
    with out_path.open("a", encoding="utf-8") as handle:
        for method in methods:
            method_rows = [r for r in all_rows if r["method"] == method]
            todo = [row for _, row in pilot.iterrows() if (row.record_id, method) not in done]
            for i, row in enumerate(todo, start=1):
                wait = MIN_SECONDS_BETWEEN_CALLS - (time.monotonic() - last_call)
                if wait > 0:
                    time.sleep(wait)
                merged = {**row.to_dict(), **traces[row.record_id]}
                answer = _judge_with_backoff(judge, merged, method)
                last_call = time.monotonic()
                result = {
                    "record_id": row.record_id,
                    "pair_id": row.get("pair_id"),
                    "label": row.label,
                    "expected_decision": row.expected_decision,
                    "method": method,
                    "agent_action": merged["agent_action"],
                    "agent_destination": merged["agent_destination"],
                    **answer,
                }
                method_rows.append(result)
                all_rows.append(result)
                handle.write(json.dumps(result) + "\n")
                handle.flush()
                if i % 10 == 0 or i == len(todo):
                    print(f"  [{method}] {i}/{len(todo)} new records judged this run", flush=True)
            print(f"[{method}] {score(method_rows)}", flush=True)
    return all_rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--n-pairs", type=int, default=100, help="paired dev records = 2x this")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--agent-model", default="llama3.2:1b", help="Ollama model for the Task 7 proposer")
    parser.add_argument("--view", default="full", help="common.dataset view the Task 7 agent sees")
    parser.add_argument(
        "--methods", nargs="+", default=list(METHODS), choices=list(METHODS),
        help=f"judge input-construction variants to run; default is all: {METHODS}",
    )
    parser.add_argument("--skip-judge", action="store_true", help="only generate Task 7 traces, don't call Gemini")
    parser.add_argument("--api-key", default=None, help="defaults to $GEMINI_API_KEY")
    parser.add_argument(
        "--judge-model", default="gemini-3.5-flash-lite",
        help="gemini-2.5-flash-lite (judge.py's old default) was retired for new users; use the current model id",
    )
    parser.add_argument(
        "--reuse-traces", action="store_true",
        help="load Task 7 traces from the existing results/task7_to_llm_judge/task7_traces.jsonl instead of "
        "re-running Ollama (use after a prior run already generated them)",
    )
    args = parser.parse_args()

    data = load_dataset(args.dataset)
    pilot = paired_pilot(data, n_pairs=args.n_pairs, seed=args.seed)
    record_ids = pilot.record_id.tolist()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    traces_path = RESULTS_DIR / "task7_traces.jsonl"

    if args.reuse_traces:
        with traces_path.open(encoding="utf-8") as handle:
            traces = {(row := json.loads(line))["record_id"]: row for line in handle}
        missing = set(record_ids) - set(traces)
        if missing:
            raise SystemExit(f"--reuse-traces: {traces_path} is missing {len(missing)} record(s) from this pilot slice")
        print(f"reusing {len(record_ids)} Task 7 traces from {traces_path}")
    else:
        print(f"generating Task 7 (undefended) traces for {len(record_ids)} records via Ollama ({args.agent_model})...")
        traces = generate_task7_traces(record_ids, model=args.agent_model, view=args.view)
        with traces_path.open("w", encoding="utf-8") as handle:
            for record_id in record_ids:
                handle.write(json.dumps({"record_id": record_id, **traces[record_id]}) + "\n")
        print(f"wrote {traces_path}")

    deviated = sum(
        1
        for _, row in pilot.iterrows()
        if str(traces[row.record_id]["agent_destination"] or "").strip().lower()
        != str(row.get("requested_destination") or "").strip().lower()
    )
    errors = sum(1 for t in traces.values() if t["agent_parse_error"])
    print(f"agent_destination differs from the dataset's requested_destination on {deviated}/{len(traces)} records")
    print(f"unparseable Task 7 output on {errors}/{len(traces)} records")

    if args.skip_judge:
        return

    api_key = args.api_key or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY is not set; export it or pass --api-key (or use --skip-judge)")

    print(f"\nrunning Gemini judge ({args.judge_model}) over methods: {args.methods}", flush=True)
    print(f"({len(record_ids)} records x {len(args.methods)} methods = {len(record_ids) * len(args.methods)} API calls)", flush=True)
    out_path = RESULTS_DIR / "judge_on_task7_traces.jsonl"
    run_judge_on_traces(pilot, traces, args.methods, api_key, out_path, judge_model=args.judge_model)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
