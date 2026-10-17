"""Unit and dev-set tests for the Orchestrator / Manager Agent."""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from parul_aggarwal.orchestrator.agent import OrchestratorAgent, MAX_ROUNDS
from parul_aggarwal.orchestrator.schema import (
    OrchestrationPlan,
    Entities,
    PlanSubtask,
    validate_plan_dict,
    create_fallback_plan
)
from agents.blackboard import Blackboard


class TestOrchestratorSchema:
    def test_valid_plan(self):
        plan = {
            "run_id": "run-123",
            "round": 1,
            "intent": "rationale",
            "entities": {"files": ["main.c"], "functions": ["init"], "features": ["boot"]},
            "subtasks": [
                {"id": "t1", "assigned_agent": "code_analysis", "sub_query": "q1", "reason": "r1"}
            ],
            "parallel": True,
            "needs_clarification": False
        }
        valid, errs = validate_plan_dict(plan)
        assert valid is True
        assert len(errs) == 0

    def test_invalid_intent_rejected(self):
        plan = {
            "run_id": "run-123",
            "round": 1,
            "intent": "unknown_intent",
            "entities": {},
            "subtasks": [{"id": "t1", "assigned_agent": "code_analysis", "sub_query": "q", "reason": "r"}]
        }
        valid, errs = validate_plan_dict(plan)
        assert valid is False
        assert any("intent" in e for e in errs)

    def test_invalid_agent_rejected(self):
        plan = {
            "run_id": "run-123",
            "round": 1,
            "intent": "timeline",
            "entities": {},
            "subtasks": [{"id": "t1", "assigned_agent": "rogue_agent", "sub_query": "q", "reason": "r"}]
        }
        valid, errs = validate_plan_dict(plan)
        assert valid is False
        assert any("agent" in e for e in errs)

    def test_fallback_plan_generation(self):
        fb = create_fallback_plan("run-999", "What is happening?")
        assert fb.run_id == "run-999"
        assert fb.intent == "other"
        assert len(fb.subtasks) == 3
        valid, errs = validate_plan_dict(fb.to_dict())
        assert valid is True


class TestOrchestratorAgent:
    @pytest.fixture
    def agent(self):
        return OrchestratorAgent()

    def test_intent_classification_rationale(self, agent):
        q = "Why was PAE (Physical Address Extension) support removed from x86 in seL4?"
        plan = agent.plan(q, run_id="test-1")
        assert plan.intent == "rationale"
        assigned = [s.assigned_agent for s in plan.subtasks]
        assert "history_rag" in assigned
        assert "code_analysis" in assigned

    def test_intent_classification_timeline(self, agent):
        q = "When was SMP lock.c moved from the x86 architecture directory to the generic smp directory?"
        plan = agent.plan(q, run_id="test-2")
        assert plan.intent == "timeline"
        assigned = [s.assigned_agent for s in plan.subtasks]
        assert "history_rag" in assigned

    def test_intent_classification_authorship(self, agent):
        q = "Who authored the hardware IBRS and jump table Spectre mitigations in seL4 x86?"
        plan = agent.plan(q, run_id="test-3")
        assert plan.intent == "authorship"
        assigned = [s.assigned_agent for s in plan.subtasks]
        assert "history_rag" in assigned
        assert "issue_pr" in assigned

    def test_intent_word_boundaries_not_fooled_by_whole_or_whose(self, agent):
        """Regression test: 'whole' and 'whose' must not be classified as authorship."""
        q1 = "Describe the whole architecture of the kernel."
        assert agent.classify_intent_heuristic(q1) != "authorship"
        q2 = "Explain the rationale whose justification is missing."
        assert agent.classify_intent_heuristic(q2) == "rationale"

    def test_intent_classification_superseded(self, agent):
        q = "What implementation did moving the cache flush from untyped reset to retype supersede?"
        plan = agent.plan(q, run_id="test-4")
        assert plan.intent == "superseded_implementation"

    def test_replan_round_limit_and_no_nested_queries(self, agent):
        orig_q = "Why was PAE removed?"
        plan1 = agent.plan(orig_q, run_id="replan-1", round_num=1)
        assert plan1.round == 1
        plan2 = agent.replan(plan1, feedback="Missing commit diffs")
        assert plan2.round == 2
        # Verify query is not nested repeatedly
        assert not any("Analyze code structure: Analyze" in st.sub_query for st in plan2.subtasks)
        plan3 = agent.replan(plan2, feedback="Still missing commit diffs")
        assert plan3.round == MAX_ROUNDS

    def test_determinism(self, agent):
        q = "Why was PAE support removed from x86 in seL4?"
        p1 = agent.plan(q, run_id="det-1")
        p2 = agent.plan(q, run_id="det-1")
        p3 = agent.plan(q, run_id="det-1")
        assert p1.intent == p2.intent == p3.intent
        assert [s.assigned_agent for s in p1.subtasks] == [s.assigned_agent for s in p2.subtasks] == [s.assigned_agent for s in p3.subtasks]

    def test_blackboard_logging(self, agent):
        bb = Blackboard(question="Why was PAE removed?", repo_slug="seL4__seL4")
        plan = agent.plan(bb.question, run_id=bb.run_id, blackboard=bb)
        logs = bb.get_logs()
        assert len(logs) > 0
        assert any(l.agent == "orchestrator" for l in logs)

    def test_dev_set_intent_accuracy(self, agent):
        """Run through the dev questions from questions.jsonl and verify intent accuracy >= 85%."""
        questions_path = Path("parul_aggarwal/eval_set/questions.jsonl")
        if not questions_path.exists():
            pytest.skip("questions.jsonl not found")

        dev_records = []
        with open(questions_path, "r", encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                if rec.get("split") == "dev":
                    dev_records.append(rec)

        correct = 0
        for rec in dev_records:
            plan = agent.plan(rec["question"], run_id=f"eval-{rec['qid']}")
            if plan.intent == rec["expected_intent"]:
                correct += 1

        accuracy = correct / len(dev_records)
        assert accuracy >= 0.85, f"Dev intent accuracy was {accuracy:.2f}, expected >= 0.85"

    def test_entity_extraction_does_not_invent_stop_words(self, agent):
        """Verify 'When' is not extracted as a function, and 'Physical'/'Extension' not extracted for PAE."""
        q1 = "When was SMP lock.c moved from the x86 architecture directory to the generic smp directory?"
        e1 = agent.extract_entities_heuristic(q1)
        assert "lock.c" in e1.files
        assert "SMP" in e1.features
        assert "When" not in e1.functions
        assert "when" not in [f.lower() for f in e1.functions]
        assert "When" not in e1.features

        q2 = "Why was PAE (Physical Address Extension) support removed from x86 in seL4?"
        e2 = agent.extract_entities_heuristic(q2)
        assert "PAE" in e2.features
        assert "Physical" not in e2.features
        assert "Extension" not in e2.features
        assert "Physical" not in e2.functions
        assert "Extension" not in e2.functions

    def test_llm_parse_error_triggers_retry(self):
        """Verify that a JSON parse error on attempt 1 retries with LLM rather than immediately falling back."""
        call_count = 0
        valid_plan_json = json.dumps({
            "run_id": "test-retry",
            "round": 1,
            "intent": "rationale",
            "entities": {"files": ["vspace_pae.c"], "functions": [], "features": ["PAE"]},
            "subtasks": [
                {"id": "t1", "assigned_agent": "history_rag", "sub_query": "Find PAE removal commit", "reason": "Examine commit"}
            ],
            "parallel": True,
            "needs_clarification": False
        })

        def mock_llm(system: str, prompt: str) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "{invalid_json_missing_quotes"
            return valid_plan_json

        agent = OrchestratorAgent(llm_fn=mock_llm)
        plan = agent.plan("Why was PAE removed?", run_id="test-retry")
        assert call_count == 2, f"Expected 2 LLM calls (1 initial + 1 retry), got {call_count}"
        assert plan.intent == "rationale"
        assert plan.subtasks[0].assigned_agent == "history_rag"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
