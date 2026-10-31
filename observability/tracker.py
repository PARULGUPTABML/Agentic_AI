"""LLM call / token tracking with per-agent attribution.

Every agent in the repository reaches the model through one of a few module
attributes (``agents._llm_retry._raw_generate`` for most agents,
``agents.multi_agent_pipeline.generate`` and ``agents.messaging.generate`` for
the pipelines).  :class:`LLMCallTracker` temporarily wraps whichever of those
are loaded, so counting needs no change to the agents themselves.

Attribution uses a thread-local "current agent" that the instrumentation
wrappers set while an agent runs (thread-local rather than contextvars because
the hierarchical / event-driven architectures run agents on worker threads).

Token counts: providers do not expose usage through ``rag.llm.generate``'s string
return value, so tokens are *estimated* at ~4 characters per token and flagged
``tokens_estimated=True``.  Pass ``usage_fn`` to plug in exact counts.
"""
from __future__ import annotations

import importlib
import sys
import threading
import time
from collections import defaultdict
from typing import Any, Callable

_local = threading.local()

# (module, attribute) pairs that hold a ``generate(system, prompt, **kw) -> str`` callable.
DEFAULT_TARGETS = [
    ("agents._llm_retry", "_raw_generate"),
    ("agents.multi_agent_pipeline", "generate"),
    ("agents.messaging", "generate"),
    ("rag.llm", "generate"),
]


def set_current_agent(name: str | None) -> None:
    _local.agent = name


def current_agent() -> str:
    return getattr(_local, "agent", None) or "unattributed"


def estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // 4)


class LLMCallTracker:
    """Context manager that counts LLM calls made while it is active.

    Usage::

        with LLMCallTracker() as llm:
            arch.run(question, run_id)
        llm.summary()   # {"calls": 7, "by_agent": {...}, "prompt_tokens": ..., ...}
    """

    def __init__(self, targets: list[tuple[str, str]] | None = None,
                 usage_fn: Callable[[str, str, str], tuple[int, int]] | None = None):
        self.targets = targets or DEFAULT_TARGETS
        self.usage_fn = usage_fn
        self._lock = threading.Lock()
        self._patched: list[tuple[Any, str, Any]] = []
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.calls = 0
            self.errors = 0
            self.prompt_tokens = 0
            self.completion_tokens = 0
            self.latency_ms = 0.0
            self.by_agent: dict[str, dict[str, float]] = defaultdict(
                lambda: {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "latency_ms": 0.0, "errors": 0})

    def _wrap(self, fn: Callable) -> Callable:
        tracker = self

        def wrapped(system: str, prompt: str, *args, **kwargs):
            agent = current_agent()
            t0 = time.perf_counter()
            try:
                out = fn(system, prompt, *args, **kwargs)
            except Exception:
                with tracker._lock:
                    tracker.errors += 1
                    tracker.by_agent[agent]["errors"] += 1
                raise
            finally:
                ms = (time.perf_counter() - t0) * 1000
            text_out = out if isinstance(out, str) else str(out)
            if tracker.usage_fn:
                p_tok, c_tok = tracker.usage_fn(system, prompt, text_out)
            else:
                p_tok, c_tok = estimate_tokens(system) + estimate_tokens(prompt), estimate_tokens(text_out)
            with tracker._lock:
                tracker.calls += 1
                tracker.prompt_tokens += p_tok
                tracker.completion_tokens += c_tok
                tracker.latency_ms += ms
                a = tracker.by_agent[agent]
                a["calls"] += 1
                a["prompt_tokens"] += p_tok
                a["completion_tokens"] += c_tok
                a["latency_ms"] += ms
            return out

        wrapped.__wrapped_by_tracker__ = True  # type: ignore[attr-defined]
        return wrapped

    def __enter__(self) -> "LLMCallTracker":
        for mod_name, attr in self.targets:
            mod = sys.modules.get(mod_name)
            if mod is None:
                try:
                    mod = importlib.import_module(mod_name)
                except Exception:
                    continue
            fn = getattr(mod, attr, None)
            if fn is None or not callable(fn) or getattr(fn, "__wrapped_by_tracker__", False):
                continue
            self._patched.append((mod, attr, fn))
            setattr(mod, attr, self._wrap(fn))
        return self

    def __exit__(self, *exc) -> None:
        for mod, attr, fn in reversed(self._patched):
            setattr(mod, attr, fn)
        self._patched.clear()

    def summary(self) -> dict[str, Any]:
        with self._lock:
            return {
                "calls": self.calls,
                "errors": self.errors,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.prompt_tokens + self.completion_tokens,
                "llm_latency_ms": round(self.latency_ms, 2),
                "tokens_estimated": self.usage_fn is None,
                "by_agent": {k: {kk: (round(vv, 2) if isinstance(vv, float) else vv) for kk, vv in v.items()}
                             for k, v in self.by_agent.items()},
            }
