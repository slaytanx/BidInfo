import os
import re
import requests
import json

def call_gemini(prompt: str, api_key: str, model_name: str = "gemini-1.5-flash") -> str:
    """Google Gemini API 호출"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    if r.status_code == 200:
        res_json = r.json()
        try:
            return res_json["candidates"][0]["content"]["parts"][0]["text"]
        except Exception:
            raise Exception("Gemini 응답 파싱 실패")
    else:
        raise Exception(f"Gemini API 오류 ({r.status_code}): {r.text[:200]}")

def call_openrouter(prompt: str, api_key: str, model_name: str = "google/gemini-2.0-flash-exp:free") -> str:
    """OpenRouter API 호출 (무료 모델 포함)"""
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}]
    }
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    if r.status_code == 200:
        res_json = r.json()
        return res_json["choices"][0]["message"]["content"]
    else:
        raise Exception(f"OpenRouter API 오류 ({r.status_code}): {r.text[:200]}")

def call_nvidia(prompt: str, api_key: str, model_name: str = "meta/llama-3.3-70b-instruct") -> str:
    """NVIDIA NIM API 호출"""
    url = "https://integrate.api.nvidia.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 2048
    }
    r = requests.post(url, headers=headers, json=payload, timeout=30)
    if r.status_code == 200:
        res_json = r.json()
        return res_json["choices"][0]["message"]["content"]
    else:
        raise Exception(f"NVIDIA API 오류 ({r.status_code}): {r.text[:200]}")

def call_ollama(prompt: str, model_name: str = "gemma4:e4b-mlx") -> str:
    """로컬 Ollama API 호출"""
    url = "http://localhost:11434/api/generate"
    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False
    }
    r = requests.post(url, json=payload, timeout=60)
    if r.status_code == 200:
        return r.json().get("response", "")
    else:
        raise Exception(f"Ollama 오류 ({r.status_code})")

def generate_llm_response(prompt: str, engine_configs: list) -> str:
    """
    🎯 1차, 2차, 3차 차례대로 폴백(Fallback) 호출하는 지능형 통합 AI 엔진
    engine_configs 예시:
    [
        {"provider": "Google Gemini", "api_key": "AIza...", "model": "gemini-1.5-flash"},
        {"provider": "OpenRouter", "api_key": "sk-or...", "model": "google/gemini-2.0-flash-exp:free"},
        {"provider": "Local Ollama", "api_key": "", "model": "gemma4:e4b-mlx"}
    ]
    """
    errors = []
    for idx, cfg in enumerate(engine_configs, 1):
        provider = cfg.get("provider", "Local Ollama")
        api_key = cfg.get("api_key", "").strip()
        model_name = cfg.get("model", "gemini-1.5-flash").strip()

        if not provider or provider == "사용 안함":
            continue

        try:
            print(f"🤖 [{idx}차 AI 엔진 시도] 프로바이더: {provider} | 모델: {model_name}")
            if provider == "Google Gemini":
                if not api_key:
                    raise Exception("Gemini API Key가 설정되지 않았습니다.")
                return call_gemini(prompt, api_key, model_name)

            elif provider == "OpenRouter":
                if not api_key:
                    raise Exception("OpenRouter API Key가 설정되지 않았습니다.")
                return call_openrouter(prompt, api_key, model_name)

            elif provider == "NVIDIA NIM":
                if not api_key:
                    raise Exception("NVIDIA API Key가 설정되지 않았습니다.")
                return call_nvidia(prompt, api_key, model_name)

            elif provider == "Local Ollama":
                return call_ollama(prompt, model_name)

        except Exception as e:
            err_msg = f"{idx}차 [{provider}] 실패: {e}"
            print(f"⚠️ {err_msg}")
            errors.append(err_msg)

    # 모든 차수 실패 시 기본 안내 반환
    return f"⚠️ 지정된 모든 AI 엔진(1차~3차) 호출에 실패했습니다.\n사유:\n" + "\n".join(errors)
