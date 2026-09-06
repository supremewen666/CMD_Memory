#!/usr/bin/env python3
"""Run the official-process LycheeMemory isolation manager."""

from __future__ import annotations

import argparse
from pathlib import Path

from cmd_audit.spec_v03.industry_services import LycheeInstanceManager, serve


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--instance-root", type=Path, required=True)
    parser.add_argument("--receipt-root", type=Path, required=True)
    parser.add_argument("--official-commit", required=True)
    parser.add_argument("--public-base-url", default="http://127.0.0.1:9000")
    parser.add_argument("--llm-proxy-base-url", default="http://127.0.0.1:9100")
    parser.add_argument("--embedding-base-url", default="http://127.0.0.1:8003")
    parser.add_argument("--embedding-model", default="all-MiniLM-L6-v2")
    parser.add_argument("--first-instance-port", type=int, default=9200)
    parser.add_argument("--startup-timeout-seconds", type=float, default=180.0)
    parser.add_argument("--request-timeout-seconds", type=float, default=300.0)
    args = parser.parse_args()
    service = LycheeInstanceManager(
        repository=args.repository, python=args.python, root=args.instance_root,
        receipt_root=args.receipt_root, official_commit=args.official_commit,
        public_base_url=args.public_base_url, llm_proxy_base_url=args.llm_proxy_base_url,
        embedding_base_url=args.embedding_base_url, embedding_model=args.embedding_model,
        first_port=args.first_instance_port, startup_timeout_seconds=args.startup_timeout_seconds,
        request_timeout_seconds=args.request_timeout_seconds,
    )
    serve(service, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
