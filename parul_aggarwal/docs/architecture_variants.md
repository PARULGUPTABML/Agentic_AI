# Four Coordination Architecture Patterns Documentation

**Owner:** Parul Aggarwal (230540)  
**Deliverable Window:** Weeks 6-9  
**Artifacts:** `parul_aggarwal/architectures/sequential.py`, `hierarchical.py`, `event_driven.py`, `workflow_dag.py`, `base.py`

---

## 1. Pattern Overview

| Pattern | Flow | Dispatch | Coordination Overhead |
|---|---|---|---|
| **Sequential** | Orchestrator -> Code -> History -> Issue/PR -> Verification -> Reviewer | Fixed linear order | Minimal coordination logic, highest end-to-end latency |
| **Hierarchical** | Orchestrator Controller -> Parallel Retrieval -> Verification -> Replan/Reviewer | Centrally dispatched in parallel | Balanced control and tracing |
| **Event-Driven** | Queue/Bus: `question_received` -> `plan_ready` -> `evidence_posted` -> `approved` | Reactive subscriptions | Loose coupling, event loop overhead |
| **Workflow DAG** | Formal DAG with conditional branching edges: Retrieval -> Verification -> Reviewer | Parallel DAG nodes | Explicit state machine, robust branch control |

---

## 2. Parity Testing & Invariant Verification

- All 4 variants tested against dev questions in `parul_aggarwal/tests/test_architectures.py`.
- Parity criteria:
  - 100% adherence to template rendering and output schema.
  - Zero invalid citations across all four variants.
  - Consistent confidence reporting under deterministic stubs.
  - Clean termination at `MAX_ROUNDS` on adversarial insufficient evidence.
