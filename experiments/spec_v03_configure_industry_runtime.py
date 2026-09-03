#!/usr/bin/env python3
"""Bind controlled industry configs to local enforcing services."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def write(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--lightmem-config", type=Path, required=True)
    parser.add_argument("--mem0-config", type=Path, required=True)
    parser.add_argument("--usage-root", type=Path, required=True)
    parser.add_argument("--instance-receipt-root", type=Path, required=True)
    parser.add_argument("--metering-url", default="http://127.0.0.1:9100")
    parser.add_argument("--lychee-manager-url", default="http://127.0.0.1:9000")
    parser.add_argument("--lychee-commit", required=True)
    args = parser.parse_args()
    protocol = json.loads(args.protocol.read_text(encoding="utf-8"))
    lightmem = json.loads(args.lightmem_config.read_text(encoding="utf-8"))
    mem0 = json.loads(args.mem0_config.read_text(encoding="utf-8"))
    receipt_template = str(args.usage_root.resolve() / "{namespace}.json")
    backend = {"mode": "enforcing_proxy_receipt", "receipt_path": receipt_template, "bootstrap_url": args.metering_url}
    for system in ("lightmem", "lycheemem", "mem0"):
        protocol["systems"][system]["backend_usage"] = dict(backend)
    protocol["systems"]["lycheemem"]["manager_url"] = args.lychee_manager_url
    protocol["systems"]["lycheemem"]["base_url"] = args.lychee_manager_url.rstrip("/") + "/instances/{namespace}"
    protocol["systems"]["lycheemem"]["instance_receipt_path"] = str(args.instance_receipt_root.resolve() / "{namespace}.json")
    protocol["systems"]["lycheemem"]["expected_commit"] = args.lychee_commit
    lightmem["memory_manager"]["configs"]["openai_base_url"] = args.metering_url.rstrip("/") + "/{namespace}/v1"
    mem0["llm"]["config"]["openai_base_url"] = args.metering_url.rstrip("/") + "/{namespace}/v1"
    args.usage_root.mkdir(parents=True, exist_ok=True)
    args.instance_receipt_root.mkdir(parents=True, exist_ok=True)
    write(args.protocol, protocol)
    write(args.lightmem_config, lightmem)
    write(args.mem0_config, mem0)
    print(f"[RESULT] protocol={args.protocol}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
