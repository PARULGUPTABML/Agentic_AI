"""Guardrails for the agentic system (requirement 8).

Three layers, wired into BOTH frameworks:

    input   ``check_input``        prompt injection / jailbreak, toxic or abusive language, secrets & PII,
                                   length and emptiness, off-topic hint (soft)
            ADK  before_model_callback   blocks the model call and returns a refusal
            Crew before_llm_call hook    returns False to block the call
    tool    ``check_tool_args``    argument type / range / path-traversal validation
            ``check_tool_output``  type-checks a tool's return against ``ToolResponse`` and strips
                                   *indirect* prompt injection found inside repository text
            ADK  before_tool_callback / after_tool_callback
            Crew before_tool_call / after_tool_call hooks
    output  ``check_final``        FinalAnswer schema, citations must be retrieved evidence (fabricated ids
                                   removed), secrets redacted, abstention when there is no evidence
            Crew Task.guardrail (``crew_final_guardrail``)  -> returns (False, reason) to force a retry
"""
from __future__ import annotations

import json
import re
from typing import Any, Tuple

from pydantic import ValidationError

from readmind.schemas import FinalAnswer, GuardrailReport, ToolResponse, extract_json, parse_model

MAX_QUESTION_CHARS = 2000

INJECTION = [
    r"ignore\s+(?:all\s+|any\s+)?(?:the\s+)?(?:previous|prior|above|earlier)?\s*(?:instructions?|prompts?|rules?|commands?|directions?)",
    r"disregard\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above)?\s*(?:instructions?|rules?|guidelines?)",
    r"forget\s+(?:everything|all)\s+(?:you|above|previous)",
    r"(?:reveal|print|show|repeat|output|leak)\s+(?:me\s+)?(?:your|the)\s+(?:system|hidden|secret|internal|initial)\s+(?:prompt|instructions?|message)",
    r"\bsystem\s*prompt\b",
    r"\bjail\s*break\b",
    r"\bDAN\b|do\s+anything\s+now",
    r"developer\s+mode|god\s+mode",
    r"pretend\s+(?:you\s+are|to\s+be)\s+(?:an?\s+)?(?:unfiltered|unrestricted|evil|different)",
    r"bypass\s+(?:the\s+)?(?:safety|filters?|rules|guardrails?|restrictions?)",
    r"you\s+are\s+no\s+longer\s+(?:an?\s+)?(?:assistant|bound)",
    r"<\s*/?\s*(?:system|instructions?)\s*>",
    r"\[\s*(?:system|INST)\s*\]",
    r"(?:new|updated)\s+instructions?\s*:",
    r"act\s+as\s+(?:an?\s+)?(?:root|admin|sudo)",
    r"exfiltrat|send\s+(?:the\s+)?(?:api\s+)?keys?\s+to",
]
_INJ = [re.compile(p, re.IGNORECASE) for p in INJECTION]

# Abusive / toxic language (compact lexicon; word-boundary matched; extend per deployment).
TOXIC = [
    r"\bf+u+c+k+(?:ing|er|ed)?\b", r"\bsh[i1]t+\b", r"\bbitch(?:es)?\b", r"\bbastard\b", r"\basshole\b",
    r"\bidiot(?:s|ic)?\b", r"\bstupid\b", r"\bmoron\b", r"\bretard(?:ed)?\b", r"\bdumbass\b",
    r"\bkill\s+(?:yourself|urself|you)\b", r"\bi\s+will\s+(?:hurt|kill)\b", r"\bdie\b\s+in\s+a\s+fire",
    r"\bhate\s+(?:you|all)\b", r"\bscum\b", r"\btrash\s+(?:human|person)\b",
]
_TOX = [re.compile(p, re.IGNORECASE) for p in TOXIC]

SECRETS = [
    (re.compile(r"\bghp_[A-Za-z0-9]{36}\b"), "github_token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}\b"), "github_token"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "openai_key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "aws_key"),
    (re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"), "google_api_key"),
    (re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"), "private_key"),
]
PII = [(re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "email"),
       (re.compile(r"\b(?:\+?\d{1,3}[ -]?)?\d{10}\b"), "phone")]

DOMAIN_HINTS = re.compile(r"commit|pr\b|pull|issue|review|code|file|function|kernel|patch|merge|refactor|bug|"
                          r"remov|add|why|when|who|implement|support|driver|arch|x86|arm|riscv|sel4|\.c\b|\.h\b",
                          re.IGNORECASE)


def _redact(text: str) -> tuple[str, list[str]]:
    found = []
    for rx, name in SECRETS:
        if rx.search(text):
            found.append(name)
            text = rx.sub(f"[REDACTED:{name}]", text)
    return text, found


# --------------------------------------------------------------------------- #
#  Input                                                                       #
# --------------------------------------------------------------------------- #
def check_input(text: str) -> GuardrailReport:
    t = (text or "").strip()
    cats, reasons = [], []
    if not t:
        return GuardrailReport(stage="input", allowed=False, categories=["empty"], reasons=["empty question"])
    if len(t) > MAX_QUESTION_CHARS:
        cats.append("too_long")
        reasons.append(f"question longer than {MAX_QUESTION_CHARS} characters")
    inj = [p.pattern for p in _INJ if p.search(t)]
    if inj:
        cats.append("prompt_injection")
        reasons.append(f"prompt-injection pattern(s): {len(inj)}")
    tox = [p.pattern for p in _TOX if p.search(t)]
    if tox:
        cats.append("toxicity")
        reasons.append("abusive or toxic language")
    sanitized, secrets = _redact(t)
    if secrets:
        cats.append("secret")
        reasons.append("credential in prompt: " + ", ".join(sorted(set(secrets))))
    pii = [name for rx, name in PII if rx.search(sanitized)]
    if pii:
        cats.append("pii")
        sanitized = PII[0][0].sub("[REDACTED:email]", sanitized)
        reasons.append("personal data redacted: " + ", ".join(sorted(set(pii))))
    blocking = {"empty", "too_long", "prompt_injection", "toxicity"}
    allowed = not (set(cats) & blocking)
    if allowed and not DOMAIN_HINTS.search(sanitized):
        cats.append("off_topic")
        reasons.append("question does not look like a repository-history question (allowed, flagged)")
    return GuardrailReport(stage="input", allowed=allowed, categories=cats, reasons=reasons,
                           sanitized=sanitized if sanitized != t else None)


REFUSAL = ("I can't help with that request. I answer questions about this repository's history and code, "
           "and requests to change my instructions or abusive messages are refused.")


# --------------------------------------------------------------------------- #
#  Tools                                                                       #
# --------------------------------------------------------------------------- #
_ARG_RULES = {
    "query": (str, 1, 500), "commit_id": (str, 4, 80), "path": (str, 0, 300), "item_id": (str, 3, 120),
    "expression": (str, 1, 200), "start": (str, 4, 40), "end": (str, 4, 40), "unit": (str, 1, 10),
    "k": (int, 1, 10), "start_line": (int, 1, 10 ** 6), "end_line": (int, 1, 10 ** 6),
    "prompt_tokens": (int, 0, 10 ** 9), "completion_tokens": (int, 0, 10 ** 9),
}


def check_tool_args(tool: str, args: dict[str, Any]) -> GuardrailReport:
    reasons = []
    for k, v in (args or {}).items():
        rule = _ARG_RULES.get(k)
        if not rule:
            continue
        typ, lo, hi = rule
        if typ is int:
            try:
                v = int(v)
            except (TypeError, ValueError):
                reasons.append(f"{k} must be an integer")
                continue
            if not lo <= v <= hi:
                reasons.append(f"{k}={v} out of range [{lo}, {hi}]")
        else:
            if not isinstance(v, str):
                reasons.append(f"{k} must be a string")
                continue
            if not lo <= len(v) <= hi:
                reasons.append(f"{k} length {len(v)} out of range")
            if k == "path" and (".." in v.split("/") or v.startswith("/")):
                reasons.append("path traversal rejected")
            if k == "query" and any(p.search(v) for p in _INJ):
                reasons.append("prompt injection in tool query")
    return GuardrailReport(stage="tool", allowed=not reasons, categories=["tool_args"] if reasons else [],
                           reasons=reasons)


def _scrub(text: str) -> tuple[str, int]:
    n = 0
    for p in _INJ:
        text, c = p.subn("[possible injected instruction removed]", text)
        n += c
    text, _ = _redact(text)
    return text, n


def coerce_tool_payload(result: Any) -> dict | None:
    """Normalise what a framework hands back from an MCP call into a plain dict."""
    if isinstance(result, ToolResponse):
        return result.model_dump()
    if isinstance(result, dict):
        if "structuredContent" in result and isinstance(result["structuredContent"], dict):
            sc = result["structuredContent"]
            return sc.get("result", sc) if isinstance(sc.get("result", sc), dict) else sc
        if "content" in result and "tool" not in result:
            txt = " ".join(c.get("text", "") for c in result.get("content", []) if isinstance(c, dict))
            data = extract_json(txt)
            return data if isinstance(data, dict) else {"tool": "?", "ok": False, "error": "unparseable tool output"}
        if "result" in result and isinstance(result["result"], dict):
            return result["result"]
        return result
    if isinstance(result, str):
        data = extract_json(result)
        return data if isinstance(data, dict) else None
    return None


def check_tool_output(tool: str, result: Any) -> tuple[dict | None, GuardrailReport]:
    """Validate a tool result's TYPE (ToolResponse) and scrub indirect injection from its text."""
    payload = coerce_tool_payload(result)
    if payload is None:
        return None, GuardrailReport(stage="tool", allowed=False, categories=["type_error"],
                                     reasons=[f"{tool}: result is not a ToolResponse-shaped object"])
    try:
        tr = ToolResponse.model_validate({"tool": tool, **payload} if "tool" not in payload else payload)
    except ValidationError as e:
        return None, GuardrailReport(stage="tool", allowed=False, categories=["type_error"],
                                     reasons=[f"{tool}: {e.error_count()} schema error(s)"])
    removed = 0
    for h in tr.results:
        h.snippet, n1 = _scrub(h.snippet)
        h.title, n2 = _scrub(h.title)
        removed += n1 + n2
    cats = ["indirect_injection"] if removed else []
    rep = GuardrailReport(stage="tool", allowed=True, categories=cats,
                          reasons=[f"{removed} injected instruction(s) removed from tool output"] if removed else [])
    return tr.model_dump(), rep


# --------------------------------------------------------------------------- #
#  Output                                                                      #
# --------------------------------------------------------------------------- #
def _id_ok(cid: str, allowed: set[str]) -> bool:
    if cid in allowed:
        return True
    if cid.startswith("commit:"):
        sha = cid[7:].lower()
        return len(sha) >= 7 and any(a.startswith("commit:") and (a[7:].startswith(sha) or sha.startswith(a[7:]))
                                     for a in allowed)
    return False


def check_final(final: FinalAnswer, allowed_ids: set[str] | None = None) -> tuple[FinalAnswer, GuardrailReport]:
    """Enforce grounding on the final structured answer. Returns a cleaned copy + report."""
    cats, reasons = [], []
    f = final.model_copy(deep=True)
    if allowed_ids is not None:
        allowed = set(allowed_ids)
        fabricated = [c for c in f.cited_ids() if not _id_ok(c, allowed)]
        if fabricated:
            cats.append("fabricated_citation")
            reasons.append("removed citations not in retrieved evidence: " + ", ".join(fabricated[:8]))
            for c in fabricated:
                f.answer = f.answer.replace(f"[{c}]", "").replace(f", {c}", "").replace(f"{c}, ", "")
            f.answer = re.sub(r"\s+(?:and|,)\s*([.;])", r"\1", f.answer)
            f.answer = re.sub(r"\[\s*,?\s*\]", "", re.sub(r"\s{2,}", " ", f.answer)).strip()
            for cl in f.claims:
                cl.evidence_ids = [i for i in cl.evidence_ids if _id_ok(i, allowed)]
            f.evidence = [e for e in f.evidence if _id_ok(e.id, allowed)]
        if not allowed and f.confidence != "low":
            cats.append("ungrounded")
            reasons.append("no evidence retrieved: confidence forced to low")
            f.confidence = "low"
    f.answer, secrets = _redact(f.answer)
    if secrets:
        cats.append("secret")
        reasons.append("secret redacted from answer")
    if not f.answer.strip():
        cats.append("empty")
        reasons.append("empty answer")
        f.answer = "The available evidence does not explain this."
    facts_without_ids = [c for c in f.claims if c.kind == "fact" and not c.evidence_ids]
    for c in facts_without_ids:
        c.kind = "inference"
    if facts_without_ids:
        cats.append("uncited_fact")
        reasons.append(f"{len(facts_without_ids)} uncited 'fact' claim(s) relabelled as inference")
    return f, GuardrailReport(stage="output", allowed=True, categories=cats, reasons=reasons)


# CrewAI Task.guardrail ----------------------------------------------------------
_ALLOWED_IDS: dict[str, set[str]] = {}


def set_allowed_ids(run_id: str, ids: set[str]) -> None:
    _ALLOWED_IDS[run_id] = set(ids)


def crew_final_guardrail(output):  # returns (bool, Any); no annotation: CrewAI inspects it at runtime
    """CrewAI task guardrail for the reviewer: valid FinalAnswer with grounded citations."""
    raw = getattr(output, "pydantic", None) or getattr(output, "json_dict", None) or getattr(output, "raw", output)
    fa = parse_model(FinalAnswer, raw)
    if fa is None:
        return False, ("Your output is not a valid FinalAnswer JSON object. Return ONLY JSON with fields question, "
                       "answer, claims, evidence, confidence, gaps.")
    if not fa.answer.strip():
        return False, "The answer field is empty."
    from readmind.observability.monitor import active_monitor
    mon = active_monitor()
    allowed = mon.retrieved_ids() if mon else set()          # live provenance ledger of this run
    if allowed:
        bad = [c for c in fa.cited_ids() if not _id_ok(c, allowed)]
        if bad:
            if mon:
                mon.event("crewai", "reviewer", "guardrail", {"rejected": bad[:8]})
            return False, (f"You cited ids that are not in the evidence: {', '.join(bad[:8])}. Cite only these ids: "
                           f"{', '.join(sorted(allowed)[:30])}.")
    return True, fa.model_dump_json()
