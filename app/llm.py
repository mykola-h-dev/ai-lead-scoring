"""Minimal LLM client for Google Gemini or OpenAI, using only `requests`.

The provider is chosen from environment variables (never from files in the repo):
  - GEMINI_API_KEY  -> Google Gemini (model from GEMINI_MODEL, default gemini-2.5-flash)
  - OPENAI_API_KEY  -> OpenAI Chat Completions (model from OPENAI_MODEL, default gpt-4o-mini)
If neither is set, `is_available()` is False and callers use their rule-based fallback.
Keys are never logged; error messages contain only the HTTP status or exception type.
"""

import json
import os
import re

import requests

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
TIMEOUT_SECONDS = 20


class LLMError(RuntimeError):
    """Any failure of the AI path; callers catch it and fall back to rules."""


def provider_name():
    if os.environ.get("GEMINI_API_KEY", "").strip():
        return "gemini"
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return "openai"
    return None


def is_available():
    return provider_name() is not None


def generate(system_prompt, user_prompt, temperature=0.2):
    provider = provider_name()
    try:
        if provider == "gemini":
            return _gemini(system_prompt, user_prompt, temperature)
        if provider == "openai":
            return _openai(system_prompt, user_prompt, temperature)
    except requests.RequestException as exc:
        raise LLMError(f"LLM request failed: {type(exc).__name__}") from exc
    raise LLMError("No LLM API key configured")


def generate_json(system_prompt, user_prompt):
    """Ask for a JSON object and parse it (tolerates ```json fences)."""
    text = generate(system_prompt, user_prompt, temperature=0.0)
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise LLMError("LLM reply contained no JSON object")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise LLMError("LLM reply was not valid JSON") from exc
    if not isinstance(data, dict):
        raise LLMError("LLM reply was not a JSON object")
    return data


def _gemini(system_prompt, user_prompt, temperature):
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    body = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {"temperature": temperature},
    }
    headers = {"x-goog-api-key": os.environ["GEMINI_API_KEY"].strip(), "Content-Type": "application/json"}
    resp = requests.post(GEMINI_URL.format(model=model), json=body, headers=headers, timeout=TIMEOUT_SECONDS)
    if resp.status_code != 200:
        raise LLMError(f"Gemini API error {resp.status_code}")
    try:
        parts = resp.json()["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError, ValueError) as exc:
        raise LLMError("Unexpected Gemini response format") from exc


def _openai(system_prompt, user_prompt, temperature):
    body = {
        "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        "temperature": temperature,
        "messages": [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": user_prompt}],
    }
    headers = {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY'].strip()}"}
    resp = requests.post(OPENAI_URL, json=body, headers=headers, timeout=TIMEOUT_SECONDS)
    if resp.status_code != 200:
        raise LLMError(f"OpenAI API error {resp.status_code}")
    try:
        return resp.json()["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, ValueError) as exc:
        raise LLMError("Unexpected OpenAI response format") from exc
