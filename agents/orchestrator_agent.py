"""Agent 1: Orchestrator / Intent Planning Agent.

Analyzes developer inquiries, identifies query intent (superseded implementation,
authorship, timeline, architectural rationale), extracts domain entities (functions,
structs, constants, files), formulates a task execution plan, and schedules
specialized worker agents.
"""
from __future__ import annotations

# Re-export core Orchestrator classes so this file serves as the canonical Agent 1 module
from orchestrator.agent import OrchestratorAgent, INTENT_PATTERNS, MAX_ROUNDS, MAX_LLM_CALLS
from orchestrator.schema import (
    ALLOWED_AGENTS,
    ALLOWED_INTENTS,
    Entities,
    OrchestrationPlan,
    PlanSubtask,
    create_fallback_plan,
    validate_plan_dict,
)

__all__ = [
    "OrchestratorAgent",
    "OrchestrationPlan",
    "PlanSubtask",
    "Entities",
    "ALLOWED_AGENTS",
    "ALLOWED_INTENTS",
    "create_fallback_plan",
    "validate_plan_dict",
    "INTENT_PATTERNS",
    "MAX_ROUNDS",
    "MAX_LLM_CALLS",
]
