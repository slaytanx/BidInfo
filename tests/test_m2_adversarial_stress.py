"""Adversarial and empirical stress tests for Milestone M2:
1. LLM retry budget reservation, settling, and leak prevention (llm_manager.py)
2. Engine exception message preservation (llm_manager.py)
3. Concurrency and stress invariants for RunBudget
4. Fact extraction short quote validation and seen-set integrity (ollama_summary.py)
"""
from decimal import Decimal
import threading
import time
import unittest
from unittest.mock import Mock, patch

import llm_manager as llm
import ollama_summary as analysis
import requests


class AdversarialOpenRouterBudgetStressTests(unittest.TestCase):
    """Adversarial stress-testing of call_openrouter retry, budget reserve and settle mechanics."""

    def test_openrouter_single_retry_429_success_exact_settle(self):
        """Verify that on 429 retry, attempt 0 is settled with cost=0 and attempt 1 settles actual cost."""
        budget = llm.RunBudget(1)
        busy = Mock(status_code=429, headers={'Retry-After': '0'})
        okay = Mock(status_code=200)
        okay.json.return_value = {
            'usage': {'cost': 0.0035},
            'choices': [{'message': {'content': '{"ok": true}'}}]
        }
        with patch.object(budget, 'price', return_value=(Decimal('.000001'), Decimal('.000001'))), \
             patch.object(llm.requests, 'post', side_effect=[busy, okay]), \
             patch.object(llm.time, 'sleep') as mock_sleep:
            res = llm.call_openrouter('테스트 프롬프트', 'test-key', budget=budget, max_tokens=100)

        self.assertEqual(res, '{"ok": true}')
        self.assertEqual(budget.calls, 2)
        self.assertEqual(budget.reserved, Decimal('0'))
        self.assertEqual(budget.spent, Decimal('0.0035'))
        mock_sleep.assert_called_once_with(1.0)  # max(1, float(0)) == 1.0

    def test_openrouter_single_retry_5xx_status_codes_exact_settle(self):
        """Verify that all retryable 5xx codes (500, 502, 503, 504) settle 0 on retry."""
        retryable_codes = [500, 502, 503, 504]
        for code in retryable_codes:
            with self.subTest(status_code=code):
                budget = llm.RunBudget(1)
                err_resp = Mock(status_code=code, headers={})
                okay_resp = Mock(status_code=200)
                okay_resp.json.return_value = {
                    'usage': {'cost': 0.0012},
                    'choices': [{'message': {'content': f'recovered from {code}'}}]
                }
                with patch.object(budget, 'price', return_value=(Decimal('.000001'), Decimal('.000001'))), \
                     patch.object(llm.requests, 'post', side_effect=[err_resp, okay_resp]), \
                     patch.object(llm.time, 'sleep'):
                    res = llm.call_openrouter('프롬프트', 'test-key', budget=budget, max_tokens=50)

                self.assertEqual(res, f'recovered from {code}')
                self.assertEqual(budget.calls, 2)
                self.assertEqual(budget.reserved, Decimal('0'))
                self.assertEqual(budget.spent, Decimal('0.0012'))

    def test_openrouter_non_retryable_4xx_never_double_charged_and_zero_spent(self):
        """Verify that non-retryable 4xx errors (400, 401, 403, 404, 422) raise immediately with 0 spent."""
        fatal_codes = [400, 401, 403, 404, 422]
        for code in fatal_codes:
            with self.subTest(status_code=code):
                budget = llm.RunBudget(1)
                err_resp = Mock(status_code=code)
                with patch.object(budget, 'price', return_value=(Decimal('.000001'), Decimal('.000001'))), \
                     patch.object(llm.requests, 'post', return_value=err_resp):
                    with self.assertRaises(llm.LLMError) as ctx:
                        llm.call_openrouter('프롬프트', 'test-key', budget=budget, max_tokens=50)

                self.assertIn(f'OpenRouter HTTP {code}', str(ctx.exception))
                self.assertEqual(budget.calls, 1)  # No retry attempted
                self.assertEqual(budget.reserved, Decimal('0'))
                self.assertEqual(budget.spent, Decimal('0'))

    def test_openrouter_double_failure_both_attempts_fail(self):
        """Verify that when both attempt 0 and attempt 1 fail (e.g. 429 then 503), spent is 0 and reserved is 0."""
        budget = llm.RunBudget(1)
        resp1 = Mock(status_code=429, headers={'Retry-After': '1'})
        resp2 = Mock(status_code=503)
        with patch.object(budget, 'price', return_value=(Decimal('.000001'), Decimal('.000001'))), \
             patch.object(llm.requests, 'post', side_effect=[resp1, resp2]), \
             patch.object(llm.time, 'sleep'):
            with self.assertRaises(llm.LLMError) as ctx:
                llm.call_openrouter('프롬프트', 'test-key', budget=budget, max_tokens=50)

        self.assertIn('OpenRouter HTTP 503', str(ctx.exception))
        self.assertEqual(budget.calls, 2)
        self.assertEqual(budget.reserved, Decimal('0'))
        self.assertEqual(budget.spent, Decimal('0'))

    def test_openrouter_timeout_on_attempt_0_charges_conservative_reserve_once(self):
        """When requests.post raises a connection/timeout error on attempt 0, conservative reserve is charged once."""
        budget = llm.RunBudget(1)
        with patch.object(budget, 'price', return_value=(Decimal('.000001'), Decimal('.000001'))), \
             patch.object(llm.requests, 'post', side_effect=requests.exceptions.ConnectTimeout('Connection timed out')):
            with self.assertRaises(llm.LLMError) as ctx:
                llm.call_openrouter('프롬프트', 'test-key', budget=budget, max_tokens=100)

        self.assertIn('OpenRouter 연결/응답 실패', str(ctx.exception))
        self.assertEqual(budget.calls, 1)
        self.assertEqual(budget.reserved, Decimal('0'))
        # Conservative assumption: unconfirmed request is charged the reserved amount once
        self.assertGreater(budget.spent, Decimal('0'))

    def test_openrouter_retry_then_timeout_charges_reserve_once_not_twice(self):
        """Attempt 0 gets 429 (cost=0 settled), Attempt 1 times out (cost=None settled).
        Ensure only ONE reservation is charged, NOT two."""
        budget = llm.RunBudget(1)
        resp429 = Mock(status_code=429, headers={'Retry-After': '1'})
        with patch.object(budget, 'price', return_value=(Decimal('.000001'), Decimal('.000001'))), \
             patch.object(llm.requests, 'post', side_effect=[resp429, requests.exceptions.ReadTimeout('Read timeout')]), \
             patch.object(llm.time, 'sleep'):
            with self.assertRaises(llm.LLMError) as ctx:
                llm.call_openrouter('테스트', 'test-key', budget=budget, max_tokens=100)

        self.assertIn('OpenRouter 연결/응답 실패', str(ctx.exception))
        self.assertEqual(budget.calls, 2)
        self.assertEqual(budget.reserved, Decimal('0'))
        # Expected: reserve for prompt '테스트' (utf-8 bytes: 9 + 256 = 265 tokens * .000001 = .000265)
        # plus output max_tokens 100 * .000001 = .0001 -> total reserve = .000365
        # Attempt 0 settled 0; Attempt 1 settled reserve. Total spent must equal exactly reserve, NOT 2 * reserve.
        expected_reserve = Decimal('.000001') * (len('테스트'.encode('utf-8')) + 256) + Decimal('.000001') * 100
        self.assertEqual(budget.spent, expected_reserve)

    def test_openrouter_retry_near_budget_limit_does_not_prematurely_exceed_budget(self):
        """If budget limit is $0.05 and reserve is $0.04:
        Attempt 0 gets 429. If attempt 0 failed to settle, attempt 1 reserve ($0.04+$0.04=$0.08)
        would falsely raise BudgetExceeded. Settle(reserve, 0) must free the reservation."""
        # Calculate prompt and max_tokens so reserve is ~0.04
        # prompt len = 0, reserve = output_price * max_tokens = 0.0004 * 100 = 0.04
        budget = llm.RunBudget(limit=Decimal('0.05'))
        busy = Mock(status_code=429, headers={'Retry-After': '1'})
        okay = Mock(status_code=200)
        okay.json.return_value = {
            'usage': {'cost': 0.005},
            'choices': [{'message': {'content': 'success near limit'}}]
        }
        with patch.object(budget, 'price', return_value=(Decimal('0'), Decimal('0.0004'))), \
             patch.object(llm.requests, 'post', side_effect=[busy, okay]), \
             patch.object(llm.time, 'sleep'):
            res = llm.call_openrouter('short', 'key', budget=budget, max_tokens=100)

        self.assertEqual(res, 'success near limit')
        self.assertEqual(budget.calls, 2)
        self.assertEqual(budget.reserved, Decimal('0'))
        self.assertEqual(budget.spent, Decimal('0.005'))

    def test_openrouter_zero_cost_free_tier(self):
        """Free models with cost=0 must settle 0 and not charge reserve."""
        budget = llm.RunBudget(1)
        resp = Mock(status_code=200)
        resp.json.return_value = {
            'usage': {'cost': 0},
            'choices': [{'message': {'content': 'free answer'}}]
        }
        with patch.object(budget, 'price', return_value=(Decimal('.000001'), Decimal('.000001'))), \
             patch.object(llm.requests, 'post', return_value=resp):
            res = llm.call_openrouter('free', 'key', budget=budget, max_tokens=10)

        self.assertEqual(res, 'free answer')
        self.assertEqual(budget.reserved, Decimal('0'))
        self.assertEqual(budget.spent, Decimal('0'))

    def test_openrouter_missing_cost_settles_conservative_reserve(self):
        """If response.json does not contain usage.cost, settle(reserve, None) charges reserve."""
        budget = llm.RunBudget(1)
        resp = Mock(status_code=200)
        resp.json.return_value = {
            'usage': {'total_tokens': 50},
            'choices': [{'message': {'content': 'no cost answer'}}]
        }
        with patch.object(budget, 'price', return_value=(Decimal('.000001'), Decimal('.000001'))), \
             patch.object(llm.requests, 'post', return_value=resp):
            res = llm.call_openrouter('no cost', 'key', budget=budget, max_tokens=100)

        self.assertEqual(res, 'no cost answer')
        self.assertEqual(budget.reserved, Decimal('0'))
        expected_reserve = Decimal('.000001') * (len('no cost'.encode('utf-8')) + 256) + Decimal('.000001') * 100
        self.assertEqual(budget.spent, expected_reserve)

    def test_openrouter_empty_response_content_raises_llm_error_and_settles_cost(self):
        """When LLM returns empty content, LLMError is raised and incurred cost is settled."""
        budget = llm.RunBudget(1)
        resp = Mock(status_code=200)
        resp.json.return_value = {
            'usage': {'cost': 0.0008},
            'choices': [{'message': {'content': ''}}]
        }
        with patch.object(budget, 'price', return_value=(Decimal('.000001'), Decimal('.000001'))), \
             patch.object(llm.requests, 'post', return_value=resp):
            with self.assertRaises(llm.LLMError) as ctx:
                llm.call_openrouter('prompt', 'key', budget=budget, max_tokens=100)

        self.assertIn('OpenRouter 빈 응답', str(ctx.exception))
        self.assertEqual(budget.reserved, Decimal('0'))
        self.assertEqual(budget.spent, Decimal('0.0008'))

    def test_openrouter_retry_after_header_bounds(self):
        """Retry-After header values must be clamped between 1 and 5 seconds."""
        cases = [
            ({'Retry-After': '0'}, 1.0),
            ({'Retry-After': '3'}, 3.0),
            ({'Retry-After': '10'}, 5.0),
            ({'Retry-After': '-5'}, 1.0),
            ({}, 1.0),
        ]
        for headers, expected_sleep in cases:
            with self.subTest(headers=headers):
                budget = llm.RunBudget(1)
                busy = Mock(status_code=429, headers=headers)
                okay = Mock(status_code=200)
                okay.json.return_value = {'usage': {'cost': .001}, 'choices': [{'message': {'content': 'ok'}}]}
                with patch.object(budget, 'price', return_value=(Decimal('.000001'), Decimal('.000001'))), \
                     patch.object(llm.requests, 'post', side_effect=[busy, okay]), \
                     patch.object(llm.time, 'sleep') as mock_sleep:
                    llm.call_openrouter('test', 'key', budget=budget, max_tokens=10)
                mock_sleep.assert_called_once_with(expected_sleep)

    def test_openrouter_malformed_json_settles_conservative_reserve(self):
        """When OpenRouter returns invalid JSON (e.g. truncated response), JSONDecodeError triggers settle(reserve, None)."""
        budget = llm.RunBudget(1)
        resp = Mock(status_code=200)
        resp.json.side_effect = requests.exceptions.JSONDecodeError("Expecting value", "doc", 0)
        with patch.object(budget, 'price', return_value=(Decimal('.000001'), Decimal('.000001'))), \
             patch.object(llm.requests, 'post', return_value=resp):
            with self.assertRaises(llm.LLMError) as ctx:
                llm.call_openrouter('test', 'key', budget=budget, max_tokens=100)

        self.assertIn('OpenRouter 연결/응답 실패', str(ctx.exception))
        self.assertEqual(budget.reserved, Decimal('0'))
        expected_reserve = Decimal('.000001') * (len('test'.encode('utf-8')) + 256) + Decimal('.000001') * 100
        self.assertEqual(budget.spent, expected_reserve)

    def test_openrouter_missing_choices_settles_and_raises(self):
        """When OpenRouter returns 200 but choices key is missing/empty, KeyError occurs and cost is settled."""
        budget = llm.RunBudget(1)
        resp = Mock(status_code=200)
        resp.json.return_value = {'usage': {'cost': 0.0005}, 'choices': []}
        with patch.object(budget, 'price', return_value=(Decimal('.000001'), Decimal('.000001'))), \
             patch.object(llm.requests, 'post', return_value=resp):
            with self.assertRaises(IndexError):
                llm.call_openrouter('test', 'key', budget=budget, max_tokens=100)

        self.assertEqual(budget.reserved, Decimal('0'))
        self.assertEqual(budget.spent, Decimal('0.0005'))

    def test_openrouter_retry_after_non_numeric_header_falls_back_without_budget_leak(self):
        """When Retry-After contains a non-numeric string (e.g. HTTP date), ValueError is raised,
        cost=0 is settled, and reservation is freed cleanly."""
        budget = llm.RunBudget(1)
        resp = Mock(status_code=429, headers={'Retry-After': 'Wed, 21 Oct 2026 07:28:00 GMT'})
        with patch.object(budget, 'price', return_value=(Decimal('.000001'), Decimal('.000001'))), \
             patch.object(llm.requests, 'post', return_value=resp):
            with self.assertRaises(ValueError):
                llm.call_openrouter('test', 'key', budget=budget, max_tokens=100)

        # Settle(reserve, 0) was called before ValueError bubbled out of try block
        self.assertEqual(budget.reserved, Decimal('0'))
        self.assertEqual(budget.spent, Decimal('0'))



class AdversarialConcurrencyBudgetStressTests(unittest.TestCase):
    """Stress-test thread-safety and invariants of RunBudget under high concurrent load."""

    def test_concurrent_reserve_and_settle_invariants(self):
        """50 concurrent threads reserving and settling on a shared RunBudget.
        Invariants:
        1. reserved is never negative
        2. spent is exact sum of actual settled costs
        3. final reserved is exactly 0
        """
        budget = llm.RunBudget(limit=100)
        num_threads = 50
        errors = []

        def worker(thread_id):
            try:
                reserve_amt = Decimal('0.05')
                actual_cost = Decimal(f'0.0{thread_id:02d}')
                budget.reserve(reserve_amt)
                if budget.reserved < 0:
                    errors.append(f'Thread {thread_id}: reserved was negative during run')
                time.sleep(0.001)
                budget.settle(reserve_amt, actual_cost)
            except Exception as e:
                errors.append(f'Thread {thread_id} failed: {e}')

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(budget.reserved, Decimal('0'))
        self.assertEqual(budget.calls, num_threads)
        expected_spent = sum(Decimal(f'0.0{i:02d}') for i in range(num_threads))
        self.assertEqual(budget.spent, expected_spent)

    def test_budget_exceeded_atomic_rejection_no_leak(self):
        """When budget is exceeded, reserve raises BudgetExceeded and reserved amount is not altered."""
        budget = llm.RunBudget(limit=Decimal('0.10'))
        budget.reserve(Decimal('0.08'))
        self.assertEqual(budget.reserved, Decimal('0.08'))
        self.assertEqual(budget.calls, 1)

        # Attempt to reserve 0.05: 0.08 + 0.05 = 0.13 > 0.10
        with self.assertRaises(llm.BudgetExceeded):
            budget.reserve(Decimal('0.05'))

        # Invariant: reserved and calls must NOT have leaked or increased
        self.assertEqual(budget.reserved, Decimal('0.08'))
        self.assertEqual(budget.calls, 1)

        # Settle the original reservation
        budget.settle(Decimal('0.08'), Decimal('0.02'))
        self.assertEqual(budget.reserved, Decimal('0'))
        self.assertEqual(budget.spent, Decimal('0.02'))


class RunBudgetEnhancementTests(unittest.TestCase):
    """Verify RunBudget.reserve_context, fallback pricing, and flexible budget configuration."""

    def test_reserve_context_success_settles_actual_cost(self):
        budget = llm.RunBudget(1)
        with budget.reserve_context(Decimal('0.05')) as alloc:
            alloc['cost'] = Decimal('0.015')
        self.assertEqual(budget.reserved, Decimal('0'))
        self.assertEqual(budget.spent, Decimal('0.015'))
        self.assertEqual(budget.calls, 1)

    def test_reserve_context_exception_settles_conservative_reserve(self):
        budget = llm.RunBudget(1)
        with self.assertRaises(RuntimeError):
            with budget.reserve_context(Decimal('0.05')) as alloc:
                raise RuntimeError('Simulated network cut')
        self.assertEqual(budget.reserved, Decimal('0'))
        self.assertEqual(budget.spent, Decimal('0.05'))

    def test_reserve_context_budget_exceeded_leaves_state_clean(self):
        budget = llm.RunBudget(limit=Decimal('0.03'))
        with self.assertRaises(llm.BudgetExceeded):
            with budget.reserve_context(Decimal('0.05')):
                pass
        self.assertEqual(budget.reserved, Decimal('0'))
        self.assertEqual(budget.spent, Decimal('0'))
        self.assertEqual(budget.calls, 0)

    def test_fallback_pricing_on_openrouter_network_error(self):
        budget = llm.RunBudget(1)
        with patch.object(llm.requests, 'get', side_effect=requests.exceptions.ConnectTimeout('OpenRouter API down')):
            prices = budget.price('deepseek/deepseek-v4-flash-0731')
        expected = llm.KNOWN_MODEL_PRICES['deepseek/deepseek-v4-flash-0731']
        self.assertEqual(prices, expected)

    def test_unknown_model_on_network_error_raises_llm_error(self):
        budget = llm.RunBudget(1)
        with patch.object(llm.requests, 'get', side_effect=requests.exceptions.ConnectTimeout('OpenRouter API down')):
            with self.assertRaises(llm.LLMError) as ctx:
                budget.price('non-existent/custom-model')
        self.assertIn('현재 모델 목록에서 모델/가격 확인 불가', str(ctx.exception))

    def test_engine_configs_numeric_budget_coercion(self):
        configs = llm.engine_configs(mode='혼합', budget=2.5)
        cloud_conf = next(c for c in configs if c['provider'] == 'OpenRouter')
        self.assertIsInstance(cloud_conf['budget'], llm.RunBudget)
        self.assertEqual(cloud_conf['budget'].limit, Decimal('2.5'))


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

    def test_gemini_and_nvidia_missing_key_preserved(self):
        gemini_configs = [{'provider': 'Google Gemini', 'model': 'gemini-1.5-flash', 'api_key': ''}]
        with self.assertRaises(llm.LLMError) as ctx:
            llm.generate_llm_response('prompt', gemini_configs)
        self.assertIn('Google Gemini: API 키 미설정', str(ctx.exception))

        nim_configs = [{'provider': 'NVIDIA NIM', 'model': 'meta/llama-3.1-8b', 'api_key': ''}]
        with self.assertRaises(llm.LLMError) as ctx2:
            llm.generate_llm_response('prompt', nim_configs)
        self.assertIn('NVIDIA NIM: API 키 미설정', str(ctx2.exception))

    def test_cascading_multi_engine_fallback_preserves_all_errors_in_order(self):
        """When multiple engines fail in sequence, all error messages are joined by semicolon."""
        configs = [
            {'provider': 'OpenRouter', 'model': 'deepseek/test', 'api_key': 'key', 'budget': llm.RunBudget(1)},
            {'provider': 'Google Gemini', 'model': 'gemini-test', 'api_key': 'gemini-key'},
            {'provider': 'Local Ollama', 'model': 'ollama-test'},
        ]
        resp_401 = Mock(status_code=401)
        resp_gemini_500 = Mock(status_code=500)
        resp_gemini_500.raise_for_status.side_effect = requests.exceptions.HTTPError('500 Server Error: Internal')

        with patch.object(llm.RunBudget, 'price', return_value=(Decimal('.000001'), Decimal('.000001'))), \
             patch.object(llm.requests, 'post', side_effect=[
                 resp_401,                           # OpenRouter attempt
                 resp_gemini_500.raise_for_status.side_effect,  # Gemini attempt
                 requests.exceptions.ConnectTimeout('Ollama timeout') # Ollama attempt
             ]):
            with self.assertRaises(llm.LLMError) as ctx:
                llm.generate_llm_response('prompt', configs)

        err_msg = str(ctx.exception)
        self.assertIn('OpenRouter: OpenRouter HTTP 401', err_msg)
        self.assertIn('Google Gemini: 500 Server Error', err_msg)
        self.assertIn('Local Ollama: Ollama 연결/응답 실패', err_msg)

    def test_first_engine_fails_second_succeeds_returns_content(self):
        """When first engine fails, second engine's success is returned transparently."""
        configs = [
            {'provider': 'OpenRouter', 'model': 'deepseek/test', 'api_key': 'bad-key', 'budget': llm.RunBudget(1)},
            {'provider': 'Local Ollama', 'model': 'local-test'}
        ]
        resp_401 = Mock(status_code=401)
        resp_ollama = Mock(status_code=200)
        resp_ollama.json.return_value = {'response': 'Ollama succeeded'}

        with patch.object(llm.RunBudget, 'price', return_value=(Decimal('.000001'), Decimal('.000001'))), \
             patch.object(llm.requests, 'post', side_effect=[resp_401, resp_ollama]):
            res = llm.generate_llm_response('prompt', configs)

        self.assertEqual(res, 'Ollama succeeded')

    def test_empty_engine_configs_raises_appropriate_message(self):
        """Empty engine configs raises '사용 가능한 엔진 없음'."""
        with self.assertRaises(llm.LLMError) as ctx:
            llm.generate_llm_response('prompt', [])
        self.assertEqual(str(ctx.exception), '사용 가능한 엔진 없음')

    def test_budget_exceeded_fallback_to_ollama_in_generate_llm_response(self):
        """When OpenRouter raises BudgetExceeded, generate_llm_response catches it and falls back to Ollama."""
        exhausted_budget = llm.RunBudget(limit=Decimal('0.0001'))
        exhausted_budget.spent = Decimal('0.0001')  # 100% spent
        configs = [
            {'provider': 'OpenRouter', 'model': 'deepseek/test', 'api_key': 'key', 'budget': exhausted_budget},
            {'provider': 'Local Ollama', 'model': 'local-test'}
        ]
        resp_ollama = Mock(status_code=200)
        resp_ollama.json.return_value = {'response': 'Ollama fallback answer after budget exceeded'}

        with patch.object(llm.RunBudget, 'price', return_value=(Decimal('.000001'), Decimal('.000001'))), \
             patch.object(llm.requests, 'post', return_value=resp_ollama):
            res = llm.generate_llm_response('prompt', configs)

        self.assertEqual(res, 'Ollama fallback answer after budget exceeded')



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
