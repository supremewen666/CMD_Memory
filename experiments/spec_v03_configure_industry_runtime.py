#!/usr/bin/env python3
"""Bind controlled industry configs to local enforcing services."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Mapping


def write(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--mem0-config", type=Path, required=True)
    parser.add_argument("--memskill-artifact", type=Path, required=True)
    parser.add_argument("--erskill-artifact", type=Path, required=True)
    parser.add_argument(
        "--erskill-implementation",
        choices=("official_erskill_artifact", "paper_faithful_erskill_reimplementation"),
        default="paper_faithful_erskill_reimplementation",
    )
    parser.add_argument("--usage-root", type=Path, required=True)
    parser.add_argument("--metering-url", default="http://127.0.0.1:9100")
    parser.add_argument("--head-endpoint")
    parser.add_argument("--head-model-id")
    parser.add_argument("--head-model-snapshot")
    parser.add_argument("--head-api-key-env")
    args = parser.parse_args()
    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    mem0 = json.loads(args.mem0_config.read_text(encoding="utf-8"))
    systems = protocol.get("systems") if isinstance(protocol, Mapping) else None
    mem0_system = systems.get("mem0") if isinstance(systems, Mapping) else None
    if not isinstance(mem0_system, Mapping):
        parser.error("protocol must contain an existing mem0 system config")
    if not isinstance(mem0, Mapping) or not isinstance(mem0.get("llm"), Mapping) or not isinstance(mem0["llm"].get("config"), Mapping):
        parser.error("mem0 config must contain llm.config")
    head = protocol.get("head") if isinstance(protocol, Mapping) else None
    if not isinstance(head, Mapping):
        parser.error("protocol must contain an existing head config")
    head_values = (args.head_endpoint, args.head_model_id, args.head_model_snapshot)
    if any(value is not None for value in head_values) and not all(value for value in head_values):
        parser.error("head endpoint, model id, and model snapshot must be supplied together")
    for path in (args.memskill_artifact, args.erskill_artifact):
        if not path.is_file():
            parser.error(f"frozen evidence artifact does not exist: {path}")
    receipt_template = str(args.usage_root.resolve() / "{namespace}.json")
    backend = {"mode": "enforcing_proxy_receipt", "receipt_path": receipt_template, "bootstrap_url": args.metering_url}
    memskill_system = {
        "artifact_path": str(args.memskill_artifact.resolve()),
        "artifact_sha256": sha256(args.memskill_artifact),
        "implementation": "official_memskill_checkpoint_export",
    }
    erskill_system = {
        "artifact_path": str(args.erskill_artifact.resolve()),
        "artifact_sha256": sha256(args.erskill_artifact),
        "implementation": args.erskill_implementation,
    }
    mem0_system = dict(mem0_system)
    mem0_system["config_path"] = str(args.mem0_config.resolve())
    mem0_system["backend_usage"] = dict(backend)
    protocol["systems"] = {
        "memskill": memskill_system,
        "erskill": erskill_system,
        "mem0": mem0_system,
    }
    if all(head_values):
        protocol["head"] = {
            **dict(head),
            "endpoint": args.head_endpoint,
            "model_id": args.head_model_id,
            "model_snapshot": args.head_model_snapshot,
            "api_key_env": args.head_api_key_env or "MODEL_API_KEY",
        }
    mem0["llm"]["config"]["openai_base_url"] = args.metering_url.rstrip("/") + "/{namespace}/v1"
    args.usage_root.mkdir(parents=True, exist_ok=True)
    write(args.protocol, protocol)
    write(args.mem0_config, mem0)
    print(f"[RESULT] protocol={args.protocol}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
