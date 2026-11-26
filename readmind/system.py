"""ReadMind facade: start services, answer a question with a chosen pattern / messaging /
prompt variant, and return a fully-instrumented structured ``RunResult``."""
from __future__ import annotations

import time
import traceback
import uuid
from pathlib import Path

from readmind import guardrails as G
from readmind import mcp_servers
from readmind.llm import model_label
from readmind.messaging import make_channel
from readmind.observability.callbacks import install_crewai_hooks
from readmind.observability.monitor import RunMonitor, setup_otel
from readmind.patterns import PARTICIPANTS, PATTERN_INFO, PATTERNS, RunCtx
from readmind.prompts import VARIANTS
from readmind.schemas import FinalAnswer, RunMetrics, RunResult
from readmind.settings import configure, get_settings

QUOTA_MARKERS = ("429", "RESOURCE_EXHAUSTED", "quota", "rate limit")


class ReadMind:
    def __init__(self):
        self.started = False
        self.trace_dir = get_settings().work_dir / "traces"

    @classmethod
    def start(cls, **overrides) -> "ReadMind":
        if overrides:
            configure(**overrides)
        rm = cls()
        s = get_settings()
        s.work_dir.mkdir(parents=True, exist_ok=True)
        setup_otel()
        install_crewai_hooks()
        mcp_servers.start_servers()
        from readmind.tools.repo_tools import _store
        store = _store()          # warm the in-process index too (A2A servers + tools share it)
        rm.store_stats = dict(store.stats)
        rm.started = True
        return rm

    # ------------------------------------------------------------------ #
    def ask(self, question: str, pattern: str = "sequential", messaging: str = "blackboard",
            variant: str = "default", run_id: str | None = None, meta: dict | None = None) -> RunResult:
        """Answer one question. Safe to call from Jupyter/Kaggle: when an asyncio loop is already
        running in this thread (notebook kernels), the run executes in a worker thread."""
        import asyncio
        import concurrent.futures
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return self._ask(question, pattern, messaging, variant, run_id, meta)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(self._ask, question, pattern, messaging, variant, run_id, meta).result()

    def _ask(self, question: str, pattern: str, messaging: str, variant: str, run_id: str | None,
             meta: dict | None) -> RunResult:
        if pattern not in PATTERNS:
            raise KeyError(f"unknown pattern {pattern}; choose from {sorted(PATTERNS)}")
        if variant not in VARIANTS:
            raise KeyError(f"unknown prompt variant {variant}")
        msg_mode = PATTERN_INFO[pattern]["messaging"]
        messaging_eff = messaging if msg_mode == "channel" else msg_mode
        run_id = run_id or uuid.uuid4().hex[:12]
        mon = RunMonitor(run_id, {"question": question, "pattern": pattern, "messaging": messaging_eff,
                                  "variant": variant, "model": model_label(), **(meta or {})}).activate()
        s = get_settings()
        status, errors = "ok", []
        in_rep = G.check_input(question)
        mon.guard(in_rep)
        q = in_rep.sanitized or question
        out_rep = None
        try:
            if not in_rep.allowed:
                status = "blocked"
                final = FinalAnswer(question=question, answer=G.REFUSAL, confidence="low",
                                    gaps=["request refused by input guardrail: " + "; ".join(in_rep.reasons)])
            else:
                channel = make_channel(messaging_eff, q, s.repo_slug, PARTICIPANTS) if msg_mode == "channel" else None
                ctx = RunCtx(question=q, variant=variant, messaging=messaging_eff, channel=channel, mon=mon)
                final = self._run_with_retry(pattern, ctx)
                if ctx.notes:
                    status = "degraded"
                    errors += ctx.notes
                if channel is not None:
                    mon.event("system", "channel", "channel_stats", channel.stats())
                final, out_rep = G.check_final(final, mon.retrieved_ids())
                mon.guard(out_rep)
                mon.mark_cited(final.cited_ids())
        except Exception as e:
            status = "failed"
            errors.append(f"{type(e).__name__}: {e}")
            mon.error("system", "readmind", traceback.format_exc()[-1500:])
            final = FinalAnswer(question=question, answer="The run failed before an answer was produced.",
                                confidence="low", gaps=[str(e)[:200]])
        finally:
            mon.deactivate()
        m = mon.metrics()
        if mon.errors and status == "ok":
            status = "degraded"
        trace = mon.save(self.trace_dir)
        return RunResult(run_id=run_id, question=question, pattern=pattern, messaging=messaging_eff, variant=variant,
                         model=model_label(), status=status, final=final, input_guard=in_rep, output_guard=out_rep,
                         metrics=RunMetrics.model_validate({k: v for k, v in m.items() if k in RunMetrics.model_fields}),
                         agents_invoked=mon.agents_invoked(), errors=errors + mon.errors[:10], trace_file=str(trace))

    def _run_with_retry(self, pattern: str, ctx: RunCtx) -> FinalAnswer:
        s = get_settings()
        delay = 20.0
        for attempt in range(s.max_retries_on_quota + 1):
            sid = ctx.mon.start_span(f"pattern:{pattern}", "pattern", "system", pattern,
                                     {"messaging": ctx.messaging, "variant": ctx.variant, "attempt": attempt},
                                     key=("system", "pattern", ctx.mon.run_id))
            try:
                fa = PATTERNS[pattern](ctx)
                ctx.mon.end_span(sid)
                return fa
            except Exception as e:
                ctx.mon.end_span(sid, status="error", attrs={"error": str(e)[:300]})
                if any(m.lower() in str(e).lower() for m in QUOTA_MARKERS) and attempt < s.max_retries_on_quota:
                    ctx.mon.counters["retry"] += 1
                    ctx.mon.event("system", pattern, "retry", {"brief": f"quota hit, sleeping {delay:.0f}s"})
                    time.sleep(delay)
                    delay = min(delay * 2, 120)
                    if ctx.channel is not None:   # fresh channel for the retry
                        ctx.channel = make_channel(ctx.messaging, ctx.question, s.repo_slug, PARTICIPANTS)
                    continue
                raise
        raise RuntimeError("unreachable")

    # ------------------------------------------------------------------ #
    def shutdown(self) -> None:
        from readmind import a2a_bridge
        a2a_bridge.shutdown()
        mcp_servers.stop_servers()
        self.started = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.shutdown()


def load_trace(path: str | Path) -> dict:
    import json
    return json.loads(Path(path).read_text(encoding="utf-8"))
