"""Rate-limit-aware wrapper around rag.llm.generate.

Gemini free tier allows only 5 requests/minute.  This module adds
automatic retry with exponential backoff so the multi-agent pipeline
doesn't crash on 429 RESOURCE_EXHAUSTED errors.
"""
from __future__ import annotations

import logging
import time

from rag.llm import generate as _raw_generate

logger = logging.getLogger(__name__)

_MAX_RETRIES = 4
_BASE_DELAY = 12  # seconds — free tier resets every ~12s


def generate_with_retry(system: str, prompt: str, **kwargs) -> str:
    """Call ``rag.llm.generate`` with automatic retry on rate-limit errors."""
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return _raw_generate(system, prompt, **kwargs)
        except Exception as exc:
            msg = str(exc).lower()
            if "429" in msg or "resource_exhausted" in msg or "quota" in msg:
                delay = _BASE_DELAY * attempt
                logger.warning(
                    "Rate limited (attempt %d/%d). Waiting %ds...",
                    attempt, _MAX_RETRIES, delay,
                )
                print(f"  [rate-limit] Waiting {delay}s before retry {attempt}/{_MAX_RETRIES}...")
                time.sleep(delay)
            else:
                raise  # non-rate-limit error — propagate immediately
    # Final attempt — let exceptions propagate
    return _raw_generate(system, prompt, **kwargs)
