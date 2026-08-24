"""P4C execution over the gold-free ECC live ABI.

This runner owns runtime execution only.  A sealed benchmark evaluator is a
separate post-run consumer and is deliberately absent from this constructor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
from typing import Callable, Mapping, Sequence

from cmd_audit.core.state_codec import (
    append_jsonl_fsync,
    atomic_json_write,
    content_sha256,
)
from cmd_audit.repair.ecc import EccRepairReceipt, EccSyndrome, MemAuditEccAdapter
from cmd_audit.repair.ghost_ecology import (
    EcologySelection,
    GhostEcology,
    PatternResponsibility,
)
from cmd_audit.repair.incident_store import IncidentLedger


CASE_SCHEMA_VERSION = "cmd-p4c-ecc-case-v2"
LEGACY_CASE_SCHEMA_VERSION = "cmd-p4c-ecc-case-v1"
RUN_SCHEMA_VERSION = "cmd-p4c-ecc-run-v1"
JOURNAL_SCHEMA_VERSION = "cmd-p4c-ecc-journal-v1"


@dataclass(frozen=True)
class P4cRepairCandidate:
    skill_revision_id: str
    probe_id: str
    operator_sha256: str

    def __post_init__(self) -> None:
        if not self.skill_revision_id or not self.probe_id:
            raise ValueError("P4C candidate identity is required")
        if (
            len(self.operator_sha256) != 64
            or any(char not in "0123456789abcdef" for char in self.operator_sha256)
        ):
            raise ValueError("P4C operator_sha256 must be lowercase SHA-256")

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "P4cRepairCandidate":
        if set(value) != set(cls.__dataclass_fields__):
            raise ValueError("P4C candidate mapping is not closed")
        return cls(**dict(value))  # type: ignore[arg-type]

    def to_mapping(self) -> dict[str, object]:
        return {
            "skill_revision_id": self.skill_revision_id,
            "probe_id": self.probe_id,
            "operator_sha256": self.operator_sha256,
        }


@dataclass(frozen=True)
class P4cEccCase:
    case_id: str
    event_index: int
    observation: Mapping[str, object]
    candidates: tuple[P4cRepairCandidate, ...]
    runtime_memory_texts: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.case_id:
            raise ValueError("P4C case_id is required")
        if (
            isinstance(self.event_index, bool)
            or not isinstance(self.event_index, int)
            or self.event_index < 1
        ):
            raise ValueError("P4C event_index must be positive")
        if not isinstance(self.observation, Mapping):
            raise ValueError("P4C observation must be a mapping")
        syndrome = MemAuditEccAdapter().decode(self.observation)
        if syndrome.observed_at_event_index >= self.event_index:
            raise ValueError("P4C repair event must follow syndrome observation")
        if not self.candidates or len(
            {candidate.skill_revision_id for candidate in self.candidates}
        ) != len(self.candidates):
            raise ValueError("P4C candidates must be non-empty and unique")
        if not isinstance(self.runtime_memory_texts, Mapping) or any(
            not isinstance(memory_id, str) or not isinstance(text, str)
            for memory_id, text in self.runtime_memory_texts.items()
        ):
            raise ValueError("P4C runtime memory text view must map strings to strings")

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "P4cEccCase":
        schema = value.get("schema_version")
        expected = {"schema_version", "case_id", "event_index", "observation", "candidates"}
        if schema == CASE_SCHEMA_VERSION:
            expected.add("runtime_memory_texts")
        elif schema != LEGACY_CASE_SCHEMA_VERSION:
            raise ValueError("P4C case mapping is not closed or versioned")
        if set(value) != expected:
            raise ValueError("P4C case mapping is not closed or versioned")
        observation = value["observation"]
        candidates = value["candidates"]
        if not isinstance(observation, Mapping) or not isinstance(candidates, list):
            raise ValueError("P4C observation/candidates have invalid shape")
        return cls(
            case_id=str(value["case_id"]),
            event_index=value["event_index"],  # type: ignore[arg-type]
            observation=dict(observation),
            candidates=tuple(
                P4cRepairCandidate.from_mapping(candidate)
                for candidate in candidates
                if isinstance(candidate, Mapping)
            ),
            runtime_memory_texts=(
                dict(value["runtime_memory_texts"])
                if isinstance(value.get("runtime_memory_texts"), Mapping)
                else {}
            ),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": CASE_SCHEMA_VERSION,
            "case_id": self.case_id,
            "event_index": self.event_index,
            "observation": dict(self.observation),
            "candidates": [candidate.to_mapping() for candidate in self.candidates],
            "runtime_memory_texts": dict(self.runtime_memory_texts),
        }


@dataclass(frozen=True)
class P4cGhostBinding:
    failure_id: str
    responsibilities: tuple[PatternResponsibility, ...]
    registry_id: str
    skill_priors: tuple[tuple[str, float], ...] = ()

    def __post_init__(self) -> None:
        if not self.failure_id or not self.registry_id or not self.responsibilities:
            raise ValueError("P4C GHOST binding is incomplete")
        if len(dict(self.skill_priors)) != len(self.skill_priors) or any(
            not isinstance(skill_id, str)
            or not skill_id
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or not -1.0 <= float(value) <= 1.0
            for skill_id, value in self.skill_priors
        ):
            raise ValueError("P4C GHOST skill priors are invalid")


class P4cGhostRouter:
    """Bind P4C cases to durable GHOST selection and receipt-only updates."""

    def __init__(
        self,
        ecology: GhostEcology,
        bindings: Mapping[str, P4cGhostBinding],
    ) -> None:
        if not isinstance(ecology, GhostEcology):
            raise TypeError("P4C GHOST router requires GhostEcology")
        if not bindings or any(
            not isinstance(binding, P4cGhostBinding)
            for binding in bindings.values()
        ):
            raise ValueError("P4C GHOST bindings must be non-empty and typed")
        self.ecology = ecology
        self.bindings = dict(bindings)

    def select(self, case: P4cEccCase, syndrome: EccSyndrome) -> EcologySelection:
        binding = self.bindings.get(case.case_id)
        if binding is None:
            raise ValueError("P4C case has no GHOST binding")
        try:
            failure = self.ecology.failures[binding.failure_id]
        except KeyError as exc:
            raise ValueError("P4C GHOST failure binding is unknown") from exc
        if syndrome.incident_id != str(case.observation["incident_id"]):
            raise ValueError("P4C syndrome/case incident binding mismatch")
        return self.ecology.select(
            failure,
            responsibilities=binding.responsibilities,
            candidate_skill_revision_ids=tuple(
                candidate.skill_revision_id for candidate in case.candidates
            ),
            registry_id=binding.registry_id,
            event_index=syndrome.observed_at_event_index,
            skill_priors=(
                None if not binding.skill_priors else dict(binding.skill_priors)
            ),
        )

    def observe_receipt(
        self,
        decision: EcologySelection,
        receipt: EccRepairReceipt,
        *,
        event_index: int,
    ) -> Mapping[str, object]:
        return self.ecology.observe_receipt(
            decision, receipt, event_index=event_index
        )

    @property
    def snapshot_sha256(self) -> str:
        return str(self.ecology.router.snapshot["snapshot_sha256"])


class _P4cRunJournal:
    _FIELDS = frozenset({
        "schema_version", "event_index", "position", "case_id",
        "run_manifest_sha256", "case_stream_sha256", "receipt_sha256",
        "incident_head", "router_snapshot_sha256", "previous_hash", "event_hash",
    })

    def __init__(
        self,
        path: Path,
        *,
        run_manifest_sha256: str,
        case_stream_sha256: str,
    ) -> None:
        self.path = Path(path)
        self.run_manifest_sha256 = run_manifest_sha256
        self.case_stream_sha256 = case_stream_sha256
        self.events: list[dict[str, object]] = []
        self.head = "0" * 64
        if self.path.exists():
            self._load()

    def append(
        self,
        *,
        position: int,
        case_id: str,
        receipt_sha256: str,
        incident_head: str,
        router_snapshot_sha256: str,
    ) -> None:
        event: dict[str, object] = {
            "schema_version": JOURNAL_SCHEMA_VERSION,
            "event_index": len(self.events) + 1,
            "position": position,
            "case_id": case_id,
            "run_manifest_sha256": self.run_manifest_sha256,
            "case_stream_sha256": self.case_stream_sha256,
            "receipt_sha256": receipt_sha256,
            "incident_head": incident_head,
            "router_snapshot_sha256": router_snapshot_sha256,
            "previous_hash": self.head,
        }
        event["event_hash"] = content_sha256(
            event, ensure_ascii=False, allow_nan=False
        )
        append_jsonl_fsync(
            self.path, event, ensure_ascii=False, allow_nan=False
        )
        self.events.append(event)
        self.head = str(event["event_hash"])

    def _load(self) -> None:
        for line in self.path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if not isinstance(event, dict) or set(event) != self._FIELDS:
                raise ValueError("P4C journal event is not closed")
            expected = content_sha256(
                {key: value for key, value in event.items() if key != "event_hash"},
                ensure_ascii=False,
                allow_nan=False,
            )
            if (
                event["schema_version"] != JOURNAL_SCHEMA_VERSION
                or event["event_index"] != len(self.events) + 1
                or event["position"] != len(self.events)
                or event["previous_hash"] != self.head
                or event["event_hash"] != expected
            ):
                raise ValueError("P4C journal hash chain mismatch")
            if (
                event["run_manifest_sha256"] != self.run_manifest_sha256
                or event["case_stream_sha256"] != self.case_stream_sha256
            ):
                raise ValueError("P4C journal manifest/case-stream mismatch")
            self.events.append(event)
            self.head = str(event["event_hash"])


class P4cEccRunner:
    """Execute immutable P4C runtime cases through receipt-only ECC repair."""

    def __init__(
        self,
        cases: Sequence[P4cEccCase],
        *,
        output_dir: Path,
        router: object,
        store_factory: Callable[[P4cEccCase], object],
        evaluator_factory: Callable[[P4cEccCase], object],
        run_mode: str = "fresh",
    ) -> None:
        if not cases:
            raise ValueError("P4C requires at least one runtime case")
        self.cases = tuple(cases)
        indexes = tuple(case.event_index for case in self.cases)
        if indexes != tuple(sorted(indexes)) or len(set(indexes)) != len(indexes):
            raise ValueError("P4C case event indexes must be strictly increasing")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("P4C case IDs must be unique")
        if not callable(getattr(router, "select", None)) or not callable(
            getattr(router, "observe_receipt", None)
        ):
            raise TypeError("P4C router requires select() and observe_receipt()")
        if run_mode not in {"fresh", "resume"}:
            raise ValueError("P4C run_mode must be fresh or resume")
        self.run_mode = run_mode
        self.output_dir = Path(output_dir)
        self.router = router
        self.store_factory = store_factory
        self.evaluator_factory = evaluator_factory
        self.adapter = MemAuditEccAdapter()

    def run(self) -> dict[str, object]:
        if (
            self.run_mode == "fresh"
            and self.output_dir.exists()
            and any(self.output_dir.iterdir())
        ):
            raise ValueError("fresh P4C run refuses a non-empty output directory")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        case_stream_sha256 = content_sha256(
            [case.to_mapping() for case in self.cases],
            ensure_ascii=False,
            allow_nan=False,
        )
        run_manifest_sha256 = content_sha256(
            {
                "schema_version": RUN_SCHEMA_VERSION,
                "case_stream_sha256": case_stream_sha256,
                "runtime_abi": "EccSyndrome->EccRepairReceipt",
            },
            ensure_ascii=False,
            allow_nan=False,
        )
        incidents = IncidentLedger(self.output_dir / "incidents.jsonl")
        receipts_path = self.output_dir / "repair_receipts.jsonl"
        journal = _P4cRunJournal(
            self.output_dir / "case_completions.jsonl",
            run_manifest_sha256=run_manifest_sha256,
            case_stream_sha256=case_stream_sha256,
        )
        existing_receipts: list[EccRepairReceipt] = []
        if receipts_path.exists():
            for line in receipts_path.read_text(encoding="utf-8").splitlines():
                try:
                    raw = json.loads(line)
                    existing_receipts.append(EccRepairReceipt.from_mapping(raw))
                except Exception as exc:
                    raise ValueError("P4C receipt prefix is invalid") from exc
        if len(existing_receipts) != len(journal.events):
            raise ValueError("P4C receipt/journal prefix length mismatch")
        for position, (case, receipt, event) in enumerate(
            zip(
                self.cases[: len(existing_receipts)],
                existing_receipts,
                journal.events,
                strict=True,
            )
        ):
            if (
                event["position"] != position
                or event["case_id"] != case.case_id
                or event["receipt_sha256"] != receipt.content_hash
            ):
                raise ValueError("P4C receipt prefix binding mismatch")
        if journal.events and journal.events[-1]["incident_head"] != incidents.head_hash:
            raise ValueError("P4C incident head does not match completed prefix")
        if len(journal.events) > len(self.cases):
            raise ValueError("P4C journal exceeds frozen case stream")

        receipt_hashes = [receipt.content_hash for receipt in existing_receipts]
        committed = sum(int(receipt.committed) for receipt in existing_receipts)
        rolled_back = sum(int(receipt.rolled_back) for receipt in existing_receipts)

        if self.run_mode == "resume" and journal.events:
            current_root = getattr(self.router, "snapshot_sha256", None)
            expected_root = journal.events[-1]["router_snapshot_sha256"]
            if current_root is not None and current_root != expected_root:
                raise ValueError("P4C router snapshot does not match completed prefix")
            if current_root is None and len(journal.events) < len(self.cases):
                raise ValueError(
                    "P4C partial resume requires a verifiable router snapshot"
                )

        for position, case in enumerate(
            self.cases[len(journal.events):], start=len(journal.events)
        ):
            syndrome = self.adapter.decode(case.observation)
            decision = self.router.select(case, syndrome)
            selected_skill = getattr(decision, "selected_skill_revision_id", None)
            candidate = next(
                (
                    row
                    for row in case.candidates
                    if row.skill_revision_id == selected_skill
                ),
                None,
            )
            if candidate is None:
                raise ValueError("P4C router selected an unregistered candidate")
            receipt = self.adapter.execute_shadow_repair(
                syndrome,
                selection_id=str(getattr(decision, "selection_id", "")),
                selected_skill_revision_id=candidate.skill_revision_id,
                probe_id=candidate.probe_id,
                observed_after_event_index=case.event_index,
                store=self.store_factory(case),
                evaluator=self.evaluator_factory(case),
            )
            _incident_event, router_snapshot = self.adapter.settle_repair(
                syndrome,
                receipt,
                ledger=incidents,
                ecology=self.router,
                decision=decision,
                event_index=case.event_index,
            )
            append_jsonl_fsync(
                receipts_path, receipt.to_mapping(), ensure_ascii=False, allow_nan=False
            )
            receipt_hashes.append(receipt.content_hash)
            committed += int(receipt.committed)
            rolled_back += int(receipt.rolled_back)
            journal.append(
                position=position,
                case_id=case.case_id,
                receipt_sha256=receipt.content_hash,
                incident_head=incidents.head_hash,
                router_snapshot_sha256=str(router_snapshot["snapshot_sha256"]),
            )

        result: dict[str, object] = {
            "schema_version": RUN_SCHEMA_VERSION,
            "status": "success",
            "case_count": len(self.cases),
            "committed": committed,
            "rolled_back": rolled_back,
            "runtime_uses_gold": False,
            "runtime_uses_labels": False,
            "same_trace_answer_replay": False,
            "runtime_abi": "EccSyndrome->EccRepairReceipt",
            "case_stream_sha256": case_stream_sha256,
            "run_manifest_sha256": run_manifest_sha256,
            "receipt_root": content_sha256(
                receipt_hashes, ensure_ascii=False, allow_nan=False
            ),
            "incident_head": incidents.head_hash,
            "case_completion_head": journal.head,
            "sealed_evaluator": "not_opened_by_runtime",
        }
        atomic_json_write(
            self.output_dir / "manifest.json",
            result,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            trailing_newline=True,
        )
        return result


def load_p4c_cases(path: Path) -> tuple[P4cEccCase, ...]:
    """Load the immutable gold-free incident overlay used by P4C runtime."""
    cases: list[P4cEccCase] = []
    for line_number, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid P4C overlay JSON at line {line_number}") from exc
        if not isinstance(raw, dict):
            raise ValueError(f"P4C overlay line {line_number} is not an object")
        cases.append(P4cEccCase.from_mapping(raw))
    if not cases:
        raise ValueError("P4C overlay contains no runtime cases")
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("P4C overlay case IDs must be unique")
    return tuple(cases)


def audit_p4c_run(
    *,
    run_dir: Path,
    sealed_sidecar: Path,
    output_path: Path,
) -> dict[str, object]:
    """Score a completed P4C run without writing back into runtime state."""
    run_dir = Path(run_dir).resolve()
    output_path = Path(output_path).resolve()
    try:
        output_path.relative_to(run_dir)
    except ValueError:
        pass
    else:
        raise ValueError("sealed audit output must be outside the runtime directory")

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != RUN_SCHEMA_VERSION
        or manifest.get("status") != "success"
    ):
        raise ValueError("P4C runtime manifest is invalid or incomplete")
    expected_manifest_root = content_sha256(
        {
            "schema_version": RUN_SCHEMA_VERSION,
            "case_stream_sha256": manifest.get("case_stream_sha256"),
            "runtime_abi": "EccSyndrome->EccRepairReceipt",
        },
        ensure_ascii=False,
        allow_nan=False,
    )
    if (
        manifest.get("run_manifest_sha256") != expected_manifest_root
        or manifest.get("runtime_abi") != "EccSyndrome->EccRepairReceipt"
    ):
        raise ValueError("P4C runtime manifest integrity mismatch")
    sidecar = json.loads(Path(sealed_sidecar).read_text(encoding="utf-8"))
    if not isinstance(sidecar, dict) or set(sidecar) != {
        "schema_version", "run_manifest_sha256", "rows"
    } or sidecar.get("schema_version") != "cmd-p4c-sealed-audit-v1":
        raise ValueError("P4C sealed audit sidecar is not closed")
    if sidecar["run_manifest_sha256"] != manifest["run_manifest_sha256"]:
        raise ValueError("P4C sealed audit manifest binding mismatch")
    rows = sidecar["rows"]
    if not isinstance(rows, list):
        raise ValueError("P4C sealed audit rows must be a list")

    receipts = [
        EccRepairReceipt.from_mapping(json.loads(line))
        for line in (run_dir / "repair_receipts.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    journal = _P4cRunJournal(
        run_dir / "case_completions.jsonl",
        run_manifest_sha256=str(manifest["run_manifest_sha256"]),
        case_stream_sha256=str(manifest["case_stream_sha256"]),
    )
    if len(receipts) != len(journal.events) or len(rows) != len(receipts):
        raise ValueError("P4C sealed audit does not exactly cover completed receipts")
    if (
        manifest.get("receipt_root")
        != content_sha256(
            [receipt.content_hash for receipt in receipts],
            ensure_ascii=False,
            allow_nan=False,
        )
        or manifest.get("case_completion_head") != journal.head
    ):
        raise ValueError("P4C runtime receipt/journal manifest mismatch")
    incident_events = IncidentLedger(run_dir / "incidents.jsonl").events
    if manifest.get("incident_head") != (
        str(incident_events[-1]["event_hash"]) if incident_events else "0" * 64
    ):
        raise ValueError("P4C runtime incident manifest mismatch")
    mechanisms = {
        str(event["event_id"]): str(event["mechanism"])
        for event in incident_events
    }
    row_fields = {
        "case_id", "receipt_sha256", "expected_incident",
        "expected_mechanism", "repair_expected", "task_correct_after",
        "sealed_label",
    }
    correct_after = 0
    false_repair_numerator = 0
    false_repair_denominator = 0
    expected_incidents = 0
    detected_incidents = 0
    typed_correct = 0
    typed_denominator = 0
    for index, (row, receipt, event) in enumerate(
        zip(rows, receipts, journal.events, strict=True)
    ):
        if not isinstance(row, dict) or set(row) != row_fields:
            raise ValueError("P4C sealed audit row is not closed")
        if (
            row["case_id"] != event["case_id"]
            or row["receipt_sha256"] != receipt.content_hash
        ):
            raise ValueError("P4C sealed audit receipt binding mismatch")
        for name in ("expected_incident", "repair_expected", "task_correct_after"):
            if not isinstance(row[name], bool):
                raise ValueError(f"P4C sealed audit {name} must be boolean")
        if not isinstance(row["expected_mechanism"], str) or not isinstance(
            row["sealed_label"], str
        ):
            raise ValueError("P4C sealed audit labels must be strings")
        correct_after += int(row["task_correct_after"])
        if not row["repair_expected"]:
            false_repair_denominator += 1
            false_repair_numerator += int(receipt.committed)
        if row["expected_incident"]:
            expected_incidents += 1
            detected_incidents += int(receipt.syndrome_id in mechanisms)
        if row["expected_mechanism"]:
            typed_denominator += 1
            typed_correct += int(
                mechanisms.get(receipt.syndrome_id) == row["expected_mechanism"]
            )

    report: dict[str, object] = {
        "schema_version": "cmd-p4c-sealed-audit-report-v1",
        "run_manifest_sha256": manifest["run_manifest_sha256"],
        "case_count": len(rows),
        "accuracy": correct_after / len(rows) if rows else None,
        "false_repair_rate": (
            false_repair_numerator / false_repair_denominator
            if false_repair_denominator
            else None
        ),
        "incident_recall": (
            detected_incidents / expected_incidents if expected_incidents else None
        ),
        "incident_type_accuracy": (
            typed_correct / typed_denominator if typed_denominator else None
        ),
        "runtime_feedback_written": False,
        "offline_evaluator_only": True,
        "sealed_sidecar_sha256": content_sha256(
            sidecar, ensure_ascii=False, allow_nan=False
        ),
    }
    atomic_json_write(
        output_path,
        report,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        trailing_newline=True,
    )
    return report


__all__ = [
    "CASE_SCHEMA_VERSION",
    "JOURNAL_SCHEMA_VERSION",
    "RUN_SCHEMA_VERSION",
    "P4cEccCase",
    "P4cEccRunner",
    "P4cGhostBinding",
    "P4cGhostRouter",
    "P4cRepairCandidate",
    "audit_p4c_run",
    "load_p4c_cases",
]
