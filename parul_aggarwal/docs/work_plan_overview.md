# Parul Aggarwal (230540): Work Plan Overview

**Project:** Repository-Aware Multi-Agent AI for Software History and Development Decisions  
**Role:** Orchestration, Review / Final Response, Evaluation Dataset, and Architecture Patterns  
**Scope:** ONLY the components owned by Parul Aggarwal. Other agents (`code_analysis`, `history_rag`, `issue_pr`, `verification`) interface cleanly through the shared Blackboard.

---

## Deliverables Status

| # | Deliverable | Location | Status |
|---|---|---|---|
| 1 | Held-out evaluation question set + ground truth | `parul_aggarwal/eval_set/` | Completed |
| 2 | Orchestrator / Manager Agent | `parul_aggarwal/orchestrator/` | Completed |
| 3 | Reviewer / Final Response Agent | `parul_aggarwal/reviewer/` | Completed |
| 4 | Four architecture-pattern variants | `parul_aggarwal/architectures/` | Completed |
| 5 | Shared experiment runs and reports | `parul_aggarwal/docs/` | In Progress |

---

## Directory Structure

```
parul_aggarwal/
  eval_set/
    questions.jsonl          # 35 benchmark questions (7 dev, 28 heldout)
    labelling_guide.md       # Annotator guidelines, schema, and rules
    validator.py             # Schema & integrity validation script
    summary.py               # Dataset distribution & breakdown script
    heldout_hash.sha256      # SHA-256 hash of frozen held-out questions
  orchestrator/
    agent.py                 # Orchestrator / Manager Agent implementation
    schema.py                # Pydantic/dataclass schema & validation
  reviewer/
    agent.py                 # Reviewer / Final Response Agent implementation
    citation_checker.py      # Programmatic citation validation engine
    renderer.py              # Output template renderer
  architectures/
    base.py                  # Abstract base class & stub agents
    sequential.py            # Variant 1: Linear pipeline
    hierarchical.py          # Variant 2: Controller-directed execution
    event_driven.py          # Variant 3: Asynchronous event bus
    workflow_dag.py          # Variant 4: Directed acyclic graph with branching
  prompts/
    orchestrator_system.md   # Versioned system prompt for Orchestrator
    reviewer_system.md       # Versioned system prompt for Reviewer
    reviewer_entailment.md   # Entailment spot-check prompt
  tests/
    test_orchestrator.py     # 12 unit and dev evaluation tests
    test_reviewer.py         # 7 unit and adversarial tests
    test_architectures.py    # 7 parity & round limit tests across all 4 variants
  docs/
    work_plan_overview.md
    evaluation_question_set.md
    orchestrator_agent.md
    reviewer_agent.md
    architecture_variants.md
```
