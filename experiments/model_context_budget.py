"""Model-aware prompt budgeting for live memory benchmark calls."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Sequence


@dataclass(frozen=True)
class BudgetedContext:
    context: str
    included_ids: tuple[str, ...]
    input_tokens: int
    truncated: bool
    counting_mode: str


class ModelContextBudget:
    """Fit complete memory items under the serving context window.

    A Hugging Face tokenizer is used when ``LLM_TOKENIZER_PATH`` is set.
    Otherwise UTF-8 bytes are treated as a conservative token upper bound.
    The fallback cannot overflow a byte-level tokenizer, but may retain less
    context than necessary and is recorded in the prediction seal.
    """

    def __init__(
        self,
        *,
        tokenizer_path: str | None = None,
        max_model_len: int | None = None,
        max_output_tokens: int | None = None,
        reserve_tokens: int = 256,
    ) -> None:
        self.max_model_len = int(
            max_model_len
            if max_model_len is not None
            else os.environ.get("LLM_MAX_MODEL_LEN", "32768")
        )
        self.max_output_tokens = int(
            max_output_tokens
            if max_output_tokens is not None
            else os.environ.get("LLM_MAX_TOKENS", "512")
        )
        self.reserve_tokens = int(reserve_tokens)
        if min(self.max_model_len, self.max_output_tokens, self.reserve_tokens) < 0:
            raise ValueError("model context budget values must be non-negative")
        if self.max_output_tokens + self.reserve_tokens >= self.max_model_len:
            raise ValueError("output plus reserve tokens exhaust the model context window")
        path = tokenizer_path or os.environ.get("LLM_TOKENIZER_PATH")
        self._tokenizer = None
        self.counting_mode = "utf8_byte_upper_bound"
        if path:
            try:
                from transformers import AutoTokenizer
            except ImportError as exc:
                raise RuntimeError(
                    "LLM_TOKENIZER_PATH requires transformers in the benchmark environment"
                ) from exc
            self._tokenizer = AutoTokenizer.from_pretrained(
                path,
                trust_remote_code=True,
                local_files_only=True,
            )
            self.counting_mode = f"huggingface:{type(self._tokenizer).__name__}"

    @property
    def max_input_tokens(self) -> int:
        return self.max_model_len - self.max_output_tokens - self.reserve_tokens

    def count_chat_tokens(self, *, prompt: str, system: str) -> int:
        if self._tokenizer is None:
            return len((system + "\n" + prompt).encode("utf-8"))
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        encoded = self._tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
        )
        return len(encoded)

    def fit_memory_items(
        self,
        *,
        query: str,
        items: Sequence[tuple[str, str]],
        system: str,
        heading: str,
    ) -> BudgetedContext:
        included: list[tuple[str, str]] = []
        last_count = 0
        for item in items:
            proposed = [*included, item]
            context = _render_context(heading, proposed)
            prompt = _answer_prompt(query, context)
            count = self.count_chat_tokens(prompt=prompt, system=system)
            if count > self.max_input_tokens:
                break
            included = proposed
            last_count = count
        if not included:
            context = _render_context(heading, ())
            last_count = self.count_chat_tokens(
                prompt=_answer_prompt(query, context),
                system=system,
            )
            if last_count > self.max_input_tokens:
                raise ValueError("query and prompt scaffold exceed the model context budget")
        else:
            context = _render_context(heading, included)
        return BudgetedContext(
            context=context,
            included_ids=tuple(item_id for item_id, _text in included),
            input_tokens=last_count,
            truncated=len(included) != len(items),
            counting_mode=self.counting_mode,
        )


def _render_context(heading: str, items: Sequence[tuple[str, str]]) -> str:
    body = "\n\n".join(f"[{item_id}]\n{text}" for item_id, text in items)
    return f"{heading}:\n{body or '(empty)'}"


def _answer_prompt(query: str, context: str) -> str:
    return "\n\n".join(("CONTEXT:", context, "QUERY:", query, "ANSWER:"))


__all__ = ["BudgetedContext", "ModelContextBudget"]
