"""Per-run monitoring, tracing and provenance.

A :class:`RunMonitor` is *activated* for the duration of one ``ReadMind.ask()``;
every callback of either framework (and the A2A server side, which runs in the
same process) writes to the active monitor:

* **events**   — ordered log: agent start/end, LLM request/response, tool start/end/error,
                 guardrail decisions, messages on the channel, state updates, A2A hops, errors
* **spans**    — a tree (trace_id = run_id) with parent/child links, duration and status
* **usage**    — prompt/completion tokens per framework (exact from Gemini; estimated in mock)
* **provenance ledger** — for every evidence id: which tool call (span) produced it, from
                 which MCP server, with which arguments, a SHA-256 of the raw result, and
                 whether the final answer cited it  -> auditability
* **otel**     — OpenTelemetry spans emitted by Google ADK itself (invoke_agent, call_llm,
                 execute_tool) captured through an in-memory exporter
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_ACTIVE: "RunMonitor | None" = None
_ACTIVE_LOCK = threading.Lock()
_OTEL_EXPORTER = None


def active_monitor() -> "RunMonitor | None":
    return _ACTIVE


def _sha(obj: Any) -> str:
    try:
        b = json.dumps(obj, sort_keys=True, default=str).encode()
    except Exception:
        b = str(obj).encode()
    return hashlib.sha256(b).hexdigest()[:16]


def _preview(obj: Any, n: int = 400) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    return s if len(s) <= n else s[:n] + "…"


def setup_otel():
    """Install a global OTel TracerProvider with an in-memory exporter (once)."""
    global _OTEL_EXPORTER
    if _OTEL_EXPORTER is not None:
        return _OTEL_EXPORTER
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
        exporter = InMemorySpanExporter()
        provider = trace.get_tracer_provider()
        if not isinstance(provider, TracerProvider):
            provider = TracerProvider()
            trace.set_tracer_provider(provider)
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        _OTEL_EXPORTER = exporter
    except Exception:
        _OTEL_EXPORTER = False
    return _OTEL_EXPORTER


class RunMonitor:
    def __init__(self, run_id: str | None = None, meta: dict | None = None):
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.meta = dict(meta or {})
        self.t0 = time.time()
        self._lock = threading.RLock()
        self.events: list[dict] = []
        self.spans: dict[str, dict] = {}
        self._stack = threading.local()
        self._open: dict[tuple, list[str]] = defaultdict(list)
        self.usage = defaultdict(lambda: {"calls": 0, "prompt": 0, "completion": 0, "estimated": False})
        self.counters = defaultdict(int)
        self.agent_ms: dict[str, float] = defaultdict(float)
        self.retrieved: dict[str, dict] = {}          # evidence id -> provenance
        self.guard_reports: list[dict] = []
        self.errors: list[str] = []
        self.otel_spans: list[dict] = []

    # ------------------------------------------------------------------ #
    def activate(self) -> "RunMonitor":
        global _ACTIVE
        with _ACTIVE_LOCK:
            _ACTIVE = self
        exp = setup_otel()
        if exp:
            exp.clear()
        self.event("system", "readmind", "run_start", self.meta)
        return self

    def deactivate(self) -> None:
        global _ACTIVE
        exp = setup_otel()
        if exp:
            for s in exp.get_finished_spans():
                ctx = s.get_span_context()
                self.otel_spans.append({
                    "name": s.name, "trace_id": f"{ctx.trace_id:032x}", "span_id": f"{ctx.span_id:016x}",
                    "parent_id": f"{s.parent.span_id:016x}" if s.parent else None,
                    "start_ns": s.start_time, "end_ns": s.end_time,
                    "duration_ms": round(((s.end_time or 0) - (s.start_time or 0)) / 1e6, 2),
                    "attributes": {k: (v if isinstance(v, (str, int, float, bool)) else str(v))[:300]
                                   if isinstance(v, str) else v for k, v in list((s.attributes or {}).items())[:25]},
                    "status": s.status.status_code.name})
            exp.clear()
        self.event("system", "readmind", "run_end", {"elapsed_ms": round((time.time() - self.t0) * 1000, 1)})
        with _ACTIVE_LOCK:
            if _ACTIVE is self:
                _ACTIVE = None

    # ------------------------------------------------------------------ #
    def event(self, framework: str, agent: str, kind: str, data: Any = None, duration_ms: float | None = None) -> None:
        with self._lock:
            self.events.append({
                "seq": len(self.events), "t_ms": round((time.time() - self.t0) * 1000, 2), "run_id": self.run_id,
                "framework": framework, "agent": agent, "kind": kind,
                "span_id": self._current_span(), "data": data if isinstance(data, (dict, list, type(None))) else
                _preview(data), **({"duration_ms": round(duration_ms, 2)} if duration_ms is not None else {})})
            self.counters[kind] += 1

    def add_usage(self, framework: str, prompt: int, completion: int, estimated: bool = False) -> None:
        with self._lock:
            u = self.usage[framework]
            u["calls"] += 1
            u["prompt"] += int(prompt or 0)
            u["completion"] += int(completion or 0)
            u["estimated"] = u["estimated"] or estimated

    def guard(self, report) -> None:
        d = report.model_dump() if hasattr(report, "model_dump") else dict(report)
        with self._lock:
            self.guard_reports.append(d)
            if not d.get("allowed", True):
                self.counters["guardrail_block"] += 1
        self.event("system", "guardrails", f"guardrail_{d.get('stage')}", d)

    def error(self, framework: str, agent: str, msg: str) -> None:
        with self._lock:
            self.errors.append(f"{framework}/{agent}: {msg}")
        self.event(framework, agent, "error", {"error": msg[:500]})

    # ------------------------------------------------------------------ #
    #  Spans                                                               #
    # ------------------------------------------------------------------ #
    def _current_span(self) -> str | None:
        st = getattr(self._stack, "s", None)
        return st[-1] if st else None

    def start_span(self, name: str, kind: str, framework: str, agent: str, attrs: dict | None = None,
                   key: tuple | None = None, parent: str | None = None, push: bool = True) -> str:
        """Open a span. Callback-driven spans pass an explicit ``parent`` and ``push=False``
        (ADK ParallelAgent interleaves agents on one thread, so a thread-local stack is unsafe)."""
        sid = uuid.uuid4().hex[:16]
        parent = parent if parent is not None else self._current_span()
        with self._lock:
            self.spans[sid] = {"span_id": sid, "parent_id": parent, "trace_id": self.run_id, "name": name,
                               "kind": kind, "framework": framework, "agent": agent, "start_ms":
                               round((time.time() - self.t0) * 1000, 2), "end_ms": None, "status": "open",
                               "attributes": dict(attrs or {})}
            if key is not None:
                self._open[key].append(sid)
        if push:
            if not hasattr(self._stack, "s"):
                self._stack.s = []
            self._stack.s.append(sid)
        return sid

    def open_span(self, key: tuple) -> str | None:
        with self._lock:
            lst = self._open.get(key)
            return lst[-1] if lst else None

    def end_span(self, sid: str | None = None, key: tuple | None = None, status: str = "ok",
                 attrs: dict | None = None) -> dict | None:
        with self._lock:
            if sid is None and key is not None and self._open.get(key):
                sid = self._open[key].pop()
            span = self.spans.get(sid or "")
            if not span:
                return None
            span["end_ms"] = round((time.time() - self.t0) * 1000, 2)
            span["duration_ms"] = round(span["end_ms"] - span["start_ms"], 2)
            span["status"] = status
            span["attributes"].update(attrs or {})
            if span["kind"] == "agent":
                self.agent_ms[f"{span['framework']}:{span['agent']}"] += span["duration_ms"]
        st = getattr(self._stack, "s", [])
        if sid in st:
            st.remove(sid)
        return span

    @contextmanager
    def span(self, name: str, kind: str = "step", framework: str = "system", agent: str = "readmind", **attrs):
        sid = self.start_span(name, kind, framework, agent, attrs)
        try:
            yield sid
            self.end_span(sid)
        except Exception as e:
            self.end_span(sid, status="error", attrs={"error": str(e)[:300]})
            raise

    # ------------------------------------------------------------------ #
    #  Provenance                                                          #
    # ------------------------------------------------------------------ #
    def record_tool_result(self, framework: str, agent: str, tool: str, args: dict, payload: dict,
                           span_id: str | None) -> list[str]:
        ids = []
        digest = _sha(payload)
        for h in (payload or {}).get("results", []) or []:
            hid = h.get("id")
            if not hid:
                continue
            ids.append(hid)
            with self._lock:
                self.retrieved.setdefault(hid, {
                    "evidence_id": hid, "type": h.get("type", ""), "title": h.get("title", "")[:160],
                    "url": h.get("url", ""), "date": h.get("date", ""), "framework": framework, "agent": agent,
                    "tool": tool, "server": payload.get("server", ""), "args": args, "result_sha256": digest,
                    "span_id": span_id, "t_ms": round((time.time() - self.t0) * 1000, 2), "cited": False})
        return ids

    def retrieved_ids(self) -> set[str]:
        with self._lock:
            return set(self.retrieved)

    def mark_cited(self, ids: list[str]) -> None:
        with self._lock:
            for i in ids:
                for rid, p in self.retrieved.items():
                    if rid == i or (i.startswith("commit:") and rid.startswith("commit:") and
                                    (rid[7:].startswith(i[7:]) or i[7:].startswith(rid[7:]))):
                        p["cited"] = True

    # ------------------------------------------------------------------ #
    def metrics(self) -> dict:
        with self._lock:
            prompt = sum(u["prompt"] for u in self.usage.values())
            comp = sum(u["completion"] for u in self.usage.values())
            from readmind.settings import get_settings
            s = get_settings()
            return {
                "latency_ms": round((time.time() - self.t0) * 1000, 2),
                "llm_calls": sum(u["calls"] for u in self.usage.values()),
                "llm_calls_by_framework": {k: v["calls"] for k, v in self.usage.items()},
                "prompt_tokens": prompt, "completion_tokens": comp, "total_tokens": prompt + comp,
                "tokens_estimated": any(u["estimated"] for u in self.usage.values()),
                "cost_usd": round(prompt / 1e6 * s.price_in_per_m + comp / 1e6 * s.price_out_per_m, 6),
                "tool_calls": self.counters["tool_end"] + self.counters["tool_error"],
                "tool_errors": self.counters["tool_error"],
                "messages": self.counters["message"],
                "message_bytes": self.counters["message_bytes"],
                "a2a_calls": self.counters["a2a_call"],
                "guardrail_blocks": self.counters["guardrail_block"],
                "retries": self.counters["retry"],
                "errors": len(self.errors),
                "per_agent_latency_ms": {k: round(v, 2) for k, v in self.agent_ms.items()},
            }

    def agents_invoked(self) -> list[str]:
        seen = []
        for e in self.events:
            if e["kind"] == "agent_start":
                tag = f"{e['framework']}:{e['agent']}"
                if tag not in seen:
                    seen.append(tag)
        return seen

    def to_dict(self) -> dict:
        with self._lock:
            return {"run_id": self.run_id, "meta": self.meta, "metrics": self.metrics(), "events": self.events,
                    "spans": list(self.spans.values()), "provenance": list(self.retrieved.values()),
                    "guardrails": self.guard_reports, "errors": self.errors, "otel_spans": self.otel_spans}

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        p = directory / f"{self.run_id}.json"
        p.write_text(json.dumps(self.to_dict(), indent=1, default=str), encoding="utf-8")
        return p

    # pretty timeline for notebooks
    def timeline(self, kinds: tuple = ("agent_start", "agent_end", "tool_end", "tool_error", "llm_response",
                                       "message", "a2a_call", "guardrail_input", "guardrail_output", "error",
                                       "task_output", "state_update")) -> list[str]:
        out = []
        for e in self.events:
            if e["kind"] in kinds:
                d = e.get("data") or {}
                brief = d.get("brief") if isinstance(d, dict) else None
                out.append(f"{e['t_ms']:>9.1f} ms  {e['framework']:<7} {e['agent']:<22} {e['kind']:<16} "
                           f"{brief or _preview(d, 110)}")
        return out
