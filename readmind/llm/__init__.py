"""Model factories for both frameworks + a process-wide rate limiter.

    adk_model()  -> ADK model: ``Gemini(...)`` with HTTP retry/back-off, or ``MockAdkLlm``
    crew_llm()   -> CrewAI LLM: native Gemini provider with retry, or ``MockCrewLlm``

The limiter is shared by ADK (``before_model_callback``) and CrewAI
(``before_llm_call`` hook), so a free-tier RPM quota holds across both frameworks.
"""
from __future__ import annotations

import threading
import time

from readmind.settings import get_settings


class RateLimiter:
    """Minimum spacing between LLM requests (simple, fair, thread-safe)."""

    def __init__(self, rpm: int):
        self.interval = 60.0 / max(1, rpm)
        self._lock = threading.Lock()
        self._next = 0.0
        self.waited_s = 0.0

    def acquire(self) -> float:
        with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.interval
        if wait > 0:
            time.sleep(wait)
            self.waited_s += wait
        return wait


_LIMITER: RateLimiter | None = None


def limiter() -> RateLimiter:
    global _LIMITER
    rpm = get_settings().rpm
    if _LIMITER is None or abs(_LIMITER.interval - 60.0 / max(1, rpm)) > 1e-9:
        _LIMITER = RateLimiter(rpm)
    return _LIMITER


def _retry_options():
    from google.genai import types
    return types.HttpRetryOptions(attempts=get_settings().max_retries_on_quota + 1, initial_delay=4.0,
                                  max_delay=60.0, exp_base=2.0, http_status_codes=[429, 500, 502, 503, 504])


def adk_model():
    s = get_settings()
    if s.is_mock:
        from readmind.llm.mock import MockAdkLlm
        return MockAdkLlm()
    from google.adk.models.google_llm import Gemini
    return Gemini(model=s.model, retry_options=_retry_options())


def crew_llm(temperature: float | None = None):
    s = get_settings()
    if s.is_mock:
        from readmind.llm.mock import MockCrewLlm
        return MockCrewLlm()
    from crewai import LLM
    from google.genai import types
    return LLM(model=s.crew_model, api_key=s.api_key, temperature=s.temperature if temperature is None else temperature,
               client_params={"http_options": types.HttpOptions(retry_options=_retry_options())})


def model_label() -> str:
    s = get_settings()
    return "mock" if s.is_mock else s.model
