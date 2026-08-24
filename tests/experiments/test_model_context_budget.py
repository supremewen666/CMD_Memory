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
