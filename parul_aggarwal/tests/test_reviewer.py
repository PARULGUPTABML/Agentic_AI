"""Unit and adversarial tests for the Reviewer / Final Response Agent."""
from __future__ import annotations

import pytest

from parul_aggarwal.reviewer.agent import ReviewerAgent
from parul_aggarwal.reviewer.citation_checker import check_citations, extract_citations
from parul_aggarwal.reviewer.renderer import render_template
from agents.blackboard import Blackboard, EvidenceFragment, BlackboardStatus


class TestCitationChecker:
    def test_extract_citations(self):
        text = "This was changed in [commit:7a81b2c] and discussed in [pr:142, issue:118]."
        extracted = extract_citations(text)
        assert "commit:7a81b2c" in extracted
        assert "pr:142" in extracted
        assert "issue:118" in extracted

    def test_valid_and_invalid_citations(self):
        valid_ids = {"commit:7a81b2c", "pr:142"}
        claims = [
            {"text": "Memory changed", "type": "evidence", "citations": ["commit:7a81b2c", "commit:ghost123"]},
            {"text": "PR merged", "type": "evidence", "citations": ["pr:142"]},
            {"text": "Fake claim", "type": "evidence", "citations": ["issue:fake999"]}
        ]
        sanitized, stats = check_citations(claims, valid_ids)
        assert stats["valid"] == 2
        assert stats["invalid"] == 2
        assert "commit:ghost123" in stats["removed"]
        assert "issue:fake999" in stats["removed"]
        # Third claim lost all citations, so it should be converted to inference
        assert sanitized[2]["type"] == "inference"


class TestTemplateRenderer:
    def test_renderer_sections(self):
        rendered = render_template(
            answer="Short direct answer [commit:123].",
            claims=[{"text": "Fact 1", "type": "evidence", "citations": ["commit:123"]}],
            inference="Likely motivated by performance",
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

    def test_incomplete_flag_handled(self, reviewer):
        input_data = {
            "run_id": "test-inc",
            "question": "Why was auth changed?",
            "evidence": [
                {"id": "commit:1", "title": "Commit 1", "text": "Changed auth tokens", "confidence": 0.8}
            ],
            "incomplete": True,
            "gap_notes": ["Missing PR review notes"]
        }
        res = reviewer.review(input_data)
        assert res["incomplete"] is True
        assert any("incomplete" in g.lower() or "re-planning limit" in g.lower() for g in res["gaps"])
        assert "incomplete" in res["answer"].lower()

    def test_adversarial_invalid_evidence_dropped(self, reviewer):
        """Evidence without an ID must be dropped during intake."""
        input_data = {
            "run_id": "test-bad-ev",
            "question": "What is the IPC buffer?",
            "evidence": [
                {"title": "Broken Item", "text": "No ID here!"},  # Missing ID
                {"id": "commit:valid1", "title": "Valid", "text": "Static IPC buffer introduced", "confidence": 0.9}
            ]
        }
        res = reviewer.review(input_data)
        assert res["citation_check"]["valid"] >= 1
        # The dropped item should not be in valid citations
        assert "commit:valid1" in [c["citations"][0] for c in res["claims"] if c["citations"]]

    def test_blackboard_integration(self, reviewer):
        bb = Blackboard(question="Why was untyped memory added?", repo_slug="seL4__seL4")
        bb.post_evidence("history_rag", [
            EvidenceFragment(
                id="commit:7a81b2c",
                source_agent="history_rag",
                evidence_type="commit",
                title="Untyped allocation",
                content="Transitioned kernel memory to untyped capability model.",
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


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
