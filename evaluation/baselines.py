"""Baseline configurations for the A / B / C comparison.

    A  no_context  — the answering LLM sees only the question.
    B  code_only   — the LLM sees the current code snapshot retrieved by the Code
                     Analysis Agent (no commits / issues / PRs / reviews).
    C  multi_agent — the full pipeline under one of the four architectures
                     (run by ``run_experiments.py`` through ``architectures/``).

A and B use the same answering model as C (``agents._llm_retry.generate_with_retry``
-> ``rag.llm.generate``) so the only thing that changes between configurations
is the context.  With ``LLM_PROVIDER=mock`` (or when ``rag`` is unavailable) a
deterministic *extractive* answer is produced instead, which is only meant for
dry-runs and tests, never for reported results.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable

from agents.blackboard import EvidenceFragment

NO_CONTEXT_SYSTEM = """You are a senior engineer answering questions about the history of a software repository.
You have NO access to the repository. Answer from what you know. If you do not know, say so plainly.
Be concise (3-6 sentences)."""

CODE_ONLY_SYSTEM = """You are a senior engineer who explains why a software repository looks the way it does.

Rules:
- Use ONLY the code evidence provided below. It is the CURRENT snapshot; it contains no commit history, issues or PRs.
- Cite evidence IDs exactly as written, e.g. [code:src/smp/lock.c].
- Never invent commit hashes, issue numbers or PR numbers.
- If the code alone cannot answer the question (e.g. WHY or WHEN something changed), say what the code shows and state the gap.
- Clearly separate what is established by evidence from what is inference.
- Be concise: 3-8 sentences."""


def answer_llm() -> Callable[[str, str], str] | None:
    if os.environ.get("LLM_PROVIDER", "").lower() in ("", "mock", "none"):
        return None
    try:
        from agents._llm_retry import generate_with_retry
    except Exception:
        return None
    return lambda s, p: generate_with_retry(s, p, temperature=0.0)


def _mock_extractive(question: str, evidence: list[EvidenceFragment]) -> str:
    if not evidence:
        return ("I cannot verify this without repository context; the available evidence does not "
                "explain it.")
    top = evidence[:3]
    lines = [f"{e.title} [{e.id}]." for e in top]
    return " ".join(lines) + " Inference: None beyond the cited evidence."


def _format(evidence: list[EvidenceFragment], max_chars: int = 1800) -> str:
    return "\n\n---\n\n".join(f"[{e.id}] ({e.evidence_type}) {e.title}\n{e.content[:max_chars]}" for e in evidence)


def _result(question: str, answer: str, evidence: list[EvidenceFragment], t0: float,
            agents: list[str], condition: str) -> dict[str, Any]:
    return {
        "architecture": "",
        "condition": condition,
        "question": question,
        "answer": answer,
        "confidence": "",
        "gaps": [],
        "rounds": 1,
        "agents_invoked": agents,
        "evidence_count": len(evidence),
        "evidence": [{"id": e.id, "type": e.evidence_type, "source_agent": e.source_agent,
                      "confidence": e.confidence, "title": e.title, "text": e.content} for e in evidence],
        "total_latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        "coordination_overhead_ms": 0.0,
        "logs": [],
    }


def run_no_context(question: str, llm_fn: Callable[[str, str], str] | None | str = "auto") -> dict[str, Any]:
    t0 = time.perf_counter()
    fn = answer_llm() if llm_fn == "auto" else llm_fn
    answer = fn(NO_CONTEXT_SYSTEM, f"Question: {question}\n\nAnswer:") if fn else _mock_extractive(question, [])
    return _result(question, answer, [], t0, ["llm"], "A_no_context")


def run_code_only(question: str, code_agent: Any, repo_slug: str = "seL4__seL4",
                  llm_fn: Callable[[str, str], str] | None | str = "auto") -> dict[str, Any]:
    t0 = time.perf_counter()
    fn = answer_llm() if llm_fn == "auto" else llm_fn
    evidence = code_agent.run(question, repo_slug=repo_slug) if code_agent else []
    if not fn:
        answer = _mock_extractive(question, evidence)
    elif evidence:
        prompt = (f"Repository: {repo_slug.replace('__', '/')}\n\nCode evidence:\n\n{_format(evidence)}\n\n"
                  f"Question: {question}\n\nAnswer with citations:")
        answer = fn(CODE_ONLY_SYSTEM, prompt)
    else:
        answer = "The available code evidence does not explain this."
    return _result(question, answer, evidence, t0, ["code_analysis", "llm"], "B_code_only")
