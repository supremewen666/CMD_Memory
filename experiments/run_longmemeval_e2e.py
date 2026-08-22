"""P3C LongMemEval E2E: frozen retrieval -> prediction seal -> score only.

This runner is offline-first.  ``fake`` is deterministic; an OpenAI-compatible
backend is opt-in, lazy, and requires an explicit JSON config file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from cmd_audit.core.state_codec import atomic_json_write, content_sha256
from experiments.run_longmemeval_m0_r1 import _safe_instance_name, iter_json_array
from experiments.v4_run_checkpoint import OutcomeJournal, RunCheckpoint, RunCheckpointStore

ARMS = ("vanilla", "static", "cmd", "ghost")
PREDICT_FORBIDDEN = frozenset({"answer", "answer_session_ids", "oracle", "gold", "question_type"})
DEFAULT_PROMPT = "Answer the question using only the retrieved memories. If unsupported, say Unknown."


@dataclass(frozen=True)
class AnswerRequest:
    question: str
    memories: tuple[Mapping[str, str], ...]
    prompt: str
    temperature: float


@dataclass(frozen=True)
class AnswerResult:
    hypothesis: str
    token_or_char_estimate: int
    retries: int = 0


class Answerer(Protocol):
    model_id: str
    def answer(self, request: AnswerRequest) -> AnswerResult: ...


class Judge(Protocol):
    model_id: str
    def score(self, *, question: str, hypothesis: str, reference: str) -> Mapping[str, object]: ...


class FakeAnswerer:
    model_id = "fake-answerer-v1"
    def answer(self, request: AnswerRequest) -> AnswerResult:
        # A deterministic wiring backend, deliberately not an answer-quality model.
        text = request.memories[0]["content"] if request.memories else "Unknown"
        return AnswerResult(text[: min(240, len(text))] or "Unknown", len(request.question) + sum(len(x["content"]) for x in request.memories))


class FakeJudge:
    model_id = "fake-judge-v1"
    def score(self, *, question: str, hypothesis: str, reference: str) -> Mapping[str, object]:
        return {"metric": "normalized_exact", "correct": _norm(hypothesis) == _norm(reference)}


class OpenAICompatibleAnswerer:
    """Lazy adapter; constructed only after explicit --llm-config selection."""
    def __init__(self, config: Mapping[str, object], model_id: str, temperature: float) -> None:
        if not config.get("base_url") or not config.get("api_key"):
            raise ValueError("live backend requires explicit config with base_url and api_key")
        self.model_id, self._config, self._temperature = model_id, dict(config), temperature
    def answer(self, request: AnswerRequest) -> AnswerResult:
        from cmd_audit.core.llm_client import LLMClient, LLMClientConfig
        client = LLMClient(LLMClientConfig(base_url=str(self._config["base_url"]), api_key=str(self._config["api_key"]), model=self.model_id, temperature=self._temperature))
        body = request.prompt + "\nQuestion: " + request.question + "\nMemories:\n" + "\n".join(m["content"] for m in request.memories)
        result = client.generate(body)
        return AnswerResult(result, len(body) + len(result))


def _norm(value: str) -> str:
    return re.sub(r"\W+", "", value).casefold()


def _sha_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping): raise ValueError(f"{path}: expected object")
    return value


def _input_cases(path: Path, limit: int | None = None) -> list[Mapping[str, str]]:
    cases: list[Mapping[str, str]] = []
    for row in iter_json_array(path):
        # Official rows may carry evaluation labels.  Projection, rather than
        # forwarding a row, makes those labels unreachable from the answerer.
        qid, question = row.get("question_id"), row.get("question")
        if not isinstance(qid, str) or not isinstance(question, str): raise ValueError("prediction input requires question_id and question")
        cases.append({"question_id": qid, "question": question})
        if limit and len(cases) >= limit: break
    return cases


def _retrieval(retrieval_run: Path, arm: str, question_id: str, budget: int) -> tuple[Mapping[str, str], ...]:
    manifest = _read_json(retrieval_run / "manifest.json")
    if manifest.get("schema_version") != "cmd-longmemeval-m0-r1-v3": raise ValueError("retrieval manifest schema mismatch")
    artifact = _read_json(retrieval_run / "retrieval" / arm / f"{_safe_instance_name(question_id)}.json")
    if artifact.get("question_id") != question_id or artifact.get("arm") != arm: raise ValueError("retrieval artifact identity mismatch")
    records = artifact.get("records")
    if not isinstance(records, list): raise ValueError("retrieval records missing; rerun P3A with retrieval artifact v1")
    result: list[Mapping[str, str]] = []; used = 0
    for record in records:
        if not isinstance(record, Mapping) or not all(isinstance(record.get(k), str) for k in ("memory_id", "content", "source_hash")): raise ValueError("invalid retrieval record")
        content = str(record["content"])
        if content_sha256(content, ensure_ascii=False, allow_nan=False) != record["source_hash"]: raise ValueError("retrieval content/source root mismatch")
        if used + len(content) > budget:
            content = content[: max(0, budget - used)]
        if content:
            result.append({"memory_id": str(record["memory_id"]), "content": content, "source_hash": str(record["source_hash"])})
            used += len(content)
        if used >= budget: break
    return tuple(result)


def _answerer(backend: str, model: str, config: Mapping[str, object] | None, temperature: float) -> Answerer:
    if backend == "fake": return FakeAnswerer()
    if backend == "openai-compatible":
        if config is None: raise ValueError("live backend is disabled by default; pass --llm-config explicitly")
        return OpenAICompatibleAnswerer(config, model, temperature)
    raise ValueError("unknown answerer backend")


def predict(*, data: Path, retrieval_run: Path, output: Path, answerer_backend: str = "fake", answerer_model: str = "fake-answerer-v1", prompt: str = DEFAULT_PROMPT, temperature: float = 0.0, context_budget: int = 12000, arms: Sequence[str] = ARMS, resume: bool = False, limit: int | None = None, llm_config: Mapping[str, object] | None = None) -> Mapping[str, object]:
    if context_budget < 1 or not arms or any(x not in ARMS for x in arms) or len(set(arms)) != len(arms): raise ValueError("invalid context budget or arms")
    output, retrieval_run = Path(output), Path(retrieval_run)
    cases = _input_cases(Path(data), limit)
    a = _answerer(answerer_backend, answerer_model, llm_config, temperature)
    binding = {"dataset_root": _sha_file(Path(data)), "retrieval_manifest_root": _sha_file(retrieval_run / "manifest.json"), "prompt_hash": content_sha256(prompt), "answerer_backend": answerer_backend, "answerer_model": a.model_id, "temperature": temperature, "context_budget": context_budget, "arms": list(arms)}
    root = content_sha256(binding)
    if not resume and output.exists() and any(output.iterdir()): raise ValueError("fresh refuses non-empty output")
    journal = OutcomeJournal(output / "prediction_outcomes.jsonl"); checkpoints = RunCheckpointStore(output / "prediction_checkpoint")
    start = checkpoints.load_latest(manifest_sha256=root, case_stream_sha256=content_sha256(cases)).next_position if resume else 0
    predictions = output / "predictions"; predictions.mkdir(parents=True, exist_ok=True)
    for pos, case in enumerate(cases[start:], start + 1):
        rows = []
        for arm in arms:
            memories = _retrieval(retrieval_run, arm, case["question_id"], context_budget)
            started = time.perf_counter(); response = a.answer(AnswerRequest(case["question"], memories, prompt, temperature)); elapsed = time.perf_counter() - started
            row = {"question_id": case["question_id"], "hypothesis": response.hypothesis}
            target = predictions / f"{arm}.jsonl"
            existing = target.read_text(encoding="utf-8") if target.exists() else ""
            if not any(json.loads(line).get("question_id") == case["question_id"] for line in existing.splitlines() if line):
                with target.open("a", encoding="utf-8") as handle: handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            rows.append({"arm": arm, "question_id": case["question_id"], "prediction_root": content_sha256(row), "retrieval_root": content_sha256(memories), "latency_seconds": elapsed, "calls": 1, "token_or_char_estimate": response.token_or_char_estimate, "retry_count": response.retries})
        journal.append(pos, case["question_id"], rows)
        checkpoints.commit(RunCheckpoint("longmemeval-e2e-predict", root, content_sha256(cases), pos, pos, {}, {}, {}, "0"*64, "0"*64, {}, outcome_head=journal.head, outcome_count=len(journal.events)))
    file_roots = {path.name: _sha_file(path) for path in sorted(predictions.glob("*.jsonl"))}
    seal = {"schema_version": "cmd-longmemeval-e2e-prediction-seal-v1", **binding, "binding_root": root, "prediction_outcome_root": journal.head, "prediction_count": len(journal.events), "prediction_file_roots": file_roots, "sealed": True, "official_jsonl_shape": ["question_id", "hypothesis"]}
    atomic_json_write(output / "prediction_seal.json", seal, ensure_ascii=False, allow_nan=False, indent=2, trailing_newline=True)
    return seal


def score(*, reference: Path, output: Path, judge_backend: str = "none", judge_model: str = "none", resume: bool = False) -> Mapping[str, object]:
    output = Path(output); seal = _read_json(output / "prediction_seal.json")
    if not seal.get("sealed"): raise ValueError("judge-after-seal violation")
    recorded = seal.get("prediction_file_roots")
    actual = {path.name: _sha_file(path) for path in sorted((output / "predictions").glob("*.jsonl"))}
    if recorded != actual: raise ValueError("prediction ledger tamper detected after seal")
    if judge_backend == "none": return {"status": "prediction_sealed_no_score"}
    if judge_backend != "fake": raise ValueError("official/live judge export only; no local official score implementation")
    refs = {str(row["question_id"]): row for row in iter_json_array(Path(reference)) if isinstance(row.get("question_id"), str)}
    journal = OutcomeJournal(output / "score_outcomes.jsonl")
    judge: Judge = FakeJudge(); scores: dict[str, list[Mapping[str, object]]] = {}
    for arm_path in sorted((output / "predictions").glob("*.jsonl")):
        arm = arm_path.stem; rows=[]
        for line in arm_path.read_text(encoding="utf-8").splitlines():
            pred = json.loads(line); ref = refs.get(pred["question_id"])
            if not ref or not isinstance(ref.get("answer"), str) or not isinstance(ref.get("question"), str): raise ValueError("score reference requires question_id/question/answer")
            result = dict(judge.score(question=ref["question"], hypothesis=pred["hypothesis"], reference=ref["answer"]))
            rows.append({"question_id": pred["question_id"], **result}); journal.append(len(journal.events)+1, f"{arm}:{pred['question_id']}", [rows[-1]])
        scores[arm] = rows
    report = {"schema_version": "cmd-longmemeval-e2e-score-v1", "prediction_seal_root": _sha_file(output / "prediction_seal.json"), "judge_backend": judge_backend, "judge_model": judge.model_id, "official_score": False, "score_outcome_root": journal.head, "arms": {arm: {"count": len(rows), "normalized_exact": sum(bool(r["correct"]) for r in rows) / len(rows) if rows else None} for arm, rows in scores.items()}}
    atomic_json_write(output / "score_report.json", report, ensure_ascii=False, allow_nan=False, indent=2, trailing_newline=True)
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__); p.add_argument("--mode", choices=("predict", "score", "all"), default="all"); p.add_argument("--retrieval-run", type=Path); p.add_argument("--data", type=Path, required=True); p.add_argument("--answerer-backend", choices=("fake", "openai-compatible"), default="fake"); p.add_argument("--answerer-model", default="fake-answerer-v1"); p.add_argument("--judge-backend", choices=("none", "fake", "openai-compatible"), default="none"); p.add_argument("--judge-model", default="none"); p.add_argument("--prompt-file", type=Path); p.add_argument("--temperature", type=float, default=0.0); p.add_argument("--context-budget", type=int, default=12000); p.add_argument("--run-mode", choices=("fresh", "resume"), default="fresh"); p.add_argument("--output", type=Path, required=True); p.add_argument("--limit", type=int); p.add_argument("--llm-config", type=Path); args = p.parse_args(argv)
    prompt = args.prompt_file.read_text(encoding="utf-8") if args.prompt_file else DEFAULT_PROMPT; config = _read_json(args.llm_config) if args.llm_config else None
    if args.mode in {"predict", "all"}:
        if args.retrieval_run is None: p.error("--retrieval-run is required for prediction")
        predict(data=args.data, retrieval_run=args.retrieval_run, output=args.output, answerer_backend=args.answerer_backend, answerer_model=args.answerer_model, prompt=prompt, temperature=args.temperature, context_budget=args.context_budget, resume=args.run_mode == "resume", limit=args.limit, llm_config=config)
    if args.mode in {"score", "all"}: score(reference=args.data, output=args.output, judge_backend=args.judge_backend, judge_model=args.judge_model, resume=args.run_mode == "resume")
    return 0

if __name__ == "__main__": raise SystemExit(main())
