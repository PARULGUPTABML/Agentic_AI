"""Orchestrator / Manager Agent implementation.

Analyzes question intent, extracts entities dynamically, builds execution plan,
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

from orchestrator.schema import (
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

INTENT_PATTERNS = [
    ("superseded_implementation", re.compile(r"\b(supersede|superseded|superseding|replace|replaced|deprecated|predecessor|prior\s+to|what\s+did\b.+\bsupersede)\b", re.IGNORECASE)),
    ("authorship", re.compile(r"\b(who|author|authored|spearheaded|created\s+by|approved\s+by|reviewed\s+by)\b", re.IGNORECASE)),
    ("timeline", re.compile(r"\b(when|timeline|date|year|sequence|introduced|merged|migrated)\b", re.IGNORECASE)),
    ("rationale", re.compile(r"\b(why|reason|motivation|purpose|rationale)\b", re.IGNORECASE)),
]

STOP_WORDS = {
    "when", "what", "why", "who", "where", "which", "how", "whose", "whom",
    "physical", "extension", "describe", "explain", "please", "check", "fix",
    "update", "after", "before", "during", "kernel", "commit", "author", "authored",
    "support", "directory", "architecture", "implementation", "addition", "removal",
    "code", "generic", "independent", "platform", "platforms", "changes", "change"
}


class OrchestratorAgent:
    def __init__(self, prompt_path: Optional[Path] = None, llm_fn: Optional[Callable[[str, str], str]] = None):
        self.prompt_path = prompt_path or PROMPT_PATH
        self.system_prompt = self._load_prompt()
        self.max_rounds = MAX_ROUNDS
        self.max_llm_calls = MAX_LLM_CALLS
        self.llm_fn = llm_fn

    def _generate_llm(self, system: str, prompt: str) -> str:
        if self.llm_fn:
            return self.llm_fn(system, prompt)
        from agents._llm_retry import generate_with_retry
        return generate_with_retry(system, prompt)

    def _load_prompt(self) -> str:
        if self.prompt_path.exists():
            return self.prompt_path.read_text(encoding="utf-8")
        return "You are an Orchestrator Agent. Decompose questions into subtasks for code_analysis, history_rag, issue_pr."

    def classify_intent_heuristic(self, question: str) -> str:
        """Classify intent using strict word-boundary matching."""
        for intent_name, pattern in INTENT_PATTERNS:
            if pattern.search(question):
                return intent_name
        return "other"

    def extract_entities_heuristic(self, question: str) -> Entities:
        """Extract files, symbols, and architectural features dynamically without stop-words."""
        # File paths (e.g. src/kernel/untyped.c or lock.c)
        file_matches = re.findall(r"\b[\w\.\-/]+\.(?:c|h|S|py|json|md|txt|yml|yaml|cmake|bf|thy)\b", question)
        
        # Function-like and type identifiers (seL4_*, c_handle_*, camelCase, or snake_case symbols)
        raw_func_matches = re.findall(r"\b(?:seL4_\w+|c_handle_\w+|sys_\w+|[a-z]+[A-Z]\w+)\b", question)
        func_matches = [f for f in raw_func_matches if f.lower() not in STOP_WORDS]

        # General architectural acronyms / uppercase tokens (e.g., SMP, PAE, IOMMU, IOAPIC, ASID, GIC, MCS, TCB)
        raw_feature_matches = re.findall(r"\b[A-Z]{2,}[0-9]*\b", question)
        feature_matches = [f for f in raw_feature_matches if f.lower() not in STOP_WORDS]

        return Entities(
            files=list(set(file_matches)),
            functions=list(set(func_matches)),
            features=list(set(feature_matches)),
            time_range=None,
        )

    def plan_rule_based(
        self,
        question: str,
        run_id: str,
        round_num: int = 1,
        feedback: Optional[str] = None,
        original_question: Optional[str] = None
    ) -> OrchestrationPlan:
        orig_q = original_question or question
        intent = self.classify_intent_heuristic(orig_q)
        entities = self.extract_entities_heuristic(orig_q)

        subtasks: List[PlanSubtask] = []
        if intent == "rationale":
            subtasks = [
                PlanSubtask("t1", "code_analysis", f"Analyze code structure and implementation trade-offs for: {orig_q}", "Examine implementation specifics"),
                PlanSubtask("t2", "history_rag", f"Search commit logs, diffs, and rationale for: {orig_q}", "Find commit messages explaining the change"),
                PlanSubtask("t3", "issue_pr", f"Find PR debates, issue discussions, and architectural reviews for: {orig_q}", "Retrieve design discussion from PRs and issues")
            ]
        elif intent == "timeline":
            subtasks = [
                PlanSubtask("t1", "history_rag", f"Find commits and merge dates for: {orig_q}", "Determine exact commit and merge timestamps"),
                PlanSubtask("t2", "code_analysis", f"Check version tags, changelog, and header definitions for: {orig_q}", "Confirm API introduction version")
            ]
        elif intent == "authorship":
            subtasks = [
                PlanSubtask("t1", "history_rag", f"Find commit authors, committers, and co-authors for: {orig_q}", "Extract author metadata from git commits"),
                PlanSubtask("t2", "issue_pr", f"Find pull request author, reviewers, and approvers for: {orig_q}", "Identify PR reviewers and approving team members")
            ]
        elif intent == "superseded_implementation":
            subtasks = [
                PlanSubtask("t1", "history_rag", f"Find older commits and removed code superseded by: {orig_q}", "Trace git history before and during replacement"),
                PlanSubtask("t2", "code_analysis", f"Inspect legacy headers, deprecated functions, and replacements for: {orig_q}", "Compare previous implementation against new one")
            ]
        else:
            return create_fallback_plan(run_id, orig_q, round_num)

        if feedback:
            subtasks.append(
                PlanSubtask(f"t_replan_{round_num}", "history_rag", f"Targeted search addressing gap: {feedback}", "Refined query targeting missing evidence")
            )

        return OrchestrationPlan(
            run_id=run_id,
            round=round_num,
            intent=intent,
            entities=entities,
            subtasks=subtasks,
            original_question=orig_q,
            parallel=True,
            needs_clarification=False
        )

    def plan(
        self,
        question: str,
        run_id: str,
        round_num: int = 1,
        feedback: Optional[str] = None,
        blackboard: Any = None,
        original_question: Optional[str] = None
    ) -> OrchestrationPlan:
        start_time = time.time()
        tokens_used = 0
        llm_calls = 0
        error_msg = ""
        orig_q = original_question or question
        plan_obj: Optional[OrchestrationPlan] = None

        use_llm = bool(self.llm_fn) or os.environ.get("LLM_PROVIDER", "").lower() not in ("mock", "none", "")

        if use_llm:
            try:
                from agents._llm_retry import generate_with_retry
                user_prompt = f"Question: {orig_q}\nRound: {round_num}\nFeedback: {feedback or 'None'}\nGenerate strict JSON plan conforming to schema."
                
                # Attempt 1
                raw_out = self._generate_llm(self.system_prompt, user_prompt)
                llm_calls += 1
                tokens_used += len(raw_out) // 4

                clean_json = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_out.strip(), flags=re.MULTILINE)
                try:
                    parsed = json.loads(clean_json)
                    valid, errs = validate_plan_dict(parsed)
                except Exception as parse_err:
                    valid = False
                    errs = [f"JSON parse error: {parse_err}"]

                # Retry on parse error or validation error
                if not valid and llm_calls < self.max_llm_calls:
                    retry_prompt = f"{user_prompt}\n\nPrevious attempt failed with errors: {errs}. Fix and return strict valid JSON only."
                    raw_retry = self._generate_llm(self.system_prompt, retry_prompt)
                    llm_calls += 1
                    tokens_used += len(raw_retry) // 4
                    clean_retry = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_retry.strip(), flags=re.MULTILINE)
                    try:
                        parsed = json.loads(clean_retry)
                        valid, errs = validate_plan_dict(parsed)
                    except Exception as retry_parse_err:
                        valid = False
                        errs = [f"JSON parse error on retry: {retry_parse_err}"]

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
                        original_question=orig_q,
                        parallel=parsed.get("parallel", True),
                        needs_clarification=parsed.get("needs_clarification", False)
                    )
                else:
                    logger.warning("LLM plan failed validation after retry (%s). Using rule-based generator.", errs)
                    plan_obj = self.plan_rule_based(question, run_id, round_num, feedback, orig_q)
            except Exception as e:
                logger.warning("LLM call failed (%s). Falling back to rule-based planner.", e)
                error_msg = str(e)
                plan_obj = self.plan_rule_based(question, run_id, round_num, feedback, orig_q)
        else:
            plan_obj = self.plan_rule_based(question, run_id, round_num, feedback, orig_q)

        duration_ms = (time.time() - start_time) * 1000

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
            return previous_plan

        orig_q = previous_plan.original_question or (previous_plan.subtasks[0].sub_query if previous_plan.subtasks else "query")
        return self.plan(
            question=orig_q,
            run_id=previous_plan.run_id,
            round_num=next_round,
            feedback=feedback,
            blackboard=blackboard,
            original_question=orig_q
        )
