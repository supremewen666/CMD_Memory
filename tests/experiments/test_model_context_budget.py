from experiments.model_context_budget import ModelContextBudget


def test_byte_fallback_drops_complete_tail_items_before_http() -> None:
    budget = ModelContextBudget(
        tokenizer_path=None,
        max_model_len=180,
        max_output_tokens=20,
        reserve_tokens=20,
    )
    result = budget.fit_memory_items(
        query="short query",
        items=(("m1", "small"), ("m2", "x" * 500)),
        system="system",
        heading="memory",
    )
    assert result.included_ids == ("m1",)
    assert result.truncated is True
    assert result.input_tokens <= budget.max_input_tokens


class _BatchEncodingTokenizer:
    def apply_chat_template(
        self, messages, *, tokenize: bool, add_generation_prompt: bool,
    ):
        assert tokenize is True
        assert add_generation_prompt is True
        token_count = sum(
            len(message["content"].split()) for message in messages
        ) + 3
        token_ids = list(range(token_count))
        return {
            "input_ids": [token_ids],
            "attention_mask": [[1] * token_count],
        }


def test_batch_encoding_counts_input_ids_instead_of_mapping_fields() -> None:
    budget = ModelContextBudget(
        tokenizer_path=None,
        max_model_len=4096,
        max_output_tokens=64,
        reserve_tokens=64,
    )
    budget._tokenizer = _BatchEncodingTokenizer()

    short = budget.fit_memory_items(
        query="short query",
        items=(),
        system="system prompt",
        heading="memory",
    )
    with_memory = budget.fit_memory_items(
        query="short query",
        items=(("m1", "one two three four five six"),),
        system="system prompt",
        heading="memory",
    )

    assert short.input_tokens > 2
    assert with_memory.input_tokens > short.input_tokens


class _TensorLikeEncoding:
    def tolist(self):
        return [[11, 12, 13, 14, 15]]


class _TensorTokenizer:
    def apply_chat_template(self, *_args, **_kwargs):
        return {"input_ids": _TensorLikeEncoding(), "attention_mask": object()}


def test_tensor_like_batch_encoding_counts_the_single_sequence() -> None:
    budget = ModelContextBudget(
        tokenizer_path=None,
        max_model_len=4096,
        max_output_tokens=64,
        reserve_tokens=64,
    )
    budget._tokenizer = _TensorTokenizer()

    assert budget.count_chat_tokens(prompt="prompt", system="system") == 5
