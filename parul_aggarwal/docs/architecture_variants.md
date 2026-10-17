# Four Coordination Architecture Patterns: Methodology and Build Guide

**Owner:** Parul Aggarwal (230540)  
**Deliverable Window:** Weeks 6-9  
**Artifacts:** `parul_aggarwal/architectures/base.py`, `sequential.py`, `hierarchical.py`, `event_driven.py`, `workflow_dag.py`, `tests/test_architectures.py`

---

## 1. Purpose & Controlled-Comparison Principles

The objective is to compare four coordination patterns empirically on latency, accuracy, and coordination overhead using the same underlying agents and prompt definitions.
1. **Identical Agent Set**: All four variants use the exact same Orchestrator, Reviewer, and retrieval agents.
2. **Common Interface**: Every architecture inherits from `Architecture` in `base.py` and implements:
   ```python
   def run(self, question: str, run_id: str, repo_slug: str, retrieval_agents, verification_agent) -> dict
   ```
3. **Honest Coordination Overhead Accounting**:
   - `coordination_overhead_ms` measures only the time spent in planning, queue dispatch, gathering, and routing decisions.
   - Worker execution time inside `agent.run()` is tracked as agent work, NOT coordination overhead.
   - Coordination overhead is strictly bounded: `coordination_overhead_ms <= total_latency_ms`.

---

## 2. The Four Variants

### 2.1 Sequential (`sequential.py`)
- **Topology:** Fixed linear execution.
- **Plan Adherence:** Inspects `plan.subtasks` and invokes **only** the agents assigned in the plan (does not blindly invoke all three agents).
- **Insufficiency:** Invokes `orchestrator.replan(previous_plan, feedback)` and loops up to `MAX_ROUNDS = 2`.
- **Profile:** Simple and deterministic; higher latency due to lack of concurrency.

### 2.2 Hierarchical (`hierarchical.py`)
- **Topology:** Centralized controller pattern.
- **Execution:** Orchestrator controller plans tasks, dispatches retrieval subtasks concurrently via a worker thread pool, aggregates evidence, consults Verification, and re-plans or delegates to Reviewer.
- **Profile:** Centralized tracing and control with parallel retrieval.

### 2.3 Event-Driven (`event_driven.py`)
- **Topology:** Reactive in-process message bus (`InProcessEventBus`).
- **Events:** `question_received` -> `plan_ready` -> `evidence_posted` -> `evidence_approved` / `evidence_insufficient` -> `answer_complete`.
- **Concurrency:** When `plan_ready` fires, retrieval agents execute concurrently in worker threads.
- **Termination:** Guarded by completion events, `MAX_ROUNDS`, and a hard timeout.

### 2.4 Workflow DAG (`workflow_dag.py`)
- **Topology:** Formal Directed Acyclic Graph executor with state machine.
- **Graph Nodes:** `orchestrator_node` -> `parallel_retrieval_node` -> `verification_node` -> conditional branching edge (`replan` loop or `reviewer_node`).
- **Profile:** Explicit state transitions and robust error handling.

---

## 3. Parity Testing and Empirical Results

In `parul_aggarwal/tests/test_architectures.py`, all four variants were evaluated across dev questions from real seL4 history with deterministic stubs:
- **Correctness:** 100% of runs produced valid answers adhering to the template with zero invalid citations.
- **Plan Fidelity:** Sequential confirmed to only invoke planned agents (e.g. for authorship queries, invokes only `history_rag` and `issue_pr`).
- **Round Limiting:** Verified that all 4 variants terminate cleanly at `MAX_ROUNDS` on adversarial insufficient evidence.
- **Overhead:** Coordination overhead verified to be honest and non-exceeding total elapsed time across all variants.

---

## 4. Definition of Done Checklist

- [x] Shared base module and common interface implemented (`base.py`)
- [x] All 4 variants implemented behind identical `run()` entry point
- [x] Sequential adheres strictly to Orchestrator plan
- [x] Event-Driven features concurrent worker dispatch
- [x] Honest coordination overhead accounting implemented
- [x] Parity test passes across all 4 variants on dev questions
- [x] 8 architecture and parity tests passing in `tests/test_architectures.py`
