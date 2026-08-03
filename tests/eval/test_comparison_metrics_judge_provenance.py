"""Tests for judge-identity + rubric-version provenance on paper-facing CSVs.

SPEC_A §3 (Red Queen GM 2606.26294): every paper-facing output CSV must
record the judge endpoint identity and rubric version, so evaluator
freezing is auditable across arms and generations. This exercises
the optional ``judge_client`` / ``rubric_version`` parameters on every
paper-facing writer end to end, the frozen pre-rollout byte contract,
the batch lane environment, and the underlying provenance helper.
"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace

import pytest

from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
from cmd_audit.eval import writers
from cmd_audit.eval.provenance import (
    derived_scorer_version,
    judge_provenance_fields,
    require_scorer_version,
)
from cmd_audit.harness import write_comparison_metrics_table


def _audit(recovery_gain: float | None, *, provenance_edges: tuple = ()):
    attribution = None
    if recovery_gain is not None:
        attribution = SimpleNamespace(recovery_gain=recovery_gain)
    comparator = SimpleNamespace(
        comparator_name="random_label",
        cost_per_diagnosis=0.01,
    )
    return SimpleNamespace(
        attribution=attribution,
        replays=[SimpleNamespace(provenance_edges=provenance_edges)],
        diagnosis_cost=1.5,
        baseline_suite=SimpleNamespace(comparator_results=(comparator,)),
    )


def test_judge_provenance_fields_reads_base_url_model_and_default_version():
    judge_client = LLMClient(
        LLMClientConfig(base_url="http://localhost:9000/v1", model="gpt-4o-judge")
    )

    fields = judge_provenance_fields(judge_client)

    assert fields["judge_base_url"] == "http://localhost:9000/v1"
    assert fields["judge_model"] == "gpt-4o-judge"
    assert fields["rubric_version"]  # non-empty default, from scoring.llm.RUBRIC_VERSION


def test_judge_provenance_fields_accepts_explicit_rubric_version():
    judge_client = LLMClient(LLMClientConfig(base_url="http://x/v1", model="m"))

    fields = judge_provenance_fields(judge_client, rubric_version="rubric-v2-exp")

    assert fields["rubric_version"] == "rubric-v2-exp"


def test_scorer_version_is_derived_from_real_judge_identity():
    judge_client = LLMClient(
        LLMClientConfig(
            base_url="http://judge.example:9000/v1",
            model="judge-model",
        )
    )

    identity = derived_scorer_version(
        judge_client,
        rubric_version="rubric-v-test",
    )
    expected = hashlib.sha256(
        b"judge-model|judge.example:9000|rubric-v-test"
    ).hexdigest()[:12]

    assert identity == {
        "scorer_version": expected,
        "judge_model": "judge-model",
        "judge_host": "judge.example:9000",
        "rubric_version": "rubric-v-test",
    }


def test_explicit_scorer_version_mismatch_fails_closed():
    judge_client = LLMClient(
        LLMClientConfig(base_url="http://judge/v1", model="judge-model")
    )

    with pytest.raises(ValueError, match="disagrees with the derived"):
        require_scorer_version(
            judge_client,
            explicit_scorer_version="free-form-label",
            rubric_version="rubric-v-test",
        )


def test_comparison_metrics_table_without_judge_client_omits_provenance_columns(
    tmp_path: Path,
) -> None:
    """Backward compatibility: omitting judge_client keeps the existing columns."""
    path = tmp_path / "comparison_metrics.csv"
    write_comparison_metrics_table([_audit(0.5)], path)

    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assert "judge_base_url" not in rows[0]
    assert "judge_model" not in rows[0]
    assert "rubric_version" not in rows[0]


def test_comparison_metrics_table_with_judge_client_carries_judge_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "comparison_metrics.csv"
    judge_client = LLMClient(
        LLMClientConfig(base_url="http://localhost:9000/v1", model="qwen2.5-judge")
    )
    results = [
        _audit(0.5, provenance_edges=("edge-1",)),
        _audit(0.0),
    ]

    write_comparison_metrics_table(
        results, path, judge_client=judge_client, rubric_version="rubric-v1"
    )

    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    for row in rows:
        assert row["judge_base_url"] == "http://localhost:9000/v1"
        assert row["judge_model"] == "qwen2.5-judge"
        assert row["rubric_version"] == "rubric-v1"


NEWLY_INSTRUMENTED_WRITERS = (
    "attribution",
    "step_level_metrics",
    "provenance_completeness",
    "confusion_matrix",
    "post_repair",
    "retrieval_metrics",
)


def _paper_audit(case_id: str):
    attribution = SimpleNamespace(
        predicted_label="retrieval_error",
        top_replay="oracle_retrieval",
        recovery_gain=0.25,
        top2_labels=("retrieval_error", "injection_error"),
        is_ambiguous=False,
        top_k_labels=("retrieval_error",),
        close_deltas=(("retrieval_error", 0.25),),
        distractor_provenance_ids=("prov-1",),
    )
    replay = SimpleNamespace(
        replay_name="oracle_retrieval",
        answer_score=0.75,
        evidence_score=0.5,
        recovery_gain=0.25,
        evidence_block="supporting evidence",
        provenance_edges=("edge-1",),
    )
    repair = SimpleNamespace(
        post_repair_answer_score=0.9,
        post_repair_evidence_score=1.0,
        repair_assessment="recovered",
        token_cost=12.0,
        regression_risk=0.0,
        had_repair_regression=False,
    )
    attribution_result = SimpleNamespace(
        primary_attribution_label="retrieval_error",
        main_culprit=(0, "retrieval_error", 0.25),
        action_credits={0: {"identity": 0.0, "retrieval_error": 0.25}},
    )
    return SimpleNamespace(
        case_id=case_id,
        perturbation_label="retrieval_error",
        predicted_label="retrieval_error",
        attribution=attribution,
        replay=replay,
        replays=[replay],
        baseline_name="base",
        baseline_answer_score=0.5,
        baseline_evidence_score=0.25,
        baseline_evidence_score_llm=None,
        baseline_answer_score_llm=None,
        diagnosis_cost=1.5,
        attribution_correct=True,
        runtime_branch="fix",
        attribution_result=attribution_result,
        post_repair=repair,
        hard_case_baseline=SimpleNamespace(repair_assessment="failed"),
    )


def _retrieval_suite():
    def metric(case_id: str, retriever_name: str, offset: float):
        return SimpleNamespace(
            case_id=case_id,
            retriever_name=retriever_name,
            recall_at_1=0.1 + offset,
            recall_at_3=0.2 + offset,
            recall_at_5=0.3 + offset,
            recall_at_10=0.4 + offset,
            mrr=0.5 + offset,
            ndcg_at_10=0.6 + offset,
            precision_at_1=0.7 + offset,
            precision_at_3=0.8 + offset,
            precision_at_5=0.9 + offset,
            context_noise_ratio=0.05 + offset,
            answer_accuracy=0.65 + offset,
            answer_f1=0.55 + offset,
        )

    return [
        SimpleNamespace(
            baseline_results=[
                SimpleNamespace(metrics=metric("case-1", "bm25", 0.0)),
                SimpleNamespace(metrics=metric("case-2", "dense", 0.01)),
            ]
        )
    ]


def _write_new_artifact(
    writer_name: str,
    path: Path,
    *,
    judge_client=...,
    rubric_version: str | None = None,
) -> None:
    audits = [_paper_audit("case-1"), _paper_audit("case-2")]
    calls = {
        "attribution": (writers.write_attribution_table, (audits, path)),
        "step_level_metrics": (
            writers.write_step_level_metrics_table,
            (audits, path),
        ),
        "provenance_completeness": (
            writers.write_provenance_completeness_summary,
            (audits, path),
        ),
        "confusion_matrix": (
            writers.write_confusion_matrix_table,
            (audits, path),
        ),
        "post_repair": (writers.write_post_repair_table, (audits, path)),
        "retrieval_metrics": (
            writers.write_retrieval_metrics_table,
            (_retrieval_suite(), path),
        ),
    }
    writer, args = calls[writer_name]
    kwargs = {}
    if judge_client is not ...:
        kwargs.update(
            judge_client=judge_client,
            rubric_version=rubric_version,
        )
    writer(*args, **kwargs)


@pytest.mark.parametrize("writer_name", NEWLY_INSTRUMENTED_WRITERS)
def test_each_paper_writer_populates_judge_provenance_on_every_row(
    tmp_path: Path,
    writer_name: str,
) -> None:
    path = tmp_path / f"{writer_name}.csv"
    judge_client = LLMClient(
        LLMClientConfig(base_url="http://frozen-judge/v1", model="frozen-judge")
    )

    _write_new_artifact(
        writer_name,
        path,
        judge_client=judge_client,
        rubric_version="rubric-test",
    )

    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) >= 2
    assert all(
        row["judge_base_url"] == "http://frozen-judge/v1"
        and row["judge_model"] == "frozen-judge"
        and row["rubric_version"] == "rubric-test"
        for row in rows
    )


BASELINE_SHA256 = {
    "attribution": "03b693e472b0b19b46010fdb2f90ca8d830e09b6fc657615efd4af08e5e2f50c",
    "step_level_metrics": "b1e800f5cc7c49daeeaae2111f18a44e633c743eada9ac1a9b349193b3a4cae6",
    "provenance_completeness": "6e85a08c5d29b755f383c3b69e265bec69346977588f7e81001f607fb69142c2",
    "confusion_matrix": "681736e4b6651f1f4117df84e42681b5ed65704512a2a6222bb6636ab20663de",
    "post_repair": "fdf0293f3b398f25aa76003900b78b9bfabc48b952700fc0f77b87de523885cc",
    "retrieval_metrics": "29f2c489d5a584ced466a4cec879ceb7441e34fecfdb47d9b4a0d2c78c45b135",
}


@pytest.mark.parametrize("writer_name", NEWLY_INSTRUMENTED_WRITERS)
def test_omitting_judge_client_preserves_pre_rollout_bytes(
    tmp_path: Path,
    writer_name: str,
) -> None:
    path = tmp_path / f"{writer_name}.csv"

    _write_new_artifact(writer_name, path)

    assert hashlib.sha256(path.read_bytes()).hexdigest() == BASELINE_SHA256[writer_name]


def test_paper_writers_delegate_judge_values_to_single_provenance_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = {
        "judge_base_url": "patched://judge",
        "judge_model": "patched-model",
        "rubric_version": "patched-rubric",
    }
    monkeypatch.setattr(
        writers,
        "judge_provenance_fields",
        lambda judge_client, *, rubric_version=None: sentinel,
    )

    for writer_name in NEWLY_INSTRUMENTED_WRITERS:
        path = tmp_path / f"{writer_name}.csv"
        _write_new_artifact(
            writer_name,
            path,
            judge_client=object(),
            rubric_version="ignored-by-patch",
        )
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert rows
        assert all(
            {key: row[key] for key in sentinel} == sentinel
            for row in rows
        ), writer_name


def test_lane_env_exports_one_frozen_judge_for_every_answer_lane() -> None:
    script_path = Path(__file__).parents[2] / "run_remaining_experiments.sh"
    script = script_path.read_text(encoding="utf-8")
    match = re.search(r"(?ms)^lane_env\(\).*?^\}", script)
    assert match is not None
    probe = (
        "JUDGE_BASE_URL=http://frozen-judge/v1\n"
        "JUDGE_MODEL=frozen-judge\n"
        "JUDGE_API_KEY=frozen-secret\n"
        f"{match.group(0)}\n"
        "lane_env http://answerer/v1 answerer-model\n"
        "printf '%s\\n' \"$LLM_BASE_URL\" \"$LLM_MODEL\" "
        "\"$LLM_JUDGE_BASE_URL\" \"$LLM_JUDGE_MODEL\" \"$LLM_JUDGE_API_KEY\"\n"
    )

    completed = subprocess.run(
        ["bash", "-c", probe],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.splitlines() == [
        "http://answerer/v1",
        "answerer-model",
        "http://frozen-judge/v1",
        "frozen-judge",
        "frozen-secret",
    ]


def test_low_level_paper_csv_writer_can_stamp_frozen_judge_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "experiment_detail.csv"
    judge_client = LLMClient(
        LLMClientConfig(base_url="http://frozen-judge/v1", model="judge-model")
    )

    writers.write_csv_table(
        path,
        ["case_id", "score"],
        [{"case_id": "c1", "score": "0.75"}],
        judge_client=judge_client,
        rubric_version="rubric-exp",
    )

    with path.open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert row == {
        "case_id": "c1",
        "score": "0.75",
        "judge_base_url": "http://frozen-judge/v1",
        "judge_model": "judge-model",
        "rubric_version": "rubric-exp",
    }
