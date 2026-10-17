"""Schema and validation for Orchestrator / Manager Agent plans."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

ALLOWED_INTENTS = {"rationale", "timeline", "authorship", "superseded_implementation", "other"}
ALLOWED_AGENTS = {"code_analysis", "history_rag", "issue_pr"}


@dataclass
class PlanSubtask:
    id: str
    assigned_agent: str
    sub_query: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Entities:
    files: List[str] = field(default_factory=list)
    functions: List[str] = field(default_factory=list)
    features: List[str] = field(default_factory=list)
    time_range: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OrchestrationPlan:
    run_id: str
    round: int
    intent: str
    entities: Entities
    subtasks: List[PlanSubtask]
    original_question: str = ""
    parallel: bool = True
    needs_clarification: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "round": self.round,
            "intent": self.intent,
            "entities": self.entities.to_dict(),
            "subtasks": [s.to_dict() for s in self.subtasks],
            "original_question": self.original_question,
            "parallel": self.parallel,
            "needs_clarification": self.needs_clarification,
        }


def validate_plan_dict(data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate a raw dictionary against the OrchestrationPlan schema."""
    errors = []
    if not isinstance(data, dict):
        return False, ["Plan must be a JSON dictionary"]

    for req in ["run_id", "round", "intent", "entities", "subtasks"]:
        if req not in data:
            errors.append(f"Missing required field: '{req}'")

    if "intent" in data and data["intent"] not in ALLOWED_INTENTS:
        errors.append(f"Invalid intent '{data['intent']}'. Allowed: {ALLOWED_INTENTS}")

    if "entities" in data and not isinstance(data["entities"], dict):
        errors.append("Field 'entities' must be a dictionary")

    subtasks = data.get("subtasks", [])
    if not isinstance(subtasks, list) or len(subtasks) == 0:
        errors.append("Field 'subtasks' must be a non-empty list")
    else:
        for idx, subtask in enumerate(subtasks):
            if not isinstance(subtask, dict):
                errors.append(f"Subtask at index {idx} must be a dictionary")
                continue
            for st_req in ["id", "assigned_agent", "sub_query", "reason"]:
                if st_req not in subtask:
                    errors.append(f"Subtask at index {idx} missing field: '{st_req}'")
            agent = subtask.get("assigned_agent")
            if agent and agent not in ALLOWED_AGENTS:
                errors.append(f"Subtask at index {idx} has invalid agent '{agent}'. Allowed: {ALLOWED_AGENTS}")

    return len(errors) == 0, errors


def create_fallback_plan(run_id: str, question: str, round_num: int = 1) -> OrchestrationPlan:
    """Create a safe fallback plan delegating to all three retrieval agents."""
    return OrchestrationPlan(
        run_id=run_id,
        round=round_num,
        intent="other",
        entities=Entities(),
        subtasks=[
            PlanSubtask(id="fb1", assigned_agent="code_analysis", sub_query=f"Code context for: {question}", reason="Fallback static analysis"),
            PlanSubtask(id="fb2", assigned_agent="history_rag", sub_query=f"Git commit history for: {question}", reason="Fallback history retrieval"),
            PlanSubtask(id="fb3", assigned_agent="issue_pr", sub_query=f"Issues and PRs for: {question}", reason="Fallback issue/PR search")
        ],
        original_question=question,
        parallel=True,
        needs_clarification=False
    )
