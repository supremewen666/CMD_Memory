from cmd_audit.repair.actions import build_repair_action_prompt


def test_repair_action_prompt_treats_guidance_as_operator_documentation() -> None:
    prompt = build_repair_action_prompt(
        label="retrieval_error",
        evidence_block="EVIDENCE",
        fm_context="FM_CONTEXT",
        supported_actions=("append", "update_routing"),
        target_store="episodic",
        content="CORRECTED",
        repair_guidance="GUIDANCE_SENTINEL",
    )

    assert "OPERATOR DOCUMENTATION:" in prompt
    assert "GUIDANCE_SENTINEL" in prompt
    assert "REPAIR GUIDANCE:" not in prompt
