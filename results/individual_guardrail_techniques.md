# Individual Guardrail Techniques

All rows use the 300-record raw-email dataset. Other technique results are retained from their stored runs; the six LLM rows use the new uniform batch evaluation.

| Technique | n | 3-cls acc. | Macro-F1 | Attack recall | Precision | FPR |
|---|---|---|---|---|---|---|
| Deterministic — basic | 300 | 0.333 | 0.222 | 0.000 | 0.000 | 0.000 |
| Deterministic — provenance | 300 | 0.960 | 0.960 | 0.880 | 1.000 | 0.000 |
| Deterministic — ci_norm | 300 | 0.667 | 0.556 | 1.000 | 0.500 | 0.500 |
| task17_generated_random_forest | 300 | 0.650 | 0.543 | 1.000 | 0.488 | — |
| task17_generated_xgboost | 300 | 0.617 | 0.521 | 1.000 | 0.476 | — |
| Qwen LoRA (binary-only vocab) | 300 | 0.533 | — | 1.000 | 0.500 | 0.500 |
| Prompt Guard 2 (86M) | 300 | — (binary only) | — | 0.600 | 1.000 | 0.000 |
| Prompt agent — v9 context-aware | 300 | 0.333 | 0.167 | 1.000 | 0.333 | 1.000 |
| LLM judge - plain | 300 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 |
| LLM judge - delimited | 300 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 |
| LLM judge - sanitized | 300 | 0.970 | 0.970 | 1.000 | 1.000 | 0.000 |
| LLM judge - datamarked | 300 | 0.970 | 0.970 | 1.000 | 1.000 | 0.000 |
| LLM judge - provenance | 300 | 0.963 | 0.963 | 1.000 | 0.980 | 0.010 |
| LLM judge - full_defense | 300 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 |

LLM model: **gemini-3.5-flash-lite**. Method definitions and all 1,800 predictions: [judge report](../llm_judge/results/raw_email_300/REPORT.md) and [records.csv](../llm_judge/results/raw_email_300/records.csv). Three-class accuracy requires an exact SAFE/REVISE/ATTACK label match.
