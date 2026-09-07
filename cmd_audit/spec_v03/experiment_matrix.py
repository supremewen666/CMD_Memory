"""Offline, fail-closed executable plan builder for CMD spec v0.3.

It is a registry and manifest compiler only: no API calls, downloads, model
loads, or implicit version discovery occur here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Mapping

from .contracts import canonical_sha256


SCHEMA_VERSION = "cmd-spec-v03-experiment-matrix-v3"
DEVELOPMENT_UNPINNED = "DEVELOPMENT_UNPINNED"
CONFIRMATORY_FROZEN = "CONFIRMATORY_FROZEN"
UNRESOLVED = "UNRESOLVED"
TRACKS = ("controlled_a1", "controlled_a2", "native")
STAGES = ("stage5", "stage6", "stage7", "stage8", "stage9")
STAGE5_VARIANTS = ("random_legal", "best_global", "global_thompson", "niche_thompson", "contextual_bandit", "ghost_hierarchy", "mix_ghost", "oracle_legal")
STAGE5_ROUTING_ABLATION_VARIANTS = (
    "routing_frozen_backbone",
    "routing_global",
    "routing_global_pattern",
    "routing_global_pattern_local",
    "routing_full_no_support_gate",
    "mix_ghost",
)
STAGE5_EXECUTABLE_VARIANTS = tuple(
    dict.fromkeys((*STAGE5_VARIANTS, *STAGE5_ROUTING_ABLATION_VARIANTS))
)
STAGE6_VARIANTS = ("no_skill", "seed_frozen", "add_only", "add_dedup", "add_revision", "add_revision_retirement", "full_ecology", "random_key_ecology", "oracle_library")
STAGE7_VARIANTS = ("detection_only", "in_place", "copy_on_write", "ecc_no_cas", "ecc_cas", "full_governance", "oracle_repair")
STAGE8A_VARIANTS = ("no_repair", "random_legal", "skill_content_only", "reset_online", "frozen_source", "niche_shuffled", "mean_only", "reset_prefix", "source_prefix", "oracle_legal_operator")
STAGE8B_VARIANTS = ("seed_only", "source_skills", "target_native_skills", "oracle_library")
STAGE9_VARIANTS = ("full_context", "bm25_rag", "cmd_full", "cmd_no_mix_ghost", "cmd_no_ecology", "cmd_no_ecc_cas", "memskill", "erskill", "mem0", "no_repair", "oracle")
_BUDGET_FIELDS = frozenset({"llm_calls", "input_tokens", "output_tokens", "wall_clock_seconds", "gpu_seconds"})
_FORBIDDEN_CMD_COMPONENTS = frozenset({"cmd_diagnosis", "cmd_router", "cmd_ecc"})
_NODE_FIELDS = frozenset({"run_id", "stage", "substage", "experiment_variant_id", "model_arm_id", "system_arm_id", "track", "family_id", "seed", "execution_status", "denominator_status", "score_namespace", "reporting_stratum"})
_STAGE9_SYSTEM = {"full_context": "full-context", "bm25_rag": "bm25-rag", "cmd_full": "cmd", "cmd_no_mix_ghost": "cmd", "cmd_no_ecology": "cmd", "cmd_no_ecc_cas": "cmd", "memskill": "memskill", "erskill": "erskill", "mem0": "mem0", "no_repair": "no-repair", "oracle": "oracle"}
_STAGE9_TRACKS = {variant: ("controlled_a1", "controlled_a2", "native") if variant == "cmd_full" else ("controlled_a1", "controlled_a2") for variant in STAGE9_VARIANTS}


@dataclass(frozen=True)
class Budget:
    llm_calls: int
    input_tokens: int
    output_tokens: int
    wall_clock_seconds: int
    gpu_seconds: int

    def __post_init__(self) -> None:
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in asdict(self).values()):
            raise ValueError("budget values must be non-negative integers")

    def to_mapping(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class DataEntitlement:
    entitlement_id: str
    dataset_revision: str
    split_manifest_sha256: str
    lockbox_manifest_sha256: str
    access_class: str = "offline-approved"

    def __post_init__(self) -> None:
        if any(not isinstance(v, str) or not v for v in asdict(self).values()):
            raise ValueError("data entitlement fields must be non-empty strings")

    def to_mapping(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class ModelArm:
    arm_id: str
    family: str
    model_id: str
    protocol: str
    pinned_commit: str
    model_snapshot: str
    closed_model: bool = False

    def __post_init__(self) -> None:
        if not all(isinstance(v, str) and v for v in (self.arm_id, self.family, self.model_id, self.protocol, self.pinned_commit, self.model_snapshot)):
            raise ValueError("model arm fields must be non-empty strings")
        if self.protocol not in {"discovery", "target", "snapshot"}:
            raise ValueError("model protocol is unsupported")

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SystemArm:
    arm_id: str
    system: str
    supported_tracks: tuple[str, ...]
    components: tuple[str, ...]
    adapter_capability_id: str
    pinned_commit: str

    def __post_init__(self) -> None:
        if not self.arm_id or not self.system or not self.adapter_capability_id or not self.pinned_commit:
            raise ValueError("system arm fields must be non-empty")
        if not self.supported_tracks or not set(self.supported_tracks).issubset(TRACKS):
            raise ValueError("system arm must declare valid tracks")
        if self.system != "cmd" and _FORBIDDEN_CMD_COMPONENTS.intersection(self.components):
            raise ValueError("baseline system cannot enable CMD diagnosis/router/ECC")

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)


def _default_model_arms() -> tuple[ModelArm, ...]:
    return (
        ModelArm("qwen25-14b-discovery", "Qwen2.5", "Qwen2.5-14B-Instruct", "discovery", UNRESOLVED, UNRESOLVED),
        ModelArm("qwen3-14b-target", "Qwen3", "Qwen3-14B", "target", UNRESOLVED, UNRESOLVED),
        ModelArm("llama-8b-target", "Llama", "Llama-3.1-8B-Instruct", "target", UNRESOLVED, UNRESOLVED),
        ModelArm("gpt-4o-target", "GPT", "GPT-4o", "snapshot", UNRESOLVED, UNRESOLVED, True),
    )


def _default_system_arms() -> tuple[SystemArm, ...]:
    all_tracks = TRACKS
    return (
        SystemArm("full-context", "full_context", ("controlled_a1", "controlled_a2"), (), "builtin:full-context", UNRESOLVED),
        SystemArm("bm25-rag", "bm25_rag", ("controlled_a1", "controlled_a2"), (), "builtin:bm25-rag", UNRESOLVED),
        SystemArm("cmd", "cmd", all_tracks, ("cmd_diagnosis", "cmd_router", "cmd_ecc"), "cmd:repair-runtime", UNRESOLVED),
        SystemArm("memskill", "memskill", ("controlled_a1", "controlled_a2"), ("evolving-memory-skills",), "memskill:adapter", UNRESOLVED),
        SystemArm("erskill", "erskill", ("controlled_a1", "controlled_a2"), ("evolving-retrieval-skills",), "erskill:adapter", UNRESOLVED),
        SystemArm("mem0", "mem0", ("controlled_a1", "controlled_a2"), ("mem0-adapter",), "mem0:adapter", UNRESOLVED),
        SystemArm("no-repair", "no_repair", ("controlled_a1", "controlled_a2"), (), "builtin:no-repair", UNRESOLVED),
        SystemArm("oracle", "oracle", ("controlled_a1", "controlled_a2"), ("sealed-evaluator-oracle",), "builtin:oracle", UNRESOLVED),
        SystemArm("random-legal", "random_legal", ("controlled_a1", "controlled_a2"), ("legal-action-sampler",), "builtin:random-legal", UNRESOLVED),
    )


def paired_family_seed_schedule(family_ids: Iterable[str], *, base_seed: int = 20260827) -> tuple[dict[str, object], ...]:
    families = tuple(sorted(set(family_ids)))
    if not families or isinstance(base_seed, bool) or not isinstance(base_seed, int) or base_seed < 0 or any(not isinstance(v, str) or not v for v in families):
        raise ValueError("family IDs and seed are invalid")
    return tuple({"family_id": family, "seed": base_seed + index, "pairing_key": f"{family}:{base_seed + index}"} for index, family in enumerate(families))


def variant_registry() -> dict[str, tuple[str, ...]]:
    return {"stage5": STAGE5_VARIANTS, "stage6": STAGE6_VARIANTS, "stage7": STAGE7_VARIANTS, "stage8a": STAGE8A_VARIANTS, "stage8b": STAGE8B_VARIANTS, "stage9": STAGE9_VARIANTS}


def stage_eligibility(models: Iterable[ModelArm] | None = None) -> dict[str, dict[str, object]]:
    arms = tuple(models or _default_model_arms())
    qwen3 = [arm.arm_id for arm in arms if arm.family == "Qwen3" and arm.protocol == "target"]
    targets = [arm.arm_id for arm in arms if arm.family in {"Qwen3", "Llama", "GPT"}]
    return {
        "stage5": {"substage": None, "variants": list(STAGE5_VARIANTS), "model_arm_ids": qwen3, "system_arm_id": "cmd", "tracks": ["controlled_a1", "controlled_a2"], "discovery_model_arm_ids": [arm.arm_id for arm in arms if arm.family == "Qwen2.5"]},
        "stage6": {"substage": None, "variants": list(STAGE6_VARIANTS), "model_arm_ids": qwen3, "system_arm_id": "cmd", "tracks": ["controlled_a1", "controlled_a2"]},
        "stage7": {"substage": None, "variants": list(STAGE7_VARIANTS), "model_arm_ids": qwen3, "system_arm_id": "cmd", "tracks": ["controlled_a1", "controlled_a2"]},
        "stage8": {"substage": "8A", "variants": list(STAGE8A_VARIANTS), "model_arm_ids": targets, "system_arm_id": "cmd", "tracks": ["controlled_a1", "controlled_a2"]},
        "stage8b": {"substage": "8B", "variants": list(STAGE8B_VARIANTS), "model_arm_ids": targets, "system_arm_id": "cmd", "tracks": ["controlled_a1", "controlled_a2"]},
        "stage9": {"substage": None, "variants": list(STAGE9_VARIANTS), "model_arm_ids": targets, "system_arm_id": None, "tracks": None, "variant_system_map": dict(_STAGE9_SYSTEM), "variant_track_map": {key: list(value) for key, value in _STAGE9_TRACKS.items()}},
    }


def resource_ledger_schema() -> dict[str, object]:
    return {"schema_version": "cmd-spec-v03-resource-ledger-v1", "required_fields": sorted(_NODE_FIELDS | {"pinned_commit", "model_snapshot"}), "denominator_status": {"planned": "included", "unsupported": "included"}, "score_namespaces": {"controlled_a1": "controlled", "controlled_a2": "controlled", "native": "native"}}


def _stratum(stage: str, model: ModelArm, track: str) -> str:
    if stage in {"stage5", "stage6", "stage7"}:
        return "primary_qwen3"
    if stage in {"stage8", "stage8b"}:
        return f"transfer_{model.family.lower().replace('-', '_')}"
    prefix = "primary" if model.family == "Qwen3" else "external_confirmation"
    return f"{prefix}_{model.family.lower().replace('-', '_')}_{track}"


def _build_dag(models: tuple[ModelArm, ...], systems: tuple[SystemArm, ...], schedule: tuple[dict[str, object]], eligibility: Mapping[str, Mapping[str, object]]) -> dict[str, list[dict[str, object]]]:
    model_map, system_map = {m.arm_id: m for m in models}, {s.arm_id: s for s in systems}
    nodes: list[dict[str, object]] = []
    for stage, scope in eligibility.items():
        for variant in scope["variants"]:  # type: ignore[index]
            system_id = scope.get("variant_system_map", {}).get(variant, scope["system_arm_id"])  # type: ignore[union-attr,index]
            tracks = scope.get("variant_track_map", {}).get(variant, scope["tracks"])  # type: ignore[union-attr,index]
            for model_id in scope["model_arm_ids"]:  # type: ignore[index]
                for track in tracks:
                    for assignment in schedule:
                        model, system = model_map[model_id], system_map[system_id]
                        family = str(assignment["family_id"])
                        nodes.append({"run_id": f"{stage}:{scope['substage'] or 'main'}:{variant}:{model_id}:{system_id}:{track}:{family}", "stage": "stage8" if stage == "stage8b" else stage, "substage": scope["substage"], "experiment_variant_id": variant, "model_arm_id": model_id, "system_arm_id": system_id, "track": track, "family_id": family, "seed": assignment["seed"], "execution_status": "planned" if track in system.supported_tracks else "unsupported", "denominator_status": "included", "score_namespace": "native" if track == "native" else "controlled", "reporting_stratum": _stratum(stage, model, track)})
    return {"nodes": nodes, "edges": []}


def _is_commit(value: object) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(c in "0123456789abcdef" for c in value)


def _is_sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _validate_pins(models: tuple[ModelArm, ...], systems: tuple[SystemArm, ...], data: DataEntitlement) -> None:
    if not all(_is_commit(m.pinned_commit) and m.model_snapshot != UNRESOLVED for m in models):
        raise ValueError("confirmatory plan requires exact model commits and snapshots")
    if not all(_is_commit(s.pinned_commit) for s in systems):
        raise ValueError("confirmatory plan requires exact system commits")
    if data.dataset_revision == UNRESOLVED or not _is_sha(data.split_manifest_sha256) or not _is_sha(data.lockbox_manifest_sha256):
        raise ValueError("confirmatory plan requires complete data entitlement pins")


def validate_manifest(manifest: Mapping[str, object], *, confirmatory: bool | None = None) -> None:
    required = {"schema_version", "status", "freeze_eligible", "models", "systems", "unified_budget", "data_entitlement", "adapter_capabilities", "variant_registry", "stage_eligibility", "paired_family_seed_schedule", "resource_ledger_schema", "reporting_strata", "run_dag"}
    if set(manifest) - {"frozen_sha256"} != required or manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("experiment matrix uses an unsupported schema")
    status = manifest["status"]
    if status not in {DEVELOPMENT_UNPINNED, CONFIRMATORY_FROZEN} or manifest["freeze_eligible"] != (status == CONFIRMATORY_FROZEN):
        raise ValueError("experiment matrix status/freeze gate is invalid")
    if confirmatory and status != CONFIRMATORY_FROZEN:
        raise ValueError("confirmatory execution requires a frozen plan")
    budget, data_value = manifest["unified_budget"], manifest["data_entitlement"]
    if not isinstance(budget, Mapping) or set(budget) != _BUDGET_FIELDS or not isinstance(data_value, Mapping):
        raise ValueError("experiment matrix requires complete budget and data entitlement")
    Budget(**budget)  # type: ignore[arg-type]
    data = DataEntitlement(**data_value)  # type: ignore[arg-type]
    models = tuple(ModelArm(**x) for x in manifest["models"] if isinstance(x, Mapping))  # type: ignore[index]
    systems = tuple(SystemArm(**x) for x in manifest["systems"] if isinstance(x, Mapping))  # type: ignore[index]
    if len(models) != len(manifest["models"]) or len(systems) != len(manifest["systems"]):  # type: ignore[arg-type]
        raise ValueError("arms must be mappings")
    if [(m.family, m.model_id) for m in models] != [("Qwen2.5", "Qwen2.5-14B-Instruct"), ("Qwen3", "Qwen3-14B"), ("Llama", "Llama-3.1-8B-Instruct"), ("GPT", "GPT-4o")]:
        raise ValueError("model arm registry is incomplete")
    if status == CONFIRMATORY_FROZEN:
        _validate_pins(models, systems, data)
    if manifest["variant_registry"] != {key: list(value) for key, value in variant_registry().items()} or manifest["stage_eligibility"] != stage_eligibility(models) or manifest["resource_ledger_schema"] != resource_ledger_schema():
        raise ValueError("variant or stage registry is unsupported")
    capabilities = manifest["adapter_capabilities"]
    if not isinstance(capabilities, Mapping) or capabilities != {s.arm_id: {"capability_id": s.adapter_capability_id, "supported_tracks": list(s.supported_tracks)} for s in systems}:
        raise ValueError("adapter capability contradicts system arm")
    schedule = manifest["paired_family_seed_schedule"]
    if not isinstance(schedule, list) or not schedule or any(not isinstance(x, Mapping) for x in schedule):
        raise ValueError("paired family seed schedule is invalid")
    if any(set(x) != {"family_id", "seed", "pairing_key"} or not isinstance(x.get("seed"), int) for x in schedule):
        raise ValueError("paired family seed schedule uses an unsupported schema")
    expected_schedule = paired_family_seed_schedule((x["family_id"] for x in schedule), base_seed=min(x["seed"] for x in schedule))  # type: ignore[index]
    if schedule != list(expected_schedule):
        raise ValueError("paired family seed schedule is not deterministic")
    expected_dag = _build_dag(models, systems, tuple(schedule), manifest["stage_eligibility"])  # type: ignore[arg-type]
    if manifest["run_dag"] != expected_dag:
        raise ValueError("run DAG violates variant eligibility or support policy")
    if not isinstance(manifest["reporting_strata"], Mapping) or set(manifest["reporting_strata"]) != {x["reporting_stratum"] for x in expected_dag["nodes"]}:
        raise ValueError("reporting strata must prevent cross-model aggregation")
    for node in expected_dag["nodes"]:
        if set(node) != _NODE_FIELDS or node["denominator_status"] != "included" or node["score_namespace"] != ("native" if node["track"] == "native" else "controlled"):
            raise ValueError("run node schema or score namespace is invalid")
    if "frozen_sha256" in manifest:
        if status != CONFIRMATORY_FROZEN:
            raise ValueError("development plan cannot be frozen")
        body = dict(manifest); body.pop("frozen_sha256")
        if manifest["frozen_sha256"] != canonical_sha256(body):
            raise ValueError("experiment matrix frozen hash does not match content")


def build_experiment_matrix(*, family_ids: Iterable[str] = ("default-family",), base_seed: int = 20260827, budget: Budget | None = None, data_entitlement: DataEntitlement | None = None, models: tuple[ModelArm, ...] | None = None, systems: tuple[SystemArm, ...] | None = None, confirmatory: bool = False) -> dict[str, object]:
    actual_models, actual_systems = models or _default_model_arms(), systems or _default_system_arms()
    schedule = paired_family_seed_schedule(family_ids, base_seed=base_seed)
    eligibility = stage_eligibility(actual_models)
    dag = _build_dag(actual_models, actual_systems, schedule, eligibility)
    manifest: dict[str, object] = {"schema_version": SCHEMA_VERSION, "status": CONFIRMATORY_FROZEN if confirmatory else DEVELOPMENT_UNPINNED, "freeze_eligible": confirmatory, "models": [m.to_mapping() for m in actual_models], "systems": [s.to_mapping() for s in actual_systems], "unified_budget": (budget or Budget(64, 65536, 8192, 1800, 0)).to_mapping(), "data_entitlement": (data_entitlement or DataEntitlement("cmd-spec-v03-data", UNRESOLVED, UNRESOLVED, UNRESOLVED)).to_mapping(), "adapter_capabilities": {s.arm_id: {"capability_id": s.adapter_capability_id, "supported_tracks": list(s.supported_tracks)} for s in actual_systems}, "variant_registry": {key: list(value) for key, value in variant_registry().items()}, "stage_eligibility": eligibility, "paired_family_seed_schedule": list(schedule), "resource_ledger_schema": resource_ledger_schema(), "reporting_strata": {stratum: {"aggregate_with": "never", "reason": "model and track are reported separately"} for stratum in {x["reporting_stratum"] for x in dag["nodes"]}}, "run_dag": dag}
    validate_manifest(manifest, confirmatory=confirmatory)
    return manifest


def freeze_manifest(manifest: Mapping[str, object]) -> dict[str, object]:
    body = dict(manifest); body.pop("frozen_sha256", None)
    validate_manifest(body, confirmatory=True)
    body["frozen_sha256"] = canonical_sha256(body)
    return body
