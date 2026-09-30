"""The 100-pair (200-record) pilot slice shared by every combination.

Reuses `llm_judge.evaluation.paired_pilot` (same sheet, split, `n_pairs`,
`seed`) so the record set here is identical to the one already scored by the
Gemini judge in `results/task7_to_llm_judge/` - the numbers are directly
comparable, not just similarly shaped.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.dataset import apply_view, load_actions
from common.schema import Action
from llm_judge.evaluation import load_dataset, paired_pilot

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "data" / "email_agent_security_dataset.xlsx"


def pilot_record_ids(
    n_pairs: int = 100, seed: int = 42, dataset_path: Path = DEFAULT_DATASET
) -> List[str]:
    data = load_dataset(str(dataset_path))
    pilot = paired_pilot(data, n_pairs=n_pairs, seed=seed)
    return pilot.record_id.tolist()


def pilot_actions(
    view: str = "full", n_pairs: int = 100, seed: int = 42, dataset_path: Path = DEFAULT_DATASET
) -> List[Action]:
    """The pilot's records, with `view` applied (features withheld, no
    labels ever touched - see `common.dataset.VIEWS`)."""
    wanted = set(pilot_record_ids(n_pairs=n_pairs, seed=seed, dataset_path=dataset_path))
    actions = [a for a in apply_view(load_actions(), view) if a.record_id in wanted]
    missing = wanted - {a.record_id for a in actions}
    if missing:
        raise KeyError(f"record_id(s) not found in view {view!r}: {sorted(missing)}")
    return actions
