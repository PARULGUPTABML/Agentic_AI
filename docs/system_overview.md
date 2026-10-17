# Repository-Aware Multi-Agent AI: System Architecture & Implementation Overview

**Project:** Repository-Aware Multi-Agent AI for Software History and Development Decisions  
**Repository:** seL4 Microkernel (`seL4/seL4`)  
**Scope:** Collaborative multi-agent architecture combining intent-driven planning, git history retrieval, static code analysis, evidence sufficiency verification, and citation-checked grounded synthesis.

---

## 1. System Architecture & Components

The multi-agent system is structured into cohesive, modular subsystems communicating through a thread-safe shared state (Blackboard):

| Component | Directory / Artifacts | Role & Capabilities |
|---|---|---|
| **Orchestrator Agent** | `orchestrator/agent.py`, `schema.py`, `prompts/orchestrator_system.md` | Intent classification (`rationale`, `timeline`, `authorship`, `superseded_implementation`), dynamic entity extraction, JSON plan schema validation with single retry and deterministic fallback, bounded re-planning (`MAX_ROUNDS = 2`). |
| **History / RAG Agent** | `agents/history_rag_agent.py`, `agents/_llm_retry.py` | ReAct reasoning loop (Reason → Act → Observe) with tools: `search_commits`, `search_issues`, `search_prs`, `search_all_history`, `get_linked`, and `finish`. Hybrid retrieval with BM25 + vector search + RRF. |
| **Evidence Verification Agent** | `agents/evidence_verification_agent.py` | Chain-of-Thought evidence sufficiency evaluation across WHAT, WHY, WHO/WHEN, CONTEXT, and COMPLETENESS. Generates targeted `retry_queries`. |
| **Reviewer / Synthesis Agent** | `reviewer/agent.py`, `citation_checker.py`, `renderer.py`, `prompts/reviewer_system.md` | Grounded drafting, 100% programmatic citation validation against approved evidence IDs, strict inference vs. fact isolation, gap honesty, template rendering, and optional entailment spot-check. |
| **Shared Blackboard** | `agents/blackboard.py`, `agents/messaging.py` | Thread-safe evidence store with fragment lifecycle management (`GATHERING → VERIFYING → RE-GATHERING → SYNTHESIZING → DONE`), confidence-based deduplication, and execution telemetry. |
| **Coordination Topologies** | `architectures/base.py`, `sequential.py`, `hierarchical.py`, `event_driven.py`, `workflow_dag.py` | Four comparative coordination variants sharing identical agent definitions, with honest coordination overhead accounting (`coordination_overhead_ms <= total_latency_ms`). |
| **Benchmark & Eval Set** | `eval_set/questions.jsonl`, `validator.py`, `summary.py`, `heldout_hash.sha256` | 35 questions (7 dev, 28 held-out) grounded in real seL4 git history. 34 unique real commit SHAs verified against git objects; canonical SHA-256 frozen hash. |

---

## 2. Shared Interface and System Boundaries

1. **Blackboard Contract**:
   - `blackboard = Blackboard(question, run_id, repo_slug)`
   - Agents post and filter evidence fragments via `post_evidence(EvidenceFragment(...))`.
   - Reviewer finalizes the response via `blackboard.final_answer = rendered` and `blackboard.set_status(BlackboardStatus.DONE)`.
2. **Telemetry & Observability**:
   - Every invocation logs through `blackboard.log(agent, action, detail, duration_ms, tokens_used)`.
3. **Execution Safety & Bounding**:
   - Hard execution limits enforce `MAX_ROUNDS = 2` and bounded LLM call counts, eliminating infinite re-plan loops.
4. **Separation of Concerns**:
   - **Orchestrator**: Decomposes inquiries and routes subtasks; does NOT execute git log retrieval or write final answers.
   - **Retrieval & Verification**: Discovers and validates historical facts; does NOT synthesize final developer-facing summaries.
   - **Reviewer**: Synthesizes ONLY from approved evidence fragments; does NOT perform new retrieval or alter sufficiency verdicts.
   - **Architectures**: Swaps the execution pattern (Linear, Parallel Controller, Reactive Bus, DAG); the underlying agents and prompt contracts remain strictly identical.

---

## 3. Directory Layout

```
Agentic_AI/
├── architectures/
│   ├── base.py                 # Abstract base class & metrics accounting
│   ├── sequential.py           # Linear execution adhering to orchestrator plan
│   ├── hierarchical.py         # Controller-driven parallel dispatch
│   ├── event_driven.py         # In-process reactive event bus
│   └── workflow_dag.py         # Directed Acyclic Graph executor
├── orchestrator/
│   ├── agent.py                # Manager agent implementation
│   └── schema.py               # JSON schema & plan validation
├── reviewer/
│   ├── agent.py                # Reviewer agent implementation
│   ├── citation_checker.py     # Pure-code citation validation engine
│   └── renderer.py             # Standardized Markdown response renderer
├── agents/
│   ├── blackboard.py           # Shared evidence blackboard
│   ├── history_rag_agent.py    # ReAct git history retrieval agent
│   ├── evidence_verification_agent.py # Evidence sufficiency evaluator
│   ├── messaging.py            # Communication protocols (Blackboard, P2P, Broadcast, MCP)
│   ├── multi_agent_pipeline.py # End-to-end multi-agent pipeline
│   └── _llm_retry.py          # Resilient LLM invocation helper
├── eval_set/
│   ├── questions.jsonl         # 35 benchmark questions (7 dev, 28 heldout)
│   ├── heldout_hash.sha256     # Canonical SHA-256 digest of held-out set
│   ├── validator.py            # Real git commit & schema validation tool
│   ├── summary.py              # Dataset metrics and split distribution reporter
│   ├── generate_eval_set.py    # Dataset generator script
│   └── labelling_guide.md      # Ground-truth annotation standards
├── prompts/
│   ├── orchestrator_system.md  # Orchestrator system prompt
│   ├── reviewer_system.md      # Reviewer system prompt
│   └── reviewer_entailment.md  # Entailment spot-check prompt
├── docs/
│   ├── system_overview.md      # System architecture and implementation guide
│   ├── orchestrator_agent.md   # Orchestrator methodology guide
│   ├── reviewer_agent.md       # Reviewer & citation checker guide
│   ├── architecture_variants.md# 4 coordination architectures comparative guide
│   └── evaluation_question_set.md # Benchmark design & verification methodology
├── tests/
│   ├── conftest.py             # Mock fixtures & environment setup
│   ├── test_agents.py          # Pipeline & retrieval unit/integration tests
│   ├── test_architectures.py   # Architecture parity & coordination tests
│   ├── test_orchestrator.py    # Intent, entity, schema & replan tests
│   └── test_reviewer.py        # Citation, entailment, & synthesis tests
└── agent_trace_runner.py       # Multi-agent trace runner for experimental runs
```

---

## 4. Verification and Parity

All subsystems are verified via a unified test suite (61 tests in `tests/`) and ground-truth validation:
- `python3 eval_set/validator.py --require-git`: 100% compliant across all 35 real seL4 historical questions.
- `python3 -m pytest tests/ -v`: 61/61 passing across all agents, coordination architectures, orchestrator policies, citation validation, and integration pipelines.
