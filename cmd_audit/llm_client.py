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
        messages: list[dict[str, str]] = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = json.dumps(
            {
                "model": self._config.model,
                "messages": messages,
                "temperature": self._config.temperature,
                "stream": False,
            }
        ).encode("utf-8")

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
                    body = json.loads(resp.read().decode("utf-8"))
                    return _extract_content(body)
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
