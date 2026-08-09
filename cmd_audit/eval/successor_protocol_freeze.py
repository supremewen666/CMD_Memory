"""Closed F0 dataset and F1 protocol validator for successor-v3.

This module is intentionally successor-only.  It verifies integrity and
preregistration; it never grants a sealed-split read or a production mutation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping, Sequence

from cmd_audit.counterfactual.item_ordering import (
    REGISTERED_SOURCE_SEMANTICS as REGISTERED_ORDERING_SEMANTICS,
)
from cmd_audit.counterfactual.successor_program_ir import (
    IR_GRAMMAR_VERSION,
    REGISTERED_BOUNDS,
    ProgramBoundsError,
    ProgramParseError,
    canonical_ast_hash,
    canonicalize,
    parse_program,
    program_to_mapping,
)
from cmd_audit.eval.successor_query_ledger import QUERY_LEDGER_GENESIS_SHA256

FREEZE_SCHEMA_VERSION = "route-a-successor-v3-freeze-schema-v2"
DATASET_MANIFEST_SCHEMA_VERSION = "route-a-successor-v3-dataset-manifest-v2"
BASELINE_CATALOG_SCHEMA_VERSION = "route-a-successor-v3-baseline-catalog-v1"
E0_ENVELOPE_SCHEMA_VERSION = "route-a-successor-v3-e0-envelope-v1"
VALIDATOR_VERSION = "route-a-successor-v3-freeze-validator-v2"
PROTOCOL_ID = "route-a-successor-semantic-actionability-v3"
GENERATION_RULE = "explicit_frozen_ast_list_v1"
REGISTERED_SCORE_METRIC = "state_fitness"
REGISTERED_FAMILY_AGGREGATION = "macro_mean_by_family"
# Compatibility export for early callers; the single source of truth is the
# canonical schema descriptor in successor_query_ledger.
GLOBAL_LEDGER_GENESIS_SHA256 = QUERY_LEDGER_GENESIS_SHA256

SPLITS = ("pilot", "cal", "dev", "search", "query", "deploy_canary")
SEALED_SPLITS = frozenset({"search", "query"})
PURPOSES = frozenset({
    "instrument_development", "calibration", "audit", "e0", "synthesis",
    "confirmation",
})
OPERATIONS = frozenset({"read", "list", "aggregate", "export", "model_input"})
MINIMAL_LEAVES = ("divergent_pair_member", "superseded_item")
MINIMAL_EFFECTS = (
    "keep", "preserve", "annotate_conflict", "abstain", "verify",
    "demote", "suppress", "replace",
)
REGISTERED_SOURCE_SEMANTICS: Mapping[str, Mapping[str, object]] = {
    "observed_at": {
        "semantic": REGISTERED_ORDERING_SEMANTICS["observed_at"],
        "comparable_domain_field": "observed_at_domain",
        "value_type": "rfc3339_timezone_aware",
        "requires_equal_domain": True,
    },
    "event_sequence": {
        "semantic": REGISTERED_ORDERING_SEMANTICS["event_sequence"],
        "comparable_domain_field": "event_stream_id",
        "value_type": "nonnegative_integer",
        "requires_equal_domain": True,
    },
    "source_priority": {
        "semantic": REGISTERED_ORDERING_SEMANTICS["source_priority"],
        "comparable_domain_field": "source_priority_domain",
        "value_type": "integer",
        "requires_equal_domain": True,
    },
}
COMMAND_LOCATORS: Mapping[str, Mapping[str, object]] = {
    "e0": {
        "script": "experiments/run_successor_v3_e0.py",
        "entrypoint": "main",
        "required_flags": [
            "--protocol-freeze", "--protocol-validation", "--upstream-gates",
            "--baseline-catalog", "--envelope", "--graph-manifest",
            "--search-split", "--access-ledger", "--results", "--output",
        ],
        "network_policy": "deny",
    },
    "synthesis": {
        "script": "experiments/run_successor_v3_synthesis.py",
        "entrypoint": "main",
        "required_flags": [
            "--f1-validation", "--f1-validation-sha256", "--f1-manifest-sha256",
            "--gate-bundle", "--e0-result", "--plan", "--candidates", "--output",
        ],
        "network_policy": "deny",
    },
    "confirmation": {
        "script": "experiments/run_successor_v3_confirmation.py",
        "entrypoint": "main",
        "required_flags": [
            "--f1-validation", "--f1-validation-sha256", "--f1-manifest-sha256",
            "--gate-bundle", "--e0-result", "--winner", "--query",
            "--query-input-sha256", "--family-block-sha256", "--ledger", "--output",
        ],
        "network_policy": "deny",
    },
}

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_RE = re.compile(r"(?:<[^>]+>|\b(?:tbd|placeholder|nan|inf|infinity)\b)", re.I)


@dataclass(frozen=True)
class FreezeValidation:
    valid: bool
    reasons: tuple[str, ...]
    validator_version: str
    manifest_sha256: str
    recomputed_hashes: Mapping[str, str]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def canonical_json_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def canonical_manifest_sha256(manifest: Mapping[str, Any]) -> str:
    return canonical_json_sha256(manifest)


def canonical_id_hash(values: Sequence[str]) -> str:
    return canonical_json_sha256(sorted(set(values)))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_validated_f1(
    manifest: Mapping[str, Any], validation: Mapping[str, Any]
) -> str:
    """Return the content-bound F1 hash or fail closed for downstream gates."""

    manifest_hash = canonical_manifest_sha256(manifest)
    if (
        manifest.get("schema_version") != FREEZE_SCHEMA_VERSION
        or manifest.get("protocol_id") != PROTOCOL_ID
        or manifest.get("freeze_stage") != "F1"
    ):
        raise ValueError("successor command requires the exact F1 v2 manifest")
    if validation.get("valid") is not True or validation.get("reasons") not in ([], ()):
        raise ValueError("successor command requires a passing validation artifact")
    if validation.get("validator_version") != VALIDATOR_VERSION:
        raise ValueError("successor validation version mismatch")
    if validation.get("manifest_sha256") != manifest_hash:
        raise ValueError("successor validation is detached from the F1 manifest")
    return manifest_hash


def _reason(reasons: list[str], value: str) -> None:
    if value not in reasons:
        reasons.append(value)


def _closed(
    value: object, keys: frozenset[str], path: str, reasons: list[str]
) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        _reason(reasons, f"invalid_object:{path}")
        return None
    for key in sorted(set(value) - keys):
        prefix = "unknown_top_level_key" if not path else "unknown_key"
        _reason(reasons, f"{prefix}:{path + '.' if path else ''}{key}")
    for key in sorted(keys - set(value)):
        _reason(reasons, f"missing_key:{path + '.' if path else ''}{key}")
    return value


def _walk_forbidden(value: object, path: str, reasons: list[str]) -> None:
    if value is None or isinstance(value, float) and not math.isfinite(value):
        _reason(reasons, f"forbidden_value:{path}")
    elif isinstance(value, str) and (not value.strip() or _FORBIDDEN_RE.search(value)):
        _reason(reasons, f"forbidden_value:{path}")
    elif isinstance(value, Mapping):
        for key, nested in value.items():
            _walk_forbidden(nested, f"{path}.{key}" if path else str(key), reasons)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, nested in enumerate(value):
            _walk_forbidden(nested, f"{path}[{index}]", reasons)


def _rfc3339(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and (
        value.endswith("Z") or bool(re.search(r"[+-]\d\d:\d\d$", value))
    )


def _sha(value: object) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))


def _safe_relative(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and str(path) == value


def _resolve(root: Path, value: object, path: str, reasons: list[str]) -> Path | None:
    if not _safe_relative(value):
        _reason(reasons, f"invalid_repository_relative_path:{path}")
        return None
    return root / str(value)


def _string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _rate(value: object) -> bool:
    return _finite_number(value) and 0 <= float(value) <= 1


def _validate_access_ledger(
    path: Path,
    *,
    genesis: object,
    reasons: list[str],
    recomputed: dict[str, str],
) -> None:
    if not _sha(genesis):
        _reason(reasons, "access_log_genesis_sha256")
    try:
        raw = path.read_bytes()
    except OSError:
        _reason(reasons, "access_log_unreadable")
        return
    recomputed["dataset.access_log_file_sha256"] = hashlib.sha256(raw).hexdigest()
    previous = genesis
    row_keys = frozenset({
        "seq", "previous_entry_sha256", "at", "actor_id", "command_sha256",
        "purpose", "operation", "requested_split", "case_ids_sha256", "allowed",
        "result_sha256", "entry_sha256",
    })
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        _reason(reasons, "access_log_not_utf8")
        return
    for index, line in enumerate(lines):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            _reason(reasons, f"access_log_invalid_json:{index}")
            continue
        item = _closed(row, row_keys, f"access_log[{index}]", reasons)
        if item is None:
            continue
        if item.get("seq") != index:
            _reason(reasons, f"access_log_seq:{index}")
        if item.get("previous_entry_sha256") != previous:
            _reason(reasons, f"access_log_chain:{index}")
        if not _rfc3339(item.get("at")):
            _reason(reasons, f"access_log_at:{index}")
        if not _string(item.get("actor_id")):
            _reason(reasons, f"access_log_actor:{index}")
        for field in ("command_sha256", "case_ids_sha256", "result_sha256", "entry_sha256"):
            if not _sha(item.get(field)):
                _reason(reasons, f"access_log_sha256:{index}:{field}")
        if item.get("purpose") not in PURPOSES:
            _reason(reasons, f"access_log_purpose:{index}")
        if item.get("operation") not in OPERATIONS:
            _reason(reasons, f"access_log_operation:{index}")
        if item.get("requested_split") not in SPLITS:
            _reason(reasons, f"access_log_split:{index}")
        if not isinstance(item.get("allowed"), bool):
            _reason(reasons, f"access_log_allowed:{index}")
        expected = canonical_json_sha256({key: value for key, value in item.items() if key != "entry_sha256"})
        if item.get("entry_sha256") != expected:
            _reason(reasons, f"access_log_entry_hash:{index}")
        if item.get("requested_split") in SEALED_SPLITS and item.get("allowed") is not False:
            _reason(reasons, f"allowed_sealed_split_before_f1:{index}")
        previous = item.get("entry_sha256")
    recomputed["dataset.access_log_head_sha256"] = str(previous)


def _validate_dataset(
    dataset: Mapping[str, Any],
    *,
    root: Path,
    freeze: Mapping[str, Any],
    reasons: list[str],
    recomputed: dict[str, str],
) -> None:
    top = _closed(dataset, frozenset({
        "schema_version", "created_at", "source_files", "cases", "pairs",
        "templates", "split_hashes", "access_log_path",
        "access_log_genesis_sha256",
    }), "dataset", reasons)
    if top is None:
        return
    if top.get("schema_version") != DATASET_MANIFEST_SCHEMA_VERSION:
        _reason(reasons, "dataset_schema_version")
    if not _rfc3339(top.get("created_at")):
        _reason(reasons, "dataset_created_at")

    source_rows = top.get("source_files")
    seen_paths: set[str] = set()
    if not isinstance(source_rows, list) or not source_rows:
        _reason(reasons, "dataset_source_files")
    else:
        for index, raw in enumerate(source_rows):
            row = _closed(raw, frozenset({"path", "sha256"}), f"dataset.source_files[{index}]", reasons)
            if row is None:
                continue
            rel = row.get("path")
            file_path = _resolve(root, rel, f"dataset.source_files[{index}].path", reasons)
            if isinstance(rel, str) and rel in seen_paths:
                _reason(reasons, f"duplicate_source_file:{rel}")
            if isinstance(rel, str):
                seen_paths.add(rel)
            if not _sha(row.get("sha256")):
                _reason(reasons, f"invalid_sha256:dataset.source_files[{index}].sha256")
            if file_path is not None:
                try:
                    digest = sha256_file(file_path)
                except OSError:
                    _reason(reasons, f"source_file_unreadable:{rel}")
                else:
                    recomputed[f"dataset.source_file:{rel}"] = digest
                    if digest != row.get("sha256"):
                        _reason(reasons, f"source_file_hash_mismatch:{rel}")

    template_rows = top.get("templates")
    templates: dict[str, Mapping[str, Any]] = {}
    if not isinstance(template_rows, list):
        _reason(reasons, "dataset_templates")
        template_rows = []
    for index, raw in enumerate(template_rows):
        row = _closed(raw, frozenset({"template_id", "family_id", "domain_id"}), f"dataset.templates[{index}]", reasons)
        if row is None:
            continue
        template_id = row.get("template_id")
        if not all(_string(row.get(field)) for field in ("template_id", "family_id", "domain_id")):
            _reason(reasons, f"invalid_template:{index}")
        elif template_id in templates:
            _reason(reasons, f"duplicate_template_id:{template_id}")
        else:
            templates[str(template_id)] = row

    case_rows = top.get("cases")
    cases: dict[str, Mapping[str, Any]] = {}
    if not isinstance(case_rows, list):
        _reason(reasons, "dataset_cases")
        case_rows = []
    for index, raw in enumerate(case_rows):
        row = _closed(raw, frozenset({"case_id", "split", "family_id", "domain_id", "template_ids", "pair_ids"}), f"dataset.cases[{index}]", reasons)
        if row is None:
            continue
        case_id = row.get("case_id")
        if not all(_string(row.get(field)) for field in ("case_id", "family_id", "domain_id")) or row.get("split") not in SPLITS:
            _reason(reasons, f"invalid_case:{index}")
            continue
        if case_id in cases:
            _reason(reasons, f"duplicate_case_id:{case_id}")
            continue
        lists_valid = True
        for field in ("template_ids", "pair_ids"):
            values = row.get(field)
            if not isinstance(values, list) or not values or not all(_string(value) for value in values) or len(values) != len(set(values)):
                _reason(reasons, f"invalid_case_{field}:{case_id}")
                lists_valid = False
        if not lists_valid:
            continue
        cases[str(case_id)] = row

    pair_rows = top.get("pairs")
    pairs: dict[str, Mapping[str, Any]] = {}
    pairs_by_case: dict[str, set[str]] = {case_id: set() for case_id in cases}
    if not isinstance(pair_rows, list):
        _reason(reasons, "dataset_pairs")
        pair_rows = []
    for index, raw in enumerate(pair_rows):
        row = _closed(raw, frozenset({"pair_id", "case_id", "left_item_id", "right_item_id", "template_id"}), f"dataset.pairs[{index}]", reasons)
        if row is None:
            continue
        if not all(_string(row.get(field)) for field in ("pair_id", "case_id", "left_item_id", "right_item_id", "template_id")):
            _reason(reasons, f"invalid_pair:{index}")
            continue
        pair_id, case_id = str(row["pair_id"]), str(row["case_id"])
        if pair_id in pairs:
            _reason(reasons, f"duplicate_pair_id:{pair_id}")
            continue
        if row["left_item_id"] == row["right_item_id"]:
            _reason(reasons, f"pair_endpoints_not_distinct:{pair_id}")
        if case_id not in cases:
            _reason(reasons, f"pair_unknown_case:{pair_id}")
        else:
            pairs_by_case[case_id].add(pair_id)
            case = cases[case_id]
            if pair_id not in case.get("pair_ids", []):
                _reason(reasons, f"pair_not_listed_by_case:{pair_id}")
            if row["template_id"] not in case.get("template_ids", []):
                _reason(reasons, f"pair_template_not_listed_by_case:{pair_id}")
        if row["template_id"] not in templates:
            _reason(reasons, f"pair_unknown_template:{pair_id}")
        pairs[pair_id] = row

    for case_id, case in cases.items():
        if set(case.get("pair_ids", [])) != pairs_by_case.get(case_id, set()):
            _reason(reasons, f"case_pair_mapping_not_total:{case_id}")
        for template_id in case.get("template_ids", []):
            template = templates.get(template_id)
            if template is None:
                _reason(reasons, f"case_unknown_template:{case_id}:{template_id}")
            elif template.get("family_id") != case.get("family_id") or template.get("domain_id") != case.get("domain_id"):
                _reason(reasons, f"case_template_mapping_mismatch:{case_id}:{template_id}")
    referenced_templates = {
        str(template_id)
        for case in cases.values()
        for template_id in case.get("template_ids", [])
    }
    if referenced_templates != set(templates):
        _reason(reasons, "template_mapping_not_total")

    split_hashes = _closed(top.get("split_hashes"), frozenset(SPLITS), "dataset.split_hashes", reasons)
    freeze_splits = _closed(freeze.get("splits"), frozenset(SPLITS), "splits", reasons)
    split_values: dict[str, dict[str, set[str]]] = {
        split: {"case": set(), "pair": set(), "family": set(), "template": set()}
        for split in SPLITS
    }
    for case_id, case in cases.items():
        split = str(case["split"])
        split_values[split]["case"].add(case_id)
        split_values[split]["family"].add(str(case["family_id"]))
        split_values[split]["template"].update(str(value) for value in case.get("template_ids", []))
        split_values[split]["pair"].update(pairs_by_case.get(case_id, set()))
    hash_keys = {
        "case_ids_sha256": "case", "pair_ids_sha256": "pair",
        "family_ids_sha256": "family", "template_ids_sha256": "template",
    }
    for split in SPLITS:
        dataset_entry = _closed(
            split_hashes.get(split) if split_hashes else None,
            frozenset(hash_keys), f"dataset.split_hashes.{split}", reasons,
        )
        freeze_entry = _closed(
            freeze_splits.get(split) if freeze_splits else None,
            frozenset(hash_keys), f"splits.{split}", reasons,
        )
        for hash_name, value_name in hash_keys.items():
            digest = canonical_id_hash(tuple(split_values[split][value_name]))
            recomputed[f"splits.{split}.{hash_name}"] = digest
            if dataset_entry is not None and dataset_entry.get(hash_name) != digest:
                _reason(reasons, f"dataset_split_hash_mismatch:{split}:{hash_name}")
            if freeze_entry is not None and freeze_entry.get(hash_name) != digest:
                _reason(reasons, f"freeze_split_hash_mismatch:{split}:{hash_name}")
    for name in ("family", "template"):
        if split_values["dev"][name] & split_values["query"][name]:
            _reason(reasons, f"blocked_{name}_overlap:dev:query")
    raw_budgets = freeze.get("budgets")
    pair_budget = raw_budgets.get("unique_pair_calls") if isinstance(raw_budgets, Mapping) else None
    if _integer(pair_budget) and len(pairs) > pair_budget:
        _reason(reasons, "unique_pair_call_budget_exceeded")

    access_path = _resolve(root, top.get("access_log_path"), "dataset.access_log_path", reasons)
    if not _sha(top.get("access_log_genesis_sha256")):
        _reason(reasons, "invalid_sha256:dataset.access_log_genesis_sha256")
    if access_path is not None:
        _validate_access_ledger(
            access_path,
            genesis=top.get("access_log_genesis_sha256"),
            reasons=reasons,
            recomputed=recomputed,
        )


def _validate_ordering(value: object, reasons: list[str]) -> None:
    policy = _closed(value, frozenset({"policy_version", "accepted_sources", "source_semantics", "conflict_policy"}), "ordering_policy", reasons)
    if policy is None:
        return
    if not _string(policy.get("policy_version")):
        _reason(reasons, "ordering_policy_version")
    sources = policy.get("accepted_sources")
    if (
        not isinstance(sources, list)
        or not sources
        or not all(isinstance(source, str) for source in sources)
        or len(sources) != len(set(sources))
        or any(source not in REGISTERED_SOURCE_SEMANTICS for source in sources)
    ):
        _reason(reasons, "ordering_policy_sources")
        sources = []
    else:
        registered_order = tuple(REGISTERED_SOURCE_SEMANTICS)
        if tuple(sources) != tuple(
            source for source in registered_order if source in sources
        ):
            _reason(reasons, "ordering_policy_sources_not_registered_subsequence")
    semantics = policy.get("source_semantics")
    if not isinstance(semantics, Mapping) or set(semantics) != set(sources):
        _reason(reasons, "ordering_policy_source_semantics_keys")
        return
    for source in sources:
        expected = REGISTERED_SOURCE_SEMANTICS[source]
        actual = _closed(semantics.get(source), frozenset(expected), f"ordering_policy.source_semantics.{source}", reasons)
        if actual != expected:
            _reason(reasons, f"ordering_policy_source_semantics:{source}")
    if policy.get("conflict_policy") != "fail_closed":
        _reason(reasons, "ordering_policy_conflict_policy")


def _validate_gates(value: object, reasons: list[str]) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]:
    gates = _closed(value, frozenset({"g0", "g1", "g2", "g3", "e0"}), "gates", reasons)
    if gates is None:
        return None, None
    schemas = {
        "g0": frozenset({"metric_version", "relation_precision_min", "relation_recall_min", "permutation_fpr_max", "canary_recall_min", "abstention_rate_max", "confidence_level", "bootstrap_iterations", "bootstrap_seed", "min_pairs", "min_positive_pairs", "min_negative_pairs", "min_families"}),
        "g1": frozenset({"metric_version", "target_precision_min", "target_recall_min", "ordering_coverage_min", "destructive_coverage_min", "unknown_rate_max", "conflict_rate_max", "confidence_level", "bootstrap_iterations", "bootstrap_seed", "min_pairs", "min_directional_pairs", "min_families"}),
        "g2": frozenset({"metric_version", "min_firing_cases", "min_firing_families", "null_false_fire_max", "field_alignment_max", "nmi_alarm_max", "permutation_target_precision_max", "reusable_value_unique_ratio_max"}),
        "g3": frozenset({"baseline_catalog_path", "baseline_catalog_sha256"}),
        "e0": frozenset({"candidate_envelope_path", "candidate_envelope_sha256", "score_metric", "family_aggregation", "strict_gain_min", "confidence_level", "bootstrap_iterations", "bootstrap_seed", "tie_epsilon", "tie_policy", "missing_policy", "nonfinite_policy"}),
    }
    closed = {name: _closed(gates.get(name), keys, f"gates.{name}", reasons) for name, keys in schemas.items()}
    rate_fields = {
        "g0": ("relation_precision_min", "relation_recall_min", "permutation_fpr_max", "canary_recall_min", "abstention_rate_max", "confidence_level"),
        "g1": ("target_precision_min", "target_recall_min", "ordering_coverage_min", "destructive_coverage_min", "unknown_rate_max", "conflict_rate_max", "confidence_level"),
        "g2": ("null_false_fire_max", "field_alignment_max", "nmi_alarm_max", "permutation_target_precision_max", "reusable_value_unique_ratio_max"),
    }
    count_fields = {
        "g0": ("bootstrap_iterations", "min_pairs", "min_positive_pairs", "min_negative_pairs", "min_families"),
        "g1": ("bootstrap_iterations", "min_pairs", "min_directional_pairs", "min_families"),
        "g2": ("min_firing_cases", "min_firing_families"),
    }
    for gate in ("g0", "g1", "g2"):
        item = closed[gate]
        if item is None:
            continue
        if not _string(item.get("metric_version")):
            _reason(reasons, f"gate_metric_version:{gate}")
        for field in rate_fields[gate]:
            if not _rate(item.get(field)):
                _reason(reasons, f"gate_rate:{gate}.{field}")
        for field in count_fields[gate]:
            if not _positive_int(item.get(field)):
                _reason(reasons, f"gate_count:{gate}.{field}")
        if gate in {"g0", "g1"} and not _integer(item.get("bootstrap_seed")):
            _reason(reasons, f"gate_seed:{gate}.bootstrap_seed")
    e0 = closed["e0"]
    if e0 is not None:
        if e0.get("score_metric") != REGISTERED_SCORE_METRIC:
            _reason(reasons, "e0_score_metric")
        if e0.get("family_aggregation") != REGISTERED_FAMILY_AGGREGATION:
            _reason(reasons, "e0_family_aggregation")
        if not _finite_number(e0.get("strict_gain_min")) or float(e0.get("strict_gain_min", -1)) < 0:
            _reason(reasons, "e0_strict_gain_min")
        if not _rate(e0.get("confidence_level")):
            _reason(reasons, "e0_confidence_level")
        if not _positive_int(e0.get("bootstrap_iterations")):
            _reason(reasons, "e0_bootstrap_iterations")
        if not _integer(e0.get("bootstrap_seed")):
            _reason(reasons, "e0_bootstrap_seed")
        if not _finite_number(e0.get("tie_epsilon")) or float(e0.get("tie_epsilon", -1)) < 0:
            _reason(reasons, "e0_tie_epsilon")
        for field in ("tie_policy", "missing_policy", "nonfinite_policy"):
            if e0.get(field) != "STOP":
                _reason(reasons, f"e0_{field}")
    return closed["g3"], closed["e0"]


def _canonical_program_mapping(mapping: object, path: str, reasons: list[str]) -> tuple[dict[str, object] | None, str | None]:
    try:
        program = parse_program(mapping)
        canonical = program_to_mapping(canonicalize(program))
        digest = canonical_ast_hash(program)
    except (ProgramParseError, ProgramBoundsError, TypeError, ValueError):
        _reason(reasons, f"invalid_program:{path}")
        return None, None
    if mapping != canonical:
        _reason(reasons, f"noncanonical_program:{path}")
    return canonical, digest


def _baseline_programs() -> Mapping[str, dict[str, object]]:
    def rule(predicate: str, action: str) -> dict[str, object]:
        return {"node": "if", "predicate": {"kind": predicate}, "action": {"kind": action}}

    return {
        "B0": {"node": "sequence", "body": []},
        "B1": rule("divergent_pair_member", "annotate_conflict"),
        "B2": rule("superseded_item", "demote"),
        "B3": rule("superseded_item", "suppress"),
        "B4": rule("superseded_item", "replace"),
    }


def _validate_catalog(path: Path, reasons: list[str], recomputed: dict[str, str]) -> set[str]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        _reason(reasons, "baseline_catalog_unreadable")
        return set()
    recomputed["gates.g3.baseline_catalog_file_sha256"] = hashlib.sha256(raw).hexdigest()
    catalog = _closed(payload, frozenset({"schema_version", "protocol_id", "grammar_version", "baselines", "catalog_sha256"}), "baseline_catalog", reasons)
    if catalog is None:
        return set()
    if catalog.get("schema_version") != BASELINE_CATALOG_SCHEMA_VERSION:
        _reason(reasons, "baseline_catalog_schema_version")
    if catalog.get("protocol_id") != PROTOCOL_ID or catalog.get("grammar_version") != IR_GRAMMAR_VERSION:
        _reason(reasons, "baseline_catalog_protocol_or_grammar")
    expected = _baseline_programs()
    rows = catalog.get("baselines")
    hashes: set[str] = set()
    if not isinstance(rows, list) or [row.get("baseline_id") for row in rows if isinstance(row, Mapping)] != list(expected):
        _reason(reasons, "baseline_catalog_ids_not_exact_sorted_b0_b4")
        rows = []
    for index, raw_row in enumerate(rows):
        row = _closed(raw_row, frozenset({"baseline_id", "description", "program", "canonical_ast_sha256"}), f"baseline_catalog.baselines[{index}]", reasons)
        if row is None:
            continue
        baseline_id = row.get("baseline_id")
        if not _string(row.get("description")):
            _reason(reasons, f"baseline_description:{baseline_id}")
        canonical, digest = _canonical_program_mapping(row.get("program"), f"baseline:{baseline_id}", reasons)
        if baseline_id not in expected or canonical != expected.get(str(baseline_id)):
            _reason(reasons, f"baseline_ast_not_exact:{baseline_id}")
        if digest is not None:
            hashes.add(digest)
            if row.get("canonical_ast_sha256") != digest:
                _reason(reasons, f"baseline_ast_hash:{baseline_id}")
    expected_hash = canonical_json_sha256({key: value for key, value in catalog.items() if key != "catalog_sha256"})
    recomputed["gates.g3.catalog_sha256"] = expected_hash
    if catalog.get("catalog_sha256") != expected_hash:
        _reason(reasons, "baseline_catalog_content_hash")
    return hashes


def _validate_envelope(
    path: Path,
    *,
    candidate_budget: object,
    baseline_hashes: set[str],
    reasons: list[str],
    recomputed: dict[str, str],
) -> None:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        _reason(reasons, "candidate_envelope_unreadable")
        return
    recomputed["gates.e0.candidate_envelope_file_sha256"] = hashlib.sha256(raw).hexdigest()
    envelope = _closed(payload, frozenset({"schema_version", "protocol_id", "grammar_version", "adaptive", "generation_rule", "generation_rule_sha256", "candidates", "candidate_envelope_sha256"}), "candidate_envelope", reasons)
    if envelope is None:
        return
    if envelope.get("schema_version") != E0_ENVELOPE_SCHEMA_VERSION:
        _reason(reasons, "candidate_envelope_schema_version")
    if envelope.get("protocol_id") != PROTOCOL_ID or envelope.get("grammar_version") != IR_GRAMMAR_VERSION:
        _reason(reasons, "candidate_envelope_protocol_or_grammar")
    if envelope.get("adaptive") is not False:
        _reason(reasons, "candidate_envelope_adaptive")
    if envelope.get("generation_rule") != GENERATION_RULE:
        _reason(reasons, "candidate_envelope_generation_rule")
    rule_hash = canonical_json_sha256(GENERATION_RULE)
    recomputed["gates.e0.generation_rule_sha256"] = rule_hash
    if envelope.get("generation_rule_sha256") != rule_hash:
        _reason(reasons, "candidate_envelope_generation_rule_hash")
    rows = envelope.get("candidates")
    if not isinstance(rows, list):
        _reason(reasons, "candidate_envelope_candidates")
        rows = []
    ids = [row.get("candidate_id") for row in rows if isinstance(row, Mapping)]
    if (
        len(ids) != len(rows)
        or not all(_string(value) for value in ids)
        or ids != sorted(ids)
        or len(ids) != len(set(ids))
    ):
        _reason(reasons, "candidate_ids_not_unique_sorted")
    if not _integer(candidate_budget) or len(rows) != candidate_budget:
        _reason(reasons, "candidate_budget_mismatch")
    hashes: list[str] = []
    for index, raw_row in enumerate(rows):
        row = _closed(raw_row, frozenset({"candidate_id", "program", "canonical_ast_sha256"}), f"candidate_envelope.candidates[{index}]", reasons)
        if row is None:
            continue
        canonical, digest = _canonical_program_mapping(row.get("program"), f"candidate:{row.get('candidate_id')}", reasons)
        if canonical == {"node": "sequence", "body": []}:
            _reason(reasons, f"candidate_empty_program:{row.get('candidate_id')}")
        if digest is not None:
            hashes.append(digest)
            if row.get("canonical_ast_sha256") != digest:
                _reason(reasons, f"candidate_ast_hash:{row.get('candidate_id')}")
    if len(hashes) != len(set(hashes)):
        _reason(reasons, "duplicate_candidate_ast")
    if set(hashes) & baseline_hashes:
        _reason(reasons, "candidate_ast_overlaps_baseline")
    envelope_hash = canonical_json_sha256({key: value for key, value in envelope.items() if key != "candidate_envelope_sha256"})
    recomputed["gates.e0.candidate_envelope_sha256"] = envelope_hash
    if envelope.get("candidate_envelope_sha256") != envelope_hash:
        _reason(reasons, "candidate_envelope_content_hash")


def _validate_query_policy(
    value: object, *, reasons: list[str], recomputed: dict[str, str]
) -> None:
    policy = _closed(value, frozenset({"ledger_path", "ledger_genesis_sha256", "max_reservations", "reservation_consumes_read"}), "query_policy", reasons)
    if policy is None:
        return
    expected_path = "artifacts/route_a_successor_v3/query/query_read_ledger.sqlite3"
    if policy.get("ledger_path") != expected_path:
        _reason(reasons, "query_ledger_path")
    if policy.get("ledger_genesis_sha256") != QUERY_LEDGER_GENESIS_SHA256:
        _reason(reasons, "query_ledger_genesis")
    if policy.get("max_reservations") != 1:
        _reason(reasons, "query_max_reservations")
    if policy.get("reservation_consumes_read") is not True:
        _reason(reasons, "query_reservation_consumes_read")
    # The genesis identifies the immutable canonical schema descriptor, never
    # mutable SQLite bytes. Protocol validation must not create or inspect the
    # live reservation database; the confirmation gate owns that state.
    recomputed["query_policy.ledger_genesis_sha256"] = QUERY_LEDGER_GENESIS_SHA256


def _validate_commands(value: object, *, root: Path, reasons: list[str], recomputed: dict[str, str]) -> None:
    commands = _closed(value, frozenset(COMMAND_LOCATORS), "commands", reasons)
    if commands is None:
        return
    keys = frozenset({"script", "script_sha256", "entrypoint", "required_flags", "network_policy"})
    for name, expected in COMMAND_LOCATORS.items():
        locator = _closed(commands.get(name), keys, f"commands.{name}", reasons)
        if locator is None:
            continue
        for field, registered in expected.items():
            if locator.get(field) != registered:
                _reason(reasons, f"command_locator:{name}:{field}")
        if not _sha(locator.get("script_sha256")):
            _reason(reasons, f"invalid_sha256:commands.{name}.script_sha256")
        script_path = _resolve(root, locator.get("script"), f"commands.{name}.script", reasons)
        if script_path is not None:
            try:
                digest = sha256_file(script_path)
            except OSError:
                _reason(reasons, f"command_script_unreadable:{name}")
            else:
                recomputed[f"commands.{name}.script_sha256"] = digest
                if digest != locator.get("script_sha256"):
                    _reason(reasons, f"command_script_hash_mismatch:{name}")


def validate_protocol_freeze(
    freeze: Mapping[str, Any],
    *,
    dataset_path: Path,
    repo_root: Path | None = None,
    prompt_path: Path | None = None,
    confirmation_reads_before_synthesis_freeze: int = 0,
) -> FreezeValidation:
    """Validate the closed F0 manifest and its closed F1 protocol authority."""

    root = (repo_root or Path.cwd()).resolve()
    reasons: list[str] = []
    recomputed: dict[str, str] = {}
    _walk_forbidden(freeze, "", reasons)
    top = _closed(freeze, frozenset({
        "schema_version", "protocol_id", "freeze_stage", "frozen_at",
        "dataset_manifest_sha256", "splits", "instrument", "ordering_policy",
        "gates", "grammar", "budgets", "commands", "query_policy",
        "predecessor_status",
    }), "", reasons)
    if top is None:
        top = freeze
    if top.get("schema_version") != FREEZE_SCHEMA_VERSION:
        _reason(reasons, "freeze_schema_version")
    if top.get("protocol_id") != PROTOCOL_ID:
        _reason(reasons, "protocol_id")
    if top.get("freeze_stage") != "F1":
        _reason(reasons, "freeze_stage_must_be_f1")
    if not _rfc3339(top.get("frozen_at")):
        _reason(reasons, "frozen_at_not_rfc3339")
    if not _sha(top.get("dataset_manifest_sha256")):
        _reason(reasons, "invalid_sha256:dataset_manifest_sha256")

    instrument = _closed(top.get("instrument"), frozenset({
        "model_id", "model_revision", "temperature", "top_p", "seed",
        "max_output_tokens", "prompt_sha256", "parser_version",
        "normalization_version", "cache_schema_version",
    }), "instrument", reasons)
    if instrument is not None:
        for field in ("model_id", "model_revision", "parser_version", "normalization_version", "cache_schema_version"):
            if not _string(instrument.get(field)):
                _reason(reasons, f"instrument_string:{field}")
        if not _finite_number(instrument.get("temperature")) or float(instrument.get("temperature", -1)) < 0:
            _reason(reasons, "instrument_temperature")
        if not _rate(instrument.get("top_p")):
            _reason(reasons, "instrument_top_p")
        if not _integer(instrument.get("seed")):
            _reason(reasons, "instrument_seed")
        if not _positive_int(instrument.get("max_output_tokens")):
            _reason(reasons, "instrument_max_output_tokens")
        if not _sha(instrument.get("prompt_sha256")):
            _reason(reasons, "invalid_sha256:instrument.prompt_sha256")
        if prompt_path is None:
            _reason(reasons, "prompt_file_not_supplied")
        else:
            try:
                prompt_hash = sha256_file(prompt_path)
            except OSError:
                _reason(reasons, "prompt_file_unreadable")
            else:
                recomputed["instrument.prompt_sha256"] = prompt_hash
                if prompt_hash != instrument.get("prompt_sha256"):
                    _reason(reasons, "prompt_file_hash_mismatch")

    _validate_ordering(top.get("ordering_policy"), reasons)
    grammar = _closed(top.get("grammar"), frozenset({"version", "leaves", "effects", "bounds"}), "grammar", reasons)
    if grammar is not None:
        if grammar.get("version") != IR_GRAMMAR_VERSION:
            _reason(reasons, "grammar_version")
        raw_leaves = grammar.get("leaves")
        raw_effects = grammar.get("effects")
        if not isinstance(raw_leaves, list) or tuple(raw_leaves) != MINIMAL_LEAVES:
            _reason(reasons, "grammar_leaves_not_exact_minimal_v3")
        if not isinstance(raw_effects, list) or tuple(raw_effects) != MINIMAL_EFFECTS:
            _reason(reasons, "grammar_effects_not_exact_minimal_v3")
        if grammar.get("bounds") != REGISTERED_BOUNDS.as_mapping():
            _reason(reasons, "grammar_bounds_not_registered")

    budget_keys = frozenset({"human_labels", "unique_pair_calls", "retries", "e0_candidates", "synthesis_seeds", "proposals_per_seed", "query_reads"})
    budgets = _closed(top.get("budgets"), budget_keys, "budgets", reasons)
    if budgets is not None:
        for name in budget_keys:
            value = budgets.get(name)
            if not _integer(value) or int(value) < 0:
                _reason(reasons, f"invalid_budget:{name}")
        for name in ("human_labels", "unique_pair_calls", "e0_candidates", "synthesis_seeds", "proposals_per_seed"):
            if not _positive_int(budgets.get(name)):
                _reason(reasons, f"nonpositive_budget:{name}")
        if budgets.get("query_reads") != 1:
            _reason(reasons, "query_reads_must_equal_one")

    g3, e0 = _validate_gates(top.get("gates"), reasons)
    if g3 is not None:
        if not _sha(g3.get("baseline_catalog_sha256")):
            _reason(reasons, "invalid_sha256:gates.g3.baseline_catalog_sha256")
    if e0 is not None:
        if not _sha(e0.get("candidate_envelope_sha256")):
            _reason(reasons, "invalid_sha256:gates.e0.candidate_envelope_sha256")

    try:
        dataset_raw = dataset_path.read_bytes()
        dataset = json.loads(dataset_raw)
    except (OSError, json.JSONDecodeError):
        _reason(reasons, "dataset_manifest_unreadable")
    else:
        dataset_hash = hashlib.sha256(dataset_raw).hexdigest()
        recomputed["dataset_manifest_sha256"] = dataset_hash
        if dataset_hash != top.get("dataset_manifest_sha256"):
            _reason(reasons, "dataset_manifest_sha256_mismatch")
        if isinstance(dataset, Mapping):
            _walk_forbidden(dataset, "dataset", reasons)
            _validate_dataset(dataset, root=root, freeze=top, reasons=reasons, recomputed=recomputed)
        else:
            _reason(reasons, "dataset_manifest_not_object")

    baseline_hashes: set[str] = set()
    if g3 is not None:
        catalog_path = _resolve(root, g3.get("baseline_catalog_path"), "gates.g3.baseline_catalog_path", reasons)
        if catalog_path is not None:
            baseline_hashes = _validate_catalog(catalog_path, reasons, recomputed)
            if recomputed.get("gates.g3.baseline_catalog_file_sha256") != g3.get("baseline_catalog_sha256"):
                _reason(reasons, "baseline_catalog_file_hash_mismatch")
    if e0 is not None:
        envelope_path = _resolve(root, e0.get("candidate_envelope_path"), "gates.e0.candidate_envelope_path", reasons)
        if envelope_path is not None:
            _validate_envelope(
                envelope_path,
                candidate_budget=budgets.get("e0_candidates") if budgets else None,
                baseline_hashes=baseline_hashes,
                reasons=reasons,
                recomputed=recomputed,
            )
            if recomputed.get("gates.e0.candidate_envelope_file_sha256") != e0.get("candidate_envelope_sha256"):
                _reason(reasons, "candidate_envelope_file_hash_mismatch")

    _validate_commands(top.get("commands"), root=root, reasons=reasons, recomputed=recomputed)
    _validate_query_policy(
        top.get("query_policy"), reasons=reasons, recomputed=recomputed
    )
    if top.get("predecessor_status") != {"route_a_v1": "E0_STOP_FROZEN", "route_a_v2_slot": "WITHDRAWN"}:
        _reason(reasons, "predecessor_status_changed")
    if confirmation_reads_before_synthesis_freeze != 0:
        _reason(reasons, "confirmation_read_before_synthesis_freeze")

    try:
        manifest_hash = canonical_manifest_sha256(top)
    except (TypeError, ValueError):
        manifest_hash = ""
        _reason(reasons, "manifest_not_canonical_json")
    return FreezeValidation(
        valid=not reasons,
        reasons=tuple(reasons),
        validator_version=VALIDATOR_VERSION,
        manifest_sha256=manifest_hash,
        recomputed_hashes=dict(sorted(recomputed.items())),
    )
