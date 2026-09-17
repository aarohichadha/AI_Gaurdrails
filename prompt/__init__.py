"""Technique: prompt-based defenses for the email-agent corpus (Tasks 7-9).

One `EmailAgent` (agent.py) is shared by every task in this folder. The
*only* thing that is meant to differ between Task 7 (no defense), Task 8
(basic defense) and Task 9 (context-aware defense) is which system prompt
`prompts.py` supplies - the agent, the dataset loading, the provider
abstraction and the evaluation wiring are identical across all three, so the
numbers stay comparable the same way the `deterministic/` variants are.
"""
