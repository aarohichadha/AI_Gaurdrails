"""Layer 2 - an XGBoost classifier trained on the Task 16 feature pipeline.

Reuses `classifiers.build_training_set.action_to_email` (renders an `Action`
as the email an agent would receive) and `classifiers.features.FeatureExtractor`
(the 99-feature vector), so this is the *same* feature code Task 17 already
validated - just trained under whichever `common.dataset` view the cascade is
evaluating, and always with the cascade's own held-out record_ids excluded
from training (no leakage from the pilot the cascade is scored on).

A prediction is "confident" (terminal ALLOW/BLOCK) when the winning class's
probability clears `confidence_threshold`; otherwise it is uncertain and the
caller (`run_cascade.py`) escalates to Layer 3.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set

import joblib
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.dataset import apply_view, load_actions
from common.schema import Action, Decision, GuardrailResult

from classifiers.build_training_set import CORPUS_CONFIG, LABEL_MAP, action_to_email
from classifiers.features import FeatureExtractor

MODELS_DIR = Path(__file__).resolve().parent / "models"

#: Task 17's classifier speaks SAFE/REVISE/ATTACK; the corpus (unlike the
#: synthetic three-class sheet) is two-class, so only these two appear here.
CLASS_TO_DECISION = {"SAFE": Decision.ALLOW, "ATTACK": Decision.BLOCK}


@dataclass
class MLPrediction:
    record_id: str
    predicted_class: str
    probabilities: Dict[str, float]
    confident: bool

    @property
    def max_proba(self) -> float:
        return max(self.probabilities.values())

    def to_guardrail_result(self) -> GuardrailResult:
        result = GuardrailResult(record_id=self.record_id)
        decision = CLASS_TO_DECISION[self.predicted_class]
        result.apply(
            decision,
            f"ML:{self.predicted_class}",
            f"xgboost p({self.predicted_class})={self.probabilities[self.predicted_class]:.3f}",
        )
        return result


class MLLayer:
    def __init__(
        self,
        model,
        feature_names: Sequence[str],
        classes: Sequence[str],
        confidence_threshold: float = 0.7,
    ):
        self.model = model
        self.feature_names = list(feature_names)
        self.classes = list(classes)
        self.confidence_threshold = confidence_threshold
        self.extractor = FeatureExtractor(CORPUS_CONFIG)

    def predict(self, action: Action) -> MLPrediction:
        vector = self.extractor.extract(action_to_email(action))
        x = np.array([vector.to_list(self.feature_names)], dtype=np.float64)
        proba = self.model.predict_proba(x)[0]
        probabilities = {cls: float(p) for cls, p in zip(self.classes, proba)}
        predicted_class = max(probabilities, key=probabilities.get)
        confident = probabilities[predicted_class] >= self.confidence_threshold
        return MLPrediction(action.record_id, predicted_class, probabilities, confident)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"model": self.model, "feature_names": self.feature_names, "classes": self.classes}, path)

    @classmethod
    def load(cls, path: Path, confidence_threshold: float = 0.7) -> "MLLayer":
        payload = joblib.load(path)
        return cls(payload["model"], payload["feature_names"], payload["classes"], confidence_threshold)


def _extract_matrix(actions: Sequence[Action], extractor: FeatureExtractor) -> "np.ndarray":
    vectors = [extractor.extract(action_to_email(a)) for a in actions]
    names = extractor.feature_names
    return np.array([v.to_list(names) for v in vectors], dtype=np.float64), names


def train(
    view: str = "destination_blind",
    exclude_ids: Optional[Set[str]] = None,
    seed: int = 0,
    confidence_threshold: float = 0.7,
) -> MLLayer:
    """Train on every corpus record under `view`, except `exclude_ids`.

    Two classes only (SAFE/ATTACK) - the corpus carries no REVISE rows unless
    `include_generated=True`, which this cascade does not opt into (see
    `cascade/README.md`).
    """
    from xgboost import XGBClassifier

    exclude_ids = exclude_ids or set()
    actions = [a for a in apply_view(load_actions(), view) if a.record_id not in exclude_ids]

    extractor = FeatureExtractor(CORPUS_CONFIG)
    X, names = _extract_matrix(actions, extractor)
    y_labels = [LABEL_MAP[a.label] for a in actions]
    classes = sorted(set(y_labels))
    class_index = {c: i for i, c in enumerate(classes)}
    y = np.array([class_index[label] for label in y_labels])

    counts = np.bincount(y)
    weights = np.array([len(y) / (len(classes) * counts[label]) for label in y])

    model = XGBClassifier(
        n_estimators=400,
        max_depth=5,
        learning_rate=0.08,
        subsample=0.9,
        colsample_bytree=0.8,
        reg_lambda=1.0,
        objective="binary:logistic" if len(classes) == 2 else "multi:softprob",
        eval_metric="logloss" if len(classes) == 2 else "mlogloss",
        tree_method="hist",
        n_jobs=-1,
        random_state=seed,
    )
    model.fit(X, y, sample_weight=weights)

    return MLLayer(model, names, classes, confidence_threshold)
