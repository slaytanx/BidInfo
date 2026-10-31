"""모델 호출, 비용 예약과 제한된 동시 실행."""
from decimal import Decimal
import json
import os
from pathlib import Path
import threading
import time
import requests

LOCAL_MODEL = 'gemma4:26b-mlx'
CLASSIFIER_MODEL = 'gemma4:e4b-mlx'
CLOUD_MODELS = ['deepseek/deepseek-v4-flash-0731', 'z-ai/glm-5.3-flash', 'upstage/solar-pro4']
LOCAL_MODELS = [LOCAL_MODEL, 'gemma4:12b-mlx', 'qwen3.8:fast', CLASSIFIER_MODEL]
_LOCAL_LOCK = threading.Semaphore(1)
_CLOUD_LOCK = threading.Semaphore(2)
SECRET_FILE = Path(__file__).parent / '.local_secrets.json'


class LLMError(RuntimeError):
    pass


class BudgetExceeded(LLMError):
    pass


def get_secret(name, default=''):
    if os.environ.get(name):
        return os.environ[name]
    dotenv = SECRET_FILE.parent / '.env'
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            if line.strip().startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            if key.strip() == name and value.strip():
                return value.strip().strip('"\x27')
    try:
        return json.loads(SECRET_FILE.read_text()).get(name, default)
    except (OSError, ValueError):
        return default


def save_secrets(values):
    try:
        existing = json.loads(SECRET_FILE.read_text())
    except (OSError, ValueError):
        existing = {}
    for key, value in values.items():
        if value:
            existing[key] = value
        else:
            existing.pop(key, None)
    descriptor = os.open(SECRET_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as file:
        json.dump(existing, file)
    SECRET_FILE.chmod(0o600)


def parse_json(value):
    text = value.strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1].rsplit('```', 1)[0]
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise LLMError('JSON 객체가 아닌 모델 응답')
    return parsed


class RunBudget:
    def __init__(self, limit=1):
        self.limit = Decimal(str(limit))
        if self.limit < 0:
            raise ValueError('예산은 0 이상이어야 합니다')
        self.spent = Decimal(0)
        self.reserved = Decimal(0)
        self.calls = 0
        self.lock = threading.Lock()
        self.prices = {}

    def price(self, model):
        with self.lock:
            if model not in self.prices:
                response = requests.get('https://openrouter.ai/api/v1/models', timeout=(5, 20))
                response.raise_for_status()
                for item in response.json()['data']:
                    p = item.get('pricing', {})
                    if p.get('prompt') is not None and p.get('completion') is not None:
                        self.prices[item['id']] = (Decimal(p['prompt']), Decimal(p['completion']))
            if model not in self.prices:
                raise LLMError('현재 모델 목록에서 모델/가격 확인 불가')
            return self.prices[model]

    def reserve(self, amount):
        with self.lock:
            if self.spent + self.reserved + amount > self.limit:
                raise BudgetExceeded('실행 예산 부족: 로컬 분석으로 전환')
            self.reserved += amount
            self.calls += 1

    def settle(self, reserved, actual=None):
        with self.lock:
            self.reserved -= reserved
            # 응답이 없으면 과금 여부를 알 수 없으므로 예약액을 보수적으로 유지한다.
            self.spent += Decimal(str(actual)) if actual is not None else reserved

    def snapshot(self):
        with self.lock:
            return {'limit_usd': float(self.limit), 'spent_or_reserved_usd': float(self.spent + self.reserved), 'cloud_calls': self.calls}


def call_ollama(prompt, model_name=LOCAL_MODEL, max_tokens=1600, json_mode=False):
    payload = {'model': model_name, 'prompt': prompt, 'stream': False, 'think': False,
               'keep_alive': '10m', 'options': {'temperature': 0, 'num_predict': max_tokens, 'num_ctx': 16384}}
    if json_mode:
        payload['format'] = 'json'
    with _LOCAL_LOCK:
        try:
            response = requests.post('http://localhost:11434/api/generate', json=payload, timeout=(5, 180))
            response.raise_for_status()
            value = response.json().get('response', '').strip()
            if not value:
                raise LLMError('Ollama 빈 응답')
            return value
        except requests.RequestException as exc:
            raise LLMError('Ollama 연결/응답 실패') from exc


def call_openrouter(prompt, api_key, model_name=CLOUD_MODELS[0], budget=None, max_tokens=1800, json_mode=False):
    if not api_key:
        raise LLMError('OPENROUTER_API_KEY 미설정')
    budget = budget or RunBudget()
    input_price, output_price = budget.price(model_name)
    # UTF-8 바이트 수는 텍스트 토큰 수의 보수적인 상한으로 사용한다.
    reserve = input_price * (len(prompt.encode('utf-8')) + 256) + output_price * max_tokens
    payload = {'model': model_name, 'messages': [{'role': 'user', 'content': prompt}], 'temperature': 0,
               # effort=low로는 deepseek-v4-flash가 max_tokens를 추론에 다 써 본문이 비거나 끊긴다.
               'max_tokens': max_tokens, 'reasoning': {'enabled': False},
               'provider': {'sort': 'throughput', 'max_price': {'prompt': float(input_price * 1000000), 'completion': float(output_price * 1000000), 'request': 0}}}
    if json_mode:
        payload['response_format'] = {'type': 'json_object'}
        payload['provider']['require_parameters'] = True
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    with _CLOUD_LOCK:
        for attempt in range(2):
            budget.reserve(reserve)
            cost = None
            try:
                response = requests.post('https://openrouter.ai/api/v1/chat/completions', headers=headers, json=payload, timeout=(5, 90))
                if response.status_code in (429, 500, 502, 503, 504):
                    if attempt == 0:
                        time.sleep(min(5, max(1, float(response.headers.get('Retry-After', '1')))))
                        continue
                if response.status_code >= 400:
                    cost = 0
                    raise LLMError(f'OpenRouter HTTP {response.status_code}')
                data = response.json()
                cost = data.get('usage', {}).get('cost')
                value = data['choices'][0]['message'].get('content', '')
                if not value:
                    raise LLMError('OpenRouter 빈 응답')
                return value
            except requests.RequestException as exc:
                raise LLMError('OpenRouter 연결/응답 실패') from exc
            finally:
                budget.settle(reserve, cost)
    raise LLMError('OpenRouter 재시도 실패')


def generate_llm_response(prompt, engine_configs, json_mode=False, max_tokens=1800):
    errors = []
    for config in engine_configs:
        provider = config.get('provider')
        try:
            if provider == 'Local Ollama':
                return call_ollama(prompt, config.get('model', LOCAL_MODEL), max_tokens, json_mode)
            if provider == 'OpenRouter':
                return call_openrouter(prompt, config.get('api_key') or get_secret('OPENROUTER_API_KEY'),
                                       config.get('model', CLOUD_MODELS[0]), config.get('budget'), max_tokens, json_mode)
            if provider in ('Google Gemini', 'NVIDIA NIM'):
                # 기존 공급자 설정도 유지하되 외부 호출은 명시적으로 선택된 경우만 수행한다.
                key = config.get('api_key', '')
                if not key:
                    raise LLMError('API 키 미설정')
                if provider == 'Google Gemini':
                    response = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{config['model']}:generateContent",
                        headers={'x-goog-api-key': key}, json={'contents': [{'parts': [{'text': prompt}]}]}, timeout=(5, 90))
                    response.raise_for_status()
                    return response.json()['candidates'][0]['content']['parts'][0]['text']
                response = requests.post('https://integrate.api.nvidia.com/v1/chat/completions',
                    headers={'Authorization': f'Bearer {key}'}, json={'model': config['model'], 'messages': [{'role': 'user', 'content': prompt}], 'max_tokens': max_tokens}, timeout=(5, 90))
                response.raise_for_status()
                return response.json()['choices'][0]['message']['content']
        except Exception:
            errors.append(f'{provider}: 호출 실패')
    raise LLMError('; '.join(errors) or '사용 가능한 엔진 없음')


def engine_configs(mode='혼합', cloud_model=CLOUD_MODELS[0], local_model=LOCAL_MODEL, budget=None):
    local = {'provider': 'Local Ollama', 'model': local_model}
    if mode == '로컬':
        return [local]
    return [{'provider': 'OpenRouter', 'model': cloud_model, 'api_key': get_secret('OPENROUTER_API_KEY'), 'budget': budget or RunBudget()}, local]
