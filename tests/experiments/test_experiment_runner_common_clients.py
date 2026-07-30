"""Tests for build_clients() judge/answerer role split (SPEC_A §3)."""

import os
import unittest
from unittest.mock import patch

from experiments.experiment_runner_common import (
    assert_live_llm_env_configured,
    build_clients,
)


class BuildClientsTest(unittest.TestCase):
    def test_only_base_llm_vars_answer_and_judge_share_config(self):
        env = {
            "LLM_BASE_URL": "http://localhost:8000/v1",
            "LLM_MODEL": "qwen2.5-7b-instruct",
            "LLM_TIMEOUT": "120",
            "LLM_API_KEY": "shared-key",
        }
        with patch.dict(os.environ, env, clear=True):
            answer_client, judge_client = build_clients()

            self.assertEqual(answer_client.config, judge_client.config)
            self.assertEqual(judge_client.config.base_url, "http://localhost:8000/v1")
            self.assertEqual(judge_client.config.model, "qwen2.5-7b-instruct")

    def test_judge_vars_set_clients_diverge(self):
        env = {
            "LLM_BASE_URL": "http://localhost:8000/v1",
            "LLM_MODEL": "llama-3.1-8b-instruct",
            "LLM_JUDGE_BASE_URL": "http://localhost:9000/v1",
            "LLM_JUDGE_MODEL": "qwen2.5-7b-instruct",
        }
        with patch.dict(os.environ, env, clear=True):
            answer_client, judge_client = build_clients()

            self.assertEqual(answer_client.config.model, "llama-3.1-8b-instruct")
            self.assertEqual(judge_client.config.model, "qwen2.5-7b-instruct")
            self.assertNotEqual(answer_client.config, judge_client.config)

    def test_live_path_rejects_implicit_local_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(
                RuntimeError,
                "LLM_BASE_URL.*LLM_MODEL.*LLM_JUDGE_BASE_URL.*LLM_JUDGE_MODEL",
            ):
                assert_live_llm_env_configured()

    def test_live_path_requires_explicit_frozen_judge_identity(self):
        env = {
            "LLM_BASE_URL": "http://localhost:8000/v1",
            "LLM_MODEL": "answerer",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(
                RuntimeError,
                "LLM_JUDGE_BASE_URL.*LLM_JUDGE_MODEL",
            ):
                assert_live_llm_env_configured()

    def test_live_path_accepts_explicit_answer_and_judge_identities(self):
        env = {
            "LLM_BASE_URL": "http://localhost:8000/v1",
            "LLM_MODEL": "answerer",
            "LLM_JUDGE_BASE_URL": "http://localhost:9000/v1",
            "LLM_JUDGE_MODEL": "judge",
        }
        with patch.dict(os.environ, env, clear=True):
            assert_live_llm_env_configured()


if __name__ == "__main__":
    unittest.main()
