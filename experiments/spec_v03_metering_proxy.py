#!/usr/bin/env python3
"""Run the namespace-scoped enforcing OpenAI-compatible proxy."""

from __future__ import annotations

import argparse
from pathlib import Path

from cmd_audit.spec_v03.industry_services import MeteringProxy, ProxyLimits, UsageReceiptStore, serve


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9100)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--receipt-root", type=Path, required=True)
    parser.add_argument("--max-llm-calls", type=int, default=20)
    parser.add_argument("--max-input-tokens", type=int, default=100000)
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    parser.add_argument("--max-gpu-seconds", type=int, default=300)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--disable-thinking", action="store_true")
    args = parser.parse_args()
    limits = ProxyLimits(args.max_llm_calls, args.max_input_tokens, args.max_output_tokens, args.max_gpu_seconds)
    service = MeteringProxy(
        upstream=args.upstream, receipts=UsageReceiptStore(args.receipt_root, limits),
        timeout_seconds=args.timeout_seconds, disable_thinking=args.disable_thinking,
    )
    serve(service, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
