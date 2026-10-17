"""Variant 4: Workflow-Based (DAG) Coordination Architecture.

Executes a formal Directed Acyclic Graph with conditional branch edges:
[Orchestrator Node] -> [Parallel Retrieval Node] -> [Verification Node]
                      ^                                       |
                      |--- (if insufficient & round < MAX) ---|
                                                              v
                                                       [Reviewer Node] -> [End]
"""
from __future__ import annotations

import concurrent.futures
import time
from typing import Any, Dict, List, Optional

from agents.blackboard import Blackboard, BlackboardStatus
from architectures.base import (
    Architecture,
    StubRetrievalAgent,
    StubVerificationAgent,
)


class WorkflowDAGArchitecture(Architecture):
    name = "workflow_dag"

    def run(
        self,
        question: str,
        run_id: str,
        repo_slug: str = "seL4__seL4",
        retrieval_agents: Optional[Dict[str, Any]] = None,
        verification_agent: Optional[Any] = None
    ) -> Dict[str, Any]:
        start_time = time.time()
        coord_overhead_ms = 0.0
        agents_invoked: List[str] = []

        blackboard = Blackboard(question=question, repo_slug=repo_slug, run_id=run_id)

        agents = retrieval_agents or {
            "code_analysis": StubRetrievalAgent("code_analysis"),
            "history_rag": StubRetrievalAgent("history_rag"),
            "issue_pr": StubRetrievalAgent("issue_pr")
        }
        verifier = verification_agent or StubVerificationAgent()

        round_num = 1
        incomplete = False
        plan = None
        v_res = {}

        # DAG Node execution loop
        while round_num <= self.max_rounds:
            # Node 1: Orchestrator Node
            c_start = time.time()
            if plan is None:
                plan = self.orchestrator.plan(question, run_id, round_num=round_num, blackboard=blackboard)
            else:
                feedback = "; ".join(v_res.get("gaps", ["Need more evidence"]))
                plan = self.orchestrator.replan(plan, feedback=feedback, blackboard=blackboard)
            coord_overhead_ms += (time.time() - c_start) * 1000
            agents_invoked.append(f"dag_orchestrator_node_r{round_num}")

            # Node 2: Parallel Retrieval DAG Node
            tasks = []
            c_prep = time.time()
            for subtask in plan.subtasks:
                ag = agents.get(subtask.assigned_agent)
                if ag:
                    tasks.append((subtask.assigned_agent, ag, subtask.sub_query))
            coord_overhead_ms += (time.time() - c_prep) * 1000

            def _dag_exec(t):
                ag_name, ag_inst, q = t
                if hasattr(ag_inst, "run"):
                    try:
                        return ag_name, ag_inst.run(q, repo_slug=repo_slug)
                    except TypeError:
                        return ag_name, ag_inst.run(blackboard)
                return ag_name, []

            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                batch_res = list(pool.map(_dag_exec, tasks))

            c_gather = time.time()
            for ag_name, frags in batch_res:
                agents_invoked.append(ag_name)
                if isinstance(frags, list):
                    blackboard.post_evidence(ag_name, frags)
            coord_overhead_ms += (time.time() - c_gather) * 1000

            # Node 3: Verification Node
            c_v = time.time()
            blackboard.set_status(BlackboardStatus.VERIFYING)
            coord_overhead_ms += (time.time() - c_v) * 1000
            agents_invoked.append("dag_verification_node")
            v_res = verifier.run(blackboard)

            # Conditional Edge Routing
            if v_res.get("sufficient", False):
                break
            else:
                if round_num < self.max_rounds:
                    round_num += 1
                    c_r = time.time()
                    blackboard.set_status(BlackboardStatus.REGATHERING)
                    coord_overhead_ms += (time.time() - c_r) * 1000
                else:
                    incomplete = True
                    break

        # Node 4: Reviewer Node
        c_rev = time.time()
        blackboard.set_status(BlackboardStatus.SYNTHESIZING)
        evidence_dicts = [
            {
                "id": e.id,
                "type": e.evidence_type,
                "text": e.content,
                "confidence": e.confidence,
                "title": e.title,
                "source_agent": e.source_agent
            }
            for e in blackboard.get_evidence()
        ]
        coord_overhead_ms += (time.time() - c_rev) * 1000

        reviewer_res = self.reviewer.review(
            {
                "run_id": run_id,
                "question": question,
                "evidence": evidence_dicts,
                "incomplete": incomplete,
                "gap_notes": v_res.get("gaps", [])
            },
            blackboard=blackboard
        )
        agents_invoked.append("dag_reviewer_node")

        return self._format_run_output(
            run_id=run_id,
            question=question,
            blackboard=blackboard,
            reviewer_res=reviewer_res,
            start_time=start_time,
            coordination_time_ms=min(coord_overhead_ms, (time.time() - start_time) * 1000),
            agents_invoked=agents_invoked,
            rounds=round_num
        )
