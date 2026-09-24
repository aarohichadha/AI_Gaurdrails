"""Task 16 - feature engineering for raw email.

    RAW EMAIL -> Email Parser -> Feature Extractor -> Feature Vector

Nothing here reads the project dataset; the input is an ordinary `.eml`
message, so the same code works on a live mailbox.
"""
from .extractor import (
    LABELS,
    ExtractorConfig,
    FeatureExtractor,
    FeatureVector,
    extract_features,
)
from .parser import Attachment, Link, RawEmail, parse

__all__ = [
    "LABELS",
    "ExtractorConfig",
    "FeatureExtractor",
    "FeatureVector",
    "extract_features",
    "Attachment",
    "Link",
    "RawEmail",
    "parse",
]
