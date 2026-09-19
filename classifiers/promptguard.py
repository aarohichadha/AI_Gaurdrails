"""PromptGuard scorer - a thin, model-agnostic wrapper for Meta's Prompt Guard.

PromptGuard is a text classifier: it sees one string and returns how likely
that string is to be a prompt attack. It has no notion of the sender, the
recipient, the data or the authorization - which is exactly what Task 15
sets out to measure.

The wrapper reads the label map from the model's own config instead of
hard-coding it, because the two generations differ:

    Prompt Guard 1 (86M)    3 classes: BENIGN / INJECTION / JAILBREAK
    Prompt Guard 2 (86M,22M) 2 classes: LABEL_0 (benign) / LABEL_1 (malicious)

In both cases the attack score is ``1 - P(benign)``.
"""
from __future__ import annotations

import hashlib
import statistics
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

#: Hugging Face ids. Both are gated: accept Meta's licence on the model page,
#: then `hf auth login` (or set HF_TOKEN) before the first download.
MODELS = {
    "pg2-86m": "meta-llama/Llama-Prompt-Guard-2-86M",
    "pg2-22m": "meta-llama/Llama-Prompt-Guard-2-22M",
}

#: Prompt Guard 2 was trained on inputs of up to 512 tokens. Longer text is
#: split into windows and the most malicious window wins (Meta's recommended
#: handling), so an attack cannot hide behind padding.
MAX_TOKENS = 512

#: Tokens of overlap between windows, so a phrase split at a boundary is
#: still seen whole by one of them.
WINDOW_STRIDE = 64

_BENIGN_LABEL_NAMES = {"BENIGN", "LABEL_0", "SAFE"}


@dataclass
class LatencyStats:
    """Per-request latency with batch size 1 - what a live agent pays."""

    samples: int
    mean_ms: float
    p50_ms: float
    p95_ms: float
    max_ms: float

    def as_dict(self) -> dict:
        return {k: round(v, 3) if isinstance(v, float) else v for k, v in self.__dict__.items()}


def _percentile(values: Sequence[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[index]


def summarise_latency(durations_s: Sequence[float]) -> LatencyStats:
    ms = [d * 1000 for d in durations_s]
    return LatencyStats(
        samples=len(ms),
        mean_ms=statistics.fmean(ms) if ms else 0.0,
        p50_ms=_percentile(ms, 50),
        p95_ms=_percentile(ms, 95),
        max_ms=max(ms) if ms else 0.0,
    )


def find_benign_index(id2label: dict) -> int:
    """Index of the benign class, for either Prompt Guard generation."""
    benign = [int(i) for i, name in id2label.items() if str(name).upper() in _BENIGN_LABEL_NAMES]
    if len(benign) != 1:
        raise ValueError(f"cannot identify the benign class from id2label={id2label}")
    return benign[0]


class PromptGuardScorer:
    """Scores text with a Hugging Face sequence classifier."""

    def __init__(self, model_id: str, device: str = "cpu", batch_size: int = 32):
        # Imported lazily so `--backend mock` and the tests need no torch.
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self._torch = torch
        self.model_id = model_id
        self.device = device
        self.batch_size = batch_size
        self.tokenizer = AutoTokenizer.from_pretrained(model_id)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_id).to(device).eval()

        self.labels = {int(i): str(name) for i, name in self.model.config.id2label.items()}
        self.benign_index = find_benign_index(self.labels)

    # ------------------------------------------------------------------
    def _windows(self, text: str) -> List[str]:
        """Split text into <=MAX_TOKENS windows (usually just one)."""
        ids = self.tokenizer(text, add_special_tokens=False, verbose=False)["input_ids"]
        budget = MAX_TOKENS - 2  # room for [CLS] / [SEP]
        if len(ids) <= budget:
            return [text]
        step = budget - WINDOW_STRIDE
        return [
            self.tokenizer.decode(ids[start:start + budget])
            for start in range(0, len(ids), step)
        ]

    def _score_batch(self, texts: List[str]) -> List[float]:
        torch = self._torch
        encoded = self.tokenizer(
            texts, return_tensors="pt", padding=True, truncation=True, max_length=MAX_TOKENS
        ).to(self.device)
        with torch.no_grad():
            logits = self.model(**encoded).logits
        probs = torch.softmax(logits.float(), dim=-1)
        return (1.0 - probs[:, self.benign_index]).tolist()

    def score(self, texts: Sequence[str], progress: Optional[Callable[[int], None]] = None) -> List[float]:
        """Attack probability for each text (max over its windows)."""
        flat: List[str] = []
        owner: List[int] = []
        for index, text in enumerate(texts):
            for window in self._windows(text or ""):
                flat.append(window)
                owner.append(index)

        window_scores: List[float] = []
        for start in range(0, len(flat), self.batch_size):
            window_scores.extend(self._score_batch(flat[start:start + self.batch_size]))
            if progress:
                progress(min(start + self.batch_size, len(flat)))

        scores = [0.0] * len(texts)
        for index, value in zip(owner, window_scores):
            scores[index] = max(scores[index], value)
        return scores

    def measure_latency(self, texts: Sequence[str], warmup: int = 5) -> LatencyStats:
        """Time single-request inference, tokenisation included."""
        for text in list(texts)[:warmup]:
            self.score([text])
        durations = []
        for text in texts:
            start = time.perf_counter()
            self.score([text])
            durations.append(time.perf_counter() - start)
        return summarise_latency(durations)


class MockScorer:
    """Deterministic pseudo-random scores, for pipeline dry runs only.

    NOT a guardrail: its metrics are meaningless (expect ~0.5 AUROC). It
    exists so the CLI, logging and reporting can run without the gated model.
    """

    model_id = "mock"
    labels = {0: "LABEL_0", 1: "LABEL_1"}

    def score(self, texts: Sequence[str], progress=None) -> List[float]:
        scores = []
        for text in texts:
            digest = hashlib.sha256((text or "").encode("utf-8")).digest()
            scores.append(int.from_bytes(digest[:4], "big") / 2**32)
        if progress:
            progress(len(texts))
        return scores

    def measure_latency(self, texts: Sequence[str], warmup: int = 0) -> LatencyStats:
        durations = []
        for text in texts:
            start = time.perf_counter()
            self.score([text])
            durations.append(time.perf_counter() - start)
        return summarise_latency(durations)
