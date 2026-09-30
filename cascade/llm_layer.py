"""Layer 2 - the Gemini judge, now a middle layer (ML is the cascade's last
layer - see `cascade/run_cascade.py` and `cascade/README.md` for why).

Reuses `llm_judge.judge.GeminiJudge` unchanged. There is no upstream agent
proposal in this cascade (Layer 1 is rule-based, not `prompt/`'s LLM agent),
so the judge scores the dataset's own `requested_action` /
`requested_destination` - `judge.prepare_input`'s existing fallback
(`row.get('agent_action', row.get('requested_action'))`) already does exactly
this when no `agent_action` key is present.

`ALLOW`/`BLOCK` are confident and terminal here. `CONFIRM`/`REVISE`/`INVALID`
mean the judge itself could not commit to a decision, so they are this
layer's "uncertain" signal and escalate to Layer 3 (ML) rather than being
scored `FLAG` on the spot - matches `cascade.ml_layer.MLPrediction`'s
confident/uncertain shape so `run_cascade.py` treats both middle layers the
same way.
"""
from __future__ import annotations

import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.schema import Action, Decision, GuardrailResult
from llm_judge.judge import GeminiJudge

JUDGE_TO_DECISION = {
    "ALLOW": Decision.ALLOW,
    "BLOCK": Decision.BLOCK,
    "CONFIRM": Decision.FLAG,
    "REVISE": Decision.FLAG,
    "INVALID": Decision.FLAG,
    "UNCERTAIN": Decision.FLAG,  # cascade.uncertain_judge only
}

#: The judge committing to a real decision is confident; anything that asks
#: for a human/another layer instead is not.
CONFIDENT_DECISIONS = {"ALLOW", "BLOCK"}


def action_to_judge_row(action: Action) -> dict:
    """`Action` -> the dict shape `judge.prepare_input` expects.

    A plain `dataclasses.asdict` covers every field `prepare_input` reads
    (`sender`, `subject`, `email_body`, `user_instruction`,
    `authorization_evidence`, `authorization_rule`, `authorized_destination`,
    `requested_action`, `requested_destination`, `data_asset`,
    `data_sensitivity`); extra keys (`sources`, ground-truth fields) are
    ignored by `row.get(...)` lookups there.
    """
    return asdict(action)


@dataclass
class LLMJudgment:
    record_id: str
    raw_decision: str          # ALLOW / BLOCK / CONFIRM / REVISE / INVALID
    reason: str
    confident: bool            # True only for ALLOW/BLOCK

    def to_guardrail_result(self) -> GuardrailResult:
        result = GuardrailResult(record_id=self.record_id)
        result.apply(JUDGE_TO_DECISION[self.raw_decision], f"LLM_JUDGE:{self.raw_decision}", self.reason)
        return result


class LLMJudgeLayer:
    def __init__(self, judge: GeminiJudge, method: str = "plain"):
        self.judge = judge
        self.method = method

    def decide(self, action: Action) -> LLMJudgment:
        row = action_to_judge_row(action)
        answer = self.judge.judge(row, self.method)
        raw_decision = answer["decision"]
        return LLMJudgment(
            record_id=action.record_id,
            raw_decision=raw_decision,
            reason=answer.get("reason") or "(no reason given)",
            confident=raw_decision in CONFIDENT_DECISIONS,
        )
