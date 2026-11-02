# Repository-Aware Multi-Agent AI for Software History and Development Decisions

A collaborative multi-agent system designed to answer **why** an existing software system was built the way it was. By treating repository history as a layered evidence source (Code → Commits → Pull Requests → Issues → Reviews → Documentation), the system retrieves, verifies, and synthesizes traceable historical evidence to explain software design decisions.

---

## Academic Information

- **Course:** AGI — CSE 2
- **Institution:** BML Munjal University
- **Project Guide:** Pranshu Tiwari
- **Team Members & Work Division:**

| Team Member | Roll No. | Agents Owned | Infrastructure & Data Streams |
|---|---|---|---|
| **Parul Gupta** | 230506 | • Repository History / RAG Agent<br>• Evidence Verification Agent | • Commit/diff history extraction & hybrid index<br>• Thread-safe Blackboard schema<br>• 4 Messaging Protocols comparison (Blackboard vs. P2P vs. Broadcast vs. MCP) |
| **Arpita Bansal** | 230546 | • Code Analysis Agent<br>• Issue / PR Agent | • Issue/PR/review corpus extraction & linking<br>• LLM-as-Judge evaluation framework & rubric<br>• Observability pipeline (latency & token tracking)<br>• Results analysis dashboard |
| **Parul Aggarwal** | 230540 | • Orchestrator / Manager Agent<br>• Reviewer / Final Response Agent | • 4 Architecture pattern variants (Sequential, Hierarchical, Event-Driven, Workflow DAG)<br>• 35-Question held-out evaluation benchmark set |

---

## System Architecture

```
                                  ┌───────────────────────────┐
                                  │    Developer Question     │
                                  └─────────────┬─────────────┘
                                                │
                                                ▼
                                  ┌───────────────────────────┐
                                  │    Orchestrator Agent     │
                                  │  (Intent, Entity, Plan)   │
                                  └─────────────┬─────────────┘
                                                │
                        ┌───────────────────────┴───────────────────────┐
                        │   Selected Coordination Architecture          │
                        │   (Sequential / Hierarchical / Event / DAG)   │
                        └───────────────────────┬───────────────────────┘
                                                │
                 ┌──────────────────────────────┼──────────────────────────────┐
                 ▼                              ▼                              ▼
      ┌────────────────────┐         ┌────────────────────┐         ┌────────────────────┐
      │ History / RAG      │         │ Code Analysis      │         │ Issue / PR         │
      │ Agent (ReAct loop) │         │ Agent (AST/Search) │         │ Agent (Discussion) │
      └──────────┬─────────┘         └──────────┬─────────┘         └──────────┬─────────┘
                 │                              │                              │
                 └──────────────────────────────┼──────────────────────────────┘
                                                │
                                                ▼
                                  ┌───────────────────────────┐
                                  │     Shared Blackboard     │
                                  │ (Thread-safe memory store)│
                                  └─────────────┬─────────────┘
                                                │
                                                ▼
                                  ┌───────────────────────────┐
                                  │ Evidence Verification     │
                                  │ Agent (CoT Sufficiency)   │
                                  └─────────────┬─────────────┘
                                                │
                                 [Sufficient]   │   [Insufficient: Refined Query Loop]
                                 ┌──────────────┴──────────────┐
                                 │                             │
                                 ▼                             ▼
                    ┌─────────────────────────┐   ┌─────────────────────────┐
                    │ Reviewer / Response     │   │ Re-retrieve from Agents │
                    │ Agent (Grounded Answer) │   └─────────────────────────┘
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │ Evidence-Based Answer   │
                    │ with Citations          │
                    └─────────────────────────┘
```

---

## What Was Implemented

### 1. The 6 Specialized Agents
1. **Orchestrator / Manager Agent (`orchestrator/agent.py`, `agents/orchestrator_agent.py`)**
   - Parses the developer's question, performs entity and keyword extraction, and delegates tasks to retrieval agents.
2. **Repository History / RAG Agent (`agents/history_rag_agent.py`)**
   - Mandatory RAG component. Executes an autonomous **ReAct** loop (Reason → Act → Observe) over commit history, diffs, and superseding code changes using hybrid BM25 + vector search and graph expansion.
3. **Code Analysis Agent (`agents/code_analysis_agent.py`)**
   - Deterministic AST and symbol parser. Grounds questions in current snapshot files, functions, macros, and structs (`find_files`, `find_definitions`, `grep_keywords`, `outline`).
4. **Issue / PR Agent (`agents/issue_pr_agent.py`)**
   - Traverses GitHub issues, pull request threads, and review comments. Walks the link graph from discussions to the concrete commits they introduced.
5. **Evidence Verification Agent (`agents/evidence_verification_agent.py`)**
   - Performs a **Chain-of-Thought (CoT)** sufficiency check across 5 dimensions (WHAT, WHY, WHO/WHEN, CONTEXT, COMPLETENESS). Emits structured verdicts and targeted retry queries when gaps exist.
6. **Reviewer / Final Response Agent (`reviewer/agent.py`, `agents/reviewer_agent.py`)**
   - Synthesizes verified evidence into a final explanation, enforces strict evidence-versus-inference separation, and validates factual citations (`[commit:...]`, `[pr:...]`, `[issue:...]`).

### 2. Multi-Agent Coordination Substrates
- **Shared Blackboard (`agents/blackboard.py`):** Thread-safe central store tracking lifecycle states (`GATHERING` → `VERIFYING` → `REGATHERING` → `SYNTHESIZING` → `DONE`), deduplicating evidence by confidence, and recording per-agent execution logs.
- **Messaging Protocol Comparison (`agents/messaging.py`):** Implements and benchmarks four inter-agent communication patterns:
  1. *Blackboard:* Shared read/write memory store.
  2. *Peer-to-Peer (P2P):* Direct agent invocation and return passing.
  3. *Broadcast:* Publish/subscribe event bus via `MessageBus`.
  4. *MCP-Style:* Agents exposed as callable tool interfaces.
- **4 Coordination Topologies (`architectures/`):**
  1. *Sequential:* Ordered pipeline with synchronous hand-offs.
  2. *Hierarchical:* Manager coordinates worker pool in parallel.
  3. *Event-Driven:* Reactive agent interactions over an event bus.
  4. *Workflow DAG:* Directed acyclic graph with conditional branching and re-retrieval loops.

### 3. Evaluation & Observability Pipeline
- **LLM-as-Judge Framework (`evaluation/`):**
  - Reference-guided evaluation with an explicit 4-point scoring rubric (`evaluation/rubric.py`).
  - Automated citation precision, recall, grounded ratio, and hallucination scoring (`evaluation/metrics.py`).
  - Baselines: Configuration A (no repository context) and Configuration B (code snapshot only).
- **Observability System (`observability/`):**
  - Thread-safe instrumentation capturing execution latency, LLM calls, prompt/completion token usage, and tool invocations per agent.
- **Ablation Study Engine (`evaluation/ablation_cases.py`, `run_ablation_study.py`):**
  - Evaluates 2-in-1 merged agents (Code+History, History+PR, Orchestrator+Verifier, Verifier+Reviewer) and ablated configurations (minus Verifier, minus Code Analysis, monolithic baseline).
- **Dashboard & Visualization (`dashboard/`, `server.py`, `static/`):**
  - Statistical analysis module with paired permutation tests and bootstrap confidence intervals (`evaluation/stats.py`).
  - Standalone HTML report generator (`dashboard/build_dashboard.py`).
  - Full interactive web interface (`server.py`, `static/index.html`).

---

## Repository Structure

```
Agentic_AI/
├── agents/                       # Specialized agent implementations
│   ├── orchestrator_agent.py     # Orchestrator / Manager Agent
│   ├── history_rag_agent.py      # History / RAG Agent (ReAct loop)
│   ├── code_analysis_agent.py    # Code Analysis Agent (AST & symbol search)
│   ├── issue_pr_agent.py         # Issue / PR Agent (GitHub discussions)
│   ├── evidence_verification_agent.py # Evidence Verification Agent (CoT audit)
│   ├── reviewer_agent.py         # Reviewer / Final Response Agent
│   ├── blackboard.py             # Thread-safe shared Blackboard evidence store
│   ├── messaging.py              # 4 Messaging protocols (Blackboard, P2P, Broadcast, MCP)
│   ├── merged_agents.py          # 2-in-1 merged agents and ablation variants
│   └── multi_agent_pipeline.py   # Multi-agent end-to-end execution pipeline
├── architectures/                # 4 Coordination architecture variants
│   ├── sequential.py             # Sequential pipeline
│   ├── hierarchical.py           # Hierarchical controller
│   ├── event_driven.py           # Event-driven bus
│   └── workflow_dag.py           # Workflow DAG state machine
├── evaluation/                   # LLM-as-Judge & ablation framework
│   ├── rubric.py                 # Evaluation rubric definition
│   ├── judge.py                  # LLM-as-Judge engine
│   ├── metrics.py                # Retrieval and hallucination metrics
│   ├── baselines.py              # Baseline A (no context) & B (code only)
│   ├── stats.py                  # Bootstrap CIs & permutation tests
│   └── ablation_cases.py         # Merged and ablated configuration cases
├── ingestion/                    # Repository data extraction & link graph
│   ├── extract_code.py           # Source code snapshot extractor
│   ├── extract_git.py            # Git commit metadata & diff extractor
│   ├── extract_github.py         # GitHub issues and PR discussion extractor
│   ├── issue_pr_extractor.py     # Issue/PR crawler CLI
│   ├── linker.py                 # Commit <-> issue/PR linking
│   └── corpus_store.py           # BM25 corpus store & link index
├── observability/                # Run instrumentation & metrics tracker
│   ├── instrument.py             # Agent runtime instrumentation & error handling
│   ├── tracker.py                # LLM call and token consumption tracker
│   └── run_logger.py             # JSONL run recorder
├── dashboard/                    # Analysis tables and HTML visualizer
│   ├── analysis.py               # Markdown and CSV comparison tables
│   └── build_dashboard.py        # Self-contained HTML dashboard builder
├── dataset/                      # Evaluation benchmark
│   └── benchmark.jsonl           # 35 verified evaluation questions with gold evidence
├── rag/                          # Shared RAG retriever and LLM utilities
├── static/                       # Web application frontend assets (UI & charts)
├── tests/                        # Unified automated test suite (148 tests)
├── demo.py                       # Step-by-step CLI demonstration
├── run_experiments.py            # Experiment runner across configs, architectures & models
├── run_ablation_study.py         # Architectural ablation runner
├── server.py                     # Interactive web platform server
└── agent_visualizer.html         # Interactive agent trace visualizer
```

---

## Quickstart & Verification

### 1. Installation
Clone the repository and install requirements:
```bash
git clone https://github.com/PARULGUPTABML/Agentic_AI.git
cd Agentic_AI
pip install -r requirements.txt
```

### 2. Run the Full Test Suite
The repository includes a comprehensive offline test suite covering all agents, Blackboard mechanics, messaging protocols, evaluation rubrics, and architecture variants without requiring live API keys:
```bash
python -m pytest tests/ -v
```
*(All 148 automated tests pass out of the box).*

### 3. Run the CLI Demo
Inspect the step-by-step reasoning of the agents, the Blackboard lifecycle, and protocol comparisons:
```bash
python demo.py
```

### 4. Run Experiments
Execute evaluation across baseline configurations and architecture variants:
```bash
python run_experiments.py --offline --limit 5
```

### 5. Launch the Interactive Web Platform
Start the local server to explore the multi-agent console, architecture comparison, and ablation graphs:
```bash
python server.py
```
Open `http://localhost:5000` in your web browser.

---

## References

1. P. Lewis et al., "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks," NeurIPS 33, 2020.
2. S. Yao et al., "ReAct: Synergizing Reasoning and Acting in Language Models," ICLR, 2023.
3. J. Wei et al., "Chain-of-Thought Prompting Elicits Reasoning in Large Language Models," NeurIPS 35, 2022.
4. H. P. Nii, "The Blackboard Model of Problem Solving and the Evolution of Blackboard Architectures," AI Magazine, 1986.
5. Model Context Protocol, documentation and specification, 2026.
