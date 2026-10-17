# Parul Aggarwal (230540): Work Plan Overview

**Project:** Repository-Aware Multi-Agent AI for Software History and Development Decisions  
**Role:** Orchestration, Review / Final Response, Evaluation Dataset, and Coordination Architecture Patterns  
**Owner:** Parul Aggarwal (230540)  
**Scope:** ONLY the components owned by Parul Aggarwal. Other members' agents (`history_rag`, `evidence_verification`, `code_analysis`, `issue_pr`) are treated as modular components communicating through the shared Blackboard.

---

## 1. Deliverables and Build Schedule

| # | Deliverable | Location | Build Window | Definition of Done Checklist | Status |
|---|---|---|---|---|---|
| 1 | Held-out evaluation question set + ground truth | `parul_aggarwal/eval_set/` | Weeks 2-3 | 35 questions, 100% verified against real seL4 history, canonical SHA-256 hash, validator passes, labelling guide | **Complete** |
| 2 | Orchestrator / Manager Agent | `parul_aggarwal/orchestrator/` | Weeks 3-5 | Intent classification, dynamic entity extraction, prompt in `prompts/`, schema validation with retry & fallback, re-plan round limit, Blackboard logging | **Complete** |
| 3 | Reviewer / Final Response Agent | `parul_aggarwal/reviewer/` | Weeks 5-6 | Grounded drafting, pure-code citation checker (100% validity), inference vs. evidence separation, gap honesty, template renderer, entailment check hook | **Complete** |
| 4 | Four architecture-pattern variants | `parul_aggarwal/architectures/` | Weeks 6-9 | Sequential (plan-abiding), Hierarchical, Event-Driven (parallel workers), Workflow DAG, common base interface, honest coordination overhead accounting, parity tests | **Complete** |
| 5 | Empirical reports and documentation | `parul_aggarwal/docs/` | Weeks 8-11 | Step-by-step methodologies, schema contracts, empirical variance comparisons, failure modes analysis | **Complete** |

---

## 2. Shared Interface and System Boundaries

1. **Blackboard Contract**:
   - The Blackboard schema is owned by Parul Gupta (`agents/blackboard.py`).
   - We do not redefine it; we use `Blackboard`, `EvidenceFragment`, and `BlackboardStatus`.
   - Reviewer sets `blackboard.final_answer = rendered` and `blackboard.set_status(BlackboardStatus.DONE)`.
2. **Logging Format**:
   - Every invocation logs through `blackboard.log(agent, action, detail, duration_ms, tokens_used)`.
3. **Bounded Execution**:
   - Hard stop at `MAX_ROUNDS = 2` and `MAX_LLM_CALLS = 4`. Never allow unbounded re-plan loops.
4. **Separation of Concerns**:
   - Orchestrator: does NOT retrieve evidence or write answers.
   - Reviewer: writes ONLY from supplied approved evidence; does NOT retrieve or re-verify.
   - Architectures: swap only the coordination topology; agents remain identical.

---

## 3. Directory Layout

```
parul_aggarwal/
  eval_set/
    questions.jsonl          # 35 benchmark questions (7 dev, 28 heldout) from real seL4 git history
    labelling_guide.md       # Full annotation guide, criteria for key facts, conflict resolution
    validator.py             # Schema, split leakage, and real seL4 commit SHA verification tool
    summary.py               # Summary breakdown script by type, difficulty, and splits
    generate_eval_set.py     # Verified reproducible generator script
    heldout_hash.sha256      # Canonical SHA-256 digest of frozen heldout set
  orchestrator/
    agent.py                 # Orchestrator / Manager Agent with retry & fallback
    schema.py                # Strict JSON schema, dataclasses, and fallback plan generator
  reviewer/
    agent.py                 # Reviewer / Final Response Agent with entailment check
    citation_checker.py      # Pure-code programmatic citation validator
    renderer.py              # Template renderer conforming to specification
  architectures/
    base.py                  # Abstract base class, stub agents, and honest overhead accounting
    sequential.py            # Variant 1: Fixed linear pipeline strictly following plan
    hierarchical.py          # Variant 2: Controller-directed parallel dispatch
    event_driven.py          # Variant 3: Reactive event bus with concurrent workers
    workflow_dag.py          # Variant 4: Directed Acyclic Graph with conditional branch edges
  prompts/
    orchestrator_system.md   # Versioned system prompt for Orchestrator
    reviewer_system.md       # Versioned system prompt for Reviewer
    reviewer_entailment.md   # Entailment spot-check prompt
  tests/
    test_orchestrator.py     # 13 tests covering schema, intents, word boundaries, re-plans, determinism
    test_reviewer.py         # 8 tests covering citation checking, inference separation, entailment, Blackboard
    test_architectures.py    # 8 tests covering parity across dev queries, plan adherence, round limits
  docs/
    work_plan_overview.md
    evaluation_question_set.md
    orchestrator_agent.md
    reviewer_agent.md
    architecture_variants.md
```
