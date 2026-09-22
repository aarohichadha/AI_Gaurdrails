# Qwen classifier experiments

This folder contains Qwen-based classifier experiments for the same email-agent security corpus used by the rest of the project.

## Goals

1. Benchmark Qwen as a zero-shot malicious-email classifier.
2. Compare its results to the Prompt Guard baselines.
3. Add a fine-tuning workflow for the same dataset.

## Files

- `qwen_zero_shot.py` — zero-shot Qwen classifier benchmark.
- `qwen_finetune.py` — fine-tune Qwen on the corpus and evaluate it.
- `__init__.py` — package marker.

## Zero-shot script

```bash
python classifiers/qwen/qwen_zero_shot.py --model Qwen/Qwen2.5-3B-Instruct --view full --limit 50
```

This script follows the same benchmark pattern as the other classifier tasks:

- load the dataset
- turn each record into a text prompt
- classify as ATTACK / BENIGN
- convert predictions to the shared `GuardrailResult`
- evaluate with the shared metrics package

## Fine-tuning script

```bash
python classifiers/qwen/qwen_finetune.py --model Qwen/Qwen2.5-0.5B-Instruct --epochs 1 --batch-size 1 --limit 200
```

This performs real parameter-efficient fine-tuning with LoRA. It updates
adapter weights using causal-language-model loss on the `ATTACK` / `BENIGN`
answer, evaluates validation loss, and saves the adapter under
`classifiers/results/qwen_lora_adapter` unless `--output-dir` is supplied.

The default 0.5B model is recommended for a laptop smoke test. The 3B model
needs substantially more disk space and memory. This training command does
not yet produce benchmark recall/F1/AUROC; those require a separate evaluator
that loads the saved adapter and predicts on held-out records.

## Notes

- Zero-shot is the easiest first experiment because it mirrors the existing Prompt Guard evaluation flow.
- Fine-tuning is a separate, heavier experiment and should be treated as a custom model-training task rather than a drop-in replacement for the baseline benchmark.
- Keep the dataset split, view, and reporting consistent so results remain comparable across models.
