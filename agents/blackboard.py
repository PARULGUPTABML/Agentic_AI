"""Blackboard: shared evidence store for multi-agent coordination.

Every agent posts timestamped, typed evidence fragments to the Blackboard.
The Blackboard tracks the overall query lifecycle:

    gathering → verifying → (re-gathering) → synthesizing → done

References:
    [5] H. P. Nii, "The Blackboard Model of Problem Solving and the
        Evolution of Blackboard Architectures," AI Magazine, 1986.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class BlackboardStatus(str, Enum):
    """Lifecycle states of a query on the Blackboard."""
    GATHERING   = "gathering"      # agents are retrieving evidence
    VERIFYING   = "verifying"      # verification agent is checking sufficiency
    REGATHERING = "re-gathering"   # evidence was insufficient → agents retry
    SYNTHESIZING = "synthesizing"  # reviewer is composing the final answer
    DONE        = "done"           # final answer is ready


@dataclass
class EvidenceFragment:
    """A single piece of evidence posted by an agent."""
    id: str                         # e.g. "commit:1a2b3c4d5e", "issue:42"
    source_agent: str               # which agent posted this
    evidence_type: str              # "commit" | "issue" | "pr" | "review" | "code" | "doc"
    title: str                      # short label
    content: str                    # full evidence text
    confidence: float               # 0.0 – 1.0  (retrieval score normalised)
    url: str = ""                   # link to GitHub
    date: str = ""                  # ISO date string
    files: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class AgentLog:
    """One observable action taken by an agent (for the observability pipeline)."""
    agent: str
    action: str                     # e.g. "search_commits", "verify", "reason"
    detail: str = ""                # free-form detail / LLM reasoning trace
    duration_ms: float = 0.0
    tokens_used: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class Blackboard:
    """Thread-safe shared workspace for a single query.

    Usage::

        bb = Blackboard(question="Why was auth redesigned?", repo_slug="seL4__seL4")
        bb.post_evidence("history_rag", fragments)
        all_evidence = bb.get_evidence()
        bb.set_status(BlackboardStatus.VERIFYING)
    """

    def __init__(self, question: str, repo_slug: str, run_id: str | None = None):
        self.run_id: str = run_id or uuid.uuid4().hex[:12]
        self.question: str = question
        self.repo_slug: str = repo_slug
        self.status: BlackboardStatus = BlackboardStatus.GATHERING
        self.created_at: str = datetime.now(timezone.utc).isoformat()

        # Evidence store — keyed by evidence id to avoid duplicates
        self._evidence: dict[str, EvidenceFragment] = {}

        # Verification results (filled by the Evidence Verification Agent)
        self.verification_result: dict[str, Any] | None = None

        # Final answer (filled by the Reviewer Agent)
        self.final_answer: str | None = None

        # Observability log
        self._logs: list[AgentLog] = []

        # Re-retrieval queries suggested by the verification agent
        self.retry_queries: list[str] = []

        # How many re-gathering rounds have happened
        self.retry_count: int = 0
        self.max_retries: int = 2   # prevent infinite loops

        # Thread safety
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    #  Evidence operations                                                #
    # ------------------------------------------------------------------ #

    def post_evidence(self, agent_name: str, fragments: list[EvidenceFragment]) -> int:
        """Post one or more evidence fragments. Returns the number of *new* fragments added."""
        added = 0
        with self._lock:
            for frag in fragments:
                frag.source_agent = agent_name
                if frag.id not in self._evidence:
                    self._evidence[frag.id] = frag
                    added += 1
                else:
                    # Update if the new fragment has higher confidence
                    if frag.confidence > self._evidence[frag.id].confidence:
                        self._evidence[frag.id] = frag
                        added += 1
        return added

    def get_evidence(self, *, filter_type: str | None = None,
                     filter_agent: str | None = None,
                     min_confidence: float = 0.0) -> list[EvidenceFragment]:
        """Return evidence fragments, optionally filtered, sorted by confidence (highest first)."""
        with self._lock:
            frags = list(self._evidence.values())
        if filter_type:
            frags = [f for f in frags if f.evidence_type == filter_type]
        if filter_agent:
            frags = [f for f in frags if f.source_agent == filter_agent]
        if min_confidence > 0:
            frags = [f for f in frags if f.confidence >= min_confidence]
        return sorted(frags, key=lambda f: f.confidence, reverse=True)

    def get_evidence_ids(self) -> list[str]:
        """Return a list of all evidence IDs currently on the Blackboard."""
        with self._lock:
            return list(self._evidence.keys())

    def evidence_count(self) -> int:
        with self._lock:
            return len(self._evidence)

    def clear_evidence(self) -> None:
        """Remove all evidence (used before a full re-gathering round)."""
        with self._lock:
            self._evidence.clear()

    # ------------------------------------------------------------------ #
    #  Status management                                                  #
    # ------------------------------------------------------------------ #

    def set_status(self, status: BlackboardStatus) -> None:
        with self._lock:
            self.status = status

    def get_status(self) -> BlackboardStatus:
        with self._lock:
            return self.status

    def can_retry(self) -> bool:
        """Check whether another re-gathering round is allowed."""
        return self.retry_count < self.max_retries

    def start_retry(self, queries: list[str]) -> None:
        """Transition to re-gathering with new queries from the verification agent."""
        with self._lock:
            self.retry_count += 1
            self.retry_queries = queries
            self.status = BlackboardStatus.REGATHERING

    # ------------------------------------------------------------------ #
    #  Observability logging                                              #
    # ------------------------------------------------------------------ #

    def log(self, agent: str, action: str, detail: str = "",
            duration_ms: float = 0.0, tokens_used: int = 0) -> None:
        entry = AgentLog(agent=agent, action=action, detail=detail,
                         duration_ms=duration_ms, tokens_used=tokens_used)
        with self._lock:
            self._logs.append(entry)

    def get_logs(self) -> list[AgentLog]:
        with self._lock:
            return list(self._logs)

    # ------------------------------------------------------------------ #
    #  Serialisation (for API responses and evaluation logging)           #
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict[str, Any]:
        evidence = self.get_evidence()
        return {
            "run_id": self.run_id,
            "question": self.question,
            "repo": self.repo_slug,
            "status": self.status.value,
            "created_at": self.created_at,
            "retry_count": self.retry_count,
            "evidence_count": len(evidence),
            "evidence": [
                {
                    "id": e.id,
                    "source_agent": e.source_agent,
                    "type": e.evidence_type,
                    "title": e.title,
                    "confidence": round(e.confidence, 4),
                    "url": e.url,
                    "date": e.date,
                    "snippet": e.content[:500],
                }
                for e in evidence
            ],
            "verification": self.verification_result,
            "final_answer": self.final_answer,
            "logs": [
                {
                    "agent": log.agent,
                    "action": log.action,
                    "detail": log.detail[:300],
                    "duration_ms": round(log.duration_ms, 1),
                    "tokens_used": log.tokens_used,
                    "timestamp": log.timestamp,
                }
                for log in self.get_logs()
            ],
        }

    def __repr__(self) -> str:
        return (f"Blackboard(run={self.run_id}, status={self.status.value}, "
                f"evidence={self.evidence_count()}, retries={self.retry_count})")
