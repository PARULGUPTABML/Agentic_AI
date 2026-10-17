"""Tests for the multi-agent system (Parul Gupta's agents).

Run with:
    cd backend
    LLM_PROVIDER=mock EMBED_MODEL=hash python -m pytest tests/test_agents.py -v

Uses mock LLM and hash embeddings — no API keys or GPU needed.
"""
import os
import sys

# Force mock/hash mode before any config import
os.environ["LLM_PROVIDER"] = "mock"
os.environ["EMBED_MODEL"] = "hash"
os.environ["USE_RERANKER"] = "false"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from agents.blackboard import Blackboard, BlackboardStatus, EvidenceFragment
from agents.history_rag_agent import HistoryRAGAgent, _parse_action
from agents.evidence_verification_agent import EvidenceVerificationAgent
from agents.multi_agent_pipeline import run_multi_agent
from agents.messaging import (
    compare_protocols,
    protocol_summary,
    MessageBus,
    PROTOCOLS,
)


# ================================================================== #
#  Blackboard Tests                                                   #
# ================================================================== #

class TestBlackboard:
    """Tests for the shared Blackboard evidence store."""

    def test_create_blackboard(self):
        bb = Blackboard(question="Why was auth changed?", repo_slug="test__repo")
        assert bb.question == "Why was auth changed?"
        assert bb.repo_slug == "test__repo"
        assert bb.get_status() == BlackboardStatus.GATHERING
        assert bb.evidence_count() == 0
        assert bb.run_id  # should have a unique ID

    def test_post_and_get_evidence(self):
        bb = Blackboard(question="test", repo_slug="test__repo")
        frags = [
            EvidenceFragment(id="commit:abc123", source_agent="test",
                             evidence_type="commit", title="Fix auth bug",
                             content="Changed auth module", confidence=0.9),
            EvidenceFragment(id="issue:42", source_agent="test",
                             evidence_type="issue", title="Auth is broken",
                             content="Users report login failure", confidence=0.7),
        ]
        added = bb.post_evidence("history_rag", frags)
        assert added == 2
        assert bb.evidence_count() == 2

        # Get all evidence (sorted by confidence, highest first)
        all_ev = bb.get_evidence()
        assert len(all_ev) == 2
        assert all_ev[0].confidence >= all_ev[1].confidence

    def test_filter_evidence(self):
        bb = Blackboard(question="test", repo_slug="test__repo")
        bb.post_evidence("agent_a", [
            EvidenceFragment(id="commit:111", source_agent="", evidence_type="commit",
                             title="c1", content="commit content", confidence=0.8),
            EvidenceFragment(id="issue:1", source_agent="", evidence_type="issue",
                             title="i1", content="issue content", confidence=0.6),
        ])
        commits = bb.get_evidence(filter_type="commit")
        assert len(commits) == 1
        assert commits[0].id == "commit:111"

    def test_no_duplicate_evidence(self):
        bb = Blackboard(question="test", repo_slug="test__repo")
        frag = EvidenceFragment(id="commit:same", source_agent="", evidence_type="commit",
                                title="t", content="c", confidence=0.5)
        bb.post_evidence("a", [frag])
        bb.post_evidence("a", [frag])  # same ID again
        assert bb.evidence_count() == 1

    def test_higher_confidence_replaces(self):
        bb = Blackboard(question="test", repo_slug="test__repo")
        low = EvidenceFragment(id="commit:x", source_agent="", evidence_type="commit",
                               title="t", content="c", confidence=0.3)
        high = EvidenceFragment(id="commit:x", source_agent="", evidence_type="commit",
                                title="t", content="c updated", confidence=0.9)
        bb.post_evidence("a", [low])
        bb.post_evidence("a", [high])
        ev = bb.get_evidence()
        assert ev[0].confidence == 0.9

    def test_status_transitions(self):
        bb = Blackboard(question="test", repo_slug="test__repo")
        assert bb.get_status() == BlackboardStatus.GATHERING
        bb.set_status(BlackboardStatus.VERIFYING)
        assert bb.get_status() == BlackboardStatus.VERIFYING
        bb.set_status(BlackboardStatus.DONE)
        assert bb.get_status() == BlackboardStatus.DONE

    def test_retry_mechanism(self):
        bb = Blackboard(question="test", repo_slug="test__repo")
        bb.max_retries = 2
        assert bb.can_retry()
        bb.start_retry(["refined query 1"])
        assert bb.retry_count == 1
        assert bb.retry_queries == ["refined query 1"]
        assert bb.get_status() == BlackboardStatus.REGATHERING
        assert bb.can_retry()
        bb.start_retry(["refined query 2"])
        assert bb.retry_count == 2
        assert not bb.can_retry()  # max retries reached

    def test_logging(self):
        bb = Blackboard(question="test", repo_slug="test__repo")
        bb.log("test_agent", "search", "searched for commits", duration_ms=100.5)
        bb.log("test_agent", "verify", "checking sufficiency")
        logs = bb.get_logs()
        assert len(logs) == 2
        assert logs[0].agent == "test_agent"
        assert logs[0].duration_ms == 100.5

    def test_to_dict(self):
        bb = Blackboard(question="Why?", repo_slug="test__repo")
        bb.post_evidence("a", [
            EvidenceFragment(id="commit:1", source_agent="", evidence_type="commit",
                             title="t", content="c", confidence=0.8),
        ])
        d = bb.to_dict()
        assert d["question"] == "Why?"
        assert d["evidence_count"] == 1
        assert len(d["evidence"]) == 1
        assert "run_id" in d


# ================================================================== #
#  ReAct Parser Tests                                                 #
# ================================================================== #

class TestReActParser:
    """Tests for the ReAct action parser."""

    def test_parse_search_commits(self):
        text = 'Thought: I need commit history\nAction: search_commits("auth redesign")'
        tool, arg = _parse_action(text)
        assert tool == "search_commits"
        assert arg == "auth redesign"

    def test_parse_search_issues(self):
        text = "Thought: check issues\nAction: search_issues('security vulnerability')"
        tool, arg = _parse_action(text)
        assert tool == "search_issues"
        assert arg == "security vulnerability"

    def test_parse_finish(self):
        text = "Thought: I have enough\nAction: finish()"
        tool, arg = _parse_action(text)
        assert tool == "finish"

    def test_parse_get_linked(self):
        text = 'Thought: expand\nAction: get_linked("commit:abc123")'
        tool, arg = _parse_action(text)
        assert tool == "get_linked"
        assert arg == "commit:abc123"

    def test_fallback_to_finish(self):
        text = "I think I should finish now."
        tool, arg = _parse_action(text)
        assert tool == "finish"

    def test_unparseable_returns_finish(self):
        text = "Some random text without any action"
        tool, arg = _parse_action(text)
        assert tool == "finish"


# ================================================================== #
#  Evidence Verification Agent Tests                                  #
# ================================================================== #

class TestEvidenceVerification:
    """Tests for the Evidence Verification Agent."""

    def test_empty_evidence_is_insufficient(self):
        bb = Blackboard(question="Why was auth changed?", repo_slug="test__repo")
        agent = EvidenceVerificationAgent()
        result = agent.run(bb)
        assert result["sufficient"] is False
        assert result["confidence"] == 0.0
        assert len(result["gaps"]) > 0

    def test_code_only_evidence_is_insufficient(self):
        """Evidence with only code (no history) should be marked insufficient."""
        bb = Blackboard(question="Why was auth changed?", repo_slug="test__repo")
        bb.post_evidence("code_agent", [
            EvidenceFragment(id="code:auth.py:1-50", source_agent="",
                             evidence_type="code", title="auth.py",
                             content="def authenticate(user):\n    ...",
                             confidence=0.8),
        ])
        agent = EvidenceVerificationAgent()
        result = agent.run(bb)
        # Heuristic should catch: code-only → insufficient for "why" question
        assert result["sufficient"] is False

    def test_verification_sets_blackboard_status(self):
        bb = Blackboard(question="test", repo_slug="test__repo")
        agent = EvidenceVerificationAgent()
        agent.run(bb)
        assert bb.verification_result is not None


# ================================================================== #
#  Message Bus Tests                                                  #
# ================================================================== #

class TestMessageBus:
    """Tests for the Broadcast protocol's MessageBus."""

    def test_publish_and_get(self):
        bus = MessageBus()
        bus.publish("agent_a", "evidence", {"count": 5})
        bus.publish("agent_b", "verdict", {"sufficient": True})
        assert len(bus.get_messages()) == 2
        assert len(bus.get_messages("evidence")) == 1
        assert len(bus.get_messages("verdict")) == 1

    def test_subscribe(self):
        bus = MessageBus()
        bus.subscribe("agent_a", "retry")
        bus.subscribe("agent_b", "evidence")
        assert "agent_a" in bus._subscribers["retry"]

    def test_get_latest(self):
        bus = MessageBus()
        bus.publish("a", "topic", "first")
        bus.publish("a", "topic", "second")
        latest = bus.get_latest("topic")
        assert latest.payload == "second"

    def test_empty_bus(self):
        bus = MessageBus()
        assert bus.get_messages() == []
        assert bus.get_latest("anything") is None


# ================================================================== #
#  Protocol Registration Tests                                       #
# ================================================================== #

class TestProtocols:
    """Tests for protocol registration."""

    def test_all_protocols_registered(self):
        assert "blackboard" in PROTOCOLS
        assert "p2p" in PROTOCOLS
        assert "broadcast" in PROTOCOLS
        assert "mcp" in PROTOCOLS

    def test_protocol_summary_with_empty(self):
        summary = protocol_summary([])
        assert summary["comparison"] == []

    def test_protocol_summary_with_error(self):
        summary = protocol_summary([{"protocol": "test", "error": "boom"}])
        assert summary["comparison"][0]["error"] == "boom"


# ================================================================== #
#  Integration Test (needs indexed repo data)                         #
# ================================================================== #

class TestIntegration:
    """Integration tests — only run if a repo is indexed."""

    @pytest.fixture
    def indexed_slug(self):
        """Find any indexed repo, skip if none available."""
        from rag.retriever import list_indexed
        repos = list_indexed()
        if not repos:
            pytest.skip("No indexed repos found — run ingestion first")
        return repos[0]["slug"]

    def test_multi_agent_pipeline(self, indexed_slug):
        """End-to-end: multi-agent pipeline should return a valid response."""
        result = run_multi_agent(indexed_slug, "Why was this project created?")
        assert "answer" in result
        assert "evidence" in result
        assert result["condition"] == "multi_agent"
        assert len(result["answer"]) > 10
        assert result["multi_agent"]["run_id"]

    def test_history_agent_standalone(self, indexed_slug):
        """History/RAG agent should run the ReAct loop and log actions.

        Note: with LLM_PROVIDER=mock the LLM doesn't produce ReAct-formatted
        output, so the agent may finish immediately with 0 fragments.  The test
        verifies the agent ran without errors and recorded its logs.
        """
        bb = Blackboard(question="What major changes happened recently?",
                        repo_slug=indexed_slug)
        agent = HistoryRAGAgent(indexed_slug)
        fragments = agent.run(bb)
        # Agent should have logged at least start + finish/complete
        logs = bb.get_logs()
        assert len(logs) >= 2
        assert any("start" in l.action for l in logs)
        assert any("finish" in l.action or "complete" in l.action for l in logs)

    def test_single_protocol(self, indexed_slug):
        """Run one protocol and check the response format."""
        from agents.messaging import run_blackboard_protocol
        result = run_blackboard_protocol(indexed_slug, "What does this project do?")
        assert "answer" in result
        assert result["protocol"] == "blackboard"
        assert "multi_agent" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
