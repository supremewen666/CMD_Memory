"""Tests for LLMClientConfig role-aware construction (SPEC_A §3).

Standing principle (decided 2026-07-13): the answering model varies across
experiment arms; the judge is frozen for the entire study. These tests
verify the ``LLM_JUDGE_*`` env vars fall back to their ``LLM_*`` counterparts
field by field (not all-or-nothing), and that plain ``LLMClientConfig()``
construction is unaffected.

Each test uses ``patch.dict(os.environ, env, clear=True)`` so the process
environment is fully replaced (and restored on exit) rather than merged —
this avoids leaking any ``LLM_*`` vars that may already be set in the host
environment into these assertions.
"""

import os
import json
import unittest
from unittest.mock import patch

from cmd_audit.core.llm_client import LLMClient, LLMClientConfig


class LLMClientConfigForRoleTest(unittest.TestCase):
    def test_plain_constructor_unchanged(self):
        """LLMClientConfig() behaves identically regardless of for_role."""
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(LLMClientConfig(), LLMClientConfig.for_role("answer"))

    def test_only_base_llm_vars_set_judge_equals_answer_field_by_field(self):
        """With only LLM_* set, judge config == answer config, field by field."""
        env = {
            "LLM_BASE_URL": "http://localhost:8000/v1",
            "LLM_MODEL": "qwen2.5-7b-instruct",
            "LLM_TIMEOUT": "120",
            "LLM_API_KEY": "shared-key",
        }
        with patch.dict(os.environ, env, clear=True):
            answer_cfg = LLMClientConfig.for_role("answer")
            judge_cfg = LLMClientConfig.for_role("judge")

            self.assertEqual(answer_cfg.base_url, judge_cfg.base_url)
            self.assertEqual(answer_cfg.model, judge_cfg.model)
            self.assertEqual(answer_cfg.timeout_seconds, judge_cfg.timeout_seconds)
            self.assertEqual(answer_cfg.api_key, judge_cfg.api_key)
            self.assertEqual(answer_cfg.base_url, "http://localhost:8000/v1")
            self.assertEqual(judge_cfg.model, "qwen2.5-7b-instruct")
            self.assertEqual(judge_cfg.timeout_seconds, 120.0)
            self.assertEqual(judge_cfg.api_key, "shared-key")

    def test_judge_vars_set_configs_differ_exactly_as_configured(self):
        """With LLM_JUDGE_* also set, the two configs differ per the overrides."""
        env = {
            "LLM_BASE_URL": "http://localhost:8000/v1",
            "LLM_MODEL": "llama-3.1-8b-instruct",
            "LLM_TIMEOUT": "120",
            "LLM_API_KEY": "answer-key",
            "LLM_JUDGE_BASE_URL": "http://localhost:9000/v1",
            "LLM_JUDGE_MODEL": "qwen2.5-7b-instruct",
            "LLM_JUDGE_TIMEOUT": "60",
            "LLM_JUDGE_API_KEY": "judge-key",
        }
        with patch.dict(os.environ, env, clear=True):
            answer_cfg = LLMClientConfig.for_role("answer")
            judge_cfg = LLMClientConfig.for_role("judge")

            self.assertEqual(answer_cfg.base_url, "http://localhost:8000/v1")
            self.assertEqual(answer_cfg.model, "llama-3.1-8b-instruct")
            self.assertEqual(answer_cfg.timeout_seconds, 120.0)
            self.assertEqual(answer_cfg.api_key, "answer-key")

            self.assertEqual(judge_cfg.base_url, "http://localhost:9000/v1")
            self.assertEqual(judge_cfg.model, "qwen2.5-7b-instruct")
            self.assertEqual(judge_cfg.timeout_seconds, 60.0)
            self.assertEqual(judge_cfg.api_key, "judge-key")

            self.assertNotEqual(answer_cfg, judge_cfg)

    def test_partial_override_falls_back_per_field_not_all_or_nothing(self):
        """Only LLM_JUDGE_MODEL set: judge falls back to LLM_* for every
        other field, proving the fallback is per-field, not all-or-nothing."""
        env = {
            "LLM_BASE_URL": "http://localhost:8000/v1",
            "LLM_MODEL": "qwen2.5-7b-instruct",
            "LLM_TIMEOUT": "120",
            "LLM_API_KEY": "shared-key",
            "LLM_JUDGE_MODEL": "gpt-4o-judge",
        }
        with patch.dict(os.environ, env, clear=True):
            judge_cfg = LLMClientConfig.for_role("judge")

            # Overridden field takes the judge-specific value.
            self.assertEqual(judge_cfg.model, "gpt-4o-judge")
            # Every other field falls back to the base LLM_* value.
            self.assertEqual(judge_cfg.base_url, "http://localhost:8000/v1")
            self.assertEqual(judge_cfg.timeout_seconds, 120.0)
            self.assertEqual(judge_cfg.api_key, "shared-key")

    def test_unknown_role_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                LLMClientConfig.for_role("referee")


class LLMClientStructuredOutputTest(unittest.TestCase):
    def test_generate_json_sends_closed_json_schema_to_openai_compatible_endpoint(self):
        response = unittest.mock.MagicMock()
        response.status = 200
        response.read.return_value = json.dumps(
            {"choices": [{"message": {"content": '{"relation":"unrelated"}'}}]}
        ).encode("utf-8")
        response.__enter__.return_value = response
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["relation"],
            "properties": {
                "relation": {"enum": ["unrelated"]},
            },
        }
        client = LLMClient(
            LLMClientConfig(
                base_url="http://localhost:8000/v1",
                model="relation-model",
                api_key="test-key",
            )
        )

        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            result = client.generate_json(
                "classify",
                schema=schema,
                schema_name="slot_relation",
            )

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(result, '{"relation":"unrelated"}')
        self.assertEqual(
            payload["response_format"],
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "slot_relation",
                    "strict": True,
                    "schema": schema,
                },
            },
        )


if __name__ == "__main__":
    unittest.main()
