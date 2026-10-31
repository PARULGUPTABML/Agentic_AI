"""Model resolution for the judge (and for baselines A/B).

Selection via environment variables::

    JUDGE_PROVIDER = auto | shared | gemini | openai | mock     (default: auto)
    JUDGE_MODEL    = model name for gemini / openai providers
    JUDGE_API_KEY  = API key (falls back to GEMINI_API_KEY / OPENAI_API_KEY)
    JUDGE_BASE_URL = OpenAI-compatible base URL (OpenAI, Groq, OpenRouter, Ollama, vLLM ...)

``auto`` uses the project's shared ``rag.llm.generate`` (through the rate-limit
retry helper) unless ``LLM_PROVIDER`` is ``mock``; then it returns ``None`` and the
judge falls back to its deterministic heuristic.

Using a *different* model family for the judge than for the answering agents
reduces self-preference bias (Zheng et al., 2023) — set JUDGE_PROVIDER/JUDGE_MODEL
accordingly for the final runs.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Callable

LLMFn = Callable[[str, str], str]


def _post_json(url: str, payload: dict, headers: dict, retries: int = 4) -> dict:
    delay = 5.0
    for attempt in range(retries):
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise


def gemini_fn(model: str, api_key: str) -> LLMFn:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    def call(system: str, prompt: str) -> str:
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.0}}
        out = _post_json(url, body, {"Content-Type": "application/json"})
        return "".join(p.get("text", "") for p in out["candidates"][0]["content"]["parts"])
    return call


def openai_compatible_fn(model: str, api_key: str, base_url: str) -> LLMFn:
    url = base_url.rstrip("/") + "/chat/completions"

    def call(system: str, prompt: str) -> str:
        body = {"model": model, "temperature": 0,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        out = _post_json(url, body, {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
        return out["choices"][0]["message"]["content"]
    return call


def shared_fn() -> LLMFn | None:
    try:
        from agents._llm_retry import generate_with_retry
    except Exception:
        return None
    return lambda system, prompt: generate_with_retry(system, prompt, temperature=0.0)


def get_judge_llm(provider: str | None = None) -> LLMFn | None:
    provider = (provider or os.environ.get("JUDGE_PROVIDER", "auto")).lower()
    model = os.environ.get("JUDGE_MODEL", "")
    if provider in ("mock", "none", "heuristic"):
        return None
    if provider == "gemini":
        key = os.environ.get("JUDGE_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
        if not (key and model):
            raise RuntimeError("JUDGE_PROVIDER=gemini needs JUDGE_MODEL and JUDGE_API_KEY/GEMINI_API_KEY")
        return gemini_fn(model, key)
    if provider == "openai":
        key = os.environ.get("JUDGE_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
        base = os.environ.get("JUDGE_BASE_URL", "https://api.openai.com/v1")
        if not model:
            raise RuntimeError("JUDGE_PROVIDER=openai needs JUDGE_MODEL (and JUDGE_BASE_URL for non-OpenAI hosts)")
        return openai_compatible_fn(model, key, base)
    # auto / shared
    if os.environ.get("LLM_PROVIDER", "").lower() in ("", "mock", "none") and provider == "auto":
        return None
    return shared_fn()


def describe_judge(provider: str | None = None) -> str:
    provider = (provider or os.environ.get("JUDGE_PROVIDER", "auto")).lower()
    if get_judge_llm(provider) is None:
        return "heuristic"
    if provider in ("gemini", "openai"):
        return f"{provider}:{os.environ.get('JUDGE_MODEL', '')}"
    return f"shared:{os.environ.get('LLM_PROVIDER', '')}:{os.environ.get('LLM_MODEL', '')}"
