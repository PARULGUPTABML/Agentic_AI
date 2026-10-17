# Repository-Aware Multi-Agent AI for Software History

A collaborative multi-agent system designed to explain **why** a software repository evolved the way it did. The system decomposes complex developer inquiries regarding design decisions, historical rationale, architectural changes, and authorship, retrieving and verifying evidence across commit histories, pull requests, issues, and code reviews to produce strictly grounded, fact-checked explanations.

---

## Architecture Overview

```
                        ┌───────────────────────────────┐
                        │         User Question         │
                        └──────────────┬────────────────┘
                                       │
                                       ▼
                        ┌───────────────────────────────┐
                        │      Orchestrator Agent       │
                        │ • Intent Classification       │
                        │ • Dynamic Entity Extraction   │
                        │ • Subtask Decomposition       │
                        └──────────────┬────────────────┘
                                       │
                 ┌─────────────────────┴─────────────────────┐
                 │ Coordination Topology (Configurable)      │
                 │ • Sequential Pipeline                     │
                 │ • Hierarchical Controller                 │
                 │ • Event-Driven Bus                        │
                 │ • Workflow DAG State Machine              │
                 └─────────────────────┬─────────────────────┘
                                       │
         ┌─────────────────────────────┼─────────────────────────────┐
         ▼                             ▼                             ▼
┌──────────────────┐          ┌──────────────────┐          ┌──────────────────┐
│ History/RAG      │          │ Code Analysis    │          │ Issue / PR       │
│ Agent            │          │ Agent            │          │ Agent            │
│ (ReAct Loop)     │          │ (Static AST)     │          │ (Issue & PR API) │
└────────┬─────────┘          └────────┬─────────┘          └────────┬─────────┘
         │                             │                             │
         └─────────────────────────────┼─────────────────────────────┘
                                       │
                                       ▼
                        ┌───────────────────────────────┐
                        │       Shared Blackboard       │
                        │ • Thread-safe evidence store  │
                        │ • Deduplication & confidence  │
                        │ • Verification Agent check    │
                        └──────────────┬────────────────┘
                                       │
                                       ▼
                        ┌───────────────────────────────┐
                        │        Reviewer Agent         │
                        │ • Programmatic Citation Check │
                        │ • Entailment Spot-Check       │
                        │ • Fact vs. Inference Isolation│
                        └──────────────┬────────────────┘
                                       │
                                       ▼
                        ┌───────────────────────────────┐
                        │    Structured Final Report    │
                        │ (Answer, Evidence, Confidence)│
                        └───────────────────────────────┘
```

---

## Core System Components

### 1. Orchestrator / Manager Agent (`orchestrator/`)
- **Intent Classification**: Classifies inquiries into `rationale`, `timeline`, `authorship`, and `superseded_implementation` using robust regex word-boundary detection.
- **Dynamic Entity Extraction**: Extracts file paths, functions, symbols, and architectural tokens without hardcoded lookups.
- **Resilience & Fallback**: Validates plan schemas with automated single-retry prompting and deterministic fallback generation.
- **Bounded Execution**: Enforces `MAX_ROUNDS = 2` to prevent unbounded re-planning cycles.

### 2. Repository History / RAG Agent (`agents/history_rag_agent.py`)
- **ReAct Loop**: Iterative Reason → Act → Observe reasoning cycles (up to 5 iterations).
- **Tool Suite**: `search_commits`, `search_issues`, `search_prs`, `search_all_history`, `get_linked`, and `finish`.
- **Hybrid Retrieval**: Combines BM25 lexical search, semantic embeddings, Reciprocal Rank Fusion (RRF), and graph expansion.

### 3. Evidence Verification Agent (`agents/evidence_verification_agent.py`)
- **Chain-of-Thought Evaluation**: Analyzes gathered evidence across 5 dimensions: WHAT, WHY, WHO/WHEN, CONTEXT, and COMPLETENESS.
- **Gap Detection**: Generates targeted `retry_queries` if evidence is insufficient to answer the question.

### 4. Reviewer / Final Synthesis Agent (`reviewer/`)
- **Grounded Synthesis**: Formulates clear responses grounded strictly in approved evidence.
- **Programmatic Citation Checker**: Pure-code citation validator verifying that 100% of cited evidence IDs exist in the approved set, stripping or flagging hallucinated references.
- **Inference vs. Fact Separation**: Strictly separates factual evidence claims from speculative inferences. If no causal inference is warranted, explicitly notes `Inference: None`.
- **Entailment Verification**: Optional automated entailment checking verifying claims against source evidence text.

### 5. Shared Blackboard (`agents/blackboard.py`)
- Thread-safe shared evidence store supporting structured fragment lifecycle:
  `GATHERING → VERIFYING → RE-GATHERING → SYNTHESIZING → DONE`
- Automated deduplication with confidence-based replacement and telemetry logging.

### 6. Four Coordination Architecture Patterns (`architectures/`)
Controlled comparison between four distinct coordination topologies sharing the same agent definitions:
- **Sequential** (`sequential.py`): Fixed linear execution strictly following the orchestrator plan.
- **Hierarchical** (`hierarchical.py`): Centralized controller managing concurrent worker threads.
- **Event-Driven** (`event_driven.py`): Reactive in-process message bus with decoupled concurrent workers.
- **Workflow DAG** (`workflow_dag.py`): Directed Acyclic Graph state machine with conditional branching and re-plan loops.
- **Honest Overhead Accounting**: Measures pure coordination latency (`coordination_overhead_ms <= total_latency_ms`) without conflating agent worker compute.

### 7. Evaluation Dataset & Benchmark Suite (`eval_set/`)
- **35 Grounded Questions**: 7 dev questions and 28 held-out questions grounded in real `seL4` microkernel repository history across rationale, timeline, authorship, and superseded implementations.
- **Real Git Verification**: 34 unique cited commit SHAs verified against official `seL4.git` commit objects.
- **Zero Hallucination Tolerance**: Includes unanswerable probe questions to test gap honesty.
- **Cryptographic Reproducibility**: Canonical SHA-256 digest validation (`heldout_hash.sha256`).

---

## Repository Structure

```
Agentic_AI/
├── architectures/              # Coordination architecture patterns
│   ├── base.py                 # Abstract base class & metrics accounting
│   ├── sequential.py           # Linear execution adhering to orchestrator plan
│   ├── hierarchical.py         # Controller-driven parallel dispatch
│   ├── event_driven.py         # In-process reactive event bus
│   └── workflow_dag.py         # Directed Acyclic Graph executor
├── orchestrator/               # Orchestrator agent
│   ├── agent.py                # Manager agent implementation
│   └── schema.py               # JSON schema & plan validation
├── reviewer/                   # Reviewer & synthesis agent
│   ├── agent.py                # Reviewer agent implementation
│   ├── citation_checker.py     # Pure-code citation validation engine
│   └── renderer.py             # Standardized Markdown response renderer
├── agents/                     # Specialized pipeline agents
│   ├── blackboard.py           # Shared evidence blackboard
│   ├── history_rag_agent.py    # ReAct git history retrieval agent
│   ├── evidence_verification_agent.py # Evidence sufficiency evaluator
│   ├── messaging.py            # Communication protocols (Blackboard, P2P, Broadcast, MCP)
│   ├── multi_agent_pipeline.py # End-to-end multi-agent pipeline
│   └── _llm_retry.py          # Resilient LLM invocation helper
├── eval_set/                   # Ground-truth evaluation dataset & tooling
│   ├── questions.jsonl         # 35 benchmark questions (7 dev, 28 heldout)
│   ├── heldout_hash.sha256     # Canonical SHA-256 digest of held-out set
│   ├── validator.py            # Real git commit & schema validation tool
│   ├── summary.py              # Dataset metrics and split distribution reporter
│   ├── generate_eval_set.py    # Dataset generator script
│   └── labelling_guide.md      # Ground-truth annotation standards
├── prompts/                    # Versioned prompt definitions
│   ├── orchestrator_system.md  # Orchestrator system prompt
│   ├── reviewer_system.md      # Reviewer system prompt
│   └── reviewer_entailment.md  # Entailment spot-check prompt
├── docs/                       # Architectural & methodology documentation
│   ├── system_overview.md      # End-to-end multi-agent system overview
│   ├── orchestrator_agent.md   # Orchestrator methodology guide
│   ├── reviewer_agent.md       # Reviewer & citation checker guide
│   ├── architecture_variants.md# 4 coordination architectures comparative guide
│   └── evaluation_question_set.md # Benchmark design & verification methodology
├── tests/                      # Unified test suite (61 tests)
│   ├── conftest.py             # Mock fixtures & environment setup
│   ├── test_agents.py          # Pipeline & retrieval unit/integration tests
│   ├── test_architectures.py   # Architecture parity & coordination tests
│   ├── test_orchestrator.py    # Intent, entity, schema & replan tests
│   └── test_reviewer.py        # Citation, entailment, & synthesis tests
├── agent_trace_runner.py       # Multi-agent trace runner for experimental runs
├── agent_visualizer.html       # Interactive web visualizer for execution traces
├── demo.py                     # Interactive CLI demonstration
├── run_eval_multi_agent.py     # Multi-scenario evaluation benchmark runner
└── test_real_pipeline.py       # End-to-end pipeline sanity test
```

---

## Getting Started

### Prerequisites
- Python 3.9+
- Git (optional, for validating commit SHAs against local `seL4.git`)

### Environment Setup

```bash
# Clone the repository
git clone https://github.com/<org>/Agentic_AI.git
cd Agentic_AI

# Install dependencies
pip install -r requirements.txt

# Configure environment (optional, defaults to mock for deterministic tests)
cp .env.example .env
```

---

## Running the Benchmark & Tests

### 1. Validate the Evaluation Dataset
Verify the 35 benchmark questions against the schema, ensure zero leakage between splits, check the canonical SHA-256 digest, and verify all cited commit SHAs against real git history:

```bash
python3 eval_set/validator.py --require-git
```

### 2. Run the Full Test Suite (61 Tests)
Execute all unit, integration, parity, and adversarial tests:

```bash
python3 -m pytest tests/ -v
```

### 3. Run the Interactive Visualizer
Generate execution traces and inspect them in the browser:

```bash
python3 agent_trace_runner.py
open agent_visualizer.html
```

### 4. Run the Live CLI Demo

```bash
python3 demo.py
```

---

## References

1. P. Lewis et al., "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks," NeurIPS, 2020.
2. S. Yao et al., "ReAct: Synergizing Reasoning and Acting in Language Models," ICLR, 2023.
3. J. Wei et al., "Chain-of-Thought Prompting Elicits Reasoning in Large Language Models," NeurIPS, 2022.
4. H. P. Nii, "The Blackboard Model of Problem Solving," AI Magazine, 1986.
5. Model Context Protocol, Architecture & Specification, 2026.
