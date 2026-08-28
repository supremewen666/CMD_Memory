"""Run development or vLLM-backed Stage 5-9 experiments over frozen runtime inputs."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cmd_audit.spec_v03.backbone_provider import (
    BackboneProviderConfig,
    DeterministicDevelopmentProvider,
    OpenAICompatibleBackboneProvider,
    ProviderBudget,
)
from cmd_audit.spec_v03.governance_system_executor import FirstLegalProposalPolicy
from cmd_audit.spec_v03.industry_adapters import ResourceUsage
from cmd_audit.spec_v03.prequential_executor import RuntimeOrderManifest
from cmd_audit.spec_v03.runtime_bundle import load_runtime_cases
from cmd_audit.spec_v03.runtime_pipeline import RuntimePipeline
from cmd_audit.spec_v03.skill_discovery_provider import (
    DiscoveryBudget,
    OpenAICompatibleSkillDiscoveryProvider,
    SkillDiscoveryConfig,
    load_skill_library,
    serialize_skill_library,
)
from cmd_audit.spec_v03.stage59_runner import Stage59Capabilities, Stage59Config, Stage59Runner
from cmd_audit.spec_v03.stage5_executor import (
    StructuralDevelopmentStage5FeedbackProvider,
    load_router_snapshot_bundle,
    router_snapshot_bundle_from_stage5_result,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-cases", type=Path, required=True)
    parser.add_argument("--event-order", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--model-id", default="development-non-model")
    parser.add_argument("--seed", type=int, default=20260827)
    parser.add_argument("--track", choices=("controlled_a1", "controlled_a2"), default="controlled_a1")
    parser.add_argument(
        "--stage", action="append", dest="stages",
        choices=("stage5", "stage6", "stage7", "stage8a", "stage8b", "stage9"),
        help="Run only this stage; repeat for multiple stages. Defaults to all stages.",
    )
    parser.add_argument(
        "--backbone-provider", choices=("none", "development-hash", "vllm"), default="none",
        help="Stage 5 backbone. vllm performs real OpenAI-compatible model calls.",
    )
    parser.add_argument("--endpoint", help="OpenAI-compatible base endpoint, for example http://127.0.0.1:8001/v1")
    parser.add_argument("--model-snapshot", help="Externally pinned model-manifest SHA-256")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY", help="Environment variable containing an optional API key")
    parser.add_argument("--max-requests", type=int, default=1000)
    parser.add_argument("--max-total-tokens", type=int, default=20_000_000)
    parser.add_argument("--max-output-tokens", type=int, default=256)
    parser.add_argument("--max-context-events", type=int, default=32)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument(
        "--feedback-provider", choices=("none", "development-structural"), default="none",
        help="Selected-action delayed feedback. The structural provider is development-only.",
    )
    parser.add_argument(
        "--candidate-provider", choices=("none", "vllm-discovery"), default="none",
        help="Stage 6 typed skill discovery provider.",
    )
    parser.add_argument("--skill-library", type=Path, help="Frozen discovered skill library to load")
    parser.add_argument("--skill-library-output", type=Path, help="Write discovered typed skills and a call-audit sidecar")
    parser.add_argument(
        "--initial-router-snapshot", type=Path,
        help="Stage 5 router snapshot bundle exported by --router-snapshot-output.",
    )
    parser.add_argument(
        "--router-snapshot-output", type=Path,
        help="Write portable mix_ghost/ghost_hierarchy posterior snapshots after Stage 5.",
    )
    parser.add_argument(
        "--adaptation-prefix-ratio", type=float, default=0.0,
        help="Initial target-order fraction used only to update GHOST posteriors; the suffix is scored.",
    )
    parser.add_argument(
        "--development-first-legal",
        action="store_true",
        help="Enable the deterministic non-model proposal policy for wiring validation.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    stages = tuple(args.stages) if args.stages else (
        "stage5", "stage6", "stage7", "stage8a", "stage8b", "stage9",
    )
    if (args.initial_router_snapshot is not None or args.router_snapshot_output is not None or args.adaptation_prefix_ratio != 0.0) and "stage5" not in stages:
        raise ValueError("router snapshot import/export and prefix adaptation require --stage stage5")
    bundles = load_runtime_cases(args.runtime_cases)
    raw_order = json.loads(args.event_order.read_text(encoding="utf-8"))
    if not isinstance(raw_order, dict):
        raise ValueError("event order must contain one JSON object")
    order = RuntimeOrderManifest.from_mapping(raw_order)
    proposal = FirstLegalProposalPolicy() if args.development_first_legal else None
    provider_budget = ProviderBudget(
        max_requests=args.max_requests,
        max_total_tokens=args.max_total_tokens,
    )
    backbone = None
    if args.backbone_provider == "development-hash":
        backbone = DeterministicDevelopmentProvider(
            BackboneProviderConfig(
                model_id=args.model_id,
                snapshot=args.model_snapshot or "development-hash-v1",
                environment="DEVELOPMENT",
                endpoint=None,
                max_output_tokens=args.max_output_tokens,
                max_context_events=args.max_context_events,
                temperature=args.temperature,
            ),
            provider_budget,
        )
    elif args.backbone_provider == "vllm":
        if not args.endpoint or not args.model_snapshot:
            raise ValueError("vllm backbone requires --endpoint and --model-snapshot")
        backbone = OpenAICompatibleBackboneProvider(
            BackboneProviderConfig(
                model_id=args.model_id,
                snapshot=args.model_snapshot,
                snapshot_binding="external_manifest",
                environment="PRODUCTION",
                endpoint=args.endpoint,
                api_key=os.environ.get(args.api_key_env),
                max_output_tokens=args.max_output_tokens,
                max_context_events=args.max_context_events,
                temperature=args.temperature,
            ),
            provider_budget,
        )
    feedback = None
    if args.feedback_provider == "development-structural":
        feedback = StructuralDevelopmentStage5FeedbackProvider(model_id=args.model_id)
    if args.backbone_provider != "none" and feedback is None:
        raise ValueError("a configured backbone requires --feedback-provider")
    candidate_provider = None
    if args.candidate_provider == "vllm-discovery":
        if not args.endpoint or not args.model_snapshot:
            raise ValueError("vllm discovery requires --endpoint and --model-snapshot")
        candidate_provider = OpenAICompatibleSkillDiscoveryProvider(
            SkillDiscoveryConfig(
                model_id=args.model_id,
                snapshot=args.model_snapshot,
                endpoint=args.endpoint,
                max_output_tokens=args.max_output_tokens,
                temperature=args.temperature,
                api_key=os.environ.get(args.api_key_env),
                snapshot_binding="external_manifest",
            ),
            DiscoveryBudget(args.max_requests, args.max_total_tokens),
        )
        if args.skill_library_output is None:
            raise ValueError("vllm discovery requires --skill-library-output")
    loaded_skills = ()
    if args.skill_library is not None:
        loaded_skills = load_skill_library(args.skill_library).skills
    # Every imported discovery library augments the typed seed catalog, giving
    # Stage 5 both a safe fallback and sibling revisions to route among.
    source_library = ()
    if loaded_skills:
        combined = RuntimePipeline().frozen_skill_library + tuple(loaded_skills)
        source_library = tuple({skill.skill_revision_id: skill for skill in combined}.values())
    initial_router_snapshots = None
    if args.initial_router_snapshot is not None:
        raw_snapshot = json.loads(args.initial_router_snapshot.read_text(encoding="utf-8"))
        if not isinstance(raw_snapshot, dict):
            raise ValueError("initial router snapshot must contain one JSON object")
        initial_router_snapshots = load_router_snapshot_bundle(raw_snapshot)
    report = Stage59Runner(
        Stage59Config(
            args.run_id, args.model_id, args.seed, track=args.track,
            stages=stages,
            development_non_model=args.backbone_provider != "vllm",
        ),
        Stage59Capabilities(
            backbone_provider=backbone,
            feedback_provider=feedback,
            candidate_provider=candidate_provider,
            proposal_provider=proposal,
            frozen_library=RuntimePipeline().frozen_skill_library if candidate_provider is not None else (),
            source_library=source_library,
            initial_router_snapshots=initial_router_snapshots,
            adaptation_prefix_ratio=args.adaptation_prefix_ratio,
        ),
    ).run(
        bundles,
        order,
        system_budget=ResourceUsage(0, 0, 0, 0.0, 0.0),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report.to_mapping(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.router_snapshot_output is not None:
        stage5_result = report.results.get("stage5")
        if not isinstance(stage5_result, dict):
            raise ValueError("Stage 5 did not produce a router snapshot report")
        router_bundle = router_snapshot_bundle_from_stage5_result(
            stage5_result, source_model_id=args.model_id,
        )
        args.router_snapshot_output.parent.mkdir(parents=True, exist_ok=True)
        args.router_snapshot_output.write_text(
            json.dumps(router_bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
    if candidate_provider is not None and args.skill_library_output is not None:
        args.skill_library_output.parent.mkdir(parents=True, exist_ok=True)
        library = serialize_skill_library(candidate_provider.discovered_skills)
        args.skill_library_output.write_text(json.dumps(library, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        audit_path = args.skill_library_output.with_suffix(args.skill_library_output.suffix + ".audit.json")
        audit_path.write_text(json.dumps({
            "model_id": args.model_id,
            "model_snapshot": args.model_snapshot,
            "usage": asdict(candidate_provider.usage),
            "call_audit": [asdict(row) for row in candidate_provider.call_audit],
            "library_sha256": library["library_sha256"],
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    uses_model = args.backbone_provider == "vllm" or args.candidate_provider == "vllm-discovery"
    status = "DEVELOPMENT_MODEL_PILOT" if uses_model else "DEVELOPMENT_WIRING_NO_MODEL_RESULTS"
    print(f"[RESULT] status={status}")
    print(f"[RESULT] report_sha256={report.report_sha256}")
    if candidate_provider is not None and args.skill_library_output is not None:
        print(f"[RESULT] skill_library={args.skill_library_output}")
    if args.router_snapshot_output is not None:
        print(f"[RESULT] router_snapshot={args.router_snapshot_output}")
    print(f"[RESULT] output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
