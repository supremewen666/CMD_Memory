#!/usr/bin/env python3
"""Run real, gold-free CMD predictions and seal them before official scoring."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from experiments.arena_backends import VLLMDualScoreArenaBackend
from experiments.locomo_arena import load_locomo_arena_cases
from experiments.longmemeval_arena import load_longmemeval_arena_cases
from experiments.sealed_memory_benchmark import predict_and_seal


DEFAULTS = {
    "locomo": Path("data/ghost_live_v2/raw_sources/locomo10.json"),
    "longmemeval": Path("data/external/longmemeval/input/longmemeval_s_cleaned.json"),
}


def preflight_openai_endpoint(backend: VLLMDualScoreArenaBackend) -> dict[str, object]:
    """Fail once, before dataset loading, when the model server is unavailable."""
    config = backend.answer_client.config
    models_url = config.base_url.rstrip("/") + "/models"
    headers = {"Accept": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    try:
        with urlopen(Request(models_url, headers=headers), timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"OpenAI-compatible endpoint is not ready at {models_url}: {exc}. "
            "Setting LLM_MODEL does not start vLLM; launch `vllm serve` first."
        ) from exc
    model_ids = [
        str(row.get("id"))
        for row in payload.get("data", [])
        if isinstance(row, dict) and row.get("id")
    ] if isinstance(payload, dict) else []
    if config.model not in model_ids:
        raise RuntimeError(
            f"LLM_MODEL={config.model!r} is not served by {models_url}; "
            f"available model ids={model_ids!r}. Set LLM_MODEL to the "
            "--served-model-name value."
        )
    return {"models_url": models_url, "model": config.model, "available": model_ids}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", choices=tuple(DEFAULTS), required=True)
    parser.add_argument("--cases", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=24)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--retrieval-top-k", type=int, default=5)
    parser.add_argument("--candidate-pool-k", type=int, default=10)
    parser.add_argument("--candidate-limit", type=int)
    parser.add_argument("--full-context", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-adversarial", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)
    path = args.cases or DEFAULTS[args.benchmark]
    backend = None
    if not args.validate_only:
        backend = VLLMDualScoreArenaBackend(enable_shadow_scoring=False)
        preflight_openai_endpoint(backend)
    kwargs = {
        "seed": args.seed,
        "limit": args.limit,
        "retrieval_top_k": args.retrieval_top_k,
        "candidate_pool_k": args.candidate_pool_k,
    }
    if args.benchmark == "locomo":
        cases = load_locomo_arena_cases(path, include_adversarial=args.include_adversarial, **kwargs)
    else:
        cases = load_longmemeval_arena_cases(path, **kwargs)
    if args.validate_only:
        print(json.dumps({
            "benchmark": args.benchmark,
            "case_count": len(cases),
            "runtime_uses_gold": False,
            "first_case_id": cases[0].case_id if cases else None,
        }, sort_keys=True))
        return 0
    assert backend is not None
    seal = predict_and_seal(
        benchmark=args.benchmark,
        cases=cases,
        backend=backend,
        dataset_path=path,
        output=args.output,
        candidate_limit=args.candidate_limit,
        include_full_context=args.full_context,
    )
    print(json.dumps(seal, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
