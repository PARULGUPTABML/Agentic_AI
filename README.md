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
├── agents/                     # Specialized Multi-Agent Roles (All 6 Agents + Merged)
│   ├── orchestrator_agent.py   # Agent 1: Intent parsing & task planning
│   ├── code_analysis_agent.py  # Agent 2: AST & static code symbol retrieval
│   ├── history_rag_agent.py    # Agent 3: Git commit history & blame traversal
│   ├── issue_pr_agent.py       # Agent 4: GitHub PRs, issues, & code reviews
│   ├── evidence_verification_agent.py # Agent 5: CoT gatekeeper & sufficiency check
│   ├── reviewer_agent.py       # Agent 6: Grounded citation synthesis
│   ├── merged_agents.py        # 2-in-1 Merged Agents (Code+History, History+PR, etc.)
│   ├── blackboard.py           # Shared thread-safe evidence blackboard store
│   └── messaging.py            # Pub/sub inter-agent event bus
├── architectures/              # 4 Coordination Topologies
│   ├── sequential.py           # 1. Sequential Pipeline (linear serial execution)
│   ├── hierarchical.py         # 2. Hierarchical Controller (parallel worker pool)
│   ├── event_driven.py         # 3. Event-Driven Bus (reactive pub/sub bus)
│   └── workflow_dag.py         # 4. Workflow DAG (state machine with fork/join)
├── evaluation/                 # Rigorous Evaluation & Ablation Framework
│   ├── ablation_cases.py       # All 8 ablation & merging test cases
│   ├── judge.py                # LLM-as-a-Judge implementation (4-point rubric)
│   ├── metrics.py              # Precision, Recall, Hallucination, Citation coverage
│   └── stats.py                # Paired permutation tests & bootstrap CIs
├── ingestion/                  # Multi-Modal Repository Indexing
│   ├── extract_code.py         # C/C++ AST syntax parsing
│   ├── extract_git.py          # Git commit log & blame extractor
│   ├── issue_pr_extractor.py   # GitHub issues & PR thread extractor
│   └── build_index.py          # BM25 search indices builder
├── static/                     # Web Application Frontend (Dashboard & Live Query)
│   ├── index.html              # 4-Tab Single Page Application UI
│   ├── style.css               # Glassmorphism dark-theme design
│   └── app.js                  # Frontend logic & live API integration
├── prompts/                    # Versioned Prompt Definitions
│   ├── orchestrator_system.md  # Orchestrator system prompt
│   ├── reviewer_system.md      # Reviewer system prompt
│   └── reviewer_entailment.md  # Entailment spot-check prompt
├── docs/                       # Architectural & Methodology Documentation
├── tests/                      # Unified Test Suite (148 tests)
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
