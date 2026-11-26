"""Callback functions for task monitoring, logging and guardrails (requirements 5 and 8).

Google ADK — per-agent callbacks (``adk_callbacks(role)`` returns all seven):
    before_agent / after_agent      agent span, state snapshot (context sharing via session.state)
    before_model                    rate limit, input guardrail (refusal LlmResponse when blocked), LLM span
    after_model                     token usage, intermediate output (text / function calls)
    before_tool                     argument validation guardrail (returns error dict to skip the tool)
    after_tool                      tool-output type check + indirect-injection scrub, provenance ledger
    on_tool_error                   error capture; returns an error payload so the agent can recover

CrewAI — ``crew_step_callback`` / ``crew_task_callback`` (per agent/crew) and process-wide
hooks registered once by ``install_crewai_hooks()``:
    before_llm_call / after_llm_call     rate limit, guardrail, LLM span, response preview
    before_tool_call / after_tool_call   argument guardrail, output type check + scrub, provenance
    event listener                       exact token usage, MCP and A2A delegation events
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from readmind import guardrails as G
from readmind.observability.monitor import _preview, active_monitor

_ROLE_TAG = re.compile(r"\[ROLE:\s*([a-z_]+)\]")
_Q = re.compile(r"QUESTION:\s*(.+)")


# =========================================================================== #
#  Google ADK                                                                  #
# =========================================================================== #
def _cc_agent(ctx) -> str:
    return getattr(ctx, "agent_name", None) or "adk_agent"


def _cc_key(ctx) -> tuple:
    return ("adk", _cc_agent(ctx), getattr(ctx, "invocation_id", ""))


def _latest_user_text(ctx) -> str:
    uc = getattr(ctx, "user_content", None)
    if uc is None:
        return ""
    return " ".join(p.text for p in (uc.parts or []) if getattr(p, "text", None))


def adk_callbacks(role: str) -> dict[str, Any]:
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types
    from readmind.llm import limiter

    def before_agent(callback_context):
        mon = active_monitor()
        if mon:
            agent = _cc_agent(callback_context)
            mon.start_span(f"agent:{agent}", "agent", "adk", agent, {"role": role}, key=_cc_key(callback_context),
                           parent=mon.open_span(("system", "pattern", mon.run_id)), push=False)
            mon.event("adk", agent, "agent_start", {"role": role, "state_keys": sorted(callback_context.state.to_dict())
                                                    if hasattr(callback_context.state, "to_dict") else []})
        return None

    def after_agent(callback_context):
        mon = active_monitor()
        if mon:
            agent = _cc_agent(callback_context)
            mon.end_span(key=_cc_key(callback_context))
            st = callback_context.state.to_dict() if hasattr(callback_context.state, "to_dict") else {}
            mon.event("adk", agent, "agent_end", {"brief": f"state keys: {sorted(st)}"})
            mon.event("adk", agent, "state_update", {"keys": sorted(st), "brief": ", ".join(sorted(st))})
        return None

    def before_model(callback_context, llm_request):
        mon = active_monitor()
        limiter().acquire()
        text = _latest_user_text(callback_context)
        if text:
            qm = _Q.search(text)
            rep = G.check_input(qm.group(1) if qm else text)
            if not rep.allowed:
                if mon:
                    mon.guard(rep)
                    mon.event("adk", _cc_agent(callback_context), "guardrail_block", {"brief": "; ".join(rep.reasons)})
                return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=G.REFUSAL)]))
        if mon:
            agent = _cc_agent(callback_context)
            mon.start_span("llm_call", "llm", "adk", agent, {"model": getattr(llm_request, "model", "")},
                           key=("adk_llm",) + _cc_key(callback_context)[1:],
                           parent=mon.open_span(_cc_key(callback_context)), push=False)
            mon.event("adk", agent, "llm_request", {"n_contents": len(llm_request.contents or []),
                                                    "tools": sorted((llm_request.tools_dict or {}).keys())})
        return None

    def after_model(callback_context, llm_response):
        mon = active_monitor()
        if not mon:
            return None
        agent = _cc_agent(callback_context)
        um = getattr(llm_response, "usage_metadata", None)
        if um is not None:
            from readmind.settings import get_settings
            mon.add_usage("adk", um.prompt_token_count or 0, um.candidates_token_count or 0,
                          estimated=get_settings().is_mock)
        parts = (llm_response.content.parts if llm_response.content else None) or []
        calls = [p.function_call.name for p in parts if getattr(p, "function_call", None)]
        text = " ".join(p.text for p in parts if getattr(p, "text", None))
        mon.end_span(key=("adk_llm",) + _cc_key(callback_context)[1:],
                     attrs={"function_calls": calls, "tokens": getattr(um, "total_token_count", None)})
        mon.event("adk", agent, "llm_response", {"function_calls": calls, "text": _preview(text, 300),
                                                 "brief": f"calls {calls}" if calls else _preview(text, 100)})
        return None

    def before_tool(tool, args, tool_context):
        mon = active_monitor()
        agent = _cc_agent(tool_context)
        rep = G.check_tool_args(tool.name, args)
        if mon:
            mon.start_span(f"tool:{tool.name}", "tool", "adk", agent, {"args": args},
                           key=("adk_tool", agent, tool.name), parent=mon.open_span(_cc_key(tool_context)), push=False)
            mon.event("adk", agent, "tool_start", {"tool": tool.name, "args": args})
        if not rep.allowed:
            if mon:
                mon.guard(rep)
                mon.end_span(key=("adk_tool", agent, tool.name), status="blocked")
            return {"ok": False, "tool": tool.name, "error": "guardrail: " + "; ".join(rep.reasons)}
        tool_context.state["temp:last_tool"] = tool.name
        return None

    def after_tool(tool, args, tool_context, tool_response):
        mon = active_monitor()
        agent = _cc_agent(tool_context)
        clean, rep = G.check_tool_output(tool.name, tool_response)
        if mon:
            sid = mon.open_span(("adk_tool", agent, tool.name))
            if clean is None:
                mon.guard(rep)
                mon.end_span(key=("adk_tool", agent, tool.name), status="type_error")
                mon.event("adk", agent, "tool_error", {"tool": tool.name, "error": rep.reasons})
            else:
                if rep.categories:
                    mon.guard(rep)
                ids = mon.record_tool_result("adk", agent, tool.name, args, clean, sid)
                mon.end_span(key=("adk_tool", agent, tool.name), attrs={"evidence_ids": ids[:20]})
                mon.event("adk", agent, "tool_end", {"tool": tool.name, "ok": clean.get("ok"), "ids": ids[:10],
                                                     "brief": f"{tool.name} -> {len(ids)} hits {ids[:4]}"})
        if clean is None:
            return {"ok": False, "tool": tool.name, "error": "tool returned an invalid type; ignore this result"}
        return clean

    def on_tool_error(tool, args, tool_context, error):
        mon = active_monitor()
        agent = _cc_agent(tool_context)
        if mon:
            mon.end_span(key=("adk_tool", agent, tool.name), status="error", attrs={"error": str(error)[:300]})
            mon.event("adk", agent, "tool_error", {"tool": tool.name, "error": str(error)[:300]})
            mon.error("adk", agent, f"{tool.name}: {error}")
        return {"ok": False, "tool": tool.name, "error": f"tool failed: {type(error).__name__}"}

    return {"before_agent_callback": before_agent, "after_agent_callback": after_agent,
            "before_model_callback": before_model, "after_model_callback": after_model,
            "before_tool_callback": before_tool, "after_tool_callback": after_tool,
            "on_tool_error_callback": on_tool_error}


def adk_remote_callbacks(name: str) -> dict[str, Any]:
    """Callbacks for ADK ``RemoteA2aAgent`` (A2A hop to a CrewAI crew)."""
    def before_agent(callback_context):
        mon = active_monitor()
        if mon:
            mon.start_span(f"a2a:{name}", "a2a", "adk", name, {"protocol": "A2A"}, key=_cc_key(callback_context),
                           parent=mon.open_span(("system", "pattern", mon.run_id)), push=False)
            mon.event("adk", name, "a2a_call", {"brief": f"ADK -> A2A -> CrewAI ({name})"})
            mon.event("adk", name, "agent_start", {"remote": True})
        return None

    def after_agent(callback_context):
        mon = active_monitor()
        if mon:
            mon.end_span(key=_cc_key(callback_context))
            mon.event("adk", name, "agent_end", {"remote": True})
        return None
    return {"before_agent_callback": before_agent, "after_agent_callback": after_agent}


# =========================================================================== #
#  CrewAI                                                                      #
# =========================================================================== #
def _crew_role(agent) -> str:
    if agent is None:
        return "crew"
    m = _ROLE_TAG.search(getattr(agent, "backstory", "") or "")
    return m.group(1) if m else (getattr(agent, "role", "crew") or "crew").lower().replace(" ", "_")[:30]


def crew_step_callback(step) -> None:
    mon = active_monitor()
    if not mon:
        return
    kind = type(step).__name__
    d: dict[str, Any] = {"step": kind}
    for attr in ("tool", "tool_input", "thought", "result", "output", "text"):
        v = getattr(step, attr, None)
        if v:
            d[attr] = _preview(v, 300)
    d["brief"] = f"{kind}: {d.get('tool') or _preview(d.get('output') or d.get('thought') or '', 90)}"
    mon.event("crewai", "step", "agent_step", d)


def crew_task_callback(output) -> None:
    mon = active_monitor()
    if not mon:
        return
    agent = getattr(output, "agent", "") or ""
    mon.event("crewai", str(agent)[:40], "task_output", {
        "pydantic": type(getattr(output, "pydantic", None)).__name__ if getattr(output, "pydantic", None) else None,
        "raw": _preview(getattr(output, "raw", ""), 300),
        "brief": f"{type(getattr(output, 'pydantic', None)).__name__} from {str(agent)[:30]}"})


_HOOKS_INSTALLED = False


def install_crewai_hooks() -> None:
    """Register process-wide CrewAI hooks + event listener (idempotent)."""
    global _HOOKS_INSTALLED
    if _HOOKS_INSTALLED:
        return
    from crewai.hooks import (register_after_llm_call_hook, register_after_tool_call_hook,
                              register_before_llm_call_hook, register_before_tool_call_hook)
    from readmind.llm import limiter

    def before_llm(ctx):
        mon = active_monitor()
        limiter().acquire()
        role = _crew_role(getattr(ctx, "agent", None))
        msgs = getattr(ctx, "messages", []) or []
        first_user = next((str(m.get("content", "")) for m in msgs if m.get("role") == "user"), "")
        qm = _Q.search(first_user)
        if qm:
            rep = G.check_input(qm.group(1))
            if not rep.allowed:
                if mon:
                    mon.guard(rep)
                return False
        if mon:
            mon.start_span("llm_call", "llm", "crewai", role, {"iteration": getattr(ctx, "iterations", 0)},
                           key=("crew_llm", role), parent=mon.open_span(("crew_agent", role)) or
                           mon.open_span(("system", "pattern", mon.run_id)), push=False)
            mon.event("crewai", role, "llm_request", {"n_messages": len(msgs), "iteration": getattr(ctx, "iterations", 0)})
        return None

    def after_llm(ctx):
        mon = active_monitor()
        if mon:
            role = _crew_role(getattr(ctx, "agent", None))
            resp = getattr(ctx, "response", "") or ""
            mon.end_span(key=("crew_llm", role))
            act = re.search(r"Action: (\S+)", str(resp))
            mon.event("crewai", role, "llm_response", {"text": _preview(resp, 300),
                                                       "brief": f"action {act.group(1)}" if act else _preview(resp, 100)})
        return None

    def before_tool(ctx):
        mon = active_monitor()
        role = _crew_role(getattr(ctx, "agent", None))
        name = getattr(ctx, "tool_name", "tool")
        args = dict(getattr(ctx, "tool_input", {}) or {})
        rep = G.check_tool_args(name, args)
        if mon:
            mon.start_span(f"tool:{name}", "tool", "crewai", role, {"args": args}, key=("crew_tool", role, name),
                           parent=mon.open_span(("crew_agent", role)) or mon.open_span(("system", "pattern", mon.run_id)),
                           push=False)
            mon.event("crewai", role, "tool_start", {"tool": name, "args": args})
        if not rep.allowed:
            if mon:
                mon.guard(rep)
                mon.end_span(key=("crew_tool", role, name), status="blocked")
            return False
        return None

    def after_tool(ctx):
        mon = active_monitor()
        role = _crew_role(getattr(ctx, "agent", None))
        name = getattr(ctx, "tool_name", "tool")
        if name.lower().startswith(("delegate", "ask question")):
            if mon:
                mon.end_span(key=("crew_tool", role, name))
                nbytes = len(str(getattr(ctx, "tool_result", ""))) + len(json.dumps(getattr(ctx, "tool_input", {}) or {}))
                mon.event("crewai", role, "message", {"brief": f"manager <-> coworker via {name}", "bytes": nbytes})
                mon.counters["message_bytes"] += nbytes
            return None
        raw = getattr(ctx, "raw_tool_result", None) or getattr(ctx, "tool_result", None)
        clean, rep = G.check_tool_output(name.split("_")[-1] if "_" in name else name, raw)
        if mon:
            sid = mon.open_span(("crew_tool", role, name))
            if clean is None:
                mon.guard(rep)
                mon.end_span(key=("crew_tool", role, name), status="type_error")
                mon.event("crewai", role, "tool_error", {"tool": name, "error": rep.reasons})
            else:
                if rep.categories:
                    mon.guard(rep)
                args = dict(getattr(ctx, "tool_input", {}) or {})
                ids = mon.record_tool_result("crewai", role, name, args, clean, sid)
                mon.end_span(key=("crew_tool", role, name), attrs={"evidence_ids": ids[:20]})
                mon.event("crewai", role, "tool_end", {"tool": name, "ids": ids[:10],
                                                       "brief": f"{name.split('_mcp_')[-1]} -> {len(ids)} hits {ids[:4]}"})
        if clean is None:
            return json.dumps({"ok": False, "error": "tool returned an invalid type; ignore this result"})
        return json.dumps(clean)

    register_before_llm_call_hook(before_llm)
    register_after_llm_call_hook(after_llm)
    register_before_tool_call_hook(before_tool)
    register_after_tool_call_hook(after_tool)
    _install_listener()
    _HOOKS_INSTALLED = True


def _install_listener() -> None:
    from crewai.events import BaseEventListener

    class ReadMindListener(BaseEventListener):
        def setup_listeners(self, bus):
            from crewai.events.types.llm_events import LLMCallCompletedEvent, LLMCallFailedEvent
            from crewai.events.types.agent_events import AgentExecutionCompletedEvent, AgentExecutionStartedEvent

            @bus.on(LLMCallCompletedEvent)
            def _llm_done(source, event):
                mon = active_monitor()
                model = str(getattr(event, "model", "") or "")
                if not mon or model.startswith("mock"):
                    return
                u = getattr(event, "usage", None) or {}
                p = u.get("prompt_tokens") or u.get("prompt_token_count") or u.get("input_tokens") or 0
                c = u.get("completion_tokens") or u.get("candidates_token_count") or u.get("output_tokens") or 0
                mon.add_usage("crewai", p, c, estimated=not bool(u))

            @bus.on(LLMCallFailedEvent)
            def _llm_fail(source, event):
                mon = active_monitor()
                if mon:
                    err = str(getattr(event, "error", ""))
                    if "429" in err or "RESOURCE_EXHAUSTED" in err:
                        mon.counters["retry"] += 1
                    mon.error("crewai", str(getattr(event, "agent_role", "") or "llm"), err[:300])

            @bus.on(AgentExecutionStartedEvent)
            def _agent_start(source, event):
                mon = active_monitor()
                if mon:
                    role = _crew_role(getattr(event, "agent", None))
                    mon.start_span(f"agent:{role}", "agent", "crewai", role, {}, key=("crew_agent", role),
                                   parent=mon.open_span(("system", "pattern", mon.run_id)), push=False)
                    mon.event("crewai", role, "agent_start", {"role": getattr(getattr(event, "agent", None), "role", "")})

            @bus.on(AgentExecutionCompletedEvent)
            def _agent_end(source, event):
                mon = active_monitor()
                if mon:
                    role = _crew_role(getattr(event, "agent", None))
                    mon.end_span(key=("crew_agent", role))
                    mon.event("crewai", role, "agent_end", {})

            try:
                from crewai.events.types import a2a_events as A

                start_cls = getattr(A, "A2ADelegationStartedEvent", None) or getattr(A, "A2AConversationStartedEvent")

                @bus.on(start_cls)
                def _a2a_start(source, event):
                    mon = active_monitor()
                    if mon:
                        mon.event("crewai", "liaison", "a2a_call", {"brief": "CrewAI -> A2A -> ADK history agent",
                                                                    "endpoint": str(getattr(event, "endpoint", ""))[:120]})
            except Exception:
                pass

    ReadMindListener()


def record_message(framework: str, sender: str, receivers: list[str], payload: str, channel: str) -> None:
    mon = active_monitor()
    if mon:
        mon.event(framework, sender, "message", {"channel": channel, "to": receivers, "bytes": len(payload),
                                                 "brief": f"[{channel}] {sender} -> {', '.join(receivers) or '-'} "
                                                          f"({len(payload)} B)"})
        mon.counters["message_bytes"] += len(payload) * max(1, len(receivers))


def timed(fn):
    """Decorator: time a function into the active monitor (used for non-framework steps)."""
    def wrapper(*a, **kw):
        t0 = time.perf_counter()
        try:
            return fn(*a, **kw)
        finally:
            mon = active_monitor()
            if mon:
                mon.event("system", fn.__name__, "step", None, duration_ms=(time.perf_counter() - t0) * 1000)
    return wrapper
