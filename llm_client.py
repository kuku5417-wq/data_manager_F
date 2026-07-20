"""llm_client.py — LLM 단발 호출 (data_manager 이식본).

원본: tbm_system_v6/modules/llm_client.py
변경: secret 접근을 settings.secrets(.env SECTION_KEY)로, _extract_sola_content 인라인.
동작(data_manager_F 사내망 전용): 사내 SOLA 단일 호출. Upstage/OpenAI 폴백 제거.
      SOLA는 프록시 없이 직접 접속(verify=False).
"""
from __future__ import annotations

import json
import re

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from settings import secrets


def _req_proxies():
    """사내망 프록시 — OS 환경변수 → .env(settings.proxy_url) 순으로 읽고, 호스트 있는 유효값만 사용.

    OpenAI SDK(httpx)는 사내 MITM 프록시에서 행(hang)이 발생 → requests 사용.
    스킴만 있는 불완전 값('http://')·미설정은 건너뛰고, 없으면 None(직접 접속, 사외망).
    """
    import os
    for c in (os.environ.get("HTTPS_PROXY"), os.environ.get("HTTP_PROXY"),
              os.environ.get("https_proxy"), os.environ.get("http_proxy"),
              secrets.proxy_url):          # .env HTTPS_PROXY/HTTP_PROXY 폴백
        c = (c or "").strip()
        if "://" in c and c.split("://", 1)[1]:   # 호스트가 있는 유효 프록시
            return {"http": c, "https": c}
    return None


_PROXY_DEFAULT = object()   # _req_proxies() 사용 표시 (None = 명시적 직접접속과 구분)


def _openai_compat_chat(base_url: str, api_key: str, model: str, system: str,
                        user_prompt: str, max_tokens: int, temperature: float,
                        proxies=_PROXY_DEFAULT, response_format=None,
                        verify=None) -> str:
    """OpenAI 호환 /chat/completions 를 requests 로 호출 (httpx 프록시 행 회피).

    model/max_tokens 가 비면 payload에서 생략(SOLA 게이트웨이는 max_tokens 거부 → None 전달).
    proxies 미지정 시 _req_proxies(); SOLA는 {"http":None,"https":None}(프록시 우회) 전달.
    response_format 지정 시 body에 포함(SOLA는 {"type":"json_object"} — OCR과 동일).
    verify 미지정 시 자동: 프록시 미경유(사외망 직접 접속)=True, 사내 프록시 경유=False
    (사내 SSL 인스펙션 대응). SOLA(사내 자가서명)는 호출부가 False 를 명시.
    """
    messages = [*([{"role": "system", "content": system}] if system else []),
                {"role": "user", "content": user_prompt}]
    body = {"messages": messages, "temperature": temperature}
    if model:
        body["model"] = model
    if max_tokens:
        body["max_tokens"] = max_tokens
    if response_format:
        body["response_format"] = response_format
    px = _req_proxies() if proxies is _PROXY_DEFAULT else proxies
    if verify is None:
        verify = not (px and (px.get("http") or px.get("https")))
    resp = requests.post(
        base_url.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body, proxies=px, timeout=60, verify=verify,
    )
    resp.raise_for_status()
    return (resp.json()["choices"][0]["message"]["content"] or "").strip()


def _call_sola(system: str, user_prompt: str, max_tokens: int, temperature: float) -> str:
    """사내망 SOLA — OCR 앱과 동일한 OpenAI 호환 /chat/completions (Bearer, 프록시 우회)."""
    base_url = secrets.sola_endpoint        # LLM_SOLAR_API_URL (OpenAI 호환 base, 예: .../v1)
    api_key  = secrets.sola_api_key         # LLM_SOLAR_API_KEY
    if not base_url or not api_key:
        raise RuntimeError(".env LLM_SOLAR_API_URL/LLM_SOLAR_API_KEY 미설정")
    return _openai_compat_chat(
        base_url, api_key, secrets.sola_model,   # model=LLM_SOLAR_MODEL (비면 생략)
        system, user_prompt,
        max_tokens=None,                         # SOLA는 max_tokens 거부(400) — OCR처럼 미전송
        temperature=temperature,
        proxies={"http": None, "https": None},   # 사내망 SOLA는 프록시 우회(직접 접속)
        response_format={"type": "json_object"}, # OCR과 동일 — JSON 강제
        verify=False,                            # 사내 자가서명 인증서 — SOLA 만 검증 생략
    )


# 마지막 call_llm 의 실패 사유 — 진단/표면화용 (성공 시 빈 리스트)
_LLM_LAST_ERRORS: list[str] = []


def call_llm(prompt: str,
             system: str = "당신은 조선소 현장 안전 전문가입니다. JSON 형식으로만 응답하세요.",
             max_tokens: int = 512,
             temperature: float = 0.3) -> dict | None:
    """사내망 SOLA 호출. Returns: dict 또는 None(실패).

    data_manager_F(사내망 전용): Upstage/OpenAI 폴백 제거, SOLA 단일.
    실패 사유는 모듈 변수 _LLM_LAST_ERRORS 에 기록(숨은 실패 표면화).
    """
    global _LLM_LAST_ERRORS
    _LLM_LAST_ERRORS = []
    try:
        content = None
        try:
            content = _call_sola(system, prompt, max_tokens, temperature)
            if not content:
                _LLM_LAST_ERRORS.append("sola: 빈 응답")
        except Exception as e:
            _LLM_LAST_ERRORS.append(f"sola: {type(e).__name__}: {e}")
        if not content:
            return None

        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content).strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", content, re.DOTALL)
            if m:
                return json.loads(m.group())
            _LLM_LAST_ERRORS.append(f"json 파싱 실패: {content[:120]}")
            return None
    except Exception as e:
        _LLM_LAST_ERRORS.append(f"call_llm: {type(e).__name__}: {e}")
        return None
