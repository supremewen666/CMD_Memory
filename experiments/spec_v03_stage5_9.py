"""Run the no-network Stage 5-9 wiring surface over frozen runtime inputs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cmd_audit.spec_v03.governance_system_executor import FirstLegalProposalPolicy
from cmd_audit.spec_v03.industry_adapters import ResourceUsage
from cmd_audit.spec_v03.prequential_executor import RuntimeOrderManifest
from cmd_audit.spec_v03.runtime_bundle import load_runtime_cases
from cmd_audit.spec_v03.stage59_runner import Stage59Capabilities, Stage59Config, Stage59Runner


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
        "--development-first-legal",
        action="store_true",
        help="Enable the deterministic non-model proposal policy for wiring validation.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    bundles = load_runtime_cases(args.runtime_cases)
    raw_order = json.loads(args.event_order.read_text(encoding="utf-8"))
    if not isinstance(raw_order, dict):
        raise ValueError("event order must contain one JSON object")
    order = RuntimeOrderManifest.from_mapping(raw_order)
    proposal = FirstLegalProposalPolicy() if args.development_first_legal else None
    report = Stage59Runner(
        Stage59Config(
            args.run_id, args.model_id, args.seed, track=args.track,
            development_non_model=args.development_first_legal,
        ),
        Stage59Capabilities(proposal_provider=proposal),
    ).run(
        bundles,
        order,
        system_budget=ResourceUsage(0, 0, 0, 0.0, 0.0),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report.to_mapping(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[RESULT] status=DEVELOPMENT_WIRING_NO_MODEL_RESULTS")
    print(f"[RESULT] report_sha256={report.report_sha256}")
    print(f"[RESULT] output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
