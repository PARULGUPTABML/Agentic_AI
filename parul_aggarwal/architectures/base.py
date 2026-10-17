"""Common base interface and agent registry for architecture variants."""
from __future__ import annotations

import abc
import time
from typing import Any, Callable, Dict, List, Optional

from agents.blackboard import Blackboard, BlackboardStatus, EvidenceFragment
from parul_aggarwal.orchestrator.agent import OrchestratorAgent
from parul_aggarwal.reviewer.agent import ReviewerAgent


class StubRetrievalAgent:
    """Deterministic stub retrieval agent for testing coordination architectures."""
    def __init__(self, name: str):
        self.name = name

    def run(self, query: str, repo_slug: str = "seL4__seL4") -> List[EvidenceFragment]:
        return [
            EvidenceFragment(
                id=f"commit:stub_{self.name[:4]}_1",
                source_agent=self.name,
                evidence_type="commit",
                title=f"Commit from {self.name}",
                content=f"Implementation evidence retrieved by {self.name} for query: {query}",
                confidence=0.88
            )
        ]


class StubVerificationAgent:
    """Deterministic stub verification agent for testing architectures."""
    def __init__(self, succeed_on_round: int = 1):
        self.succeed_on_round = succeed_on_round
        self.calls = 0

    def run(self, blackboard: Blackboard) -> Dict[str, Any]:
        self.calls += 1
        sufficient = (self.calls >= self.succeed_on_round) or (blackboard.retry_count >= blackboard.max_retries)
        gaps = [] if sufficient else ["Additional commit history needed"]
        res = {
            "sufficient": sufficient,
            "confidence": 0.9 if sufficient else 0.4,
            "gaps": gaps,
            "suggested_queries": ["Detailed commit diffs"] if not sufficient else []
        }
        blackboard.verification_result = res
        return res


class Architecture(abc.ABC):
    """Abstract base class for multi-agent coordination architectures."""
    name: str = "base"

    def __init__(
        self,
        orchestrator: Optional[OrchestratorAgent] = None,
        reviewer: Optional[ReviewerAgent] = None,
        max_rounds: int = 2,
        timeout_seconds: float = 30.0
    ):
        self.orchestrator = orchestrator or OrchestratorAgent()
        self.reviewer = reviewer or ReviewerAgent()
        self.max_rounds = max_rounds
        self.timeout_seconds = timeout_seconds

    @abc.abstractmethod
    def run(
        self,
        question: str,
        run_id: str,
        repo_slug: str = "seL4__seL4",
        retrieval_agents: Optional[Dict[str, Any]] = None,
        verification_agent: Optional[Any] = None
    ) -> Dict[str, Any]:
        """Execute the query under this architecture pattern and return results + metrics."""
        pass

    def _format_run_output(
        self,
        run_id: str,
        question: str,
        blackboard: Blackboard,
        reviewer_res: Dict[str, Any],
        start_time: float,
        coordination_time_ms: float,
        agents_invoked: List[str],
        rounds: int
    ) -> Dict[str, Any]:
        total_latency_ms = (time.time() - start_time) * 1000
        return {
            "architecture": self.name,
            "run_id": run_id,
            "question": question,
            "answer": reviewer_res.get("rendered", ""),
            "confidence": reviewer_res.get("confidence", "low"),
            "gaps": reviewer_res.get("gaps", []),
            "citation_check": reviewer_res.get("citation_check", {}),
            "rounds": rounds,
            "agents_invoked": agents_invoked,
            "evidence_count": blackboard.evidence_count(),
            "total_latency_ms": round(total_latency_ms, 2),
            "coordination_overhead_ms": round(coordination_time_ms, 2),
            "agent_latency_ms": round(max(0.0, total_latency_ms - coordination_time_ms), 2),
            "logs": [
                {
                    "agent": l.agent,
                    "action": l.action,
                    "duration_ms": l.duration_ms,
                    "tokens": l.tokens_used
                }
                for l in blackboard.get_logs()
            ]
        }
