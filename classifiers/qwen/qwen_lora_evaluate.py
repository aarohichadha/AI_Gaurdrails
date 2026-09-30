"""Evaluate an imported Qwen LoRA adapter on the project validation split."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from common import load_actions
from classifiers.qwen.qwen_finetune import prepare_dataset
from classifiers.qwen.qwen_lora_infer import DEFAULT_MODEL, find_adapter, load_classifier


def classify_example(model, tokenizer, device: str, text: str) -> tuple[str, str]:
    prompt = text + "\nAnswer:"
    inputs = tokenizer(prompt, return_tensors="pt").to(device)
    with torch.no_grad():
        output = model.generate(
            **inputs,
            max_new_tokens=4,
            do_sample=False,
            pad_token_id=tokenizer.pad_token_id,
        )
    raw = tokenizer.decode(
        output[0][inputs["input_ids"].shape[1] :], skip_special_tokens=True
    ).strip().upper()
    return ("ATTACK" if "ATTACK" in raw else "BENIGN"), raw


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", default=str(ROOT / "classifiers/results/qwen_lora_colab"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--output", default=str(ROOT / "classifiers/results/qwen_lora_colab_evaluation.json"))
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    model, tokenizer, device, temporary_dir = load_classifier(args.model, args.adapter)
    try:
        actions = load_actions()
        examples = prepare_dataset(actions, args.limit, seed=0)
        split_at = max(1, int(len(examples) * 0.9))
        validation = examples[split_at:] or examples[:1]
        rows = []
        for item in validation:
            predicted, raw = classify_example(model, tokenizer, device, item["text"])
            rows.append({
                "expected": item["label"],
                "predicted": predicted,
                "raw_output": raw,
            })
        accuracy = sum(row["expected"] == row["predicted"] for row in rows) / len(rows)
        result = {
            "model": args.model,
            "adapter": str(find_adapter(Path(args.adapter))),
            "device": device,
            "n_examples": len(examples),
            "n_validation": len(rows),
            "accuracy": accuracy,
            "predictions": rows,
        }
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"Device: {device}")
        print(f"Validation examples: {len(rows)}")
        print(f"Validation accuracy: {accuracy:.3f}")
        print(f"Wrote: {output}")
    finally:
        if temporary_dir is not None:
            temporary_dir.cleanup()


if __name__ == "__main__":
    main()