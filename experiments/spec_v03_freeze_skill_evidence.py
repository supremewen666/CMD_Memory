#!/usr/bin/env python3
"""Freeze MemSkill or ERSkill inference records into the Stage 9 contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from wrappers.skill_evidence_common import ARTIFACT_SCHEMA, validate_frozen_skill_artifact


IMPLEMENTATIONS = {
    "memskill": frozenset({"official_memskill_checkpoint_export"}),
    "erskill": frozenset({
        "official_erskill_artifact",
        "paper_faithful_erskill_reimplementation",
    }),
}
RECORD_FIELDS = frozenset({
    "case_id", "evidence", "selected_skill_ids", "retrieval_trace",
    "source_event_ids", "usage",
})


def _read_rows(path: Path) -> list[object]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    value = json.loads(text)
    if not isinstance(value, list):
        raise ValueError("records input must be a JSON array or JSONL file")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system-id", choices=tuple(IMPLEMENTATIONS), required=True)
    parser.add_argument("--implementation", required=True)
    parser.add_argument("--artifact-revision", required=True)
    parser.add_argument("--producer-repository", required=True)
    parser.add_argument("--producer-commit", required=True)
    parser.add_argument(
        "--training-split", action="append", required=True,
        choices=("D_skill", "D_router", "D_cal", "D_lifecycle"),
    )
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    allowed = IMPLEMENTATIONS[args.system_id]
    if args.implementation not in allowed:
        parser.error(f"{args.implementation!r} is not valid for {args.system_id}")
    producer_commit = args.producer_commit.lower()
    if len(producer_commit) != 40 or any(character not in "0123456789abcdef" for character in producer_commit):
        parser.error("--producer-commit must be an exact 40-character git commit")

    records: dict[str, object] = {}
    for index, row in enumerate(_read_rows(args.records), start=1):
        if not isinstance(row, dict) or set(row) != RECORD_FIELDS:
            raise ValueError(f"records row {index} must use the closed schema")
        case_id = row["case_id"]
        if not isinstance(case_id, str) or not case_id:
            raise ValueError(f"records row {index} has an invalid case_id")
        if case_id in records:
            raise ValueError(f"duplicate case_id in records: {case_id}")
        records[case_id] = {key: value for key, value in row.items() if key != "case_id"}

    artifact = {
        "schema_version": ARTIFACT_SCHEMA,
        "system_id": args.system_id,
        "implementation": args.implementation,
        "artifact_revision": args.artifact_revision,
        "producer_repository": args.producer_repository,
        "producer_commit": producer_commit,
        "frozen": True,
        "training_splits": list(dict.fromkeys(args.training_split)),
        "records": records,
    }
    validate_frozen_skill_artifact(
        artifact, expected_system_id=args.system_id,
        allowed_implementations=allowed,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(artifact, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, args.output)
    print(f"[RESULT] records={len(records)}")
    print(f"[RESULT] artifact_sha256={_sha256(args.output)}")
    print(f"[RESULT] output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
