"""Unit and adversarial tests for the Reviewer / Final Response Agent."""
from __future__ import annotations

import os
from unittest.mock import patch
import pytest

from parul_aggarwal.reviewer.agent import ReviewerAgent
from parul_aggarwal.reviewer.citation_checker import check_citations, extract_citations
from parul_aggarwal.reviewer.renderer import render_template
from agents.blackboard import Blackboard, EvidenceFragment, BlackboardStatus


class TestCitationChecker:
    def test_extract_citations(self):
        text = "This was changed in [commit:3e57e647e] and discussed in [pr:420, issue:SELFOUR-501]."
        extracted = extract_citations(text)
        assert "commit:3e57e647e" in extracted
        assert "pr:420" in extracted
        assert "issue:SELFOUR-501" in extracted

    def test_valid_and_invalid_citations(self):
        valid_ids = {"commit:3e57e647e", "pr:420"}
        claims = [
            {"text": "PAE removed", "type": "evidence", "citations": ["commit:3e57e647e", "commit:ghost123"]},
            {"text": "PR merged", "type": "evidence", "citations": ["pr:420"]},
            {"text": "Fake claim", "type": "evidence", "citations": ["issue:fake999"]}
        ]
        sanitized, stats = check_citations(claims, valid_ids)
        assert stats["valid"] == 2
        assert stats["invalid"] == 2
        assert "commit:ghost123" in stats["removed"]
        assert "issue:fake999" in stats["removed"]
        assert sanitized[2]["type"] == "inference"


class TestTemplateRenderer:
    def test_renderer_sections(self):
        rendered = render_template(
            answer="Short direct answer [commit:3e57e647e].",
            claims=[{"text": "Fact 1", "type": "evidence", "citations": ["commit:3e57e647e"]}],
            inference="None",
            confidence="high",
            gaps=["None"]
        )
        assert "Answer" in rendered
        assert "Supporting Evidence" in rendered
        assert "Inference (not directly stated in the evidence)" in rendered
        assert "Gaps and Confidence" in rendered
        assert "- Confidence: high" in rendered


class TestReviewerAgent:
    @pytest.fixture
    def reviewer(self):
        return ReviewerAgent()

    def test_empty_evidence_behavior(self, reviewer):
        input_data = {
            "run_id": "test-empty",
            "question": "Why was the blockchain added?",
            "evidence": [],
            "incomplete": False,
            "gap_notes": []
        }
        res = reviewer.review(input_data)
        assert res["confidence"] == "low"
        assert len(res["gaps"]) > 0
        assert "no evidence" in res["answer"].lower()
        assert res["citation_check"]["valid"] == 0
        assert res["inference"] == "None"

    def test_no_invented_inference_on_unrelated_items(self, reviewer):
        """Reviewer must not invent a consolidation inference on unrelated items."""
        input_data = {
            "run_id": "test-unrelated",
            "question": "What happened?",
            "evidence": [
                {"id": "commit:1", "title": "Fix typo in README", "text": "Fix typo in README", "confidence": 0.5},
                {"id": "commit:2", "title": "Add CI badge", "text": "Add CI badge", "confidence": 0.5}
            ]
        }
        res = reviewer.review(input_data)
        assert "architectural consolidation" not in res["inference"]
        assert res["inference"] == "None"
        # Should NOT report high confidence on unrelated items
        assert res["confidence"] != "high"

    def test_citation_checker_runs_on_llm_output(self, reviewer):
        """Blocker 3: Model reply citing a made-up [commit:deadbeef99] must be detected and removed."""
        fake_llm_reply = "PAE support was removed in [commit:3e57e647e], but also affected by [commit:deadbeef99]."
        input_data = {
            "run_id": "test-llm-cites",
            "question": "Why was PAE removed?",
            "evidence": [
                {"id": "commit:3e57e647e", "title": "Remove PAE", "text": "SELFOUR-501: x86 - Remove PAE support", "confidence": 0.9}
            ]
        }
        with patch.dict(os.environ, {"LLM_PROVIDER": "real_mock"}):
            with patch("agents._llm_retry.generate_with_retry", return_value=fake_llm_reply):
                res = reviewer.review(input_data)

        # Must detect deadbeef99 as invalid and report it
        assert res["citation_check"]["valid"] == 1
        assert res["citation_check"]["invalid"] == 1
        assert "commit:deadbeef99" in res["citation_check"]["removed"]
        assert "deadbeef99" not in res["answer"]
        assert len(res["claims"]) >= 1

    def test_incomplete_flag_handled(self, reviewer):
        input_data = {
            "run_id": "test-inc",
            "question": "Why was PAE removed?",
            "evidence": [
                {"id": "commit:3e57e647e", "title": "Remove PAE", "text": "SELFOUR-501: x86 - Remove PAE support", "confidence": 0.8}
            ],
            "incomplete": True,
            "gap_notes": ["Missing PR review notes"]
        }
        res = reviewer.review(input_data)
        assert res["incomplete"] is True
        assert any("incomplete" in g.lower() or "limit" in g.lower() for g in res["gaps"])

    def test_entailment_check_flag(self):
        reviewer_with_check = ReviewerAgent(enable_entailment_check=True)
        input_data = {
            "run_id": "test-entail",
            "question": "Why was PAE removed?",
            "evidence": [
                {"id": "commit:3e57e647e", "title": "Remove PAE", "text": "SELFOUR-501: x86 - Remove PAE support and vspace_pae.c deleted", "confidence": 0.9}
            ]
        }
        res = reviewer_with_check.review(input_data)
        assert res["citation_check"]["valid"] >= 1

    def test_blackboard_integration(self, reviewer):
        bb = Blackboard(question="Why was PAE removed?", repo_slug="seL4__seL4")
        bb.post_evidence("history_rag", [
            EvidenceFragment(
                id="commit:3e57e647e",
                source_agent="history_rag",
                evidence_type="commit",
                title="Remove PAE",
                content="SELFOUR-501: x86 - Remove PAE support",
                confidence=0.95
            )
        ])

        input_data = {
            "run_id": bb.run_id,
            "question": bb.question,
            "evidence": [
                {"id": e.id, "type": e.evidence_type, "text": e.content, "confidence": e.confidence, "title": e.title}
                for e in bb.get_evidence()
            ]
        }
        res = reviewer.review(input_data, blackboard=bb)
        assert bb.final_answer is not None
        assert "Supporting Evidence" in bb.final_answer
        assert bb.get_status() == BlackboardStatus.DONE
        logs = bb.get_logs()
        assert any(l.agent == "reviewer" for l in logs)

    def test_reviewer_polish_no_stray_dot_and_no_duplicate_citations(self):
        """Verify reviewer polish: unsupported sentences dropped from Answer, fake IDs stripped from Inference, citations not repeated."""
        fake_llm_reply = "PAE support was removed in [commit:3e57e647e]. It also changed the scheduler [commit:deadbeef99]."
        reviewer = ReviewerAgent(llm_fn=lambda s, p: fake_llm_reply)
        input_data = {
            "run_id": "test-polish",
            "question": "Why was PAE removed?",
            "evidence": [
                {"id": "commit:3e57e647e", "title": "Remove PAE", "text": "SELFOUR-501: x86 - Remove PAE support", "confidence": 0.9}
            ]
        }
        res = reviewer.review(input_data)

        # Answer should only contain supported claim, without stray dot or fake ID
        assert "PAE support was removed in [commit:3e57e647e]." in res["answer"]
        assert "deadbeef99" not in res["answer"]
        assert " ." not in res["answer"]
        assert "scheduler" not in res["answer"]

        # Rendered output checks
        rendered = res["rendered"]
        assert "deadbeef99" not in rendered
        # Supporting evidence should cite commit:3e57e647e exactly once per line, not twice
        sup_lines = [l for l in rendered.splitlines() if "- PAE support" in l]
        assert len(sup_lines) == 1
        assert sup_lines[0].count("commit:3e57e647e") == 1

        # Inference should contain the sanitized unsupported claim without fake ID
        inf_lines = [l for l in rendered.splitlines() if "scheduler" in l.lower()]
        assert len(inf_lines) == 1
        assert "deadbeef99" not in inf_lines[0]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
