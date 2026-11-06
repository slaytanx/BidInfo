"""모델 호출과 제한된 동시 실행."""
import json
import os
from pathlib import Path
import threading
import time
import requests

LOCAL_MODEL = 'nemo-bolt:fast'
CLASSIFIER_MODEL = 'nemo-bolt:fast'
CLOUD_MODELS = ['deepseek/deepseek-v4-flash-0731', 'z-ai/glm-5.3-flash', 'upstage/solar-pro4']
LOCAL_MODELS = [LOCAL_MODEL, 'gemma4:26b-mlx', 'gemma4:12b-mlx', 'qwen3.8:fast']
_LOCAL_LOCK = threading.Semaphore(1)
_CLOUD_LOCK = threading.Semaphore(2)
SECRET_FILE = Path(__file__).parent / '.local_secrets.json'


class LLMError(RuntimeError):
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


def call_openrouter(prompt, api_key, model_name=CLOUD_MODELS[0], max_tokens=1800, json_mode=False):
    if not api_key:
        raise LLMError('OPENROUTER_API_KEY 미설정')
    payload = {'model': model_name, 'messages': [{'role': 'user', 'content': prompt}], 'temperature': 0,
               # effort=low로는 deepseek-v4-flash가 max_tokens를 추론에 다 써 본문이 비거나 끊긴다.
               'max_tokens': max_tokens, 'reasoning': {'enabled': False}}
    if json_mode:
        payload['response_format'] = {'type': 'json_object'}
    headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
    with _CLOUD_LOCK:
        for attempt in range(2):
            try:
                response = requests.post('https://openrouter.ai/api/v1/chat/completions', headers=headers, json=payload, timeout=(5, 90))
                if response.status_code in (429, 500, 502, 503, 504):
                    if attempt == 0:
                        time.sleep(min(5, max(1, float(response.headers.get('Retry-After', '1')))))
                        continue
                if response.status_code >= 400:
                    raise LLMError(f'OpenRouter HTTP {response.status_code}')
                data = response.json()
                value = data['choices'][0]['message'].get('content', '')
                if not value:
                    raise LLMError('OpenRouter 빈 응답')
                return value
            except requests.RequestException as exc:
                raise LLMError('OpenRouter 연결/응답 실패') from exc
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
                                       config.get('model', CLOUD_MODELS[0]), max_tokens, json_mode)
            raise LLMError(f'미지원 공급자: {provider}')
        except Exception as exc:
            errors.append(f'{provider}: {exc}')
    raise LLMError('; '.join(errors) or '사용 가능한 엔진 없음')


def engine_configs(mode='혼합', cloud_model=CLOUD_MODELS[0], local_model=LOCAL_MODEL):
    local = {'provider': 'Local Ollama', 'model': local_model}
    if mode == '로컬':
        return [local]
    return [{'provider': 'OpenRouter', 'model': cloud_model, 'api_key': get_secret('OPENROUTER_API_KEY')}, local]
