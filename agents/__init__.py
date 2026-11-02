"""ReadMind Agents Package.

Provides all 6 specialized multi-agent architectural roles:
  1. OrchestratorAgent (orchestrator_agent.py): Intent parsing & task planning
  2. CodeAnalysisAgent (code_analysis_agent.py): AST & static code symbol retrieval
  3. HistoryRAGAgent (history_rag_agent.py): Git commit history & blame traversal
  4. IssuePRAgent (issue_pr_agent.py): GitHub issues, PR discussions, and code reviews
  5. EvidenceVerificationAgent (evidence_verification_agent.py): CoT gatekeeper & sufficiency auditor
  6. ReviewerAgent (reviewer_agent.py): Grounded citation synthesis & gap reporting

Plus 2-in-1 Merged and Ablation Agents (merged_agents.py):
  - CodeAndHistoryMergedAgent
  - HistoryAndIssuePRMergedAgent
  - OrchestratorVerifierMergedAgent
  - VerifierReviewerMergedAgent
  - BypassedVerificationAgent
  - MonolithicSingleAgent

Shared coordination substrate:
  - Blackboard (blackboard.py): Shared read/write memory
  - MessageBus (messaging.py): Decoupled pub/sub event communications
  - run_multi_agent (multi_agent_pipeline.py): Deterministic sequential pipeline
"""
from __future__ import annotations

from agents.orchestrator_agent import OrchestratorAgent
from agents.code_analysis_agent import CodeAnalysisAgent
from agents.history_rag_agent import HistoryRAGAgent
from agents.issue_pr_agent import IssuePRAgent
from agents.evidence_verification_agent import EvidenceVerificationAgent
from agents.reviewer_agent import ReviewerAgent

from agents.merged_agents import (
    CodeAndHistoryMergedAgent,
    HistoryAndIssuePRMergedAgent,
    OrchestratorVerifierMergedAgent,
    VerifierReviewerMergedAgent,
    BypassedVerificationAgent,
    MonolithicSingleAgent,
    CASE_DEFINITIONS,
    run_ablation_case,
)

from agents.blackboard import (
    Blackboard,
    BlackboardStatus,
    EvidenceFragment,
    AgentLog,
)
from agents.messaging import Message, MessageBus
from agents.multi_agent_pipeline import run_multi_agent

__all__ = [
    # 6 Core Agents
    "OrchestratorAgent",
    "CodeAnalysisAgent",
    "HistoryRAGAgent",
    "IssuePRAgent",
    "EvidenceVerificationAgent",
    "ReviewerAgent",
    # 2-in-1 Merged and Ablated Agents
    "CodeAndHistoryMergedAgent",
    "HistoryAndIssuePRMergedAgent",
    "OrchestratorVerifierMergedAgent",
    "VerifierReviewerMergedAgent",
    "BypassedVerificationAgent",
    "MonolithicSingleAgent",
    "CASE_DEFINITIONS",
    "run_ablation_case",
    # Coordination & Memory
    "Blackboard",
    "BlackboardStatus",
    "EvidenceFragment",
    "AgentLog",
    "Message",
    "MessageBus",
    "run_multi_agent",
]
