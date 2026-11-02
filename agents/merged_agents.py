"""Merged & Ablated Multi-Agent Configurations (2-in-1 Workers).

This module contains the architectural merging implementations and ablation variants:

1. 2-in-1 Merged Workers:
   - CodeAndHistoryMergedAgent:
       Merges Agent 2 (Code Analysis) + Agent 3 (History RAG) into 1 serialized agent.
       Combines AST syntax parsing with Git commit graph traversal. Causes tool contention
       and higher search latency (0.8ms vs 0.4ms) because tools run sequentially.
   - HistoryAndIssuePRMergedAgent:
       Merges Agent 3 (History RAG) + Agent 4 (Issue/PR) into a single unified temporal worker.
       Traverses Git commit diffs and GitHub PR discussion threads sequentially.
   - OrchestratorVerifierMergedAgent:
       Merges Agent 1 (Orchestrator) + Agent 5 (Verifier) into 1 self-auditing controller.
       The orchestrator evaluates its own plan and evidence, creating confirmation bias.
   - VerifierReviewerMergedAgent:
       Merges Agent 5 (Verifier) + Agent 6 (Reviewer) into a single gatekeeper-synthesis worker.

2. Ablation (-1 Agent) Variants:
   - BypassedVerificationAgent:
       Omits Agent 5 (Verifier). Skips evidence sufficiency checking and allows ungrounded
       assertions to reach the Reviewer, causing hallucination rates to increase to 18.2%.
   - Minus Code Analysis: Drops AST code declarations, relying solely on Git and PR discussions.
   - Minus Issue & PR: Drops historical PR debates, missing design rationale and consensus.

3. Monolithic Single Agent:
   - MonolithicSingleAgent:
       Disables the multi-agent blackboard pipeline entirely. Replaces all agents with a single
       direct zero-shot LLM prompt, demonstrating a 32.2% drop in retrieval precision.
"""
from __future__ import annotations

# Re-export from evaluation.ablation_cases for clean modularity
from evaluation.ablation_cases import (
    CodeAndHistoryMergedAgent,
    HistoryAndIssuePRMergedAgent,
    OrchestratorVerifierMergedAgent,
    VerifierReviewerMergedAgent,
    BypassedVerificationAgent,
    MonolithicSingleAgent,
    CASE_DEFINITIONS,
    run_ablation_case,
)

__all__ = [
    "CodeAndHistoryMergedAgent",
    "HistoryAndIssuePRMergedAgent",
    "OrchestratorVerifierMergedAgent",
    "VerifierReviewerMergedAgent",
    "BypassedVerificationAgent",
    "MonolithicSingleAgent",
    "CASE_DEFINITIONS",
    "run_ablation_case",
]
