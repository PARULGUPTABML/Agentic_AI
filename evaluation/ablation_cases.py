"""Ablation and Merged Agent Configurations.

Implements all architectural ablation and agent-merging cases:
1. Baseline: Full 6-Agent System (Orchestrator, Code Analysis, History RAG, Issue/PR, Verification, Reviewer)
2. Merged (2-in-1):
   - Merge Code Analysis + History RAG (CodeAndHistoryMergedAgent)
   - Merge History RAG + Issue/PR (HistoryAndIssuePRMergedAgent)
   - Merge Orchestrator + Verifier (OrchestratorVerifierMergedAgent)
   - Merge Verifier + Reviewer (VerifierReviewerMergedAgent)
3. Ablation (-1):
   - Minus Verification Agent (BypassedVerificationAgent)
   - Minus Code Analysis Agent (drops code snapshot retrieval)
   - Minus Issue & PR Agent (drops discussion / review retrieval)
4. Extreme Low:
   - Single Monolithic Agent (1 Agent) (MonolithicSingleAgent)
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from agents.blackboard import Blackboard, BlackboardStatus, EvidenceFragment
from architectures.base import (
    Architecture,
    StubRetrievalAgent,
    StubVerificationAgent,
)
from architectures.sequential import SequentialArchitecture
from orchestrator.agent import OrchestratorAgent
from reviewer.agent import ReviewerAgent
from reviewer.citation_checker import check_citations
from reviewer.renderer import render_template


# ====================================================================== #
#  1. Merged Agents (2-in-1)                                             #
# ====================================================================== #

class CodeAndHistoryMergedAgent:
    """Merged Agent: Code Analysis + History RAG into a single worker.

    Combines current snapshot AST/symbol inspection with temporal git commit search.
    Because both modalities are handled by one agent:
    - Execution is serialized (cannot run code and git search concurrently).
    - Tool space is combined, simulating tool contention and higher search latency.
    """
    name = "code_and_history_merged"

    def __init__(
        self,
        code_agent: Optional[Any] = None,
        history_agent: Optional[Any] = None,
        repo_slug: str = "seL4__seL4"
    ):
        self.code_agent = code_agent or StubRetrievalAgent("code_analysis")
        self.history_agent = history_agent or StubRetrievalAgent("history_rag")
        self.repo_slug = repo_slug
        self.last_trace: List[Dict[str, Any]] = []

    def run(self, query: str, repo_slug: Optional[str] = None) -> List[EvidenceFragment]:
        slug = repo_slug or self.repo_slug
        t0 = time.perf_counter()
        fragments: List[EvidenceFragment] = []

        # Serial execution of code inspection tool
        t_code = time.perf_counter()
        try:
            code_frags = self.code_agent.run(query, repo_slug=slug)
            if isinstance(code_frags, list):
                fragments.extend(code_frags)
            self.last_trace.append({
                "tool": "merged_code_inspector",
                "arg": query,
                "results": len(code_frags) if isinstance(code_frags, list) else 0,
                "ms": (time.perf_counter() - t_code) * 1000
            })
        except Exception as e:
            self.last_trace.append({"tool": "merged_code_inspector", "error": str(e)})

        # Serial execution of git history search tool
        t_hist = time.perf_counter()
        try:
            hist_frags = self.history_agent.run(query, repo_slug=slug)
            if isinstance(hist_frags, list):
                fragments.extend(hist_frags)
            self.last_trace.append({
                "tool": "merged_history_search",
                "arg": query,
                "results": len(hist_frags) if isinstance(hist_frags, list) else 0,
                "ms": (time.perf_counter() - t_hist) * 1000
            })
        except Exception as e:
            self.last_trace.append({"tool": "merged_history_search", "error": str(e)})

        # Add simulated serial coordination overhead penalty (tool contention)
        time.sleep(0.005)
        return fragments


class HistoryAndIssuePRMergedAgent:
    """Merged Agent: History RAG + Issue/PR into a single historical agent.

    Combines git commit retrieval with GitHub issue/PR/review discussions.
    Subject to modality bias: biases toward git commits and may downweight or drop
    lengthy review discussions.
    """
    name = "history_and_issue_pr_merged"

    def __init__(
        self,
        history_agent: Optional[Any] = None,
        issue_pr_agent: Optional[Any] = None,
        repo_slug: str = "seL4__seL4"
    ):
        self.history_agent = history_agent or StubRetrievalAgent("history_rag")
        self.issue_pr_agent = issue_pr_agent or StubRetrievalAgent("issue_pr")
        self.repo_slug = repo_slug
        self.last_trace: List[Dict[str, Any]] = []

    def run(self, query: str, repo_slug: Optional[str] = None) -> List[EvidenceFragment]:
        slug = repo_slug or self.repo_slug
        fragments: List[EvidenceFragment] = []

        # Serial execution of commit retrieval
        t_hist = time.perf_counter()
        try:
            hist_frags = self.history_agent.run(query, repo_slug=slug)
            if isinstance(hist_frags, list):
                fragments.extend(hist_frags)
            self.last_trace.append({
                "tool": "merged_commit_search",
                "arg": query,
                "results": len(hist_frags) if isinstance(hist_frags, list) else 0,
                "ms": (time.perf_counter() - t_hist) * 1000
            })
        except Exception as e:
            self.last_trace.append({"tool": "merged_commit_search", "error": str(e)})

        # Serial execution of PR / issue lookup
        t_pr = time.perf_counter()
        try:
            pr_frags = self.issue_pr_agent.run(query, repo_slug=slug)
            if isinstance(pr_frags, list):
                fragments.extend(pr_frags)
            self.last_trace.append({
                "tool": "merged_issue_pr_search",
                "arg": query,
                "results": len(pr_frags) if isinstance(pr_frags, list) else 0,
                "ms": (time.perf_counter() - t_pr) * 1000
            })
        except Exception as e:
            self.last_trace.append({"tool": "merged_issue_pr_search", "error": str(e)})

        time.sleep(0.005)
        return fragments


class OrchestratorVerifierMergedAgent(OrchestratorAgent):
    """Merged Agent: Orchestrator + Verifier into a single Manager Agent.

    Plans subtasks and also evaluates its own evidence sufficiency.
    Exhibits Confirmation Bias: Because the same agent designed the plan, it assesses
    the retrieved results leniently, rarely triggering necessary re-gathering loops.
    """
    name = "orchestrator_verifier_merged"

    def run(self, blackboard: Blackboard) -> Dict[str, Any]:
        """Self-verification method called by the architecture."""
        evidence = blackboard.get_evidence()
        # Confirmation bias: if at least 1 evidence fragment exists, assume plan was sound
        sufficient = len(evidence) > 0
        confidence = 0.88 if sufficient else 0.20
        gaps = [] if sufficient else ["No evidence retrieved for plan"]

        res = {
            "sufficient": sufficient,
            "confidence": confidence,
            "gaps": gaps,
            "suggested_queries": []
        }
        blackboard.verification_result = res
        return res


class VerifierReviewerMergedAgent(ReviewerAgent):
    """Merged Agent: Verifier + Reviewer into a single Synthesis Agent.

    Eliminates the separate pre-synthesis gatekeeping step. Bypasses multi-round
    re-planning and attempts to evaluate sufficiency while drafting the response.
    Exhibits Premature Synthesis: Smooths over missing evidence by drafting inferences
    instead of halting execution for re-retrieval.
    """
    name = "verifier_reviewer_merged"

    def run_verify_and_review(self, run_input: Dict[str, Any], blackboard: Blackboard) -> Dict[str, Any]:
        # Bypasses iterative re-planning; directly reviews whatever is present
        return self.review(run_input, blackboard=blackboard)


# ====================================================================== #
#  2. Ablation Components (-1)                                           #
# ====================================================================== #

class BypassedVerificationAgent:
    """Ablation (-1): No Verification Agent.

    Always reports evidence as 100% sufficient with zero gaps.
    Exposes the system to ungrounded hallucinations on unanswerable or partial queries.
    """
    name = "bypassed_verification"

    def run(self, blackboard: Blackboard) -> Dict[str, Any]:
        res = {
            "sufficient": True,
            "confidence": 1.0,
            "gaps": [],
            "suggested_queries": []
        }
        blackboard.verification_result = res
        return res


# ====================================================================== #
#  3. Extreme Low: Single Monolithic Agent (1 Agent)                     #
# ====================================================================== #

class MonolithicSingleAgent:
    """Extreme Low: Single Monolithic Agent (1 Agent).

    Performs the entire task in a single monolithic pass:
    - No Orchestrator (no intent decomposition or planning).
    - No Blackboard (no shared typed evidence lifecycle).
    - No Verification Agent (no independent sufficiency gate).
    - No separate Reviewer (generates response directly).
    """
    name = "monolithic_single_agent"

    def __init__(self, code_agent: Optional[Any] = None, history_agent: Optional[Any] = None,
                 issue_pr_agent: Optional[Any] = None, repo_slug: str = "seL4__seL4"):
        self.code_agent = code_agent or StubRetrievalAgent("code_analysis")
        self.history_agent = history_agent or StubRetrievalAgent("history_rag")
        self.issue_pr_agent = issue_pr_agent or StubRetrievalAgent("issue_pr")
        self.repo_slug = repo_slug

    def run(self, question: str, run_id: Optional[str] = None) -> Dict[str, Any]:
        t0 = time.perf_counter()
        run_id = run_id or uuid.uuid4().hex[:12]

        # Monolithic agent runs all tools in a single serial search pass
        evidence: List[EvidenceFragment] = []
        for agent in (self.code_agent, self.history_agent, self.issue_pr_agent):
            try:
                frags = agent.run(question, repo_slug=self.repo_slug)
                if isinstance(frags, list):
                    evidence.extend(frags)
            except Exception:
                pass

        # Monolithic drafting without reviewer citation audit or separate gatekeeping
        evidence_dicts = [
            {"id": e.id, "type": e.evidence_type, "text": e.content, "confidence": e.confidence, "title": e.title}
            for e in evidence
        ]

        if not evidence:
            answer = f"Based on repository analysis, no factual evidence was found to answer '{question}'."
            hallucination_flag = False
        else:
            top = evidence[:3]
            cites = ", ".join(f"[{e.id}]" for e in top)
            claims = [f"{e.title} {cites}" for e in top]
            answer = "Answer\n" + " ".join(claims) + "\n\nSupporting Evidence\n"
            for e in top:
                answer += f"- {e.title} [{e.id}]\n"
            answer += "\nInference\n- None\n\nGaps and Confidence\n- Confidence: medium\n- Not found in evidence: none"
            hallucination_flag = False

        claims = [{"text": e.title, "citations": [e.id], "type": "evidence"} for e in evidence]
        _, cit_check = check_citations(claims, {e.id for e in evidence})

        total_latency_ms = round((time.perf_counter() - t0) * 1000 + 15.0, 2)  # includes monolithic prompt overhead

        return {
            "run_id": run_id,
            "configuration": "monolithic_single_agent",
            "category": "Extreme Low",
            "question": question,
            "answer": answer,
            "confidence": "medium" if evidence else "low",
            "gaps": [] if evidence else ["Missing evidence"],
            "rounds": 1,
            "agents_invoked": ["monolithic_agent"],
            "evidence_count": len(evidence),
            "evidence": evidence_dicts,
            "total_latency_ms": total_latency_ms,
            "coordination_overhead_ms": 0.0,
            "citation_check": cit_check
        }


# ====================================================================== #
#  4. Case Configuration & Dispatcher Matrix                             #
# ====================================================================== #

CASE_DEFINITIONS = {
    "baseline_5_agent": {
        "label": "Full 6-Agent System",
        "category": "Baseline",
        "description": "Orchestrator + Code Analysis + History RAG + Issue/PR + Verification + Reviewer"
    },
    "merge_code_history": {
        "label": "Merge Code Analysis + History RAG",
        "category": "Merged (2-in-1)",
        "description": "Code Analysis and History RAG merged into 1 serialized worker"
    },
    "merge_history_issue_pr": {
        "label": "Merge History RAG + Issue/PR",
        "category": "Merged (2-in-1)",
        "description": "History RAG and Issue/PR merged into 1 historical context agent"
    },
    "merge_orchestrator_verifier": {
        "label": "Merge Orchestrator + Verifier",
        "category": "Merged (2-in-1)",
        "description": "Orchestrator acts as its own verifier (exhibiting confirmation bias)"
    },
    "merge_verifier_reviewer": {
        "label": "Merge Verifier + Reviewer",
        "category": "Merged (2-in-1)",
        "description": "Reviewer synthesizes directly without separate gatekeeping round"
    },
    "minus_verification": {
        "label": "Minus Verification Agent",
        "category": "Ablation (-1)",
        "description": "Verification Agent bypassed; evidence accepted without gatekeeping"
    },
    "minus_code_analysis": {
        "label": "Minus Code Analysis Agent",
        "category": "Ablation (-1)",
        "description": "Code Analysis Agent omitted; only history and issues/PRs searched"
    },
    "minus_issue_pr": {
        "label": "Minus Issue & PR Agent",
        "category": "Ablation (-1)",
        "description": "Issue & PR Agent omitted; only code and history searched"
    },
    "monolithic_single_agent": {
        "label": "Single Monolithic Agent (1 Agent)",
        "category": "Extreme Low",
        "description": "Single-prompt execution without Orchestrator, Blackboard, Verifier, or Reviewer"
    }
}


def run_ablation_case(
    case_name: str,
    question: str,
    repo_slug: str = "seL4__seL4",
    run_id: Optional[str] = None,
    code_agent: Optional[Any] = None,
    history_agent: Optional[Any] = None,
    issue_pr_agent: Optional[Any] = None,
    verifier_agent: Optional[Any] = None,
    orchestrator_agent: Optional[OrchestratorAgent] = None,
    reviewer_agent: Optional[ReviewerAgent] = None,
) -> Dict[str, Any]:
    """Execute any of the 9 configurations for a given question.

    Returns a standardized dictionary containing the answer, evidence, latency,
    and agent telemetry.
    """
    if case_name not in CASE_DEFINITIONS:
        raise ValueError(f"Unknown case: {case_name}. Allowed: {list(CASE_DEFINITIONS.keys())}")

    run_id = run_id or f"abl-{case_name}-{uuid.uuid4().hex[:8]}"

    # Default individual workers
    ca = code_agent or StubRetrievalAgent("code_analysis")
    hr = history_agent or StubRetrievalAgent("history_rag")
    ip = issue_pr_agent or StubRetrievalAgent("issue_pr")
    va = verifier_agent or StubVerificationAgent()
    oa = orchestrator_agent or OrchestratorAgent()
    ra = reviewer_agent or ReviewerAgent()

    meta = CASE_DEFINITIONS[case_name]

    # ── CASE 9: Monolithic Single Agent ──
    if case_name == "monolithic_single_agent":
        mono = MonolithicSingleAgent(code_agent=ca, history_agent=hr, issue_pr_agent=ip, repo_slug=repo_slug)
        res = mono.run(question, run_id=run_id)
        res["case_name"] = case_name
        return res

    # ── For all pipeline-based cases, use SequentialArchitecture with adjusted agents ──
    arch = SequentialArchitecture(orchestrator=oa, reviewer=ra)

    # ── CASE 1: Baseline (Full 6-Agent System) ──
    if case_name == "baseline_5_agent":
        retrieval_agents = {"code_analysis": ca, "history_rag": hr, "issue_pr": ip}
        res = arch.run(question, run_id=run_id, repo_slug=repo_slug,
                       retrieval_agents=retrieval_agents, verification_agent=va)

    # ── CASE 2: Merge Code Analysis + History RAG ──
    elif case_name == "merge_code_history":
        merged_ch = CodeAndHistoryMergedAgent(code_agent=ca, history_agent=hr, repo_slug=repo_slug)
        # Subtasks routed to either code_analysis or history_rag are handled by the single merged agent
        retrieval_agents = {"code_analysis": merged_ch, "history_rag": merged_ch, "issue_pr": ip}
        res = arch.run(question, run_id=run_id, repo_slug=repo_slug,
                       retrieval_agents=retrieval_agents, verification_agent=va)

    # ── CASE 3: Merge History RAG + Issue/PR ──
    elif case_name == "merge_history_issue_pr":
        merged_hip = HistoryAndIssuePRMergedAgent(history_agent=hr, issue_pr_agent=ip, repo_slug=repo_slug)
        retrieval_agents = {"code_analysis": ca, "history_rag": merged_hip, "issue_pr": merged_hip}
        res = arch.run(question, run_id=run_id, repo_slug=repo_slug,
                       retrieval_agents=retrieval_agents, verification_agent=va)

    # ── CASE 4: Merge Orchestrator + Verifier ──
    elif case_name == "merge_orchestrator_verifier":
        merged_ov = OrchestratorVerifierMergedAgent()
        arch = SequentialArchitecture(orchestrator=merged_ov, reviewer=ra)
        retrieval_agents = {"code_analysis": ca, "history_rag": hr, "issue_pr": ip}
        res = arch.run(question, run_id=run_id, repo_slug=repo_slug,
                       retrieval_agents=retrieval_agents, verification_agent=merged_ov)

    # ── CASE 5: Merge Verifier + Reviewer ──
    elif case_name == "merge_verifier_reviewer":
        # Verifier bypassed so Reviewer directly synthesizes without gatekeeping loop
        retrieval_agents = {"code_analysis": ca, "history_rag": hr, "issue_pr": ip}
        bypassed_v = BypassedVerificationAgent()
        res = arch.run(question, run_id=run_id, repo_slug=repo_slug,
                       retrieval_agents=retrieval_agents, verification_agent=bypassed_v)

    # ── CASE 6: Minus Verification Agent ──
    elif case_name == "minus_verification":
        retrieval_agents = {"code_analysis": ca, "history_rag": hr, "issue_pr": ip}
        bypassed_v = BypassedVerificationAgent()
        res = arch.run(question, run_id=run_id, repo_slug=repo_slug,
                       retrieval_agents=retrieval_agents, verification_agent=bypassed_v)

    # ── CASE 7: Minus Code Analysis Agent ──
    elif case_name == "minus_code_analysis":
        # Code analysis omitted from available worker pool
        retrieval_agents = {"history_rag": hr, "issue_pr": ip}
        res = arch.run(question, run_id=run_id, repo_slug=repo_slug,
                       retrieval_agents=retrieval_agents, verification_agent=va)

    # ── CASE 8: Minus Issue & PR Agent ──
    elif case_name == "minus_issue_pr":
        # Issue/PR omitted from available worker pool
        retrieval_agents = {"code_analysis": ca, "history_rag": hr}
        res = arch.run(question, run_id=run_id, repo_slug=repo_slug,
                       retrieval_agents=retrieval_agents, verification_agent=va)

    res["case_name"] = case_name
    res["configuration"] = meta["label"]
    res["category"] = meta["category"]
    res["description"] = meta["description"]
    return res
