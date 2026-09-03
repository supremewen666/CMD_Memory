#!/usr/bin/env python3
"""Minimal CPU OpenAI-compatible endpoint for the frozen shared embedder."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--served-model-name", default="all-MiniLM-L6-v2")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8003)
    args = parser.parse_args()
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(args.model_path, local_files_only=True, device="cpu")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *values: object) -> None:
            return

        def send_json(self, status: int, value: object) -> None:
            raw = json.dumps(value, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            if self.path == "/health":
                self.send_json(200, {"status": "ok"})
            elif self.path == "/v1/models":
                self.send_json(200, {"object": "list", "data": [{"id": args.served_model_name, "object": "model"}]})
            else:
                self.send_json(404, {"error": "not_found"})

        def do_POST(self) -> None:
            if self.path != "/v1/embeddings":
                self.send_json(404, {"error": "not_found"})
                return
            try:
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                payload = json.loads(raw)
                values = payload.get("input")
                texts = [values] if isinstance(values, str) else values
                if not isinstance(texts, list) or not texts or any(not isinstance(item, str) for item in texts):
                    raise ValueError("input must be a string or non-empty string list")
                vectors = model.encode(texts, normalize_embeddings=True).tolist()
                tokens = sum(max(1, math.ceil(len(text.encode("utf-8")) / 4)) for text in texts)
                self.send_json(200, {
                    "object": "list", "model": args.served_model_name,
                    "data": [{"object": "embedding", "index": index, "embedding": vector} for index, vector in enumerate(vectors)],
                    "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
                })
            except Exception as exc:
                self.send_json(400, {"error": {"type": type(exc).__name__, "message": str(exc)}})

    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
