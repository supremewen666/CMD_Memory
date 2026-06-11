"""Tests for AnswerRubricScorer on the G-Eval answer axis."""

import unittest
from unittest.mock import Mock, patch

from cmd_audit.core.llm_client import LLMResponse, TokenLogprob
from cmd_audit.scoring.llm import (
    AnswerRubricScorer,
    RubricParseError,
    _continuous_verify_answer,
    _parse_rubric_output,
)


class TestAnswerRubricScorer(unittest.TestCase):
    """Tests for AnswerRubricScorer class."""

    def setUp(self):
        self.mock_client = Mock()
        self.scorer = AnswerRubricScorer(self.mock_client)

    def test_verify_continuous_score_range(self):
        """Test that continuous scores are in [0,1] range."""
        # Mock logprobs response
        mock_response = LLMResponse(
            text='{"reasoning": "Good match", "score": 3}',
            token_logprobs=(
                TokenLogprob("score", -0.1, [("score", -0.1)]),
                TokenLogprob("3", -0.05, [("3", -0.05), ("2", -1.2), ("4", -2.1)]),
            )
        )
        self.mock_client.generate_with_logprobs.return_value = mock_response

        score = self.scorer.verify("Paris is the capital", "The capital is Paris")
        self.assertIsInstance(score, float)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_verify_identical_strings(self):
        """Test identical strings should score close to 1.0."""
        # Mock logprobs with high score
        mock_response = LLMResponse(
            text='{"reasoning": "Identical", "score": 4}',
            token_logprobs=(
                TokenLogprob("score", -0.1, [("score", -0.1)]),
                TokenLogprob("4", -0.05, [("4", -0.05), ("3", -1.5)]),
            )
        )
        self.mock_client.generate_with_logprobs.return_value = mock_response

        answer = "The capital of France is Paris"
        score = self.scorer.verify(answer, answer)
        self.assertGreater(score, 0.8)  # Should be high for identical strings

    def test_verify_empty_strings(self):
        """Test empty strings should score 0."""
        # Mock fallback to discrete rubric
        self.mock_client.generate_with_logprobs.side_effect = Exception("No logprobs")
        self.mock_client.generate.return_value = '{"reasoning": "Both empty", "score": 0}'

        score = self.scorer.verify("", "")
        self.assertEqual(score, 0.0)

    def test_verify_contradictory_answers(self):
        """Test contradictory answers should score low."""
        # Mock logprobs with low score
        mock_response = LLMResponse(
            text='{"reasoning": "Contradictory", "score": 0}',
            token_logprobs=(
                TokenLogprob("score", -0.1, [("score", -0.1)]),
                TokenLogprob("0", -0.05, [("0", -0.05), ("1", -2.0)]),
            )
        )
        self.mock_client.generate_with_logprobs.return_value = mock_response

        score = self.scorer.verify("Paris is the capital", "Berlin is the capital")
        self.assertLess(score, 0.3)  # Should be low for contradictory answers

    def test_fallback_to_discrete_rubric(self):
        """Test fallback when logprobs unavailable."""
        # Mock no logprobs support
        self.mock_client.generate_with_logprobs.side_effect = Exception("No logprobs")
        self.mock_client.generate.return_value = '{"reasoning": "Partial match", "score": 2}'

        score = self.scorer.verify("Paris", "The capital is Paris")
        self.assertEqual(score, 2.0 / 4.0)  # 2/4 = 0.5

    def test_fallback_to_zero_on_failure(self):
        """Test fallback to 0 when all methods fail."""
        # Mock all failures
        self.mock_client.generate_with_logprobs.side_effect = Exception("No logprobs")
        self.mock_client.generate.side_effect = Exception("Network error")

        score = self.scorer.verify("Paris is capital", "Paris is the capital")
        self.assertEqual(score, 0.0)

    def test_parse_failure_fallback(self):
        """Test fallback to 0 when parse fails."""
        # Mock unparseable response
        self.mock_client.generate_with_logprobs.side_effect = Exception("No logprobs")
        self.mock_client.generate.return_value = "Invalid JSON response"

        score = self.scorer.verify("Paris is capital", "Paris is the capital")
        self.assertEqual(score, 0.0)

    def test_callable_interface(self):
        """Test that scorer can be called as a function."""
        self.mock_client.generate_with_logprobs.side_effect = Exception("No logprobs")
        self.mock_client.generate.return_value = '{"reasoning": "Test", "score": 3}'

        # Test __call__ method
        score = self.scorer("Paris is capital", "Paris is the capital")
        self.assertEqual(score, 0.75)  # 3/4


class TestContinuousVerifyAnswer(unittest.TestCase):
    """Tests for _continuous_verify_answer function."""

    def test_returns_none_when_no_client(self):
        """Test returns None when client is None."""
        result = _continuous_verify_answer(None, "answer", "gold_answer")
        self.assertIsNone(result)

    def test_returns_none_when_no_logprobs_support(self):
        """Test returns None when client doesn't support logprobs."""
        mock_client = Mock()
        del mock_client.generate_with_logprobs  # Remove the method

        result = _continuous_verify_answer(mock_client, "answer", "gold_answer")
        self.assertIsNone(result)

    def test_returns_none_on_exception(self):
        """Test returns None when LLM call fails."""
        mock_client = Mock()
        mock_client.generate_with_logprobs.side_effect = Exception("Network error")

        result = _continuous_verify_answer(mock_client, "Paris is capital", "Paris is the capital")
        self.assertIsNone(result)

    def test_returns_none_when_no_token_logprobs(self):
        """Test returns None when response has no logprobs."""
        mock_client = Mock()
        mock_response = LLMResponse(text="response", token_logprobs=None)
        mock_client.generate_with_logprobs.return_value = mock_response

        result = _continuous_verify_answer(mock_client, "Paris is capital", "Paris is the capital")
        self.assertIsNone(result)

    def test_valid_logprobs_calculation(self):
        """Test proper calculation from logprobs."""
        mock_client = Mock()
        mock_response = LLMResponse(
            text='{"reasoning": "Test", "score": 3}',
            token_logprobs=(
                TokenLogprob("score", -0.1, [("score", -0.1)]),
                TokenLogprob("3", -0.2, [("3", -0.2), ("2", -1.0), ("4", -1.5)]),
            )
        )
        mock_client.generate_with_logprobs.return_value = mock_response

        result = _continuous_verify_answer(mock_client, "Paris is capital", "Paris is the capital")
        self.assertIsInstance(result, float)
        self.assertGreater(result, 2.0)  # Should be close to 3
        self.assertLess(result, 4.0)


class TestRubricParsing(unittest.TestCase):
    """Tests for rubric output parsing."""

    def test_parse_valid_json(self):
        """Test parsing valid JSON response."""
        response = '{"reasoning": "Good match", "score": 3}'
        score = _parse_rubric_output(response)
        self.assertEqual(score, 3)

    def test_parse_json_with_extra_text(self):
        """Test parsing JSON embedded in extra text."""
        response = 'Here is my assessment: {"reasoning": "Partial", "score": 2} - done.'
        score = _parse_rubric_output(response)
        self.assertEqual(score, 2)

    def test_parse_failure_invalid_json(self):
        """Test parse failure on invalid JSON."""
        response = 'Not valid JSON at all'
        with self.assertRaises(RubricParseError):
            _parse_rubric_output(response)

    def test_parse_failure_no_score_field(self):
        """Test parse failure when no score field."""
        response = '{"reasoning": "Good", "other": 3}'
        with self.assertRaises(RubricParseError):
            _parse_rubric_output(response)

    def test_parse_failure_invalid_score_type(self):
        """Test parse failure when score is not an integer."""
        response = '{"reasoning": "Good", "score": "three"}'
        with self.assertRaises(RubricParseError):
            _parse_rubric_output(response)

    def test_parse_failure_score_out_of_range(self):
        """Test parse failure when score is out of range."""
        response = '{"reasoning": "Good", "score": 5}'
        with self.assertRaises(RubricParseError):
            _parse_rubric_output(response)

        response_negative = '{"reasoning": "Bad", "score": -1}'
        with self.assertRaises(RubricParseError):
            _parse_rubric_output(response_negative)


if __name__ == "__main__":
    unittest.main()
