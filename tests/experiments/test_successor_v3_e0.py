from cmd_audit.counterfactual.successor_program_ir import (
    IR_GRAMMAR_VERSION,
    canonical_ast_hash,
    parse_program,
)
from experiments.run_successor_v3_e0 import (
    PROTOCOL_ID,
    canonical_sha256,
    evaluate_e0,
)

H = {
    "protocol": "a" * 64,
    "gates": "b" * 64,
    "graph": "c" * 64,
    "search": "d" * 64,
    "ledger": "e" * 64,
}


def _program(predicate: str | None, action: str | None) -> dict:
    if predicate is None:
        return {"node": "sequence", "body": []}
    return {
        "node": "if",
        "predicate": {"kind": predicate},
        "action": {"kind": action},
    }


def _hashed(program: dict) -> str:
    return canonical_ast_hash(parse_program(program))


def catalog() -> dict:
    specs = [
        ("B0", "identity", _program(None, None)),
        ("B1", "annotate", _program("divergent_pair_member", "annotate_conflict")),
        ("B2", "demote", _program("superseded_item", "demote")),
        ("B3", "suppress", _program("superseded_item", "suppress")),
        ("B4", "replace", _program("superseded_item", "replace")),
    ]
    value = {
        "schema_version": "baseline-catalog-v1",
        "protocol_id": PROTOCOL_ID,
        "grammar_version": IR_GRAMMAR_VERSION,
        "baselines": [
            {
                "baseline_id": arm_id,
                "description": description,
                "program": program,
                "canonical_ast_sha256": _hashed(program),
            }
            for arm_id, description, program in specs
        ],
    }
    value["catalog_sha256"] = canonical_sha256(value)
    return value


def envelope() -> dict:
    specs = [
        ("C0", _program("divergent_pair_member", "verify")),
        ("C1", {
            "node": "sequence",
            "body": [
                _program("divergent_pair_member", "annotate_conflict"),
                _program("superseded_item", "demote"),
            ],
        }),
    ]
    value = {
        "schema_version": "candidate-envelope-v1",
        "protocol_id": PROTOCOL_ID,
        "grammar_version": IR_GRAMMAR_VERSION,
        "adaptive": False,
        "generation_rule": "exact-minimal-v3-enumeration-v1",
        "generation_rule_sha256": canonical_sha256("exact-minimal-v3-enumeration-v1"),
        "candidates": [
            {"candidate_id": arm_id, "program": program, "canonical_ast_sha256": _hashed(program)}
            for arm_id, program in specs
        ],
    }
    value["candidate_envelope_sha256"] = canonical_sha256(value)
    return value


def policy() -> dict:
    return {
        "candidate_envelope_path": "artifacts/envelope.json",
        "candidate_envelope_sha256": envelope()["candidate_envelope_sha256"],
        "score_metric": "offline_state_success",
        "family_aggregation": "macro_mean",
        "strict_gain_min": 0.1,
        "confidence_level": 0.95,
        "bootstrap_iterations": 200,
        "bootstrap_seed": 7,
        "tie_epsilon": 0.001,
        "tie_policy": "STOP",
        "missing_policy": "STOP",
        "nonfinite_policy": "STOP",
    }


def upstream() -> dict:
    value = {
        "protocol_version": PROTOCOL_ID,
        "decision": "GO",
        "headroom_authorized": True,
    }
    value["report_sha256"] = canonical_sha256(value)
    return value


def _row(id_key: str, arm_id: str, ast_hash: str, score: float) -> dict:
    return {
        id_key: arm_id,
        "canonical_ast_sha256": ast_hash,
        "per_family_scores": {"f1": score, "f2": score, "f3": score},
        "aggregate_score": score,
    }


def results() -> dict:
    base = catalog()
    candidates = envelope()
    baseline_rows = [
        _row("baseline_id", row["baseline_id"], row["canonical_ast_sha256"], 0.6 if row["baseline_id"] == "B3" else 0.5)
        for row in base["baselines"]
    ]
    candidate_rows = [
        _row("candidate_id", row["candidate_id"], row["canonical_ast_sha256"], 0.9 if row["candidate_id"] == "C1" else 0.7)
        for row in candidates["candidates"]
    ]
    return {
        "schema_version": "e0-scored-input-v1",
        "protocol_id": PROTOCOL_ID,
        "protocol_freeze_sha256": H["protocol"],
        "upstream_gate_sha256": H["gates"],
        "baseline_catalog_sha256": base["catalog_sha256"],
        "candidate_envelope_sha256": candidates["candidate_envelope_sha256"],
        "graph_manifest_sha256": H["graph"],
        "search_split_sha256": H["search"],
        "access_ledger_head_before": H["ledger"],
        "model_calls": 0,
        "gold_visible_to_policy": False,
        "policy_visible_intermediate_results": False,
        "baseline_rows": baseline_rows,
        "candidate_rows": candidate_rows,
    }


def run(*, gates: dict | None = None, scored: dict | None = None) -> dict:
    gate_bundle = gates or upstream()
    H["gates"] = gate_bundle.get("report_sha256", H["gates"])
    scored_input = scored or results()
    scored_input["upstream_gate_sha256"] = H["gates"]
    return evaluate_e0(
        protocol_manifest_sha256=H["protocol"],
        registered_baseline_catalog_sha256=catalog()["catalog_sha256"],
        upstream_gate_sha256=H["gates"],
        graph_manifest_sha256=H["graph"],
        search_split_sha256=H["search"],
        access_ledger_head_before=H["ledger"],
        e0_policy=policy(),
        upstream=gate_bundle,
        baseline_catalog=catalog(),
        envelope=envelope(),
        results=scored_input,
    )


def test_e0_go_requires_paired_family_ci_over_strongest_baseline() -> None:
    report = run()
    assert report["decision"] == "GO"
    assert report["best_baseline_ids"] == ["B3"]
    assert report["best_candidate_id"] == "C1"
    assert report["confidence_interval"][0] > 0.1
    assert report["query_read_authorized"] is False


def test_e0_upstream_stop_blocks_positive_scores() -> None:
    gates = upstream()
    gates["decision"] = "REFUSE"
    report = run(gates=gates)
    assert report["decision"] == "STOP"
    assert "upstream_not_go" in report["failures"]


def test_e0_missing_family_or_aggregate_tamper_is_stop() -> None:
    scored = results()
    scored["candidate_rows"][0]["per_family_scores"].pop("f3")
    scored["candidate_rows"][1]["aggregate_score"] = 99.0
    report = run(scored=scored)
    assert report["decision"] == "STOP"
    assert "family_pairing_mismatch" in report["failures"]
    assert "invalid_candidate_id_row" in report["failures"]


def test_e0_candidate_tie_is_stop_without_id_tiebreak() -> None:
    scored = results()
    for row in scored["candidate_rows"]:
        row["per_family_scores"] = {"f1": 0.9, "f2": 0.9, "f3": 0.9}
        row["aggregate_score"] = 0.9
    report = run(scored=scored)
    assert report["decision"] == "STOP"
    assert "candidate_tie_stop" in report["failures"]


def test_e0_hash_drift_is_stop() -> None:
    scored = results()
    scored["graph_manifest_sha256"] = "0" * 64
    report = run(scored=scored)
    assert report["decision"] == "STOP"
    assert "binding:graph_manifest_sha256" in report["failures"]
