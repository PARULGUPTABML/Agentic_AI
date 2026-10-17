"""Tests and parity verification for the 4 coordination architecture variants."""
from __future__ import annotations

import pytest

from parul_aggarwal.architectures.sequential import SequentialArchitecture
from parul_aggarwal.architectures.hierarchical import HierarchicalArchitecture
from parul_aggarwal.architectures.event_driven import EventDrivenArchitecture
from parul_aggarwal.architectures.workflow_dag import WorkflowDAGArchitecture
from parul_aggarwal.architectures.base import StubVerificationAgent


DEV_SAMPLE_QUESTIONS = [
    "Why was PAE (Physical Address Extension) support removed from x86 in seL4?",
    "When was SMP lock.c moved from the x86 architecture directory to the generic smp directory?",
    "Who authored the hardware IBRS and jump table Spectre mitigations in seL4 x86?",
    "What caching behavior did moving the cache flush from untyped reset to retype supersede?",
    "Why were endpoint and notification queues unified into a single queue structure under MCS?"
]


class TestArchitectures:
    @pytest.fixture
    def architectures(self):
        return [
            SequentialArchitecture(),
            HierarchicalArchitecture(),
            EventDrivenArchitecture(),
            WorkflowDAGArchitecture()
        ]

    def test_all_architectures_instantiate(self, architectures):
        names = [a.name for a in architectures]
        assert "sequential" in names
        assert "hierarchical" in names
        assert "event_driven" in names
        assert "workflow_dag" in names

    @pytest.mark.parametrize("q", DEV_SAMPLE_QUESTIONS)
    def test_parity_across_variants(self, architectures, q):
        """Parity test: All four variants must produce valid cited answers on dev questions."""
        results = {}
        for arch in architectures:
            res = arch.run(question=q, run_id=f"test-parity-{arch.name}")
            results[arch.name] = res

            # Integrity checks
            assert res["architecture"] == arch.name
            assert len(res["answer"]) > 10
            assert "Supporting Evidence" in res["answer"]
            assert res["evidence_count"] >= 1
            assert res["citation_check"]["invalid"] == 0
            assert res["total_latency_ms"] > 0
            assert res["coordination_overhead_ms"] >= 0
            # Coordination overhead must never exceed total latency
            assert res["coordination_overhead_ms"] <= res["total_latency_ms"] + 0.1

        # Parity check: all return consistent confidence under deterministic stubs
        confidences = {r["confidence"] for r in results.values()}
        assert len(confidences) == 1, f"Confidence divergence across architectures: {confidences}"

    def test_sequential_follows_plan_strictly(self):
        """Sequential must only invoke agents assigned in the Orchestrator plan."""
        seq = SequentialArchitecture()
        # Authorship question: plan assigns history_rag and issue_pr, NOT code_analysis
        q = "Who authored the hardware IBRS and jump table Spectre mitigations in seL4 x86?"
        res = seq.run(question=q, run_id="test-seq-plan")
        invoked = res["agents_invoked"]
        assert "history_rag" in invoked
        assert "issue_pr" in invoked
        assert "code_analysis" not in invoked

    def test_replan_round_limit_across_variants(self, architectures):
        """When verification always fails, all variants must terminate cleanly at max_rounds."""
        for arch in architectures:
            stub_v = StubVerificationAgent(succeed_on_round=999)  # Never succeeds
            res = arch.run(
                question="Adversarial unanswerable query",
                run_id=f"test-limit-{arch.name}",
                verification_agent=stub_v
            )
            assert res["rounds"] <= arch.max_rounds
            assert any("incomplete" in g.lower() or "limit" in g.lower() for g in res["gaps"])


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
