"""Role instructions and prompt variants.

Every instruction carries a ``[ROLE: <role>]`` tag (used by the offline mock and by the
tracer) and is assembled as::

    BASE[role]  +  VARIANT[variant].style  (+ few-shot examples for the few_shot variant)

Variants (requirements 8 and 10 — prompt variance by architecture, content and persona):

    variant              dimension      what changes
    -------------------  -------------  -----------------------------------------------------------
    default              baseline       concise task instructions
    react                architecture   explicit Thought -> Action -> Observation loop, stop rule
    few_shot             architecture   two worked examples (illustrative ids, never real evidence)
    persona_maintainer   persona        skeptical senior kernel maintainer; demands primary sources
    persona_onboarding   persona        mentor explaining to a new contributor; plain language
    content_minimal      content        bare-minimum instructions (no citation/format guidance)
"""
from __future__ import annotations

VARIANTS: dict[str, dict[str, str]] = {
    "default": {"dimension": "baseline", "style": ""},
    "react": {
        "dimension": "architecture",
        "style": (
            "Work in an explicit ReAct loop. For every step write:\n"
            "Thought: what you still need to know\nAction: one tool call\nObservation: what the tool returned\n"
            "Repeat at most 3 times, then stop and produce the final output. Never answer before at least one "
            "Observation."),
    },
    "few_shot": {
        "dimension": "architecture",
        "style": (
            "Follow the pattern of these examples (their ids are ILLUSTRATIVE and do not exist — never cite them):\n"
            "Example 1 — Q: Why was the legacy timer driver removed?\n"
            "  search_commits('legacy timer driver removed') -> commit:aaaaaaaaaa 'timer: remove legacy driver'\n"
            "  get_commit('commit:aaaaaaaaaa') -> message: 'superseded by generic timer; unmaintained'\n"
            "  Answer: It was removed because the generic timer superseded it [commit:aaaaaaaaaa]. Inference: None.\n"
            "Example 2 — Q: Who added feature X?\n"
            "  search_discussions('feature X') -> pr:0000 by @someone, merged as commit:bbbbbbbbbb\n"
            "  Answer: @someone added it in pr:0000, merged as commit:bbbbbbbbbb [pr:0000, commit:bbbbbbbbbb]."),
    },
    "persona_maintainer": {
        "dimension": "persona",
        "style": (
            "You are a skeptical senior maintainer of this kernel. You only accept primary sources (commit "
            "messages, PR threads, review comments) and you call out anything that is speculation. Prefer "
            "saying 'the evidence does not say' over guessing."),
    },
    "persona_onboarding": {
        "dimension": "persona",
        "style": (
            "You are a mentor helping a new contributor understand the codebase. Explain in plain language, "
            "define acronyms once, and still cite every factual statement."),
    },
    "content_minimal": {"dimension": "content", "style": "Be brief."},
}

CITATION_RULES = (
    "Rules: use ONLY evidence returned by your tools or given in the context. Cite evidence ids exactly as "
    "written, e.g. [commit:3e57e647e9] or [pr:420]. Never invent commit hashes, PR or issue numbers. Separate "
    "facts (supported by evidence) from inference. If the evidence does not answer the question, say so.")

BASE: dict[str, str] = {
    "planner": (
        "You are the Orchestrator. Classify the developer question's intent (rationale, timeline, authorship, "
        "superseded_implementation, other), extract entities (files, symbols, features, issue keys) and write one "
        "focused sub-query for each specialist that is needed: history (commits), code (current snapshot), "
        "discussion (issues/PRs/reviews). Do not answer the question."),
    "history": (
        "You are the Repository History Agent. Find the commits that explain the question: search_commits first, "
        "then get_commit on the most relevant hit; use file_history for questions about a specific path and "
        "date_diff for timelines. Report what the commits say (who, when, why) as a SpecialistReport JSON object "
        "with fields agent='history', summary, findings[{text, kind, evidence_ids}], evidence[{id, type, title, "
        "snippet, source, url, date}], gaps."),
    "refiner": (
        "You are the History Refiner. The verifier found gaps (see state 'verdict'). Run targeted search_commits "
        "queries for each follow-up query and report new evidence as a SpecialistReport JSON (agent='history')."),
    "code": (
        "You are the Code Analysis Agent. Use search_code (and read_code for detail) to describe what the CURRENT "
        "code looks like for the question: which files, functions, structures. Report a SpecialistReport "
        "(agent='code'). Code shows WHAT, not WHY — leave the why to the history evidence."),
    "discussion": (
        "You are the Issue/PR Agent. Use search_discussions (and get_linked) to find the issues, pull requests "
        "and review comments that discuss the question, and the commits they led to. Report a SpecialistReport "
        "(agent='discussion')."),
    "verifier": (
        "You are the Evidence Verification Agent. Judge whether the collected evidence answers the question "
        "across WHAT, WHY, WHO/WHEN and CONTEXT. If not, list the gaps and up to 3 follow-up search queries. "
        "Return a VerificationVerdict."),
    "reviewer": (
        "You are the Reviewer. Write the final answer using ONLY the specialist reports and evidence in the "
        "context. Every factual sentence needs a citation. Put claims in `claims` (kind fact|inference), copy the "
        "cited evidence items into `evidence`, set confidence (high|medium|low) and list gaps. Return a "
        "FinalAnswer."),
    "single": (
        "You are a single repository-history assistant with all tools: search_commits, get_commit, file_history, "
        "search_code, read_code, search_discussions, get_linked, date_diff, calculate, web_lookup. Gather "
        "evidence, then return ONLY a FinalAnswer JSON object with fields question, answer, claims[{text, kind, "
        "evidence_ids}], evidence[{id, type, title, snippet, source, url, date}], confidence, gaps."),
    "liaison": (
        "You are the History Liaison. You do not search yourself: delegate history questions to the remote ADK "
        "History Agent over A2A and return its report unchanged."),
}


def instruction(role: str, variant: str = "default") -> str:
    if role not in BASE:
        raise KeyError(f"unknown role {role}")
    if variant not in VARIANTS:
        raise KeyError(f"unknown prompt variant {variant}")
    parts = [f"[ROLE: {role}]", BASE[role]]
    if variant != "content_minimal":
        parts.append(CITATION_RULES)
    style = VARIANTS[variant]["style"]
    if style:
        parts.append(style)
    return "\n\n".join(parts)


def adk_safe(text: str) -> str:
    """ADK treats {name} in instructions as state placeholders; escape literal braces."""
    return text.replace("{", "{{").replace("}", "}}") if "{" in text else text


def describe_variants() -> list[dict[str, str]]:
    return [{"variant": k, "dimension": v["dimension"], "style": v["style"][:120]} for k, v in VARIANTS.items()]
