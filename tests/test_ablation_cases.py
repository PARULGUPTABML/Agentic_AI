"""Unit and integration tests for architectural ablation and agent-merging configurations."""
from __future__ import annotations

import pytest

from evaluation.ablation_cases import (
    CASE_DEFINITIONS,
    BypassedVerificationAgent,
    CodeAndHistoryMergedAgent,
    HistoryAndIssuePRMergedAgent,
    MonolithicSingleAgent,
    OrchestratorVerifierMergedAgent,
    VerifierReviewerMergedAgent,
    run_ablation_case,
)
from agents.blackboard import Blackboard


class TestAblationCases:
    def test_all_9_cases_registered(self):
        assert len(CASE_DEFINITIONS) == 9
        expected_keys = {
            "baseline_5_agent",
            "merge_code_history",
            "merge_history_issue_pr",
            "merge_orchestrator_verifier",
            "merge_verifier_reviewer",
            "minus_verification",
            "minus_code_analysis",
            "minus_issue_pr",
            "monolithic_single_agent"
        }
        assert set(CASE_DEFINITIONS.keys()) == expected_keys

    def test_code_and_history_merged_agent(self):
        agent = CodeAndHistoryMergedAgent()
        frags = agent.run("Why was lock.c moved?")
        assert len(frags) >= 2
        sources = {f.source_agent for f in frags}
        assert "code_analysis" in sources or "code_and_history_merged" in sources
        assert len(agent.last_trace) == 2
        tools = [t["tool"] for t in agent.last_trace]
        assert "merged_code_inspector" in tools
        assert "merged_history_search" in tools

    def test_history_and_issue_pr_merged_agent(self):
        agent = HistoryAndIssuePRMergedAgent()
        frags = agent.run("Spectre mitigation PR #420")
        assert len(frags) >= 2
        assert len(agent.last_trace) == 2
        tools = [t["tool"] for t in agent.last_trace]
        assert "merged_commit_search" in tools
        assert "merged_issue_pr_search" in tools

    def test_orchestrator_verifier_merged_agent(self):
        bb = Blackboard(question="Test question", repo_slug="seL4__seL4")
        merged = OrchestratorVerifierMergedAgent()

        # Planning
        plan = merged.plan("Why was lock.c moved?", "run-ov-1")
        assert len(plan.subtasks) > 0

        # Lenient verification on empty blackboard
        res = merged.run(bb)
        assert res["sufficient"] is False
        assert "No evidence" in res["gaps"][0]

        # Lenient verification with evidence (confirmation bias)
        from architectures.base import StubRetrievalAgent
        stub = StubRetrievalAgent("code_analysis")
        bb.post_evidence("code_analysis", stub.run("lock.c"))
        res_with_ev = merged.run(bb)
        assert res_with_ev["sufficient"] is True
        assert res_with_ev["confidence"] >= 0.8

    def test_bypassed_verification_agent(self):
        bb = Blackboard(question="Adversarial missing evidence", repo_slug="seL4__seL4")
        bv = BypassedVerificationAgent()
        res = bv.run(bb)
        assert res["sufficient"] is True
        assert len(res["gaps"]) == 0

    def test_monolithic_single_agent(self):
        mono = MonolithicSingleAgent()
        res = mono.run("Why was PAE removed?")
        assert res["configuration"] == "monolithic_single_agent"
        assert res["category"] == "Extreme Low"
        assert "Answer" in res["answer"]
        assert res["evidence_count"] >= 1
        assert res["total_latency_ms"] > 0

    @pytest.mark.parametrize("case_name", list(CASE_DEFINITIONS.keys()))
    def test_run_all_cases_end_to_end(self, case_name):
        q = "Why was SMP lock.c moved from the x86 architecture directory to generic smp?"
        res = run_ablation_case(case_name=case_name, question=q)

        assert res["case_name"] == case_name
        assert "configuration" in res
        assert "category" in res
        assert len(res["answer"]) > 10
        assert res["total_latency_ms"] >= 0

        if case_name == "minus_code_analysis":
            # Code analysis was excluded
            assert "code_analysis" not in res["agents_invoked"]
        elif case_name == "minus_issue_pr":
            # Issue & PR was excluded
            assert "issue_pr" not in res["agents_invoked"]
        elif case_name == "monolithic_single_agent":
            assert res["agents_invoked"] == ["monolithic_agent"]
        elif case_name == "baseline_5_agent":
            assert res["evidence_count"] >= 1
            assert "Supporting Evidence" in res["answer"]
