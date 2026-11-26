"""Pydantic models for every structured hand-off in the system.

The models used as LLM output schemas (Plan, SpecialistReport, VerificationVerdict,
FinalAnswer) contain only primitives, lists and nested models — no free-form dicts —
so they are accepted by Gemini structured output (ADK ``output_schema``) and by
CrewAI ``output_pydantic``.
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

EVIDENCE_ID = re.compile(r"^(commit|pr|issue|review|code|doc|web|calc)(:[^\s\[\],]+)$")
INTENTS = ("rationale", "timeline", "authorship", "superseded_implementation", "other")
SPECIALISTS = ("history", "code", "discussion")


# --------------------------------------------------------------------------- #
#  Evidence + claims                                                           #
# --------------------------------------------------------------------------- #
class EvidenceItem(BaseModel):
    id: str = Field(description="Evidence identifier, e.g. commit:3e57e647e0, pr:420, issue:88, code:src/smp/lock.c")
    type: str = Field(default="", description="commit | pr | issue | review | code | web")
    title: str = ""
    snippet: str = Field(default="", description="Short excerpt that supports the claim")
    source: str = Field(default="", description="Tool / MCP server that produced it (provenance)")
    url: str = ""
    date: str = ""

    @field_validator("id")
    @classmethod
    def _norm(cls, v: str) -> str:
        v = (v or "").strip().strip("[]()., ")
        if ":" in v:
            k, rest = v.split(":", 1)
            k = k.lower().strip()
            k = {"pull": "pr"}.get(k, k)
            v = f"{k}:{rest.strip()}"
        return v


class Claim(BaseModel):
    text: str
    kind: Literal["fact", "inference"] = "fact"
    evidence_ids: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
#  Agent hand-offs                                                             #
# --------------------------------------------------------------------------- #
class SubTask(BaseModel):
    agent: Literal["history", "code", "discussion"]
    query: str


class Plan(BaseModel):
    intent: Literal["rationale", "timeline", "authorship", "superseded_implementation", "other"] = "rationale"
    entities: list[str] = Field(default_factory=list, description="Files, symbols, features, issue keys")
    subtasks: list[SubTask] = Field(default_factory=list)
    reasoning: str = ""


class SpecialistReport(BaseModel):
    agent: str
    summary: str = ""
    findings: list[Claim] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


class VerificationVerdict(BaseModel):
    sufficient: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    gaps: list[str] = Field(default_factory=list)
    follow_up_queries: list[str] = Field(default_factory=list)
    rationale: str = ""


class FinalAnswer(BaseModel):
    """The system's final structured output."""
    question: str = ""
    answer: str = Field(description="Direct answer, 2-6 sentences, with [id] citations")
    claims: list[Claim] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "low"
    gaps: list[str] = Field(default_factory=list)

    def evidence_ids(self) -> list[str]:
        return list(dict.fromkeys(e.id for e in self.evidence))

    def cited_ids(self) -> list[str]:
        ids = re.findall(r"\[([a-z]+:[^\]\s,]+)", self.answer)
        for c in self.claims:
            ids += c.evidence_ids
        return list(dict.fromkeys(ids))

    def to_markdown(self) -> str:
        out = [f"### Answer\n\n{self.answer}\n", f"**Confidence:** {self.confidence}\n"]
        facts = [c for c in self.claims if c.kind == "fact"]
        infs = [c for c in self.claims if c.kind == "inference"]
        if facts:
            out.append("**Established by evidence**\n")
            out += [f"- {c.text} " + " ".join(f"[{i}]" for i in c.evidence_ids) for c in facts]
            out.append("")
        out.append("**Inference**\n")
        out += [f"- {c.text}" for c in infs] or ["- None"]
        out.append("")
        if self.evidence:
            out.append("| Evidence | Type | Title | Date | Source |")
            out.append("|---|---|---|---|---|")
            for e in self.evidence:
                title = e.title.replace("|", "\\|")[:80]
                out.append(f"| `{e.id}` | {e.type} | {title} | {e.date[:10]} | {e.source} |")
            out.append("")
        if self.gaps:
            out.append("**Not found in evidence:** " + "; ".join(self.gaps))
        return "\n".join(out)

    def to_table_rows(self) -> list[dict[str, str]]:
        return [{"id": e.id, "type": e.type, "title": e.title, "date": e.date, "source": e.source} for e in self.evidence]


# --------------------------------------------------------------------------- #
#  Tool results (type-checked by the guardrail layer)                           #
# --------------------------------------------------------------------------- #
class ToolHit(BaseModel):
    id: str
    type: str = ""
    title: str = ""
    snippet: str = ""
    url: str = ""
    date: str = ""
    score: float = 0.0
    files: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)


class ToolResponse(BaseModel):
    ok: bool = True
    tool: str
    server: str = ""
    query: str = ""
    results: list[ToolHit] = Field(default_factory=list)
    value: str | float | int | None = None
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
#  Guardrails + run results                                                     #
# --------------------------------------------------------------------------- #
class GuardrailReport(BaseModel):
    stage: Literal["input", "tool", "output"]
    allowed: bool = True
    categories: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    sanitized: str | None = None


class RunMetrics(BaseModel):
    latency_ms: float = 0.0
    llm_calls: int = 0
    llm_calls_by_framework: dict[str, int] = Field(default_factory=dict)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    tokens_estimated: bool = False
    cost_usd: float = 0.0
    tool_calls: int = 0
    tool_errors: int = 0
    messages: int = 0
    message_bytes: int = 0
    a2a_calls: int = 0
    guardrail_blocks: int = 0
    retries: int = 0
    errors: int = 0
    per_agent_latency_ms: dict[str, float] = Field(default_factory=dict)


class RunResult(BaseModel):
    run_id: str
    question: str
    pattern: str
    messaging: str
    variant: str
    model: str
    status: Literal["ok", "blocked", "degraded", "failed"] = "ok"
    final: FinalAnswer
    input_guard: GuardrailReport | None = None
    output_guard: GuardrailReport | None = None
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    agents_invoked: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    trace_file: str = ""


# --------------------------------------------------------------------------- #
#  Lenient parsing of LLM JSON                                                  #
# --------------------------------------------------------------------------- #
def extract_json(text: str) -> Any:
    """Return the first JSON object/array in *text* (tolerates ```json fences and prose)."""
    if text is None:
        return None
    if isinstance(text, (dict, list)):
        return text
    t = re.sub(r"```(?:json)?", "", str(text)).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start = t.find(opener)
        while start >= 0:
            depth, in_str, esc = 0, False, False
            for i in range(start, len(t)):
                ch = t[i]
                if in_str:
                    esc = (ch == "\\") and not esc
                    if ch == '"' and not esc:
                        in_str = False
                    elif ch != "\\":
                        esc = False
                    continue
                if ch == '"':
                    in_str = True
                elif ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(t[start:i + 1])
                        except json.JSONDecodeError:
                            break
            start = t.find(opener, start + 1)
    return None


def parse_model(model: type[BaseModel], raw: Any) -> BaseModel | None:
    """Validate *raw* (model instance, dict or text containing JSON) into *model*; None if impossible."""
    if isinstance(raw, model):
        return raw
    if isinstance(raw, BaseModel):
        raw = raw.model_dump()
    data = raw if isinstance(raw, dict) else extract_json(raw)
    if not isinstance(data, dict):
        return None
    try:
        return model.model_validate(data)
    except ValidationError:
        # tolerate extra / slightly-off keys: keep known fields only
        known = {k: v for k, v in data.items() if k in model.model_fields}
        try:
            return model.model_validate(known)
        except ValidationError:
            return None
