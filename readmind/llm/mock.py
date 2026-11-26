"""Deterministic offline LLMs for ADK and CrewAI (``READMIND_LLM=mock``).

They exist so the *whole* system — MCP tool calls, A2A delegation, callbacks,
guardrails, tracing, structured output and the experiment runner — can be executed
and tested without an API key. They are NOT a model of answer quality: the
"answer" is an extractive summary of the evidence the real tools returned, so
retrieval metrics are real but judge scores from mock runs must not be reported.

Both adapters share one policy (:func:`next_tool` / :func:`final_output`) keyed on
the ``[ROLE: <name>]`` tag every instruction in :mod:`readmind.prompts` carries.
"""
from __future__ import annotations

import json
import re
import typing
from typing import Any, AsyncGenerator

from readmind.schemas import (EvidenceItem, FinalAnswer, Plan, SpecialistReport, SubTask,
                              VerificationVerdict, extract_json)

ROLE_TAG = re.compile(r"\[ROLE:\s*([a-z_]+)\]")
QUESTION = re.compile(r"QUESTION:\s*(.+)")
ID_RE = re.compile(r"\b((?:commit|pr|issue|review|code):[A-Za-z0-9_\-./]+[A-Za-z0-9_])")

# tool plan per role: list of (tool_suffix, arg_builder)
_PLANS: dict[str, list[str]] = {
    "history": ["search_commits", "get_commit"],
    "refiner": ["search_commits"],
    "code": ["search_code"],
    "discussion": ["search_discussions"],
    "single": ["search_commits", "search_code", "search_discussions", "get_commit"],
}


def _keywords(q: str) -> str:
    return re.sub(r"\s+", " ", q).strip()[:240]


def _hits(observations: list[dict]) -> list[dict]:
    out, seen = [], set()
    for o in observations:
        for h in (o.get("results") or []):
            if isinstance(h, dict) and h.get("id") and h["id"] not in seen:
                seen.add(h["id"])
                out.append({**h, "_source": o.get("server") or o.get("tool", "")})
    return out


def next_tool(role: str, done: list[str], question: str, observations: list[dict],
              available: list[str]) -> tuple[str, dict] | None:
    plan = _PLANS.get(role, [])
    for step in plan:
        if step in done:
            continue
        name = next((a for a in available if a == step or a.endswith("_" + step)), None)
        if not name:
            continue
        if step == "get_commit":
            commits = [h["id"] for h in _hits(observations) if h["id"].startswith("commit:")]
            if not commits:
                continue
            return name, {"commit_id": commits[0]}
        return name, {"query": _keywords(question)}
    return None


def _evidence(hits: list[dict], limit: int = 6) -> list[EvidenceItem]:
    ev = []
    for h in hits[:limit]:
        if h["id"].startswith("code:absent:"):
            continue
        ev.append(EvidenceItem(id=h["id"], type=h.get("type") or h["id"].split(":", 1)[0], title=h.get("title", "")[:160],
                               snippet=(h.get("snippet") or "")[:300], source=h.get("_source", ""),
                               url=h.get("url", ""), date=h.get("date", "")))
    return ev


def _evidence_from_context(text: str) -> list[EvidenceItem]:
    """Collect evidence items embedded (as JSON) in a context / blackboard dump."""
    items: dict[str, EvidenceItem] = {}
    for m in re.finditer(r'\{[^{}]*"id"\s*:\s*"((?:commit|pr|issue|review|code):[^"]+)"[^{}]*\}', text):
        try:
            d = json.loads(m.group(0))
            e = EvidenceItem.model_validate({k: v for k, v in d.items() if k in EvidenceItem.model_fields})
            items.setdefault(e.id, e)
        except Exception:
            items.setdefault(m.group(1), EvidenceItem(id=m.group(1), type=m.group(1).split(":", 1)[0]))
    for m in re.finditer(r"- \[((?:commit|pr|issue|review|code):[^\]]+)\]\s*\(([^)]*)\)\s*([^\n]*)", text):
        items.setdefault(m.group(1), EvidenceItem(id=m.group(1), type=m.group(2), title=m.group(3)[:160]))
    return [e for e in items.values() if not e.id.startswith("code:absent:")]


def _intent(q: str) -> str:
    ql = q.lower()
    if re.search(r"\bwho\b|\bauthor", ql):
        return "authorship"
    if re.search(r"\bwhen\b|\bdate\b|\btimeline\b", ql):
        return "timeline"
    if re.search(r"replac|supersed|remov|deprecat|instead", ql):
        return "superseded_implementation"
    return "rationale"


def final_output(role: str, schema: str | None, question: str, observations: list[dict], context: str) -> str:
    hits = _hits(observations)
    if role == "planner" or schema == "Plan":
        return Plan(intent=_intent(question), entities=sorted(set(re.findall(r"[\w./-]+\.[ch]\b", question))),
                    subtasks=[SubTask(agent=a, query=question) for a in ("history", "code", "discussion")],
                    reasoning="mock plan: all three specialists").model_dump_json()
    if role == "verifier" or schema == "VerificationVerdict":
        ids = set(ID_RE.findall(context)) | {h["id"] for h in hits}
        ids = {i for i in ids if not i.startswith("code:absent")}
        hist = [i for i in ids if i.split(":")[0] in ("commit", "pr", "issue")]
        ok = len(hist) >= 1 and len(ids) >= 2
        return VerificationVerdict(sufficient=ok, confidence=round(min(0.9, 0.2 + 0.1 * len(ids)), 2),
                                   gaps=[] if ok else ["no historical evidence (commit/PR/issue) for the question"],
                                   follow_up_queries=[] if ok else [question],
                                   rationale=f"mock: {len(ids)} evidence ids, {len(hist)} historical").model_dump_json()
    if role in ("history", "code", "discussion", "refiner") or schema == "SpecialistReport":
        ev = _evidence(hits)
        findings = [{"text": e.title or e.id, "kind": "fact", "evidence_ids": [e.id]} for e in ev[:4]]
        return SpecialistReport(agent=role, summary=("; ".join(f"{e.title} [{e.id}]" for e in ev[:3]) or
                                                     "No matching evidence found."),
                                findings=findings, evidence=ev,
                                gaps=[] if ev else [f"{role}: no evidence"]).model_dump_json()
    # reviewer / single / manager -> FinalAnswer
    ev = _evidence(hits) if hits else []
    if not ev:
        ev = _evidence_from_context(context)
    ev = ev[:6]
    if not ev:
        return FinalAnswer(question=question, answer="The available evidence does not explain this; no matching "
                           "commit, pull request, issue or code was found.", confidence="low",
                           gaps=["no evidence retrieved"]).model_dump_json()
    hist = [e for e in ev if e.type in ("commit", "pr", "issue", "review")]
    lead = hist[0] if hist else ev[0]
    sentences = [f"{lead.title or 'The change'} [{lead.id}]."]
    for e in (hist[1:3] if hist else ev[1:3]):
        sentences.append(f"Related: {e.title or e.id} [{e.id}].")
    claims = [{"text": e.title or e.id, "kind": "fact", "evidence_ids": [e.id]} for e in ev[:4]]
    conf = "high" if len(hist) >= 2 else "medium" if hist else "low"
    return FinalAnswer(question=question, answer=" ".join(sentences), claims=claims, evidence=ev,
                       confidence=conf, gaps=[] if hist else ["no historical evidence; answer reflects current code only"]
                       ).model_dump_json()


def _json_objects(text: str) -> list[dict]:
    """Every parseable top-level JSON object in *text* (tool observations sit after Action Input JSON)."""
    out, dec, i = [], json.JSONDecoder(), 0
    while True:
        i = text.find("{", i)
        if i < 0:
            return out
        try:
            obj, end = dec.raw_decode(text, i)
            if isinstance(obj, dict):
                out.append(obj)
            i = end
        except ValueError:
            i += 1


def estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // 4)


def _record_usage(framework: str, prompt: str, completion: str) -> None:
    try:
        from readmind.observability.monitor import active_monitor
        mon = active_monitor()
        if mon:
            mon.add_usage(framework, estimate_tokens(prompt), estimate_tokens(completion), estimated=True)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
#  Google ADK adapter                                                          #
# --------------------------------------------------------------------------- #
from google.adk.models.base_llm import BaseLlm  # noqa: E402
from google.adk.models.llm_response import LlmResponse  # noqa: E402
from google.genai import types  # noqa: E402


def _content_text(c) -> str:
    return " ".join(p.text for p in (c.parts or []) if getattr(p, "text", None))


class MockAdkLlm(BaseLlm):
    model: str = "mock-adk"

    @classmethod
    def supported_models(cls) -> list[str]:
        return [r"mock-.*"]

    async def generate_content_async(self, llm_request, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        cfg = llm_request.config
        sys_text = cfg.system_instruction if isinstance(getattr(cfg, "system_instruction", None), str) else (
            _content_text(cfg.system_instruction) if getattr(cfg, "system_instruction", None) else "")
        role_m = ROLE_TAG.search(sys_text or "")
        role = role_m.group(1) if role_m else "single"
        contents = list(llm_request.contents or [])
        all_text = sys_text + "\n" + "\n".join(_content_text(c) for c in contents)
        qm = QUESTION.search(all_text)
        question = qm.group(1).strip() if qm else (_content_text(contents[-1]) if contents else "")
        observations, done = [], []
        for c in contents:
            for p in c.parts or []:
                if p.function_call:
                    done.append(p.function_call.name)
                if p.function_response:
                    r = p.function_response.response or {}
                    data = r.get("structuredContent") or r.get("result") or r
                    if isinstance(data, dict) and "content" in data and not data.get("results"):
                        txt = " ".join(x.get("text", "") for x in data.get("content", []) if isinstance(x, dict))
                        data = extract_json(txt) or {}
                    if isinstance(data, str):
                        data = extract_json(data) or {}
                    if isinstance(data, dict):
                        observations.append(data)
        available = list((llm_request.tools_dict or {}).keys())
        step = next_tool(role, done, question, observations, available)
        prompt_text = all_text
        if step and role != "verifier":
            name, args = step
            part = types.Part(function_call=types.FunctionCall(name=name, args=args))
            out_text = json.dumps(args)
            resp = LlmResponse(content=types.Content(role="model", parts=[part]))
        else:
            schema = getattr(cfg, "response_schema", None)
            schema_name = getattr(schema, "__name__", None) if schema is not None else None
            out_text = final_output(role, schema_name, question, observations, all_text)
            resp = LlmResponse(content=types.Content(role="model", parts=[types.Part(text=out_text)]))
        p_tok, c_tok = estimate_tokens(prompt_text), estimate_tokens(out_text)
        resp.usage_metadata = types.GenerateContentResponseUsageMetadata(
            prompt_token_count=p_tok, candidates_token_count=c_tok, total_token_count=p_tok + c_tok)
        yield resp


# --------------------------------------------------------------------------- #
#  CrewAI adapter                                                              #
# --------------------------------------------------------------------------- #
from crewai.llms.base_llm import BaseLLM  # noqa: E402

_TOOL_NAME = re.compile(r"Tool Name: (\S+)")
_COWORKERS = re.compile(r"one of the following coworkers:\s*([^\n]+)", re.IGNORECASE)


class MockCrewLlm(BaseLLM):
    def __init__(self, **kw):
        super().__init__(model="mock-crew", **kw)

    def supports_function_calling(self) -> bool:
        return False

    def get_input_tokens_count(self, messages, *args, **kwargs) -> int:   # silence CrewAI's approximation warning
        if isinstance(messages, list):
            return sum(estimate_tokens(str(m.get("content", ""))) for m in messages)
        return estimate_tokens(str(messages))

    def call(self, messages, tools=None, callbacks=None, available_functions=None, from_task=None,
             from_agent=None, response_model=None):
        msgs = messages if isinstance(messages, list) else [{"role": "user", "content": str(messages)}]
        texts = [str(m.get("content", "")) for m in msgs]
        full = "\n".join(texts)
        out = self._respond(msgs, texts, full, response_model)
        _record_usage("crewai", full, out)
        return out

    # -- policy ---------------------------------------------------------------
    def _respond(self, msgs, texts, full, response_model) -> str:
        qm = QUESTION.search(full)
        question = qm.group(1).strip() if qm else texts[-1][:200]
        role_m = ROLE_TAG.search(full)
        role = role_m.group(1) if role_m else "manager"

        # A2A delegation (CrewAI AgentResponse protocol)
        if response_model is not None and "a2a_ids" in getattr(response_model, "model_fields", {}):
            ann = response_model.model_fields["a2a_ids"].annotation
            args = typing.get_args(ann)
            lits = typing.get_args(args[0]) if args else ()
            remote = [m.group(0) for m in re.finditer(r'\{"agent"\s*:\s*"history".*?\}(?=\s*$|\s*\n|\s*")', full, re.S)]
            unesc = full.replace('\\"', '"')
            if re.search(r'"agent"\s*:\s*"history"', unesc):
                blob = next((o for o in _json_objects(unesc) if o.get("agent") == "history"), None)
                answer = json.dumps(blob) if blob else (remote[-1] if remote else texts[-1])
                return json.dumps({"a2a_ids": list(lits[:1]), "message": answer[-6000:], "is_a2a": False})
            return json.dumps({"a2a_ids": list(lits[:1]), "message": f"QUESTION: {question}", "is_a2a": True})

        tool_names = _TOOL_NAME.findall(texts[0] if texts else full) or _TOOL_NAME.findall(full)
        observations = [o for t in texts[2:] for o in _json_objects(t) if "results" in o and "tool" in o]
        done_full = re.findall(r"Action: (\S+)", "\n".join(texts[2:]))

        # hierarchical manager: delegate once to each coworker, then synthesise
        delegate = next((t for t in tool_names if t.lower().startswith("delegate")), None)
        if delegate and role == "manager":
            m = _COWORKERS.search(full)
            coworkers = [c.strip(" .-\t") for c in (m.group(1).split(",") if m else []) if c.strip(" .-\t")]
            n_done = sum(1 for a in done_full if a.lower().startswith("delegate"))
            if n_done < len(coworkers):
                return ("Thought: delegate to the next specialist\nAction: " + delegate + "\nAction Input: " +
                        json.dumps({"task": f"QUESTION: {question}", "context": f"QUESTION: {question}",
                                    "coworker": coworkers[n_done]}))
            body = final_output("reviewer", "FinalAnswer", question, observations, full)
            return "Thought: I now know the final answer\nFinal Answer: " + body

        if tool_names:
            # CrewAI prefixes MCP tool names with the server id; plan steps match by suffix
            plan_done = [st for st in _PLANS.get(role, []) if any(a == st or a.endswith("_" + st) for a in done_full)]
            step = next_tool(role, plan_done, question, observations, tool_names)
            if step:
                name, args = step
                return f"Thought: gather evidence\nAction: {name}\nAction Input: {json.dumps(args)}"
        schema = None
        for s in ("FinalAnswer", "SpecialistReport", "VerificationVerdict", "Plan"):
            if f'"title": "{s}"' in full:
                schema = s
                break
        body = final_output(role, schema, question, observations, full)
        return "Thought: I now know the final answer\nFinal Answer: " + body
