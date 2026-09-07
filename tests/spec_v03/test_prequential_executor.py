from __future__ import annotations

import json
from pathlib import Path

import pytest

from cmd_audit.spec_v03.backbone_provider import (
    BackboneProviderConfig,
    DeterministicDevelopmentProvider,
    ProviderBudget,
)
from cmd_audit.spec_v03.event_order import compile_event_order
from cmd_audit.spec_v03.prequential_executor import (
    ExecutionConfig,
    PrequentialExperimentExecutor,
    RuntimeOrderManifest,
    StructuralDevelopmentMaturityProvider,
)
from cmd_audit.spec_v03.repair_stream import (
    build_intervention,
    compile_repair_case,
    iter_public_episodes,
)
from cmd_audit.spec_v03.runtime_bundle import deserialize


def _inputs():
    episode = next(iter_public_episodes("halumem", Path("data/external/group_a")))
    cases = tuple(
        compile_repair_case(episode, build_intervention(episode, template, seed=71))
        for template in ("clean", "drop", "explicit_supersede", "untrusted_injection")
    )
    bundles = tuple(deserialize(case.public_mapping()) for case in cases)
    order = RuntimeOrderManifest.from_mapping(
        json.loads(json.dumps(
            compile_event_order(cases, seed=73, schedule="stationary", maturity_delay=2).to_mapping()
        ))
    )
    return bundles, order


def _provider() -> DeterministicDevelopmentProvider:
    return DeterministicDevelopmentProvider(
        BackboneProviderConfig(
            model_id="development-hash-provider",
            snapshot="development-non-model-v1",
            environment="DEVELOPMENT",
            max_output_tokens=64,
            endpoint=None,
        ),
        ProviderBudget(max_requests=10, max_total_tokens=1_000_000),
    )


def test_runtime_bundles_execute_prequentially_with_shared_ecology_and_censoring() -> None:
    bundles, order = _inputs()
    provider = _provider()
    executor = PrequentialExperimentExecutor(
        ExecutionConfig(
            run_id="development-stage5-smoke",
            model_id="development-hash-provider",
            router_name="mix_ghost",
            development=True,
        ),
        provider,
    )

    report = executor.run(bundles, order)

    assert report.status == "DEVELOPMENT_COMPLETE"
    assert len(report.records) == 4
    assert {row.case_id for row in report.records} == {bundle.case_id for bundle in bundles}
    assert sum(row.abstained for row in report.records) == 1
    assert provider.usage.request_count == 3
    assert provider.call_audit and all(row.provider_kind == "deterministic_development_non_model" for row in provider.call_audit)
    assert len(report.report_sha256) == 64
    assert report.censored_selection_ids


def test_prequential_execution_replays_from_seed_and_frozen_order() -> None:
    bundles, order = _inputs()
    config = ExecutionConfig("development-replay", "development-hash-provider", "mix_ghost", True)

    first = PrequentialExperimentExecutor(config, _provider()).run(bundles, order)
    second = PrequentialExperimentExecutor(config, _provider()).run(bundles, order)

    assert first.to_mapping() == second.to_mapping()


def test_confirmatory_execution_rejects_structural_only_maturity_feedback() -> None:
    with pytest.raises(ValueError, match="development-only"):
        PrequentialExperimentExecutor(
            ExecutionConfig("confirmatory", "pinned-model", "mix_ghost", False),
            _provider(),
            maturity_provider=StructuralDevelopmentMaturityProvider(),
        )
