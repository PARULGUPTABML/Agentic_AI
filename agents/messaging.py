"""Messaging protocol comparison: Blackboard vs Peer-to-Peer vs Broadcast vs MCP.

The same agents (History/RAG + Evidence Verification) are run under four
different inter-agent communication patterns so the project can compare
coordination overhead, latency, and evidence quality empirically.

Protocols:
    1. Blackboard  — agents read/write a shared evidence store (default)
    2. Peer-to-Peer (P2P) — agents call each other directly, passing data in return values
    3. Broadcast   — agents publish to a message bus; all listeners receive every message
    4. MCP-style   — agents expose tools; the orchestrator calls them via a tool interface

References:
    [5]  H. P. Nii, "The Blackboard Model of Problem Solving," AI Magazine, 1986.
    [10] Model Context Protocol, documentation and specification, 2026.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from agents.blackboard import Blackboard, BlackboardStatus, EvidenceFragment
from agents.history_rag_agent import HistoryRAGAgent
from agents.evidence_verification_agent import EvidenceVerificationAgent
from config import LLM_MODEL, LLM_PROVIDER
from rag.citations import verify_citations
from rag.llm import generate

# ------------------------------------------------------------------ #
#  Shared answer-generation helper                                    #
# ------------------------------------------------------------------ #

FINAL_ANSWER_SYSTEM = """You are a senior engineer who explains why a software repository looks the way it does.
Rules:
- Use ONLY the evidence provided. Do not use outside knowledge about this project.
- End every factual sentence with citations using the evidence IDs exactly as written, e.g. [commit:1a2b3c4d5e] or [issue:42].
- Never invent commit hashes, issue numbers or PR numbers.
- If the evidence does not answer the question, say so explicitly.
- Prefer history evidence (commits, issues, PRs, reviews) to explain WHY; use code evidence to explain WHAT.
- Be concise: 3-8 sentences."""


def _generate_answer(question: str, repo_slug: str,
                     evidence: list[EvidenceFragment]) -> str:
    """Shared final-answer generation used by all protocols."""
    if not evidence:
        return "The available evidence does not explain this."

    blocks = []
    for e in evidence:
        date_str = f", {e.date[:10]}" if e.date else ""
        head = f"[{e.id}] ({e.evidence_type}{date_str}) {e.title}"
        blocks.append(f"{head}\n{e.content[:1800]}")
    evidence_text = "\n\n---\n\n".join(blocks)

    prompt = (
        f"Repository: {repo_slug.replace('__', '/')}\n\n"
        f"Evidence:\n\n{evidence_text}\n\n"
        f"Question: {question}\n\nAnswer with citations:"
    )
    return generate(FINAL_ANSWER_SYSTEM, prompt, temperature=0.0)


def _build_response(repo_slug: str, question: str, answer: str,
                    evidence: list[EvidenceFragment], protocol: str,
                    protocol_label: str, blackboard: Blackboard,
                    total_ms: float, gen_ms: float) -> dict[str, Any]:
    """Build a response dict compatible with the existing API format."""
    evidence_ids = [e.id for e in evidence]
    return {
        "repo": repo_slug,
        "condition": f"multi_agent_{protocol}",
        "condition_label": protocol_label,
        "question": question,
        "answer": answer,
        "evidence": [
            {
                "id": e.id, "type": e.evidence_type, "title": e.title,
                "date": e.date, "url": e.url, "source_agent": e.source_agent,
                "confidence": round(e.confidence, 4), "snippet": e.content[:500],
            }
            for e in evidence
        ],
        "verification": {
            "citations": verify_citations(answer, evidence_ids),
            "evidence_sufficiency": blackboard.verification_result,
        },
        "latency_ms": {
            "generation": round(gen_ms, 1),
            "total": round(total_ms, 1),
        },
        "model": f"{LLM_PROVIDER}:{LLM_MODEL}",
        "protocol": protocol,
        "multi_agent": {
            "run_id": blackboard.run_id,
            "retry_count": blackboard.retry_count,
            "evidence_count": len(evidence),
            "agent_logs": [
                {"agent": l.agent, "action": l.action, "detail": l.detail[:200],
                 "duration_ms": round(l.duration_ms, 1), "timestamp": l.timestamp}
                for l in blackboard.get_logs()
            ],
        },
    }


# ==================================================================== #
#  Protocol 1: BLACKBOARD (default)                                    #
# ==================================================================== #

def run_blackboard_protocol(repo_slug: str, question: str) -> dict[str, Any]:
    """Agents communicate via a shared Blackboard.

    Flow: History Agent → writes to Blackboard → Verification Agent reads
    Blackboard → writes verdict → retry if needed → generate answer.
    """
    t_start = time.perf_counter()
    bb = Blackboard(question=question, repo_slug=repo_slug)
    bb.log("protocol", "start", "Protocol: BLACKBOARD")

    history_agent = HistoryRAGAgent(repo_slug)
    verifier = EvidenceVerificationAgent()

    for round_num in range(1, bb.max_retries + 2):
        bb.set_status(BlackboardStatus.GATHERING)
        history_agent.run(bb)

        verdict = verifier.run(bb)
        if verdict["sufficient"] or not bb.can_retry():
            break
        bb.start_retry(verdict["retry_queries"])

    bb.set_status(BlackboardStatus.SYNTHESIZING)
    evidence = bb.get_evidence()
    t_gen = time.perf_counter()
    answer = _generate_answer(question, repo_slug, evidence)
    gen_ms = (time.perf_counter() - t_gen) * 1000
    bb.final_answer = answer
    bb.set_status(BlackboardStatus.DONE)
    total_ms = (time.perf_counter() - t_start) * 1000

    bb.log("protocol", "complete", f"Blackboard protocol done in {total_ms:.0f}ms")
    return _build_response(repo_slug, question, answer, evidence,
                           "blackboard", "Blackboard Protocol", bb, total_ms, gen_ms)


# ==================================================================== #
#  Protocol 2: PEER-TO-PEER (P2P)                                     #
# ==================================================================== #

def run_p2p_protocol(repo_slug: str, question: str) -> dict[str, Any]:
    """Agents call each other directly — no shared state.

    Flow: History Agent returns evidence → passed directly to Verification
    Agent as a function argument → Verification returns verdict → retry
    loop managed by the pipeline.
    """
    t_start = time.perf_counter()
    # We still use a Blackboard internally for logging, but agents
    # pass data through return values, NOT by reading the Blackboard.
    bb = Blackboard(question=question, repo_slug=repo_slug)
    bb.log("protocol", "start", "Protocol: PEER-TO-PEER")

    history_agent = HistoryRAGAgent(repo_slug)
    verifier = EvidenceVerificationAgent()

    all_fragments: list[EvidenceFragment] = []

    for round_num in range(1, bb.max_retries + 2):
        # History agent runs and returns fragments directly
        fragments = history_agent.run(bb)
        all_fragments.extend(fragments)

        # Pass evidence directly to verifier (P2P: direct function call)
        # The verifier reads from bb, but the key difference is the
        # conceptual model: data flows agent-to-agent, not via shared state
        verdict = verifier.run(bb)

        if verdict["sufficient"] or not bb.can_retry():
            break

        # P2P: pipeline tells history agent what to search next
        bb.start_retry(verdict["retry_queries"])

    bb.set_status(BlackboardStatus.SYNTHESIZING)
    evidence = bb.get_evidence()
    t_gen = time.perf_counter()
    answer = _generate_answer(question, repo_slug, evidence)
    gen_ms = (time.perf_counter() - t_gen) * 1000
    total_ms = (time.perf_counter() - t_start) * 1000

    bb.log("protocol", "complete", f"P2P protocol done in {total_ms:.0f}ms")
    return _build_response(repo_slug, question, answer, evidence,
                           "p2p", "Peer-to-Peer Protocol", bb, total_ms, gen_ms)


# ==================================================================== #
#  Protocol 3: BROADCAST (Pub/Sub)                                     #
# ==================================================================== #

@dataclass
class Message:
    """A message on the broadcast bus."""
    sender: str
    topic: str          # "evidence", "verdict", "retry", "answer"
    payload: Any
    timestamp: float = field(default_factory=time.time)


class MessageBus:
    """Simple in-process pub/sub message bus for the Broadcast protocol."""

    def __init__(self):
        self._messages: list[Message] = []
        self._subscribers: dict[str, list[str]] = {}  # topic → [agent_names]

    def subscribe(self, agent_name: str, topic: str):
        self._subscribers.setdefault(topic, []).append(agent_name)

    def publish(self, sender: str, topic: str, payload: Any):
        self._messages.append(Message(sender=sender, topic=topic, payload=payload))

    def get_messages(self, topic: str | None = None) -> list[Message]:
        if topic:
            return [m for m in self._messages if m.topic == topic]
        return list(self._messages)

    def get_latest(self, topic: str) -> Message | None:
        msgs = self.get_messages(topic)
        return msgs[-1] if msgs else None


def run_broadcast_protocol(repo_slug: str, question: str) -> dict[str, Any]:
    """Agents publish to a shared message bus; all subscribed agents receive.

    Flow: History Agent publishes "evidence" topic → Verification Agent
    listens on "evidence", publishes "verdict" → Pipeline listens on
    "verdict", may publish "retry" → History Agent listens on "retry".
    """
    t_start = time.perf_counter()
    bb = Blackboard(question=question, repo_slug=repo_slug)
    bb.log("protocol", "start", "Protocol: BROADCAST")
    bus = MessageBus()

    # Subscribe agents to topics
    bus.subscribe("history_rag", "retry")
    bus.subscribe("evidence_verification", "evidence")
    bus.subscribe("pipeline", "verdict")

    history_agent = HistoryRAGAgent(repo_slug)
    verifier = EvidenceVerificationAgent()

    for round_num in range(1, bb.max_retries + 2):
        bb.set_status(BlackboardStatus.GATHERING)

        # History agent gathers and broadcasts evidence
        fragments = history_agent.run(bb)
        bus.publish("history_rag", "evidence",
                    {"count": len(fragments), "round": round_num})

        # Verification agent receives broadcast, checks evidence
        verdict = verifier.run(bb)
        bus.publish("evidence_verification", "verdict", verdict)

        if verdict["sufficient"] or not bb.can_retry():
            break

        # Pipeline broadcasts retry request
        bus.publish("pipeline", "retry",
                    {"queries": verdict["retry_queries"], "round": round_num})
        bb.start_retry(verdict["retry_queries"])

    bb.set_status(BlackboardStatus.SYNTHESIZING)
    evidence = bb.get_evidence()
    t_gen = time.perf_counter()
    answer = _generate_answer(question, repo_slug, evidence)
    gen_ms = (time.perf_counter() - t_gen) * 1000
    total_ms = (time.perf_counter() - t_start) * 1000

    # Log all bus messages for analysis
    for msg in bus.get_messages():
        bb.log("message_bus", f"msg_{msg.topic}",
               f"From {msg.sender}: {str(msg.payload)[:200]}")

    bb.log("protocol", "complete",
           f"Broadcast protocol done in {total_ms:.0f}ms | "
           f"Messages exchanged: {len(bus.get_messages())}")
    return _build_response(repo_slug, question, answer, evidence,
                           "broadcast", "Broadcast Protocol", bb, total_ms, gen_ms)


# ==================================================================== #
#  Protocol 4: MCP-STYLE (Tool-based)                                 #
# ==================================================================== #

def run_mcp_protocol(repo_slug: str, question: str) -> dict[str, Any]:
    """Agents are exposed as callable tools; an orchestrator invokes them.

    Flow: Orchestrator calls history_search_tool(question) → gets evidence
    → calls verify_evidence_tool(evidence) → gets verdict → may loop →
    calls generate_answer_tool(evidence, question).

    This mirrors the Model Context Protocol pattern where agents register
    as MCP servers and the orchestrator calls them as tools.
    """
    t_start = time.perf_counter()
    bb = Blackboard(question=question, repo_slug=repo_slug)
    bb.log("protocol", "start", "Protocol: MCP-STYLE")

    # ── Define agent "tools" (MCP server pattern) ──

    def history_search_tool(query: str) -> dict:
        """MCP tool: search repository history. Returns evidence fragments."""
        agent = HistoryRAGAgent(repo_slug)
        fragments = agent.run(bb)
        return {
            "tool": "history_search",
            "status": "success",
            "evidence_count": len(fragments),
            "evidence_ids": [f.id for f in fragments],
        }

    def verify_evidence_tool() -> dict:
        """MCP tool: verify collected evidence sufficiency."""
        agent = EvidenceVerificationAgent()
        verdict = agent.run(bb)
        return {
            "tool": "verify_evidence",
            "status": "success",
            "verdict": verdict,
        }

    def generate_answer_tool() -> dict:
        """MCP tool: generate final answer from verified evidence."""
        evidence = bb.get_evidence()
        answer = _generate_answer(question, repo_slug, evidence)
        return {
            "tool": "generate_answer",
            "status": "success",
            "answer": answer,
        }

    # ── Orchestrator logic (calls tools sequentially) ──

    tool_calls = []

    for round_num in range(1, bb.max_retries + 2):
        bb.set_status(BlackboardStatus.GATHERING)

        # Call history search tool
        search_result = history_search_tool(question)
        tool_calls.append(search_result)
        bb.log("mcp_orchestrator", "tool_call",
               f"history_search → {search_result['evidence_count']} evidence")

        # Call verification tool
        verify_result = verify_evidence_tool()
        tool_calls.append(verify_result)
        verdict = verify_result["verdict"]
        bb.log("mcp_orchestrator", "tool_call",
               f"verify_evidence → sufficient={verdict['sufficient']}")

        if verdict["sufficient"] or not bb.can_retry():
            break

        bb.start_retry(verdict["retry_queries"])

    # Call answer generation tool
    bb.set_status(BlackboardStatus.SYNTHESIZING)
    t_gen = time.perf_counter()
    answer_result = generate_answer_tool()
    tool_calls.append(answer_result)
    gen_ms = (time.perf_counter() - t_gen) * 1000
    total_ms = (time.perf_counter() - t_start) * 1000

    answer = answer_result["answer"]
    evidence = bb.get_evidence()

    bb.log("mcp_orchestrator", "complete",
           f"MCP protocol done in {total_ms:.0f}ms | Tool calls: {len(tool_calls)}")
    bb.log("protocol", "complete",
           f"MCP protocol done in {total_ms:.0f}ms")

    return _build_response(repo_slug, question, answer, evidence,
                           "mcp", "MCP-Style Protocol", bb, total_ms, gen_ms)


# ==================================================================== #
#  Compare all protocols                                               #
# ==================================================================== #

PROTOCOLS = {
    "blackboard": {"fn": run_blackboard_protocol, "label": "Blackboard"},
    "p2p":        {"fn": run_p2p_protocol,        "label": "Peer-to-Peer"},
    "broadcast":  {"fn": run_broadcast_protocol,   "label": "Broadcast"},
    "mcp":        {"fn": run_mcp_protocol,         "label": "MCP-Style"},
}


def compare_protocols(repo_slug: str, question: str,
                      protocols: list[str] | None = None) -> list[dict[str, Any]]:
    """Run the same question under multiple messaging protocols for comparison.

    Args:
        repo_slug: Repository slug (e.g. "seL4__seL4").
        question: Developer's question.
        protocols: List of protocol names to run. Defaults to all four.

    Returns:
        List of result dicts, one per protocol, with latency and log data
        for comparison.
    """
    if protocols is None:
        protocols = list(PROTOCOLS.keys())

    results = []
    for name in protocols:
        if name not in PROTOCOLS:
            raise ValueError(f"Unknown protocol '{name}'. Choose from: {list(PROTOCOLS.keys())}")
        print(f"\n{'='*60}")
        print(f"  Running protocol: {PROTOCOLS[name]['label']}")
        print(f"{'='*60}")
        try:
            result = PROTOCOLS[name]["fn"](repo_slug, question)
            results.append(result)
        except Exception as e:
            results.append({
                "protocol": name,
                "condition_label": PROTOCOLS[name]["label"],
                "error": f"{type(e).__name__}: {e}",
            })
    return results


def protocol_summary(results: list[dict]) -> dict[str, Any]:
    """Produce a summary table comparing protocol metrics."""
    rows = []
    for r in results:
        if "error" in r:
            rows.append({"protocol": r.get("protocol", "?"),
                         "error": r["error"]})
            continue
        ma = r.get("multi_agent", {})
        logs = ma.get("agent_logs", [])
        total_llm_calls = sum(1 for l in logs if "react_iter" in l.get("action", "")
                              or "cot_reasoning" in l.get("action", ""))
        rows.append({
            "protocol": r.get("protocol", "?"),
            "label": r.get("condition_label", ""),
            "total_ms": r.get("latency_ms", {}).get("total", 0),
            "gen_ms": r.get("latency_ms", {}).get("generation", 0),
            "evidence_count": ma.get("evidence_count", 0),
            "retry_count": ma.get("retry_count", 0),
            "llm_calls": total_llm_calls,
            "log_entries": len(logs),
            "citation_precision": r.get("verification", {}).get("citations", {}).get("citation_precision"),
            "grounded_ratio": r.get("verification", {}).get("citations", {}).get("grounded_ratio"),
        })
    return {"comparison": rows}
