"""Run local inference with the Qwen base model and a trained LoRA adapter."""
from __future__ import annotations

import argparse
import sys
import tempfile
import zipfile
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DEFAULT_MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
DEFAULT_ADAPTER = ROOT / "classifiers" / "results" / "qwen_lora_colab"


def find_adapter(path: Path) -> Path:
    if path.is_dir() and (path / "adapter_config.json").exists():
        return path
    if path.is_dir():
        matches = sorted(path.rglob("adapter_config.json"))
        if len(matches) == 1:
            return matches[0].parent
    raise FileNotFoundError(
        f"Could not find adapter_config.json under {path}. "
        "Extract qwen_lora_colab_output.zip into classifiers/results/qwen_lora_colab."
    )


def load_classifier(model_id: str, adapter_path: str):
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    adapter_input = Path(adapter_path).expanduser().resolve()
    temporary_dir = None
    if adapter_input.is_file() and adapter_input.suffix.lower() == ".zip":
        temporary_dir = tempfile.TemporaryDirectory()
        with zipfile.ZipFile(adapter_input) as archive:
            archive.extractall(temporary_dir.name)
        adapter_dir = find_adapter(Path(temporary_dir.name))
    else:
        adapter_dir = find_adapter(adapter_input)

    tokenizer = AutoTokenizer.from_pretrained(model_id)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    dtype = torch.float16 if torch.cuda.is_available() else torch.float32
    base_model = AutoModelForCausalLM.from_pretrained(model_id, torch_dtype=dtype)
    model = PeftModel.from_pretrained(base_model, adapter_dir)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.to(device)
    model.eval()
    return model, tokenizer, device, temporary_dir


def classify(model, tokenizer, device: str, subject: str, body: str) -> str:
    prompt = f"""You are a security classifier. Determine whether this email request is malicious or safe.

Subject: {subject}

Body: {body}

Return only one word: ATTACK or BENIGN.

Answer:"""
    inputs = tokenizer(prompt, return_tensors="pt").to(device)
    with torch.no_grad():
        output = model.generate(
            **inputs,
            max_new_tokens=4,
            do_sample=False,
            pad_token_id=tokenizer.pad_token_id,
        )
    answer = tokenizer.decode(
        output[0][inputs["input_ids"].shape[1] :], skip_special_tokens=True
    ).strip().upper()
    return "ATTACK" if "ATTACK" in answer else "BENIGN"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", default=str(DEFAULT_ADAPTER))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--body", required=True)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    model, tokenizer, device, temporary_dir = load_classifier(args.model, args.adapter)
    try:
        print(f"Device: {device}")
        print(f"Prediction: {classify(model, tokenizer, device, args.subject, args.body)}")
    finally:
        if temporary_dir is not None:
            temporary_dir.cleanup()


if __name__ == "__main__":
    main()