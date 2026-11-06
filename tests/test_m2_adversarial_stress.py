"""Adversarial and empirical stress tests for Milestone M2:
1. LLM retry and network failure handling (llm_manager.py)
2. Engine exception message preservation (llm_manager.py)
3. Fact extraction short quote validation and seen-set integrity (ollama_summary.py)
"""
import unittest
from unittest.mock import Mock, patch

import llm_manager as llm
import ollama_summary as analysis
import requests


class OpenRouterRetryStressTests(unittest.TestCase):
    """Stress-test call_openrouter retry mechanics."""

    def test_openrouter_single_retry_429_success(self):
        busy = Mock(status_code=429, headers={'Retry-After': '0'})
        okay = Mock(status_code=200)
        okay.json.return_value = {
            'choices': [{'message': {'content': '{"ok": true}'}}]
        }
        with patch.object(llm.requests, 'post', side_effect=[busy, okay]), \
             patch.object(llm.time, 'sleep') as mock_sleep:
            res = llm.call_openrouter('테스트 프롬프트', 'test-key', max_tokens=100)

        self.assertEqual(res, '{"ok": true}')
        mock_sleep.assert_called_once()

    def test_openrouter_400_fatal_no_retry(self):
        fatal = Mock(status_code=400)
        with patch.object(llm.requests, 'post', return_value=fatal):
            with self.assertRaises(llm.LLMError) as ctx:
                llm.call_openrouter('프롬프트', 'test-key', max_tokens=50)
        self.assertIn('OpenRouter HTTP 400', str(ctx.exception))


class AdversarialExceptionPreservationStressTests(unittest.TestCase):
    """Stress-test exception message preservation in generate_llm_response across various engines."""

    def test_single_engine_ollama_failure_preserves_verbatim_error(self):
        configs = [{'provider': 'Local Ollama', 'model': 'gemma4:26b-mlx'}]
        with patch.object(llm.requests, 'post', side_effect=requests.exceptions.ConnectionError('Failed to connect to localhost:11434')):
            with self.assertRaises(llm.LLMError) as ctx:
                llm.generate_llm_response('prompt', configs)
        self.assertIn('Local Ollama: Ollama 연결/응답 실패', str(ctx.exception))

    def test_single_engine_openrouter_auth_failure_preserves_error(self):
        configs = [{'provider': 'OpenRouter', 'model': 'deepseek/deepseek-v4-flash-0731', 'api_key': ''}]
        with patch.object(llm, 'get_secret', return_value=''):
            with self.assertRaises(llm.LLMError) as ctx:
                llm.generate_llm_response('prompt', configs)
        self.assertIn('OpenRouter: OPENROUTER_API_KEY 미설정', str(ctx.exception))

    def test_unsupported_provider_rejected(self):
        unsupported = [{'provider': 'UnsupportedProvider', 'model': 'test-model'}]
        with self.assertRaises(llm.LLMError) as ctx:
            llm.generate_llm_response('prompt', unsupported)
        self.assertIn('UnsupportedProvider: 미지원 공급자: UnsupportedProvider', str(ctx.exception))

    def test_cascading_multi_engine_fallback_preserves_all_errors_in_order(self):
        """When multiple engines fail in sequence, all error messages are joined by semicolon."""
        configs = [
            {'provider': 'OpenRouter', 'model': 'deepseek/test', 'api_key': 'key'},
            {'provider': 'Local Ollama', 'model': 'ollama-test'},
        ]
        resp_401 = Mock(status_code=401)

        with patch.object(llm.requests, 'post', side_effect=[
                 resp_401,                           # OpenRouter attempt
                 requests.exceptions.ConnectTimeout('Ollama timeout') # Ollama attempt
             ]):
            with self.assertRaises(llm.LLMError) as ctx:
                llm.generate_llm_response('prompt', configs)

        err_msg = str(ctx.exception)
        self.assertIn('OpenRouter: OpenRouter HTTP 401', err_msg)
        self.assertIn('Local Ollama: Ollama 연결/응답 실패', err_msg)

    def test_first_engine_fails_second_succeeds_returns_content(self):
        """When first engine fails, second engine's success is returned transparently."""
        configs = [
            {'provider': 'OpenRouter', 'model': 'deepseek/test', 'api_key': 'bad-key'},
            {'provider': 'Local Ollama', 'model': 'local-test'}
        ]
        resp_401 = Mock(status_code=401)
        resp_ollama = Mock(status_code=200)
        resp_ollama.json.return_value = {'response': 'Ollama succeeded'}

        with patch.object(llm.requests, 'post', side_effect=[resp_401, resp_ollama]):
            res = llm.generate_llm_response('prompt', configs)

        self.assertEqual(res, 'Ollama succeeded')

    def test_empty_engine_configs_raises_appropriate_message(self):
        """Empty engine configs raises '사용 가능한 엔진 없음'."""
        with self.assertRaises(llm.LLMError) as ctx:
            llm.generate_llm_response('prompt', [])
        self.assertEqual(str(ctx.exception), '사용 가능한 엔진 없음')


class AdversarialFactExtractionShortQuotesStressTests(unittest.TestCase):
    """Stress-test short quote acceptance (<= 3 chars) and seen-set integrity in ollama_summary.py."""

    def test_various_short_quotes_verified_when_in_text(self):
        """Quotes of 1, 2, 3 chars present in text must be verified=True."""
        text = '사업기간은 6월이며 사업예산은 1억원이다. 제출기한은 3일이며 평가는 70점이다.'
        raw_facts = [
            {'field': 'duration', 'fact': '사업기간 6월', 'quote': '6월'},
            {'field': 'budget', 'fact': '사업예산 1억원', 'quote': '1억원'},
            {'field': 'deadline', 'fact': '제출기한 3일', 'quote': '3일'},
            {'field': 'evaluation', 'fact': '기술평가 70점', 'quote': '70점'},
        ]
        fields = ['duration', 'budget', 'deadline', 'evaluation']
        with patch.object(analysis, '_cached_facts', return_value=raw_facts):
            facts = analysis._extract_facts(text, fields, {}, [])

        self.assertEqual(len(facts), 4)
        for f in facts:
            self.assertTrue(f['verified'], f"Fact {f['field']} was not verified")
            self.assertIn(f['quote'], text)

    def test_short_quotes_rejected_when_not_in_text(self):
        """Quotes of <= 3 chars NOT present in text must be rejected."""
        text = '사업기간은 12개월이다.'
        raw_facts = [
            {'field': 'duration', 'fact': '사업기간 6개월', 'quote': '6개월'},  # Not in text
            {'field': 'duration', 'fact': '사업기간 12개월', 'quote': '12개월'}, # In text
        ]
        with patch.object(analysis, '_cached_facts', return_value=raw_facts):
            facts = analysis._extract_facts(text, ['duration'], {}, [])

        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]['fact'], '사업기간 12개월')
        self.assertEqual(facts[0]['quote'], '12개월')
        self.assertTrue(facts[0]['verified'])

    def test_seen_set_not_poisoned_multiple_attempts(self):
        """Multiple facts with identical field and fact text:
        Attempts 1 & 2 have invalid hallucinated quotes.
        Attempt 3 has valid quote.
        Verify that Attempt 3 is accepted and verified."""
        text = '사업예산은 5억원으로 책정되었다.'
        raw_facts = [
            {'field': 'budget', 'fact': '사업예산 5억원', 'quote': '100억원'}, # invalid
            {'field': 'budget', 'fact': '사업예산 5억원', 'quote': '거짓'},     # invalid
            {'field': 'budget', 'fact': '사업예산 5억원', 'quote': '5억원'},    # valid!
        ]
        with patch.object(analysis, '_cached_facts', return_value=raw_facts):
            facts = analysis._extract_facts(text, ['budget'], {}, [])

        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]['quote'], '5억원')
        self.assertTrue(facts[0]['verified'])

    def test_unverified_fact_without_quote_accepted_and_deduplicated(self):
        """Facts without a quote (empty string) are accepted with verified=False and deduplicated."""
        text = '사업기간은 12개월이다.'
        raw_facts = [
            {'field': 'duration', 'fact': '사업기간 12개월', 'quote': ''},
            {'field': 'duration', 'fact': '사업기간 12개월', 'quote': '   '}, # duplicate
        ]
        with patch.object(analysis, '_cached_facts', return_value=raw_facts):
            facts = analysis._extract_facts(text, ['duration'], {}, [])

        self.assertEqual(len(facts), 1)
        self.assertFalse(facts[0]['verified'])
        self.assertEqual(facts[0]['fact'], '사업기간 12개월')

    def test_malformed_raw_entries_handled_gracefully(self):
        """Malformed raw facts (None, non-dict, non-string quote) must not crash _extract_facts."""
        text = '정상 본문 내용 12개월.'
        raw_facts = [
            None,
            "not a dict",
            {'field': 'unknown_field', 'fact': 'test'},
            {'field': 'duration', 'fact': None},
            {'field': 'duration', 'fact': ''},
            {'field': 'duration', 'fact': '기간 12개월', 'quote': None}, # None quote
            {'field': 'duration', 'fact': '기간 12개월 2차', 'quote': 12345}, # Int quote
        ]
        with patch.object(analysis, '_cached_facts', return_value=raw_facts):
            facts = analysis._extract_facts(text, ['duration'], {}, [])

        self.assertEqual(len(facts), 2)
        self.assertEqual(facts[0]['fact'], '기간 12개월')
        self.assertFalse(facts[0]['verified'])
        self.assertEqual(facts[1]['fact'], '기간 12개월 2차')
        self.assertFalse(facts[1]['verified'])


if __name__ == '__main__':
    unittest.main()
