"""Provider-agnostic LLM API client for CMD-Audit (Issue 0019).

Zero external dependencies — uses stdlib ``urllib.request`` against any
OpenAI-compatible ``/v1/chat/completions`` endpoint (ollama, vllm, openai, etc).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import urllib.error
import urllib.request


class LLMClientError(Exception):
    """Base exception for LLM client failures."""


class LLMUnavailableError(LLMClientError):
    """The LLM endpoint is unreachable or returned a transport error."""


class LLMTimeoutError(LLMClientError):
    """The LLM request timed out."""


class LLMResponseError(LLMClientError):
    """The LLM returned a non-200 HTTP status."""


class LLMEmptyResponseError(LLMClientError):
    """The LLM returned a response with no content."""


@dataclass(frozen=True)
class TokenLogprob:
    """One generated token with the top-K alternatives and their logprobs.

    ``alternatives`` is the OpenAI-spec ``top_logprobs`` list — each entry is
    ``(token, logprob)`` for one of the K most likely tokens at that step.
    The chosen token itself appears first via :attr:`token` / :attr:`logprob`.
    """

    token: str
    logprob: float
    alternatives: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class LLMResponse:
    """Generation result with optional per-token logprobs."""

    text: str
    token_logprobs: tuple[TokenLogprob, ...] | None = None


@dataclass(frozen=True)
class LLMClientConfig:
    """Configuration for the provider-agnostic LLM client.

    Reads defaults from environment variables:
      ``LLM_BASE_URL``, ``LLM_MODEL``, ``LLM_TIMEOUT``, ``LLM_API_KEY``,
      ``OPENAI_API_KEY``, or ``DEEPSEEK_API_KEY``.
    """

    base_url: str = field(
        default_factory=lambda: os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1")
    )
    model: str = field(
        default_factory=lambda: os.environ.get("LLM_MODEL", "qwen2.5:7b")
    )
    timeout_seconds: float = field(
        default_factory=lambda: float(os.environ.get("LLM_TIMEOUT", "60"))
    )
    api_key: str = field(
        default_factory=lambda: (
            os.environ.get("LLM_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("DEEPSEEK_API_KEY")
            or ""
        )
    )
    max_retries: int = 1
    temperature: float = 0.0

    @property
    def chat_endpoint(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/chat/completions"


class LLMClient:
    """Provider-agnostic LLM API client.

    Sends chat completion requests to any OpenAI-compatible endpoint.
    """

    def __init__(self, config: LLMClientConfig | None = None) -> None:
        self._config = config or LLMClientConfig()

    @property
    def config(self) -> LLMClientConfig:
        return self._config

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        """Send a single-turn chat completion and return the response text.

        Args:
            prompt: The user message content.
            system: Optional system message inserted as the first message
                with ``role="system"`` before the user message.

        Raises:
            LLMUnavailableError: Endpoint unreachable or transport error.
            LLMTimeoutError: Request timed out.
            LLMResponseError: Non-200 HTTP response.
            LLMEmptyResponseError: Response body contains no content.
        """
        body = self._post_chat_completion(prompt, system=system, top_logprobs=None)
        return _extract_content(body)

    def generate_with_logprobs(
        self,
        prompt: str,
        *,
        system: str | None = None,
        top_logprobs: int = 10,
    ) -> LLMResponse:
        """Send a chat completion and return text plus per-token logprobs.

        Returns an :class:`LLMResponse` whose ``token_logprobs`` is populated
        when the endpoint includes a ``logprobs.content`` block (vLLM,
        OpenAI).  Endpoints that ignore the logprobs request return
        ``token_logprobs=None`` and the caller should fall back to discrete
        parsing.
        """
        body = self._post_chat_completion(prompt, system=system, top_logprobs=top_logprobs)
        text = _extract_content(body)
        token_logprobs = _extract_logprobs(body)
        return LLMResponse(text=text, token_logprobs=token_logprobs)

    def _post_chat_completion(
        self,
        prompt: str,
        *,
        system: str | None,
        top_logprobs: int | None,
    ) -> dict:
        messages: list[dict[str, str]] = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload_obj: dict[str, object] = {
            "model": self._config.model,
            "messages": messages,
            "temperature": self._config.temperature,
            "stream": False,
        }
        if top_logprobs is not None:
            payload_obj["logprobs"] = True
            payload_obj["top_logprobs"] = int(top_logprobs)

        payload = json.dumps(payload_obj).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"

        req = urllib.request.Request(
            self._config.chat_endpoint,
            data=payload,
            headers=headers,
            method="POST",
        )

        last_error: Exception | None = None
        for attempt in range(self._config.max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self._config.timeout_seconds) as resp:
                    if resp.status != 200:
                        raise LLMResponseError(
                            f"LLM endpoint returned HTTP {resp.status}"
                        )
                    return json.loads(resp.read().decode("utf-8"))
            except (LLMClientError, LLMEmptyResponseError):
                raise
            except urllib.error.URLError as exc:
                last_error = exc
            except (OSError, ValueError) as exc:
                last_error = exc

        if isinstance(last_error, urllib.error.URLError):
            if "timed out" in str(last_error).lower():
                raise LLMTimeoutError(
                    f"LLM request timed out after {self._config.timeout_seconds}s"
                ) from last_error
            raise LLMUnavailableError(
                f"LLM endpoint unreachable: {last_error}"
            ) from last_error
        raise LLMUnavailableError(
            f"LLM request failed: {last_error}"
        ) from last_error


def _extract_content(body: dict) -> str:
    choices = body.get("choices")
    if not choices:
        raise LLMEmptyResponseError("LLM response has no choices")
    message = choices[0].get("message")
    if not message:
        raise LLMEmptyResponseError("LLM response choice has no message")
    content = message.get("content")
    if content is None:
        raise LLMEmptyResponseError("LLM response message has no content")
    text = str(content).strip()
    if not text:
        raise LLMEmptyResponseError("LLM response content is empty")
    return text


def _extract_logprobs(body: dict) -> tuple[TokenLogprob, ...] | None:
    """Pull per-token logprobs from an OpenAI-spec choices[0].logprobs block.

    Returns ``None`` when the endpoint did not produce a ``logprobs.content``
    array (older Ollama, models without logprob support).
    """
    choices = body.get("choices") or []
    if not choices:
        return None
    logprobs_block = choices[0].get("logprobs")
    if not isinstance(logprobs_block, dict):
        return None
    content = logprobs_block.get("content")
    if not isinstance(content, list) or not content:
        return None

    out: list[TokenLogprob] = []
    for entry in content:
        if not isinstance(entry, dict):
            continue
        token = entry.get("token")
        logprob = entry.get("logprob")
        if token is None or logprob is None:
            continue
        alts_raw = entry.get("top_logprobs") or []
        alts: list[tuple[str, float]] = []
        for alt in alts_raw:
            if not isinstance(alt, dict):
                continue
            tok = alt.get("token")
            lp = alt.get("logprob")
            if tok is None or lp is None:
                continue
            try:
                alts.append((str(tok), float(lp)))
            except (TypeError, ValueError):
                continue
        try:
            out.append(
                TokenLogprob(
                    token=str(token),
                    logprob=float(logprob),
                    alternatives=tuple(alts),
                )
            )
        except (TypeError, ValueError):
            continue
    return tuple(out) if out else None
