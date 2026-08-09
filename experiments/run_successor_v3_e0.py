"""Legacy fixed-envelope, family-paired successor-v3 E0 baseline.

Retained for historical comparison only.  Its result does not authorize v4
policy updates, species deposition, chain promotion, or deployment mutation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from pathlib import Path
from statistics import fmean
from typing import Any, Mapping, Sequence

from cmd_audit.counterfactual.successor_program_ir import (
    IR_GRAMMAR_VERSION,
    canonical_ast_hash,
    parse_program,
)
from cmd_audit.eval.successor_protocol_freeze import require_validated_f1

PROTOCOL_ID = "route-a-successor-semantic-actionability-v3"
E0_SCHEMA_VERSION = "route-a-successor-v3-e0-result-v1"
_HEX = set("0123456789abcdef")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_hash(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX


def _closed(value: object, keys: frozenset[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError(f"{name} must have exactly {sorted(keys)}")
    return value


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    return value


def _validate_catalog(catalog: Mapping[str, Any]) -> dict[str, str]:
    _closed(
        catalog,
        frozenset(
            {
                "schema_version",
                "protocol_id",
                "grammar_version",
                "baselines",
                "catalog_sha256",
            }
        ),
        "baseline catalog",
    )
    if (
        catalog["protocol_id"] != PROTOCOL_ID
        or catalog["grammar_version"] != IR_GRAMMAR_VERSION
    ):
        raise ValueError("baseline catalog protocol/grammar mismatch")
    payload = {key: value for key, value in catalog.items() if key != "catalog_sha256"}
    if catalog["catalog_sha256"] != canonical_sha256(payload):
        raise ValueError("baseline catalog content hash mismatch")
    rows = catalog["baselines"]
    if not isinstance(rows, list):
        raise ValueError("baseline catalog rows must be a list")
    result: dict[str, str] = {}
    for row in rows:
        row = _closed(
            row,
            frozenset(
                {"baseline_id", "description", "program", "canonical_ast_sha256"}
            ),
            "baseline",
        )
        program_hash = canonical_ast_hash(parse_program(row["program"]))
        if row["canonical_ast_sha256"] != program_hash:
            raise ValueError("baseline AST hash mismatch")
        result[row["baseline_id"]] = program_hash
    if list(result) != sorted(result) or len(result) != len(rows):
        raise ValueError("baseline IDs must be unique and sorted")
    if set(result) != {"B0", "B1", "B2", "B3", "B4"}:
        raise ValueError("strongest baseline catalog must contain exactly B0-B4")
    return result


def _validate_envelope(envelope: Mapping[str, Any]) -> dict[str, str]:
    _closed(
        envelope,
        frozenset(
            {
                "schema_version",
                "protocol_id",
                "grammar_version",
                "adaptive",
                "generation_rule",
                "generation_rule_sha256",
                "candidates",
                "candidate_envelope_sha256",
            }
        ),
        "candidate envelope",
    )
    if (
        envelope["protocol_id"] != PROTOCOL_ID
        or envelope["grammar_version"] != IR_GRAMMAR_VERSION
        or envelope["adaptive"] is not False
    ):
        raise ValueError("envelope is not frozen minimal v3")
    if envelope["generation_rule_sha256"] != canonical_sha256(
        envelope["generation_rule"]
    ):
        raise ValueError("generation rule hash mismatch")
    payload = {
        key: value
        for key, value in envelope.items()
        if key != "candidate_envelope_sha256"
    }
    if envelope["candidate_envelope_sha256"] != canonical_sha256(payload):
        raise ValueError("candidate envelope content hash mismatch")
    rows = envelope["candidates"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("candidate envelope must be non-empty")
    result: dict[str, str] = {}
    ast_hashes: set[str] = set()
    for row in rows:
        row = _closed(
            row,
            frozenset({"candidate_id", "program", "canonical_ast_sha256"}),
            "candidate",
        )
        program_hash = canonical_ast_hash(parse_program(row["program"]))
        if row["canonical_ast_sha256"] != program_hash:
            raise ValueError("candidate AST hash mismatch")
        if program_hash in ast_hashes:
            raise ValueError("duplicate candidate AST")
        result[row["candidate_id"]] = program_hash
        ast_hashes.add(program_hash)
    if list(result) != sorted(result) or len(result) != len(rows):
        raise ValueError("candidate IDs must be unique and sorted")
    return result


def _rows(
    raw: object, *, id_key: str, expected: Mapping[str, str], failures: list[str]
) -> dict[str, dict[str, float]]:
    if not isinstance(raw, list):
        failures.append(f"invalid_{id_key}_rows")
        return {}
    output: dict[str, dict[str, float]] = {}
    for value in raw:
        try:
            row = _closed(
                value,
                frozenset(
                    {id_key, "canonical_ast_sha256", "per_family_scores", "aggregate_score"}
                ),
                f"{id_key} row",
            )
            arm_id = row[id_key]
            scores = row["per_family_scores"]
            if (
                not isinstance(arm_id, str)
                or arm_id in output
                or expected.get(arm_id) != row["canonical_ast_sha256"]
                or not isinstance(scores, Mapping)
                or not scores
                or any(not isinstance(family, str) or not family for family in scores)
                or any(
                    isinstance(score, bool)
                    or not isinstance(score, (int, float))
                    or not math.isfinite(score)
                    for score in scores.values()
                )
                or isinstance(row["aggregate_score"], bool)
                or not isinstance(row["aggregate_score"], (int, float))
                or not math.isfinite(row["aggregate_score"])
                or not math.isclose(
                    row["aggregate_score"], fmean(scores.values()), abs_tol=1e-12
                )
            ):
                raise ValueError("invalid scored row")
            output[arm_id] = {key: float(score) for key, score in scores.items()}
        except (ValueError, TypeError):
            failures.append(f"invalid_{id_key}_row")
    if set(output) != set(expected):
        failures.append(f"incomplete_{id_key}_rows")
    return output


def _percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _bootstrap_ci(
    differences: Sequence[float], *, confidence: float, iterations: int, seed: int
) -> tuple[float, float]:
    rng = random.Random(seed)
    count = len(differences)
    samples = [
        fmean(differences[rng.randrange(count)] for _ in range(count))
        for _ in range(iterations)
    ]
    tail = (1.0 - confidence) / 2.0
    return _percentile(samples, tail), _percentile(samples, 1.0 - tail)


def evaluate_e0(
    *,
    protocol_manifest_sha256: str,
    registered_baseline_catalog_sha256: str,
    upstream_gate_sha256: str,
    graph_manifest_sha256: str,
    search_split_sha256: str,
    access_ledger_head_before: str,
    e0_policy: Mapping[str, Any],
    upstream: Mapping[str, Any],
    baseline_catalog: Mapping[str, Any],
    envelope: Mapping[str, Any],
    results: Mapping[str, Any],
) -> dict[str, Any]:
    failures: list[str] = []
    if not all(
        _is_hash(value)
        for value in (
            protocol_manifest_sha256,
            registered_baseline_catalog_sha256,
            upstream_gate_sha256,
            graph_manifest_sha256,
            search_split_sha256,
            access_ledger_head_before,
        )
    ):
        failures.append("invalid_registered_hash")
    upstream_payload = {
        key: value for key, value in upstream.items() if key != "report_sha256"
    }
    if (
        upstream.get("protocol_version") != PROTOCOL_ID
        or upstream.get("decision") != "GO"
        or upstream.get("headroom_authorized") is not True
        or upstream.get("report_sha256") != upstream_gate_sha256
        or canonical_sha256(upstream_payload) != upstream_gate_sha256
    ):
        failures.append("upstream_not_go")
    try:
        baseline_hashes = _validate_catalog(baseline_catalog)
        candidate_hashes = _validate_envelope(envelope)
    except (ValueError, TypeError) as error:
        failures.append(f"frozen_artifact:{type(error).__name__}")
        baseline_hashes, candidate_hashes = {}, {}
    if baseline_catalog.get("catalog_sha256") != registered_baseline_catalog_sha256:
        failures.append("baseline_catalog_f1_hash_mismatch")
    result_keys = frozenset(
        {
            "schema_version",
            "protocol_id",
            "protocol_freeze_sha256",
            "upstream_gate_sha256",
            "baseline_catalog_sha256",
            "candidate_envelope_sha256",
            "graph_manifest_sha256",
            "search_split_sha256",
            "access_ledger_head_before",
            "model_calls",
            "gold_visible_to_policy",
            "policy_visible_intermediate_results",
            "baseline_rows",
            "candidate_rows",
        }
    )
    try:
        _closed(results, result_keys, "E0 scored input")
    except ValueError:
        failures.append("invalid_results_schema")
    expected_bindings = {
        "protocol_id": PROTOCOL_ID,
        "protocol_freeze_sha256": protocol_manifest_sha256,
        "upstream_gate_sha256": upstream_gate_sha256,
        "baseline_catalog_sha256": baseline_catalog.get("catalog_sha256"),
        "candidate_envelope_sha256": envelope.get("candidate_envelope_sha256"),
        "graph_manifest_sha256": graph_manifest_sha256,
        "search_split_sha256": search_split_sha256,
        "access_ledger_head_before": access_ledger_head_before,
        "model_calls": 0,
        "gold_visible_to_policy": False,
        "policy_visible_intermediate_results": False,
    }
    for field, expected in expected_bindings.items():
        if results.get(field) != expected:
            failures.append(f"binding:{field}")
    baselines = _rows(
        results.get("baseline_rows"),
        id_key="baseline_id",
        expected=baseline_hashes,
        failures=failures,
    )
    candidates = _rows(
        results.get("candidate_rows"),
        id_key="candidate_id",
        expected=candidate_hashes,
        failures=failures,
    )
    family_sets = [set(scores) for scores in (*baselines.values(), *candidates.values())]
    families = family_sets[0] if family_sets else set()
    family_pairing_ok = bool(families) and all(
        value == families for value in family_sets
    )
    if not family_pairing_ok:
        failures.append("family_pairing_mismatch")
    policy_fields = frozenset(
        {
            "candidate_envelope_path",
            "candidate_envelope_sha256",
            "strict_gain_min",
            "confidence_level",
            "bootstrap_iterations",
            "bootstrap_seed",
            "tie_epsilon",
            "tie_policy",
            "missing_policy",
            "nonfinite_policy",
            "score_metric",
            "family_aggregation",
        }
    )
    try:
        policy = _closed(e0_policy, policy_fields, "E0 policy")
        if (
            policy["tie_policy"] != "STOP"
            or policy["missing_policy"] != "STOP"
            or policy["nonfinite_policy"] != "STOP"
            or policy["family_aggregation"] != "macro_mean"
            or policy["candidate_envelope_sha256"]
            != envelope.get("candidate_envelope_sha256")
            or not 0 < policy["confidence_level"] < 1
            or isinstance(policy["confidence_level"], bool)
            or not isinstance(policy["bootstrap_iterations"], int)
            or isinstance(policy["bootstrap_iterations"], bool)
            or policy["bootstrap_iterations"] <= 0
            or not isinstance(policy["bootstrap_seed"], int)
            or isinstance(policy["bootstrap_seed"], bool)
            or not isinstance(policy["tie_epsilon"], (int, float))
            or isinstance(policy["tie_epsilon"], bool)
            or policy["tie_epsilon"] < 0
            or not isinstance(policy["strict_gain_min"], (int, float))
            or isinstance(policy["strict_gain_min"], bool)
            or not math.isfinite(policy["strict_gain_min"])
            or not isinstance(policy["score_metric"], str)
            or not policy["score_metric"]
        ):
            raise ValueError("invalid policy")
    except (ValueError, TypeError):
        failures.append("invalid_e0_policy")
        policy = {
            "strict_gain_min": math.inf,
            "confidence_level": 0.95,
            "bootstrap_iterations": 1,
            "bootstrap_seed": 0,
            "tie_epsilon": math.inf,
        }
    baseline_aggregates = {
        arm_id: fmean(scores.values()) for arm_id, scores in baselines.items()
    }
    candidate_aggregates = {
        arm_id: fmean(scores.values()) for arm_id, scores in candidates.items()
    }
    best_baseline_ids: list[str] = []
    best_candidate_id: str | None = None
    strict_gain: float | None = None
    confidence_interval: list[float] | None = None
    if baseline_aggregates and candidate_aggregates and family_pairing_ok:
        best_baseline_score = max(baseline_aggregates.values())
        best_baseline_ids = sorted(
            arm_id
            for arm_id, score in baseline_aggregates.items()
            if math.isclose(score, best_baseline_score, abs_tol=1e-12)
        )
        ordered_candidates = sorted(
            candidate_aggregates, key=candidate_aggregates.get, reverse=True
        )
        leader = ordered_candidates[0]
        runner_up = (
            candidate_aggregates[ordered_candidates[1]]
            if len(ordered_candidates) > 1
            else -math.inf
        )
        if candidate_aggregates[leader] - runner_up <= policy["tie_epsilon"]:
            failures.append("candidate_tie_stop")
        else:
            best_candidate_id = leader
            differences = [
                candidates[leader][family]
                - max(baselines[baseline][family] for baseline in best_baseline_ids)
                for family in sorted(families)
            ]
            strict_gain = fmean(differences)
            confidence_interval = list(
                _bootstrap_ci(
                    differences,
                    confidence=policy["confidence_level"],
                    iterations=policy["bootstrap_iterations"],
                    seed=policy["bootstrap_seed"],
                )
            )
            if confidence_interval[0] <= policy["strict_gain_min"]:
                failures.append("strict_headroom_not_met")
    else:
        failures.append("missing_paired_scores")
    passed = not failures
    report: dict[str, Any] = {
        "schema_version": E0_SCHEMA_VERSION,
        "protocol_id": PROTOCOL_ID,
        "protocol_freeze_sha256": protocol_manifest_sha256,
        "upstream_gate_sha256": upstream_gate_sha256,
        "baseline_catalog_sha256": baseline_catalog.get("catalog_sha256"),
        "candidate_envelope_sha256": envelope.get("candidate_envelope_sha256"),
        "graph_manifest_sha256": graph_manifest_sha256,
        "search_split_sha256": search_split_sha256,
        "access_ledger_head_before": access_ledger_head_before,
        "baseline_rows": results.get("baseline_rows", []),
        "candidate_rows": results.get("candidate_rows", []),
        "best_baseline_ids": best_baseline_ids,
        "best_candidate_id": best_candidate_id,
        "strict_gain": strict_gain,
        "confidence_interval": confidence_interval,
        "decision": "GO" if passed else "STOP",
        "adaptive_synthesis_authorized": passed,
        "query_read_authorized": False,
        "failures": failures,
    }
    report["e0_result_sha256"] = canonical_sha256(report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol-freeze", type=Path, required=True)
    parser.add_argument("--protocol-validation", type=Path, required=True)
    parser.add_argument("--upstream-gates", type=Path, required=True)
    parser.add_argument("--baseline-catalog", type=Path, required=True)
    parser.add_argument("--envelope", type=Path, required=True)
    parser.add_argument("--graph-manifest", type=Path, required=True)
    parser.add_argument("--search-split", type=Path, required=True)
    parser.add_argument("--access-ledger", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = _load(args.protocol_freeze)
        validation = _load(args.protocol_validation)
        manifest_hash = require_validated_f1(manifest, validation)
        upstream = _load(args.upstream_gates)
        catalog = _load(args.baseline_catalog)
        envelope = _load(args.envelope)
        report = evaluate_e0(
            protocol_manifest_sha256=manifest_hash,
            registered_baseline_catalog_sha256=manifest["gates"]["g3"]["baseline_catalog_sha256"],
            upstream_gate_sha256=upstream.get("report_sha256", ""),
            graph_manifest_sha256=file_sha256(args.graph_manifest),
            search_split_sha256=file_sha256(args.search_split),
            access_ledger_head_before=file_sha256(args.access_ledger),
            e0_policy=manifest["gates"]["e0"],
            upstream=upstream,
            baseline_catalog=catalog,
            envelope=envelope,
            results=_load(args.results),
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        report = {
            "schema_version": E0_SCHEMA_VERSION,
            "protocol_id": PROTOCOL_ID,
            "decision": "STOP",
            "adaptive_synthesis_authorized": False,
            "query_read_authorized": False,
            "failures": [f"input_error:{type(error).__name__}"],
        }
        report["e0_result_sha256"] = canonical_sha256(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0 if report["decision"] == "GO" else 2


if __name__ == "__main__":
    raise SystemExit(main())
