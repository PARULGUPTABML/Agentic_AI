"""Instrumentation wrappers: per-agent latency, tool calls, evidence counts, errors.

Wrappers are transparent to the architecture variants: they keep the
``run(query, repo_slug=...)`` / ``run(blackboard)`` contract (including
re-raising ``TypeError`` so the architectures' blackboard fall-back still works).
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any

from observability.tracker import current_agent, set_current_agent


class RunRecorder:
    """Collects per-agent measurements for ONE run."""

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.agent_stats: dict[str, dict[str, Any]] = defaultdict(
                lambda: {"calls": 0, "latency_ms": 0.0, "fragments": 0, "errors": 0})
            self.tool_calls: list[dict[str, Any]] = []
            self.errors: list[dict[str, str]] = []
            self.events: list[dict[str, Any]] = []
            self.verification_rounds = 0

    def record(self, agent: str, ms: float, fragments: int = 0, tools: list[dict] | None = None,
               error: str | None = None) -> None:
        with self._lock:
            s = self.agent_stats[agent]
            s["calls"] += 1
            s["latency_ms"] += ms
            s["fragments"] += fragments
            for t in tools or []:
                res = t.get("results")
                entry = {"agent": agent, "tool": t.get("tool", "?"), "arg": str(t.get("arg", ""))[:200],
                         "results": res if isinstance(res, (int, float)) else (len(res) if hasattr(res, "__len__") else 0),
                         "ms": t.get("ms", 0.0)}
                if t.get("error"):
                    entry["error"] = t["error"]
                self.tool_calls.append(entry)
            if error:
                s["errors"] += 1
                self.errors.append({"agent": agent, "error": error})
            self.events.append({"agent": agent, "ms": round(ms, 2), "t": time.time(), "fragments": fragments})

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "agent_stats": {k: {kk: (round(vv, 2) if isinstance(vv, float) else vv) for kk, vv in v.items()}
                                for k, v in self.agent_stats.items()},
                "tool_calls": list(self.tool_calls),
                "errors": list(self.errors),
                "verification_rounds": self.verification_rounds,
            }


class InstrumentedAgent:
    """Wraps a retrieval agent (code_analysis / history_rag / issue_pr)."""

    def __init__(self, name: str, agent: Any, recorder: RunRecorder):
        self.name = name
        self.agent = agent
        self.recorder = recorder

    def __getattr__(self, item):
        return getattr(self.agent, item)

    def run(self, *args, **kwargs):
        prev = current_agent()
        set_current_agent(self.name)
        t0 = time.perf_counter()
        try:
            out = self.agent.run(*args, **kwargs)
        except TypeError:
            # Signature mismatch: let the architecture retry with run(blackboard).
            set_current_agent(prev if prev != "unattributed" else None)
            raise
        except Exception as e:
            ms = (time.perf_counter() - t0) * 1000
            self.recorder.record(self.name, ms, 0, getattr(self.agent, "last_trace", None),
                                 error=f"{type(e).__name__}: {e}")
            set_current_agent(prev if prev != "unattributed" else None)
            # Degrade gracefully: a failing agent contributes no evidence, the run continues.
            return []
        ms = (time.perf_counter() - t0) * 1000
        n = len(out) if isinstance(out, list) else 0
        self.recorder.record(self.name, ms, n, getattr(self.agent, "last_trace", None))
        set_current_agent(prev if prev != "unattributed" else None)
        return out


class InstrumentedVerifier(InstrumentedAgent):
    def run(self, blackboard):
        self.recorder.verification_rounds += 1
        prev = current_agent()
        set_current_agent(self.name)
        t0 = time.perf_counter()
        try:
            out = self.agent.run(blackboard)
        except Exception as e:
            ms = (time.perf_counter() - t0) * 1000
            self.recorder.record(self.name, ms, error=f"{type(e).__name__}: {e}")
            set_current_agent(prev if prev != "unattributed" else None)
            # Fail safe: treat as sufficient so the run still produces an answer, and flag it.
            out = {"sufficient": True, "confidence": 0.0, "gaps": [f"verifier error: {e}"],
                   "suggested_queries": [], "error": True}
            blackboard.verification_result = out
            return out
        self.recorder.record(self.name, (time.perf_counter() - t0) * 1000)
        set_current_agent(prev if prev != "unattributed" else None)
        return out


def instrument_method(obj: Any, method: str, agent_name: str, recorder: RunRecorder) -> None:
    """Patch ``obj.method`` in place (orchestrator.plan / replan, reviewer.review)."""
    original = getattr(obj, method)
    if getattr(original, "__instrumented__", False):
        return

    def wrapped(*args, **kwargs):
        prev = current_agent()
        set_current_agent(agent_name)
        t0 = time.perf_counter()
        err = None
        try:
            return original(*args, **kwargs)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            raise
        finally:
            recorder.record(agent_name, (time.perf_counter() - t0) * 1000, error=err)
            set_current_agent(prev if prev != "unattributed" else None)

    wrapped.__instrumented__ = True  # type: ignore[attr-defined]
    setattr(obj, method, wrapped)
