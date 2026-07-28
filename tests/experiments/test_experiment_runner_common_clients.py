"""Tests for build_clients() judge/answerer role split (SPEC_A §3)."""

import os
import unittest
from unittest.mock import patch

from experiments.experiment_runner_common import build_clients


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


if __name__ == "__main__":
    unittest.main()
