"""Technique: hybrid guardrail (prompt-defense agent x deterministic rules).

Combines a Task 7-9 prompt-based agent decision with a Task 10-12
deterministic rule decision, taking the most restrictive of the two - see
`hybrid/combine.py`. Reuses `common/` (same `Action`, same views, same
`evaluate()`) and `llm_judge/`'s paired-pilot sampling, so results here stay
directly comparable to every other technique's numbers.
"""
