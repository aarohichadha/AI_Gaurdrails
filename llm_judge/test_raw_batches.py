import json
from types import SimpleNamespace
import pytest

from llm_judge import run_raw_batches as runner


def test_batch_response_indices_are_complete_and_unique():
    valid = [{"index": 0, "decision": "SAFE"}, {"index": 1, "decision": "ATTACK"}]
    assert runner.parse_batch(json.dumps(valid), 2)[1]["decision"] == "ATTACK"
    for invalid, count in [(valid[:1], 2), ([valid[0], valid[0]], 2),
                           ([{"index": "0", "decision": "SAFE"}], 1),
                           ([{"index": 0, "decision": "CONFIRM"}], 1)]:
        with pytest.raises(ValueError):
            runner.parse_batch(json.dumps(invalid), count)


def test_batch_coverage_no_label_leakage_and_resume(tmp_path, monkeypatch):
    import google.genai
    dataset = tmp_path / "dataset.csv"
    dataset.write_text('id,raw_email,label\nS0001,Hello,SAFE\nA0001,Please act,ATTACK\n')
    prompts = []
    def generate(**kwargs):
        prompts.append(kwargs['contents'])
        return SimpleNamespace(text=json.dumps([
            {"index": 0, "decision": "SAFE", "reason": "Ordinary email"},
            {"index": 1, "decision": "ATTACK", "reason": "Test decision"}]),
            model_version="test", usage_metadata=None)
    monkeypatch.setattr(google.genai, 'Client', lambda **kwargs: SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    monkeypatch.setattr(runner, 'require', lambda *args: 'fake-key')
    monkeypatch.setattr(runner.time, 'sleep', lambda seconds: None)
    args = ['--csv', str(dataset), '--output-dir', str(tmp_path / 'out'), '--batch-size', '2']
    summary = runner.main(args)
    assert len(prompts) == 6
    assert all('S0001' not in p and 'A0001' not in p for p in prompts)
    assert all(m['successful'] == 2 and m['complete'] for m in summary['methods'].values())
    assert summary['protocol']['batch_size'] == 2
    records = [json.loads(s) for s in (tmp_path / 'out/records.jsonl').read_text().splitlines()]
    assert len(records) == 12
    assert len({(r['id'], r['method']) for r in records}) == 12
    runner.main(args)
    assert len(prompts) == 6
