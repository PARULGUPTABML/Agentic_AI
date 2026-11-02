"""One interface over several LLM providers: gemini | openai | ollama | mock.
`mock` needs no API key and is used by tests/CI."""
from __future__ import annotations
import json
import re
import time
from functools import lru_cache

import requests

from config import GEMINI_API_KEY, LLM_MODEL, LLM_PROVIDER, OLLAMA_URL, OPENAI_API_KEY


@lru_cache(maxsize=1)
def _gemini():
    from google import genai
    return genai.Client(api_key=GEMINI_API_KEY or None)


@lru_cache(maxsize=1)
def _openai():
    from openai import OpenAI
    return OpenAI(api_key=OPENAI_API_KEY or None)


def _call(provider, model, system, prompt, temperature, json_mode):
    if provider == "gemini":
        from google.genai import types
        cfg = {"system_instruction": system, "temperature": temperature}
        if json_mode:
            cfg["response_mime_type"] = "application/json"
        
        # Build candidate list with primary model first, followed by resilient fallbacks
        candidates = [model]
        for fb in ("gemini-3.5-flash-lite", "gemini-3-flash-preview", "gemini-3.5-flash"):
            if fb not in candidates:
                candidates.append(fb)
                
        last_err = None
        for candidate_model in candidates:
            for attempt in range(3):
                try:
                    resp = _gemini().models.generate_content(
                        model=candidate_model, contents=prompt,
                        config=types.GenerateContentConfig(**cfg)
                    )
                    return resp.text or ""
                except Exception as e:
                    last_err = e
                    err_msg = str(e).lower()
                    if "503" in err_msg or "unavailable" in err_msg:
                        time.sleep(1.0 * (attempt + 1))
                        continue
                    # If 429 quota exhausted or 404, immediately switch to next fallback model
                    break
        raise last_err
    if provider == "openai":
        kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
        resp = _openai().chat.completions.create(
            model=model, temperature=temperature,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}], **kwargs)
        return resp.choices[0].message.content or ""
    if provider == "ollama":
        body = {"model": model, "stream": False, "options": {"temperature": temperature},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        if json_mode:
            body["format"] = "json"
        r = requests.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=300)
        r.raise_for_status()
        return r.json()["message"]["content"]
    if provider == "mock":
        return _mock(prompt, json_mode)
    raise ValueError(f"Unknown LLM provider '{provider}'")


def _mock(prompt, json_mode):
    if json_mode:
        return json.dumps({"accuracy": 3, "relevance": 3, "key_points_covered": [True, False],
                           "claims_total": 2, "claims_unsupported": 1, "rationale": "mock judge",
                           "question": "Why was this changed?", "category": "rationale",
                           "reference_answer": "mock", "key_points": ["mock point"]})
    ids = re.findall(r"^\[((?:code|commit|issue|pr|review|doc):[^\]]+)\]", prompt, re.M)
    
    # Simulate LLM Only (no context)
    if not ids:
        return "I am not certain about the history of this code without more information. The authentication logic is not a standard pattern, and without access to the repository's historical context or issue tracker, I cannot definitively explain the architectural decision behind it."
        
    has_history = any(i.startswith("commit:") or i.startswith("issue:") or i.startswith("pr:") for i in ids)
    
    # Simulate Code Only (has code, but no history)
    if not has_history:
        return (f"Based on the provided codebase evidence [{ids[0]}], the authentication logic uses a secure token system and rotates session IDs during login.\n\n"
                "However, looking purely at the source code, there are no comments explaining *why* this specific approach was chosen over standard JWT tokens. To understand the rationale, we would need to review the historical Pull Requests or Issues associated with this file.")
                
    # Simulate Full History (RAG - Code + History)
    history_id = next((i for i in ids if not i.startswith("code:") and not i.startswith("doc:")), ids[0])
    code_id = next((i for i in ids if i.startswith("code:")), ids[-1])
    
    return (f"Based on the retrieved code [{code_id}] and the historical context from [{history_id}], the authentication logic was implemented this way to resolve a critical security vulnerability.\n\n"
            f"According to the discussions in [{history_id}], the team discovered a potential session fixation attack vector. As a result, the maintainers explicitly chose to rotate the session ID on every single login attempt. This architectural decision guarantees that older, potentially compromised session tokens cannot be reused maliciously.")


def generate(system: str, prompt: str, provider: str | None = None, model: str | None = None,
             temperature: float = 0.0, json_mode: bool = False, retries: int = 3) -> str:
    provider, model = provider or LLM_PROVIDER, model or LLM_MODEL
    for attempt in range(retries):
        try:
            return _call(provider, model, system, prompt, temperature, json_mode)
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** (attempt + 1))
    return ""


def parse_json(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else {}
