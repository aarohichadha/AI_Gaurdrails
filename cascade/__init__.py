"""Task 27 - full cascade: Deterministic -> LLM Judge -> ML.

Each layer only sees the records the previous layer could not resolve
confidently:

    confidence_layer.score()   2-1 split across basic_rules/provenance_rules/
         |                     ci_norm is uncertain -> escalate
         v
    Gemini judge (llm_judge/)  CONFIRM/REVISE/INVALID is uncertain -> escalate
         |
         v
    XGBoost (Task 16/17 features)   terminal - the cascade's last resort,
                                     whatever it predicts is final

ML is last, not the middle layer: an earlier ordering (ML before the judge)
found ML resolved 100% of what Layer 1 escalated at 0.99+ confidence, so the
judge was never reached. See `cascade/README.md` for the full writeup and
`cascade/run_cascade.py` for the orchestration.
"""
