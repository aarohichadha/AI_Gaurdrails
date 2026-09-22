"""Fine-tune a Qwen model with LoRA for the email security task.

Usage sketch:

    python classifiers/qwen/qwen_finetune.py --model Qwen/Qwen2.5-0.5B-Instruct --epochs 1 --batch-size 1 --limit 200

The command performs real causal-language-model training and saves a LoRA
adapter. It uses a held-out validation split for loss reporting, but the
benchmark metrics should be produced by a separate evaluation script.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common import apply_view, load_actions

RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"


def build_training_example(action) -> dict:
    label = "ATTACK" if action.expected_decision == "BLOCK" else "BENIGN"
    return {
        "text": (
            f"You are a security classifier. Determine whether this email request is malicious or safe.\n\n"
            f"Subject: {action.subject}\n\n"
            f"Body: {action.email_body}\n\n"
            f"User instruction: {action.user_instruction}\n\n"
            f"Requested action: {action.requested_action}\n"
            f"Requested destination: {action.requested_destination or 'unknown'}\n\n"
            f"Return only one word: ATTACK or BENIGN."
        ),
        "label": label,
    }


def prepare_dataset(actions, limit: int | None = None, seed: int = 0):
    subset = list(actions if limit is None else actions[:limit])
    random.Random(seed).shuffle(subset)
    return [build_training_example(action) for action in subset]


def train_and_evaluate(
    model_id: str,
    epochs: int,
    batch_size: int,
    limit: int | None = None,
    output_dir: str | None = None,
    max_length: int = 512,
    learning_rate: float = 2e-4,
    seed: int = 0,
):
    try:
        from datasets import Dataset
        from peft import LoraConfig, TaskType, get_peft_model
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            DataCollatorForSeq2Seq,
            Trainer,
            TrainingArguments,
        )
    except ImportError as exc:  # pragma: no cover - user-facing guidance
        print("Missing training dependencies for Qwen fine-tuning.")
        print("Install with:")
        print("  pip install datasets peft accelerate")
        print(f"Encountered: {exc}")
        return None

    import torch

    random.seed(seed)
    torch.manual_seed(seed)
    actions = apply_view(load_actions(), "full")
    examples = prepare_dataset(actions, limit, seed)
    split_at = max(1, int(len(examples) * 0.9))
    train_rows = examples[:split_at]
    validation_rows = examples[split_at:] or examples[:1]

    tokenizer = AutoTokenizer.from_pretrained(model_id)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    def tokenize(item):
        prompt = item["text"] + "\nAnswer:"
        full = prompt + " " + item["label"] + tokenizer.eos_token
        encoded = tokenizer(full, truncation=True, max_length=max_length)
        prompt_tokens = tokenizer(prompt, add_special_tokens=False)["input_ids"]
        prompt_length = min(len(prompt_tokens), len(encoded["input_ids"]))
        encoded["labels"] = [-100] * prompt_length + encoded["input_ids"][prompt_length:]
        return encoded

    train_dataset = Dataset.from_list(train_rows).map(tokenize, remove_columns=["text", "label"])
    validation_dataset = Dataset.from_list(validation_rows).map(tokenize, remove_columns=["text", "label"])

    model = AutoModelForCausalLM.from_pretrained(model_id, torch_dtype=torch.float32)
    model.config.pad_token_id = tokenizer.pad_token_id
    lora = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=8,
        lora_alpha=16,
        lora_dropout=0.05,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
    )
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    destination = output_dir or str(RESULTS_DIR / "qwen_lora_adapter")
    training_args = TrainingArguments(
        output_dir=destination,
        num_train_epochs=epochs,
        per_device_train_batch_size=batch_size,
        per_device_eval_batch_size=batch_size,
        learning_rate=learning_rate,
        logging_steps=max(1, len(train_dataset) // max(batch_size * 10, 1)),
        eval_strategy="epoch",
        save_strategy="epoch",
        report_to=[],
        seed=seed,
        remove_unused_columns=False,
    )
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=validation_dataset,
        data_collator=DataCollatorForSeq2Seq(tokenizer, model=model, padding=True),
    )
    result = trainer.train()
    trainer.save_model(destination)
    tokenizer.save_pretrained(destination)

    summary = {
        "model_id": model_id,
        "epochs": epochs,
        "batch_size": batch_size,
        "n_examples": len(examples),
        "n_train": len(train_dataset),
        "n_validation": len(validation_dataset),
        "max_length": max_length,
        "learning_rate": learning_rate,
        "train_loss": result.training_loss,
        "validation": trainer.evaluate(),
        "adapter_dir": destination,
        "status": "trained_lora_adapter",
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"qwen_finetune_{model_id.replace('/', '_')}_summary.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Trained LoRA adapter on {len(train_dataset)} examples")
    print(f"Wrote {out}")
    return summary


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--seed", type=int, default=0)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    train_and_evaluate(
        args.model, args.epochs, args.batch_size, args.limit,
        args.output_dir, args.max_length, args.learning_rate, args.seed,
    )


if __name__ == "__main__":
    main()
