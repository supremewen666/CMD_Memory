"""Subagent-based LLM scoring replacing phrase-matching (Issue 0019 Phase B).

Binary atomic subagent judgments — continuous scores emerge from aggregation.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
import math
import re
from typing import Any, Callable

from typing import Literal

from .core.llm_client import LLMResponse, TokenLogprob
from .core.models import GoldEvidence
from .scoring import evidence_recall_from_text

_logger = logging.getLogger(__name__)

# ── Prompts ────────────────────────────────────────────────────────────

_EVIDENCE_SYSTEM_PROMPT = """\
TASK: Verify whether the fact exists in the given text.

DECISION RULES:

  PRESENT — The core propositional content of FACT is communicated by TEXT:
    - Paraphrase or reworded expression with same meaning
    - Abbreviation or expansion preserving the core claims
    - Fact embedded within a larger passage
    - Different ordering of the same claims

  ABSENT — The core proposition is NOT communicated:
    - Fact completely missing from TEXT
    - TEXT mentions a related but different concept (not the same proposition)
    - TEXT directly contradicts the fact
    - Only tangential mention; proposition not actually established
    - UNCERTAIN / BOUNDARY CASE -> ABSENT (conservative tie-break)

  To resolve boundary cases, extract the core subject-verb-object triple
  from FACT and check whether TEXT preserves that same triple.

OUTPUT: PRESENT | ABSENT"""

_ANSWER_SYSTEM_PROMPT = """\
TASK: Verify whether two answers are semantically equivalent.

DECISION RULES:

  EQUIVALENT — Both answers communicate the same factual information:
    - Different word choice, same meaning
    - Non-contradictory extra details in either answer
    - Different presentation order of the same facts
    - Abbreviation or expansion of the same claims

  NOT_EQUIVALENT — The answers differ in factual content:
    - Core fact(s) missing from ANSWER
    - ANSWER contains information contradicting GOLD ANSWER
    - Extra details change the factual meaning of the original
    - UNCERTAIN / BOUNDARY CASE -> NOT_EQUIVALENT (conservative tie-break)

OUTPUT: EQUIVALENT | NOT_EQUIVALENT"""

# ── Forbidden context tokens ───────────────────────────────────────────

_FORBIDDEN_CONTEXT_TOKENS = (
    "case_id",
    "gold_label",
    "perturbation_type",
    "perturbation_label",
    "gold_answer",
    "gold_evidence",
    "ptype",
    "cross_case",
    "other_case",
)

# ── Exceptions ─────────────────────────────────────────────────────────


class ContextLeakError(ValueError):
    """Raised when a subagent prompt contains forbidden context data."""


class OutputFormatError(ValueError):
    """Raised when a subagent response is not a valid binary output."""


# ── Validation ─────────────────────────────────────────────────────────


def _validate_context_isolation(prompt: str) -> None:
    """Reject prompts that leak gold labels, case metadata, or cross-case data."""
    lowered = prompt.casefold()
    violations = [tok for tok in _FORBIDDEN_CONTEXT_TOKENS if tok in lowered]
    if violations:
        raise ContextLeakError(
            f"Subagent prompt contains forbidden tokens: {', '.join(violations)}"
        )


def _validate_output_format(
    response: str, *, kind: Literal["evidence", "answer"] = "evidence"
) -> str:
    """Validate and normalise binary subagent output.

    Returns canonical form: ``"PRESENT"`` or ``"ABSENT"`` for evidence
    verdicts; ``"EQUIVALENT"`` or ``"NOT_EQUIVALENT"`` for answer verdicts.
    """
    stripped = response.strip().upper()

    if kind == "evidence":
        if stripped.startswith("PRESENT"):
            return "PRESENT"
        if stripped.startswith("ABSENT"):
            return "ABSENT"
        valid = ("PRESENT", "ABSENT")
    else:
        if stripped.startswith("EQUIVALENT"):
            return "EQUIVALENT"
        if stripped.startswith("NOT_EQUIVALENT"):
            return "NOT_EQUIVALENT"
        valid = ("EQUIVALENT", "NOT_EQUIVALENT")

    raise OutputFormatError(
        f"Expected one of {valid}, got: {response!r}"
    )


# ── Verifiers ──────────────────────────────────────────────────────────


class EvidenceVerifier:
    """Binary atomic verifier for a single evidence fact against a text.

    Each :meth:`verify` call is one subagent invocation: receives
    ``{FACT, TEXT}`` as user message and fixed decision rules as system
    prompt.  Returns ``"PRESENT"`` or ``"ABSENT"``.
    """

    def __init__(self, llm_client: Any, *, max_retries: int = 1) -> None:
        self._client = llm_client
        self._max_retries = max_retries

    @property
    def is_available(self) -> bool:
        """True when an LLM client is configured and ready."""
        return self._client is not None

    def verify(self, fact: str, text: str) -> str:
        """Run one atomic evidence verification.

        On LLM failure falls back to ``"ABSENT"`` (conservative tie-break).
        On output format failure retries once with stricter prompt before
        falling back.
        """
        user_message = f"FACT:\n  {fact}\n\nTEXT:\n  {text}"
        _validate_context_isolation(user_message)

        try:
            response = self._client.generate(
                user_message, system=_EVIDENCE_SYSTEM_PROMPT
            )
        except Exception as exc:
            _logger.warning("EvidenceVerifier LLM call failed: %s", exc)
            return "ABSENT"

        for attempt in range(self._max_retries + 1):
            try:
                return _validate_output_format(response, kind="evidence")
            except OutputFormatError:
                if attempt < self._max_retries:
                    _logger.debug(
                        "EvidenceVerifier parse failed, retrying: %r", response
                    )
                    retry_msg = (
                        user_message
                        + "\n\nOUTPUT ONLY THE SINGLE WORD PRESENT OR ABSENT. NO OTHER TEXT."
                    )
                    try:
                        response = self._client.generate(
                            retry_msg, system=_EVIDENCE_SYSTEM_PROMPT
                        )
                    except Exception as exc:
                        _logger.warning(
                            "EvidenceVerifier retry LLM call failed: %s", exc
                        )

        _logger.warning(
            "EvidenceVerifier exhausted retries. Last response: %r", response
        )
        return "ABSENT"


class AnswerVerifier:
    """Binary atomic verifier for answer equivalence.

    Used only at Post-Repair Context Replay validation — not in the
    attribution loop.
    """

    def __init__(self, llm_client: Any, *, max_retries: int = 1) -> None:
        self._client = llm_client
        self._max_retries = max_retries

    def verify(self, answer: str, gold_answer: str) -> str:
        """Run one atomic answer equivalence check.

        Returns ``"EQUIVALENT"`` or ``"NOT_EQUIVALENT"``.
        """
        user_message = (
            f"ANSWER:\n  {answer}\n\nGOLD ANSWER:\n  {gold_answer}"
        )
        _validate_context_isolation(user_message)

        try:
            response = self._client.generate(
                user_message, system=_ANSWER_SYSTEM_PROMPT
            )
        except Exception as exc:
            _logger.warning("AnswerVerifier LLM call failed: %s", exc)
            return "NOT_EQUIVALENT"

        for attempt in range(self._max_retries + 1):
            try:
                return _validate_output_format(response, kind="answer")
            except OutputFormatError:
                if attempt < self._max_retries:
                    retry_msg = (
                        user_message
                        + "\n\nOUTPUT ONLY THE SINGLE WORD EQUIVALENT OR NOT_EQUIVALENT. NO OTHER TEXT."
                    )
                    try:
                        response = self._client.generate(
                            retry_msg, system=_ANSWER_SYSTEM_PROMPT
                        )
                    except Exception as exc:
                        _logger.warning(
                            "AnswerVerifier retry LLM call failed: %s", exc
                        )

        _logger.warning(
            "AnswerVerifier exhausted retries. Last response: %r", response
        )
        return "NOT_EQUIVALENT"


def score_answer_with_verifier(
    answer_verifier: Any,
    answer: str,
    gold_answer: str,
    *,
    fallback: Callable[[str, str], float] | None = None,
) -> float:
    """Score answer equivalence using an LLM verifier when provided.

    Shared by the harness baseline path and the replay path so both legs of
    the dual-axis recovery_gain go through the same evaluator.

    Resolution order:
      1. ``answer_verifier`` is ``None`` → ``fallback(answer, gold_answer)``,
         or substring :func:`scoring.answer_score` when no fallback given.
      2. Object exposing ``.verify(answer, gold_answer)`` (e.g.
         :class:`AnswerVerifier`).
      3. Plain callable ``(answer, gold_answer) -> verdict``.

    Verdict normalisation: numeric verdicts are returned as-is; ``EQUIVALENT``
    / ``NOT_EQUIVALENT`` map to ``1.0`` / ``0.0``.
    """
    if answer_verifier is None:
        from .scoring import answer_score as _substring_answer_score

        if fallback is not None:
            return fallback(answer, gold_answer)
        return _substring_answer_score(answer, gold_answer)

    if hasattr(answer_verifier, "verify"):
        verdict = answer_verifier.verify(answer, gold_answer)
    elif callable(answer_verifier):
        verdict = answer_verifier(answer, gold_answer)
    else:
        raise TypeError("answer_verifier must be callable or expose verify()")

    if isinstance(verdict, (int, float)):
        return float(verdict)
    normalized = str(verdict).strip().upper()
    if normalized.startswith("EQUIVALENT"):
        return 1.0
    if normalized.startswith("NOT_EQUIVALENT"):
        return 0.0
    try:
        return float(normalized)
    except ValueError as exc:
        raise ValueError(
            f"invalid answer verifier verdict: {verdict!r}"
        ) from exc


# ── Scorer ─────────────────────────────────────────────────────────────


class SubagentScorer:
    """Subagent-based evidence scoring — replaces phrase-matching.

    Implements the scorer contract ``(gold_evidence, text) -> float`` so it
    can be passed as the ``scorer`` parameter to
    :func:`_score_recovered_evidence`.

    Evidence items are evaluated in parallel via :class:`ThreadPoolExecutor`.
    """

    def __init__(
        self,
        llm_client: Any,
        *,
        max_workers: int = 5,
        max_retries: int = 1,
        fallback_scorer: Callable[[tuple[GoldEvidence, ...], str], float]
        | None = None,
    ) -> None:
        self._verifier = EvidenceVerifier(llm_client, max_retries=max_retries)
        self._max_workers = max_workers
        self._fallback = fallback_scorer

    def score_evidence(
        self, gold_evidence: tuple[GoldEvidence, ...], text: str
    ) -> float:
        """Score evidence presence in text using parallel subagent calls.

        Returns fraction of evidence items judged PRESENT, a float in [0,1].
        """
        if not gold_evidence:
            return 0.0
        if not text:
            return 0.0
        if not self._verifier.is_available:
            _logger.info("SubagentScorer: LLM client unavailable, using fallback")
            if self._fallback is not None:
                return self._fallback(gold_evidence, text)
            return evidence_recall_from_text(gold_evidence, text)

        facts = [ev.text for ev in gold_evidence]

        try:
            with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
                futures = {
                    pool.submit(self._score_one, i, fact, text): i
                    for i, fact in enumerate(facts)
                }
                verdicts: list[str | None] = [None for _ in facts]
                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        verdicts[idx] = future.result()
                    except Exception as exc:
                        _logger.warning(
                            "Subagent evidence item %d failed: %s", idx, exc
                        )
                        verdicts[idx] = "ABSENT"

                present = sum(1 for v in verdicts if v == "PRESENT")
                return present / len(facts)
        except Exception as exc:
            _logger.warning(
                "SubagentScorer parallel execution failed: %s", exc
            )
            if self._fallback is not None:
                return self._fallback(gold_evidence, text)
            return evidence_recall_from_text(gold_evidence, text)

    def __call__(
        self, gold_evidence: tuple[GoldEvidence, ...], text: str
    ) -> float:
        """Scorer contract — drop-in replacement for phrase-matching."""
        return self.score_evidence(gold_evidence, text)

    def _score_one(self, index: int, fact: str, text: str) -> str:
        return self._verifier.verify(fact, text)


def _phrase_fallback(
    gold_evidence: tuple[GoldEvidence, ...], text: str
) -> float:
    """Deterministic phrase-matching — delegates to canonical scorer."""
    return evidence_recall_from_text(gold_evidence, text)


# ── Rubric Scoring (Issue 0019 Phase C) ────────────────────────────────

RUBRIC_MAX_SCORE = 4

_RUBRIC_SYSTEM_PROMPT = """\
TASK: Rate how completely the FACT is communicated by TEXT, on a 0–4 scale.

RUBRIC ANCHORS:

  0 = ABSENT or UNRELATED. TEXT does not mention the fact, or mentions an
      unrelated topic, or directly contradicts the fact.

  1 = VAGUE. TEXT alludes to the same general area but communicates none of
      the specific subject/verb/object content of the fact.

  2 = PARTIAL PARAPHRASE missing key detail. TEXT carries some of the
      propositional content but a key entity, value, or relation is missing
      or only weakly implied.

  3 = STRONG PARAPHRASE or partial exact match. TEXT communicates the same
      core proposition with possibly different wording; a minor detail may
      be reworded but the subject/verb/object triple is preserved.

  4 = EXACT or fully equivalent. TEXT contains the fact verbatim, or a
      complete equivalent paraphrase that preserves all entities, values,
      and relations.

GUIDANCE:
  - Extract the subject/verb/object triple from FACT, then judge how much
    of that triple is preserved in TEXT.
  - When uncertain between two adjacent levels, choose the lower one
    (conservative tie-break).

OUTPUT: A single JSON object with this exact shape, no prose around it:
  {"reasoning": "<one short sentence>", "score": <integer 0..4>}"""


class RubricParseError(ValueError):
    """Raised when a rubric subagent response is not valid JSON or out of range."""


_RUBRIC_JSON_RE = re.compile(r"\{[^{}]*\"score\"[^{}]*\}", re.DOTALL)


def _parse_rubric_output(response: str) -> int:
    """Parse a rubric JSON response and return the integer score in [0, 4].

    Tolerates extra prose around the JSON object by extracting the first
    object that contains a ``"score"`` key.
    """
    text = response.strip()
    payload: dict[str, Any] | None = None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = _RUBRIC_JSON_RE.search(text)
        if match is not None:
            try:
                payload = json.loads(match.group(0))
            except json.JSONDecodeError:
                payload = None

    if not isinstance(payload, dict) or "score" not in payload:
        raise RubricParseError(
            f"Expected JSON with 'score' field, got: {response!r}"
        )

    raw_score = payload["score"]
    try:
        score = int(raw_score)
    except (TypeError, ValueError) as exc:
        raise RubricParseError(
            f"score field not coercible to int: {raw_score!r}"
        ) from exc

    if score < 0 or score > RUBRIC_MAX_SCORE:
        raise RubricParseError(
            f"score out of range [0, {RUBRIC_MAX_SCORE}]: {score}"
        )
    return score


class RubricVerifier:
    """Continuous 5-level rubric verifier for one fact against a text.

    Returns an integer score in [0, 4]; aggregation into [0, 1] is the
    caller's job.  On LLM failure or parse failure (after one retry),
    falls back to ``0`` — same conservative tie-break as the binary path.
    """

    def __init__(self, llm_client: Any, *, max_retries: int = 1) -> None:
        self._client = llm_client
        self._max_retries = max_retries

    @property
    def is_available(self) -> bool:
        return self._client is not None

    def verify(self, fact: str, text: str) -> int:
        user_message = f"FACT:\n  {fact}\n\nTEXT:\n  {text}"
        _validate_context_isolation(user_message)

        try:
            response = self._client.generate(
                user_message, system=_RUBRIC_SYSTEM_PROMPT
            )
        except Exception as exc:
            _logger.warning("RubricVerifier LLM call failed: %s", exc)
            return 0

        for attempt in range(self._max_retries + 1):
            try:
                return _parse_rubric_output(response)
            except RubricParseError:
                if attempt < self._max_retries:
                    _logger.debug(
                        "RubricVerifier parse failed, retrying: %r", response
                    )
                    retry_msg = (
                        user_message
                        + '\n\nOUTPUT ONLY A JSON OBJECT OF THE FORM '
                        + '{"reasoning": "...", "score": <0-4>}. NO OTHER TEXT.'
                    )
                    try:
                        response = self._client.generate(
                            retry_msg, system=_RUBRIC_SYSTEM_PROMPT
                        )
                    except Exception as exc:
                        _logger.warning(
                            "RubricVerifier retry LLM call failed: %s", exc
                        )

        _logger.warning(
            "RubricVerifier exhausted retries. Last response: %r", response
        )
        return 0


class RubricScorer:
    """Rubric-based evidence scoring — continuous drop-in for SubagentScorer.

    Each evidence item is rated 0..4 by :class:`RubricVerifier`; the per-item
    score is normalised to ``[0, 1]`` via division by :data:`RUBRIC_MAX_SCORE`,
    and the final score is the mean across items.

    Implements the scorer contract ``(gold_evidence, text) -> float`` so it
    can be passed wherever :class:`SubagentScorer` is accepted.
    """

    def __init__(
        self,
        llm_client: Any,
        *,
        max_workers: int = 5,
        max_retries: int = 1,
        fallback_scorer: Callable[[tuple[GoldEvidence, ...], str], float]
        | None = None,
    ) -> None:
        self._verifier = RubricVerifier(llm_client, max_retries=max_retries)
        self._max_workers = max_workers
        self._fallback = fallback_scorer

    def score_evidence(
        self, gold_evidence: tuple[GoldEvidence, ...], text: str
    ) -> float:
        if not gold_evidence:
            return 0.0
        if not text:
            return 0.0
        if not self._verifier.is_available:
            _logger.info("RubricScorer: LLM client unavailable, using fallback")
            if self._fallback is not None:
                return self._fallback(gold_evidence, text)
            return evidence_recall_from_text(gold_evidence, text)

        facts = [ev.text for ev in gold_evidence]

        try:
            with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
                futures = {
                    pool.submit(self._score_one, i, fact, text): i
                    for i, fact in enumerate(facts)
                }
                scores: list[int] = [0 for _ in facts]
                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        scores[idx] = future.result()
                    except Exception as exc:
                        _logger.warning(
                            "Rubric evidence item %d failed: %s", idx, exc
                        )
                        scores[idx] = 0

                normalised = [s / RUBRIC_MAX_SCORE for s in scores]
                return sum(normalised) / len(normalised)
        except Exception as exc:
            _logger.warning(
                "RubricScorer parallel execution failed: %s", exc
            )
            if self._fallback is not None:
                return self._fallback(gold_evidence, text)
            return evidence_recall_from_text(gold_evidence, text)

    def __call__(
        self, gold_evidence: tuple[GoldEvidence, ...], text: str
    ) -> float:
        return self.score_evidence(gold_evidence, text)

    def _score_one(self, index: int, fact: str, text: str) -> int:
        return self._verifier.verify(fact, text)


# ── Continuous (G-Eval) Rubric Scoring ─────────────────────────────────


def _find_score_digit_logprobs(
    token_logprobs: tuple[TokenLogprob, ...],
) -> dict[int, float] | None:
    """Locate the rubric ``"score"`` digit and return digit→logprob.

    Walks the generated token stream looking for the ``"score"`` JSON key,
    skips structural tokens (``":``, whitespace), and returns the
    ``top_logprobs`` distribution at the first single-digit slot in
    ``[0, RUBRIC_MAX_SCORE]``.

    Returns ``None`` when the score position can't be located or no digit
    candidates appear in the top-K.
    """
    if not token_logprobs:
        return None

    score_key_idx: int | None = None
    for i, tok in enumerate(token_logprobs):
        if "score" in tok.token.lower():
            score_key_idx = i
            break
    if score_key_idx is None:
        return None

    for j in range(score_key_idx + 1, len(token_logprobs)):
        tok = token_logprobs[j]
        stripped = _normalise_score_digit_token(tok.token)
        if not stripped:
            continue
        if stripped.isdigit() and 0 <= int(stripped) <= RUBRIC_MAX_SCORE:
            digits: dict[int, float] = {}
            chosen = stripped
            digits[int(chosen)] = tok.logprob
            for alt_token, alt_lp in tok.alternatives:
                cand = _normalise_score_digit_token(alt_token)
                if cand.isdigit() and 0 <= int(cand) <= RUBRIC_MAX_SCORE:
                    digits.setdefault(int(cand), alt_lp)
            return digits or None
        # Skip JSON structural tokens (":, space, quotes) but bail if we
        # hit something non-trivial — protects against finding a digit
        # buried elsewhere in the response.
        if stripped in {":", ",", "{", "}"}:
            continue
        # Anything else (a word, multi-char fragment) means the score
        # wasn't emitted as a single-digit token — give up.
        if not all(c in "\":' \t\n" for c in tok.token):
            return None
    return None


def _normalise_score_digit_token(token: str) -> str:
    """Normalize tokenizer fragments around a JSON score digit.

    vLLM/OpenAI-compatible servers can emit score values as tokens such as
    ``"3"``, ``" 3"``, ``"3}"``, ``"3,"`` or sentencepiece-style ``"▁3"``.
    The G-Eval parser only needs the first standalone rubric digit at the
    score-value position.
    """
    stripped = token.strip().strip('"').strip("'")
    stripped = stripped.lstrip("Ġ▁")
    if stripped and stripped[0].isdigit():
        return stripped[0]
    return stripped


def _expected_score_from_logprobs(digits: dict[int, float]) -> float:
    """Softmax over rubric-digit logprobs, return the score expectation.

    Returns a float in ``[0, RUBRIC_MAX_SCORE]``.  Uses the standard
    log-sum-exp trick for numerical stability.
    """
    if not digits:
        raise ValueError("digits must be non-empty")
    max_lp = max(digits.values())
    weights = {d: math.exp(lp - max_lp) for d, lp in digits.items()}
    total = sum(weights.values())
    if total <= 0.0:
        raise ValueError("non-positive softmax denominator")
    return sum(d * (w / total) for d, w in weights.items())


def _continuous_verify(
    client: Any,
    fact: str,
    text: str,
    *,
    top_logprobs: int = 10,
) -> float | None:
    """Run one rubric call requesting logprobs; return E[score] or None.

    ``None`` signals the caller should fall back to the discrete path —
    either the client doesn't support logprobs, the endpoint stripped
    them, or the score-digit position couldn't be parsed.
    """
    if client is None:
        return None
    if not hasattr(client, "generate_with_logprobs"):
        return None

    user_message = f"FACT:\n  {fact}\n\nTEXT:\n  {text}"
    _validate_context_isolation(user_message)

    try:
        response = client.generate_with_logprobs(
            user_message,
            system=_RUBRIC_SYSTEM_PROMPT,
            top_logprobs=top_logprobs,
        )
    except Exception as exc:
        _logger.warning("RubricVerifier logprob call failed: %s", exc)
        return None

    if not isinstance(response, LLMResponse):
        return None
    if not response.token_logprobs:
        return None

    digits = _find_score_digit_logprobs(response.token_logprobs)
    if not digits:
        return None
    try:
        return _expected_score_from_logprobs(digits)
    except ValueError:
        return None


# Patch continuous methods onto the existing RubricVerifier / RubricScorer
# classes — kept as module-level functions to keep the Phase B class
# definition above unchanged and easy to read.


def _verifier_verify_continuous(
    self: "RubricVerifier", fact: str, text: str
) -> float:
    """Continuous verify: returns E[score] in ``[0, RUBRIC_MAX_SCORE]``.

    Falls back to the discrete :meth:`RubricVerifier.verify` integer path
    (cast to float) when logprobs are unavailable.
    """
    expected = _continuous_verify(self._client, fact, text)
    if expected is not None:
        return expected
    return float(self.verify(fact, text))


RubricVerifier.verify_continuous = _verifier_verify_continuous  # type: ignore[attr-defined]


def _scorer_score_continuous(
    self: "RubricScorer",
    gold_evidence: tuple[GoldEvidence, ...],
    text: str,
) -> float:
    """Mean of per-item E[score]/RUBRIC_MAX_SCORE — continuous in ``[0, 1]``."""
    if not gold_evidence:
        return 0.0
    if not text:
        return 0.0
    if not self._verifier.is_available:
        _logger.info("RubricScorer.continuous: client unavailable, fallback")
        if self._fallback is not None:
            return self._fallback(gold_evidence, text)
        return evidence_recall_from_text(gold_evidence, text)

    facts = [ev.text for ev in gold_evidence]
    try:
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures = {
                pool.submit(self._verifier.verify_continuous, fact, text): i
                for i, fact in enumerate(facts)
            }
            scores: list[float] = [0.0 for _ in facts]
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    scores[idx] = float(future.result())
                except Exception as exc:
                    _logger.warning(
                        "Rubric continuous item %d failed: %s", idx, exc
                    )
                    scores[idx] = 0.0

            normalised = [s / RUBRIC_MAX_SCORE for s in scores]
            return sum(normalised) / len(normalised)
    except Exception as exc:
        _logger.warning("RubricScorer continuous parallel failed: %s", exc)
        if self._fallback is not None:
            return self._fallback(gold_evidence, text)
        return evidence_recall_from_text(gold_evidence, text)


RubricScorer.score_continuous = _scorer_score_continuous  # type: ignore[attr-defined]
