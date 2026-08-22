import json

import pytest

from cmd_audit.core.state_codec import content_sha256
from experiments.run_longmemeval_e2e import _safe_instance_name, predict, score


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value), encoding="utf-8")


def _fixture(tmp_path):
    data = tmp_path / "data.json"; _write(data, [{"question_id": "q1", "question": "What?", "answer": "secret", "question_type": "x"}])
    run = tmp_path / "r"; _write(run / "manifest.json", {"schema_version": "cmd-longmemeval-m0-r1-v3"})
    content = "deployment-visible memory"
    _write(run / "retrieval" / "vanilla" / f"{_safe_instance_name('q1')}.json", {"question_id":"q1", "arm":"vanilla", "records":[{"memory_id":"m", "content":content, "source_hash":content_sha256(content)}]})
    return data, run


def test_fake_predict_firewalls_gold_and_writes_official_shape(tmp_path):
    data, run = _fixture(tmp_path); out = tmp_path / "out"
    predict(data=data, retrieval_run=run, output=out, arms=("vanilla",), context_budget=99)
    row = json.loads((out / "predictions/vanilla.jsonl").read_text())
    assert set(row) == {"question_id", "hypothesis"}
    assert "secret" not in row["hypothesis"]


def test_judge_requires_seal_and_rejects_prediction_tamper(tmp_path):
    data, run = _fixture(tmp_path); out = tmp_path / "out"
    with pytest.raises(FileNotFoundError): score(reference=data, output=out, judge_backend="fake")
    predict(data=data, retrieval_run=run, output=out, arms=("vanilla",))
    with (out / "predictions/vanilla.jsonl").open("a") as h: h.write('{"question_id":"q2","hypothesis":"x"}\n')
    with pytest.raises(ValueError, match="tamper"): score(reference=data, output=out, judge_backend="fake")


def test_retrieval_content_tamper_is_rejected(tmp_path):
    data, run = _fixture(tmp_path)
    artifact = run / "retrieval" / "vanilla" / f"{_safe_instance_name('q1')}.json"
    item = json.loads(artifact.read_text()); item["records"][0]["content"] = "tampered"; artifact.write_text(json.dumps(item))
    with pytest.raises(ValueError, match="root mismatch"):
        predict(data=data, retrieval_run=run, output=tmp_path / "out", arms=("vanilla",))
