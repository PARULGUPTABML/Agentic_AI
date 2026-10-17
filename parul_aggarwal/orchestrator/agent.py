"""Orchestrator / Manager Agent implementation.

Analyzes question intent, extracts entities, builds execution plan,
delegates sub-tasks to agents, and handles re-planning upon insufficiency feedback.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from parul_aggarwal.orchestrator.schema import (
    ALLOWED_AGENTS,
    ALLOWED_INTENTS,
    Entities,
    OrchestrationPlan,
    PlanSubtask,
    create_fallback_plan,
    validate_plan_dict,
)

logger = logging.getLogger(__name__)

MAX_ROUNDS = 2
MAX_LLM_CALLS = 4

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "orchestrator_system.md"


class OrchestratorAgent:
    def __init__(self, prompt_path: Optional[Path] = None):
        self.prompt_path = prompt_path or PROMPT_PATH
        self.system_prompt = self._load_prompt()
        self.max_rounds = MAX_ROUNDS
        self.max_llm_calls = MAX_LLM_CALLS

    def _load_prompt(self) -> str:
        if self.prompt_path.exists():
            return self.prompt_path.read_text(encoding="utf-8")
        return "You are an Orchestrator Agent. Decompose questions into subtasks for code_analysis, history_rag, issue_pr."

    def classify_intent_heuristic(self, question: str) -> str:
        q = question.lower()
        if any(w in q for w in ["why", "reason", "motivation", "purpose", "rationale"]):
            return "rationale"
        if any(w in q for w in ["when", "timeline", "date", "year", "sequence", "introduced", "merged", "migrated"]):
            return "timeline"
        if any(w in q for w in ["who", "author", "authored", "spearheaded", "created by", "approved by", "reviewed by"]):
            return "authorship"
        if any(w in q for w in ["supersede", "replace", "deprecated", "what did", "prior to", "predecessor"]):
            return "superseded_implementation"
        return "other"

    def extract_entities_heuristic(self, question: str) -> Entities:
        # File paths
        file_matches = re.findall(r"[\w\./\-]+\.(?:c|h|S|py|json|md|txt|yml|yaml|thy)", question)
        # Function-like identifiers
        func_matches = re.findall(r"\b(?:seL4_\w+|[a-z]+[A-Z]\w+|sys_\w+|c_handle_\w+)\b", question)
        # Features / keywords
        feature_candidates = ["untyped memory", "fastpath IPC", "MCS", "scheduler", "BKL", "IOAPIC", "IOMMU", "ASID", "CNode", "RISC-V", "AArch64", "PMP"]
        features = [f for f in feature_candidates if f.lower() in question.lower()]

        return Entities(
            files=list(set(file_matches)),
            functions=list(set(func_matches)),
            features=features,
            time_range=None,
        )

    def plan_rule_based(self, question: str, run_id: str, round_num: int = 1, feedback: Optional[str] = None) -> OrchestrationPlan:
        intent = self.classify_intent_heuristic(question)
        entities = self.extract_entities_heuristic(question)

        subtasks: List[PlanSubtask] = []
        if intent == "rationale":
            subtasks = [
                PlanSubtask("t1", "code_analysis", f"Analyze code structure and implementation trade-offs for: {question}", "Examine implementation specifics"),
                PlanSubtask("t2", "history_rag", f"Search commit logs, diffs, and rationale for: {question}", "Find commit messages explaining the change"),
                PlanSubtask("t3", "issue_pr", f"Find PR debates, issue discussions, and architectural reviews for: {question}", "Retrieve design discussion from PRs and issues")
            ]
        elif intent == "timeline":
            subtasks = [
                PlanSubtask("t1", "history_rag", f"Find commits and merge dates for: {question}", "Determine exact commit and merge timestamps"),
                PlanSubtask("t2", "code_analysis", f"Check version tags, changelog, and header definitions for: {question}", "Confirm API introduction version")
            ]
        elif intent == "authorship":
            subtasks = [
                PlanSubtask("t1", "history_rag", f"Find commit authors, committers, and co-authors for: {question}", "Extract author metadata from git commits"),
                PlanSubtask("t2", "issue_pr", f"Find pull request author, reviewers, and approvers for: {question}", "Identify PR reviewers and approving team members")
            ]
        elif intent == "superseded_implementation":
            subtasks = [
                PlanSubtask("t1", "history_rag", f"Find older commits and removed code superseded by: {question}", "Trace git history before and during replacement"),
                PlanSubtask("t2", "code_analysis", f"Inspect legacy headers, deprecated functions, and replacements for: {question}", "Compare previous implementation against new one")
            ]
        else:
            return create_fallback_plan(run_id, question, round_num)

        if feedback:
            # Re-plan adaptation: append targeted query for the specific gap
            subtasks.append(
                PlanSubtask(f"t_replan_{round_num}", "history_rag", f"Targeted search addressing feedback gap: {feedback}", "Refined query targeting missing evidence")
            )

        return OrchestrationPlan(
            run_id=run_id,
            round=round_num,
            intent=intent,
            entities=entities,
            subtasks=subtasks,
            parallel=True,
            needs_clarification=False
        )

    def plan(self, question: str, run_id: str, round_num: int = 1, feedback: Optional[str] = None, blackboard: Any = None) -> OrchestrationPlan:
        start_time = time.time()
        tokens_used = 0
        llm_calls = 0
        error_msg = ""
        plan_obj: Optional[OrchestrationPlan] = None

        # Check LLM availability
        use_llm = os.environ.get("LLM_PROVIDER", "").lower() not in ("mock", "none", "")

        if use_llm:
            try:
                from agents._llm_retry import generate_with_retry
                user_prompt = f"Question: {question}\nRound: {round_num}\nFeedback: {feedback or 'None'}\nGenerate strict JSON plan conforming to schema."
                raw_out = generate_with_retry(self.system_prompt, user_prompt)
                llm_calls += 1
                tokens_used += len(raw_out) // 4

                # Strip markdown fences if present
                clean_json = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_out.strip(), flags=re.MULTILINE)
                parsed = json.loads(clean_json)
                parsed["run_id"] = run_id
                parsed["round"] = round_num

                valid, errs = validate_plan_dict(parsed)
                if valid:
                    ent_dict = parsed.get("entities", {})
                    plan_obj = OrchestrationPlan(
                        run_id=run_id,
                        round=round_num,
                        intent=parsed["intent"],
                        entities=Entities(
                            files=ent_dict.get("files", []),
                            functions=ent_dict.get("functions", []),
                            features=ent_dict.get("features", []),
                            time_range=ent_dict.get("time_range")
                        ),
                        subtasks=[PlanSubtask(**s) for s in parsed["subtasks"]],
                        parallel=parsed.get("parallel", True),
                        needs_clarification=parsed.get("needs_clarification", False)
                    )
                else:
                    logger.warning("LLM plan failed validation: %s. Using rule-based generator.", errs)
                    plan_obj = self.plan_rule_based(question, run_id, round_num, feedback)
            except Exception as e:
                logger.warning("LLM call failed (%s). Falling back to rule-based planner.", e)
                error_msg = str(e)
                plan_obj = self.plan_rule_based(question, run_id, round_num, feedback)
        else:
            plan_obj = self.plan_rule_based(question, run_id, round_num, feedback)

        duration_ms = (time.time() - start_time) * 1000

        # Log to blackboard if provided
        if blackboard and hasattr(blackboard, "log"):
            blackboard.log(
                agent="orchestrator",
                action="plan",
                detail=f"Round {round_num} plan for intent '{plan_obj.intent}' with {len(plan_obj.subtasks)} subtasks. {error_msg}".strip(),
                duration_ms=duration_ms,
                tokens_used=tokens_used
            )

        return plan_obj

    def replan(self, previous_plan: OrchestrationPlan, feedback: str, blackboard: Any = None) -> OrchestrationPlan:
        next_round = previous_plan.round + 1
        if next_round > self.max_rounds:
            logger.info("Reached MAX_ROUNDS (%d). Terminating re-planning.", self.max_rounds)
            # Return current plan marked as not needing further re-plan
            return previous_plan

        # Re-plan targeting missing gaps
        return self.plan(
            question=previous_plan.subtasks[0].sub_query if previous_plan.subtasks else "Software history query",
            run_id=previous_plan.run_id,
            round_num=next_round,
            feedback=feedback,
            blackboard=blackboard
        )
