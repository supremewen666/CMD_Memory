from __future__ import annotations

import json
from pathlib import Path

import experiments.analyze_ecc_mechanism_results as analyze


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_process_fault_analysis_is_paired_stratified_and_never_pooled(
    tmp_path: Path, monkeypatch,
) -> None:
    run = tmp_path / "run"
    case_ids = [f"case-{index}" for index in range(4)]
    subtypes = ["retrieval", "injection", "granularity", "safety"]
    monkeypatch.setattr(analyze, "validate_ecc_prediction_seal", lambda _path: {
        "mechanism": "process_fault", "case_count": 4, "binding_root": "seal-root",
    })
    for arm in analyze.ARMS:
        _write_jsonl(run / "predictions" / f"{arm}.jsonl", [
            {"question_id": case_id, "hypothesis": arm} for case_id in case_ids
        ])
    _write_jsonl(run / "runtime_ledger.jsonl", [
        {"case_id": case_id, "mechanism": "process_fault", "process_fault_subtype": subtype}
        for case_id, subtype in zip(case_ids, subtypes, strict=True)
    ])
    arms = {}
    for arm, score in (("incident_before", 0.0), ("repaired_after", 1.0)):
        arms[arm] = {"per_case": [
            {"question_id": case_id, "category": 4, "official_f1": score}
            for case_id in case_ids
        ]}
    (run / "official_score_report.json").write_text(json.dumps({
        "schema_version": "cmd-locomo-official-score-v2", "arms": arms,
    }), encoding="utf-8")
    report = analyze.analyze(
        run_dir=run, output=tmp_path / "analysis.json",
        min_cases_per_stratum=1, bootstrap_samples=100, seed=7,
    )
    assert report["pooled_score_prohibited"] is True
    assert report["official_f1"]["paired_delta_mean"] == 1.0
    assert set(report["by_process_fault_subtype"]) == set(subtypes)
    assert report["confirmatory_gate"]["passed"] is True
