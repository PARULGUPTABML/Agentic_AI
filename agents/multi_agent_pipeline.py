"""Multi-agent answering pipeline.

Orchestrates the History/RAG Agent and Evidence Verification Agent
through the Blackboard, with optional re-retrieval loops.

This module provides a drop-in replacement for the single-shot
``rag.pipeline.answer_question`` for the multi-agent condition.

Flow::

    Developer question
        │
        ▼
    ┌─────────────────────┐
    │  History/RAG Agent   │──► Blackboard (post evidence)
    │  (ReAct loop)        │
    └─────────────────────┘
        │
        ▼
    ┌─────────────────────┐
    │  Verification Agent  │──► Blackboard (post verdict)
    │  (CoT check)         │
    └─────────────────────┘
        │
        ├─ insufficient? ──► re-run History/RAG Agent with refined queries
        │
        └─ sufficient? ──► Generate final answer with citations
"""
from __future__ import annotations

import time
from typing import Any

from agents.blackboard import Blackboard, BlackboardStatus
from agents.history_rag_agent import HistoryRAGAgent
from agents.evidence_verification_agent import EvidenceVerificationAgent
from config import LLM_MODEL, LLM_PROVIDER
from rag.citations import verify_citations
from rag.llm import generate

# ------------------------------------------------------------------ #
#  Final answer generation                                            #
# ------------------------------------------------------------------ #

FINAL_ANSWER_SYSTEM = """You are a senior engineer who explains why a software repository looks the way it does.

Rules:
- Use ONLY the evidence provided below. Do not use outside knowledge about this project.
- End every factual sentence with citations using the evidence IDs exactly as written, e.g. [commit:1a2b3c4d5e] or [issue:42] or [pr:77].
- Never invent commit hashes, issue numbers or PR numbers.
- If the evidence does not fully answer the question, say what the evidence does show and note the gap.
- Prefer history evidence (commits, issues, PRs, reviews) to explain WHY; use code evidence to explain WHAT.
- Clearly separate what is established by evidence from what is inference.
- Be concise: 3-8 sentences. If the developer is planning a change, finish with one line starting "Recommendation:"."""


def _format_evidence_for_answer(blackboard: Blackboard, max_chars: int = 1800) -> str:
    """Format Blackboard evidence into the prompt for the final answer."""
    evidence = blackboard.get_evidence()
    blocks = []
    for e in evidence:
        date_str = f", {e.date[:10]}" if e.date else ""
        head = f"[{e.id}] ({e.evidence_type}{date_str}) {e.title}"
        blocks.append(f"{head}\n{e.content[:max_chars]}")
    return "\n\n---\n\n".join(blocks)


# ------------------------------------------------------------------ #
#  Multi-agent pipeline                                               #
# ------------------------------------------------------------------ #

def run_multi_agent(repo_slug: str, question: str) -> dict[str, Any]:
    """Run the full multi-agent pipeline for a question.

    Returns a dict compatible with the existing API response format.
    """
    t_start = time.perf_counter()

    # ── 1. Create Blackboard ──
    blackboard = Blackboard(question=question, repo_slug=repo_slug)
    blackboard.log("pipeline", "start", f"Multi-agent pipeline for: {question}")

    # ── 2. Create agents ──
    history_agent = HistoryRAGAgent(repo_slug=repo_slug)
    verification_agent = EvidenceVerificationAgent()

    # ── 3. Gathering loop (with re-retrieval) ──
    max_rounds = blackboard.max_retries + 1  # initial + retries

    for round_num in range(1, max_rounds + 1):
        blackboard.log("pipeline", f"round_{round_num}",
                       f"Starting gathering round {round_num}")

        # Run History/RAG Agent (ReAct loop)
        blackboard.set_status(BlackboardStatus.GATHERING)
        history_agent.run(blackboard)

        # Run Evidence Verification Agent (CoT check)
        verdict = verification_agent.run(blackboard)

        if verdict["sufficient"]:
            blackboard.log("pipeline", "evidence_sufficient",
                           f"Evidence sufficient after round {round_num}")
            break

        # Evidence insufficient → check if we can retry
        if not blackboard.can_retry():
            blackboard.log("pipeline", "max_retries_reached",
                           f"Evidence insufficient but max retries reached")
            break

        # Start re-gathering with refined queries
        blackboard.log("pipeline", "re_gathering",
                       f"Evidence insufficient. Gaps: {verdict['gaps'][:3]}. "
                       f"Retrying with: {verdict['retry_queries'][:3]}")
        blackboard.start_retry(verdict["retry_queries"])

    # ── 4. Generate final answer ──
    blackboard.set_status(BlackboardStatus.SYNTHESIZING)
    t_gen = time.perf_counter()

    evidence = blackboard.get_evidence()
    evidence_text = _format_evidence_for_answer(blackboard)

    if evidence:
        prompt = (
            f"Repository: {repo_slug.replace('__', '/')}\n\n"
            f"Evidence:\n\n{evidence_text}\n\n"
            f"Question: {question}\n\n"
            f"Answer with citations:"
        )
        answer = generate(FINAL_ANSWER_SYSTEM, prompt, temperature=0.0)
    else:
        answer = ("The available evidence does not explain this. "
                  "No relevant historical evidence was found in the repository.")

    gen_ms = (time.perf_counter() - t_gen) * 1000
    total_ms = (time.perf_counter() - t_start) * 1000

    # ── 5. Citation verification ──
    evidence_ids = [e.id for e in evidence]
    citation_check = verify_citations(answer, evidence_ids)

    # ── 6. Finalise ──
    blackboard.final_answer = answer
    blackboard.set_status(BlackboardStatus.DONE)

    blackboard.log("pipeline", "complete",
                   f"Total: {total_ms:.0f}ms | Evidence: {len(evidence)} | "
                   f"Retries: {blackboard.retry_count}",
                   duration_ms=total_ms)

    # ── 7. Build response (compatible with existing API format) ──
    return {
        "repo": repo_slug,
        "condition": "multi_agent",
        "condition_label": "Multi-Agent RAG (ReAct + CoT Verification)",
        "question": question,
        "answer": answer,
        "evidence": [
            {
                "id": e.id,
                "type": e.evidence_type,
                "title": e.title,
                "date": e.date,
                "url": e.url,
                "source_agent": e.source_agent,
                "confidence": round(e.confidence, 4),
                "snippet": e.content[:500],
            }
            for e in evidence
        ],
        "verification": {
            "citations": citation_check,
            "evidence_sufficiency": blackboard.verification_result,
        },
        "latency_ms": {
            "generation": round(gen_ms, 1),
            "total": round(total_ms, 1),
        },
        "model": f"{LLM_PROVIDER}:{LLM_MODEL}",
        "multi_agent": {
            "run_id": blackboard.run_id,
            "retry_count": blackboard.retry_count,
            "evidence_count": len(evidence),
            "agent_logs": [
                {
                    "agent": log.agent,
                    "action": log.action,
                    "detail": log.detail[:200],
                    "duration_ms": round(log.duration_ms, 1),
                    "timestamp": log.timestamp,
                }
                for log in blackboard.get_logs()
            ],
        },
    }
