# ReadMind: Codebase File Structure & Viva Presentation Guide

> **Quick Summary (Hindi & English):**
> Yeh guide aapko code presentation aur viva mein help karegi. Agar Sir / Professor code mein se poochte hain: *"Kaunsi file kahan hai? Yeh part kahan implement kiya hai?"*, aap directly is document ke reference se turant file dikha sakte hain. Har file ka naam self-explanatory aur clear rakha gaya hai.

---

## 📁 1. `agents/` — All 6 Agents + Merged Agents + Coordination
Yahan ReadMind ke **saare 6 agents**, **merged 2-in-1 agents**, aur unka **blackboard coordination** hai.

| File Path | Role / Purpose | Explain to Sir (In Simple Words) |
|---|---|---|
| [`agents/orchestrator_agent.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/agents/orchestrator_agent.py) | **Agent 1: Orchestrator** | Question ka intent samajhta hai, domain entities extract karta hai, aur execution plan formulate karta hai. |
| [`agents/code_analysis_agent.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/agents/code_analysis_agent.py) | **Agent 2: Code Analysis** | Active codebase ka AST parse karta hai, C/C++ function declarations aur struct definitions dhoondhta hai. |
| [`agents/history_rag_agent.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/agents/history_rag_agent.py) | **Agent 3: History RAG** | Git commit history, code diffs, aur `git blame` logs traverse karta hai taaki historical *why* pata chal sake. |
| [`agents/issue_pr_agent.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/agents/issue_pr_agent.py) | **Agent 4: Issue & PR** | GitHub PR discussions, issue bug reports, aur inline code reviews retrieve karta hai developer consensus ke liye. |
| [`agents/evidence_verification_agent.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/agents/evidence_verification_agent.py) | **Agent 5: Evidence Verification** | Chain-of-Thought (CoT) gatekeeper hai. Retrieval sufficiency verify karta hai aur hallucination rokta hai. |
| [`agents/reviewer_agent.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/agents/reviewer_agent.py) | **Agent 6: Reviewer** | Verified evidence ko grounded answer mein synthesize karta hai aur har factual sentence pe citations audit karta hai. |
| [`agents/merged_agents.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/agents/merged_agents.py) | **Merged (2-in-1) & Ablation Agents** | **(Merging wala part)** Code+History, History+PR, Orchestrator+Verifier, Verifier+Reviewer merged workers aur monolithic agent. |
| [`agents/blackboard.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/agents/blackboard.py) | **Shared Blackboard Memory** | Thread-safe shared memory jahan saare agents evidence fragments read aur write karte hain. |
| [`agents/messaging.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/agents/messaging.py) | **Inter-Agent Message Bus** | Pub/Sub messaging bus for decoupled event-driven communication between agents. |
| [`agents/multi_agent_pipeline.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/agents/multi_agent_pipeline.py) | **Sequential Pipeline Execution** | Sequential multi-agent answering loop with verification and re-planning. |
| [`agents/__init__.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/agents/__init__.py) | **Package Index** | Exports all 6 agents and merged agents for clean imports. |

---

## 🏛️ 2. `architectures/` — 4 Multi-Agent Topology Patterns
Yahan charon fundamental multi-agent architectures implement ki gayi hain jo Compare Architecture tab mein dikhti hain:

| File Path | Architecture Topology | How It Works |
|---|---|---|
| [`architectures/sequential.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/architectures/sequential.py) | **1. Sequential Pipeline** | Agents strictly one-by-one execute karte hain in a linear serialized chain (Concurrency = 1). |
| [`architectures/hierarchical.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/architectures/hierarchical.py) | **2. Hierarchical Controller** | Master Orchestrator sub-tasks create karke parallel specialist workers ko dispatch karta hai (ThreadPoolExecutor). |
| [`architectures/event_driven.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/architectures/event_driven.py) | **3. Event-Driven Bus** | Agents decoupled pub/sub message bus par communicate karte hain. Topics: `question.asked`, `evidence.posted`, `verification.completed`. |
| [`architectures/workflow_dag.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/architectures/workflow_dag.py) | **4. Workflow DAG** | Directed Acyclic Graph state machine with Fork/Join barriers and conditional feedback loops. |
| [`architectures/base.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/architectures/base.py) | **Base Architecture Classes** | Abstract base class `Architecture` and blackboard state protocol. |

---

## 🔬 3. `evaluation/` — Rigorous Benchmarks & Ablation Cases
Yahan system ka evaluation framework, metrics, aur ablation experiments hain:

| File Path | Module | Explain to Sir |
|---|---|---|
| [`evaluation/ablation_cases.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/evaluation/ablation_cases.py) | **Ablation & Merging Engine** | All 8 ablation & merging test cases: Baseline, Merge Code+History, Merge History+PR, Minus Verifier, Monolithic, etc. |
| [`evaluation/judge.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/evaluation/judge.py) | **LLM-as-a-Judge** | Automated evaluator that grades factual accuracy, citation coverage, completeness, and hallucination on a 4-point scale. |
| [`evaluation/rubric.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/evaluation/rubric.py) | **Scoring Rubric** | Standardized evaluation rubric (1 to 4 stars) across correctness and citation grounding. |
| [`evaluation/metrics.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/evaluation/metrics.py) | **Scientific Metrics** | Precision@k, Recall@k, Citation Entailment, and Hallucination Rate calculator. |
| [`evaluation/stats.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/evaluation/stats.py) | **Statistical Significance** | Paired permutation tests, Bootstrap 95% Confidence Intervals, and Holm-Bonferroni corrections. |
| [`evaluation/benchmark.jsonl`](file:///Users/parulaggarwal/Desktop/Agentic_AI/evaluation/benchmark.jsonl) | **Benchmark Ground Truth** | Expert-curated ground truth evaluation cases across real operating system codebases. |

---

## 📥 4. `ingestion/` — Data Extraction & Indexing Pipeline
Yahan real GitHub repos se code, commits, aur PRs extract karne ka code hai:

| File Path | Module | Explain to Sir |
|---|---|---|
| [`ingestion/extract_code.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/ingestion/extract_code.py) | **Code & AST Extractor** | Parses C/C++/Rust header and source files for functions, structs, enums, and macros. |
| [`ingestion/extract_git.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/ingestion/extract_git.py) | **Git Commit Log Extractor** | Extracts commit hashes, author timestamps, commit messages, and diff patches. |
| [`ingestion/issue_pr_extractor.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/ingestion/issue_pr_extractor.py) | **GitHub PR & Issue Extractor** | Pulls issues, pull request threads, developer comments, and code review remarks via GitHub API. |
| [`ingestion/corpus_store.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/ingestion/corpus_store.py) | **Multi-Modal Corpus Store** | Organizes unified evidence across code symbols, commits, PRs, and docs into searchable partitions. |
| [`ingestion/build_index.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/ingestion/build_index.py) | **BM25 Search Index Builder** | Generates fast inverted BM25 search indices for high-recall lexical retrieval. |
| [`ingestion/link_graph.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/ingestion/link_graph.py) | **Cross-Modal Link Graph** | Connects code AST symbols to Git commits and GitHub PR discussions. |

---

## 🖥️ 5. `static/` — Frontend User Interface (Dashboard & Web App)
Yahan pura front-end user interface hai:

| File Path | Component | Explain to Sir |
|---|---|---|
| [`static/index.html`](file:///Users/parulaggarwal/Desktop/Agentic_AI/static/index.html) | **Main Web Application UI** | Single Page Application containing all 4 tabs: (1) Knowledge Base, (2) Ask Assistant + Methodology, (3) Compare Architecture, (4) Evaluation Benchmark. |
| [`static/style.css`](file:///Users/parulaggarwal/Desktop/Agentic_AI/static/style.css) | **Glassmorphism Design System** | Cyberpunk/Dark-mode styling, responsive flexbox/grid layouts, card animations, and badges. |
| [`static/app.js`](file:///Users/parulaggarwal/Desktop/Agentic_AI/static/app.js) | **Frontend Controller & API Client** | Handles live queries, methodology toggle rendering, Chart.js interactive charts, inspect modals, and topology simulations. |

---

## ⚡ 6. Root Execution Scripts

| File Path | Description |
|---|---|
| [`server.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/server.py) | **FastAPI Backend Server**: Exposes `/api/run-query`, `/api/repositories`, `/api/inspect`, running on port 8080. |
| [`run_experiments.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/run_experiments.py) | **Batch Experiment Runner**: Executes benchmark queries across A/B/C configurations. |
| [`run_ablation_study.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/run_ablation_study.py) | **Ablation Study Runner**: Runs the 8 ablation modes and outputs precision/latency trade-offs. |
| [`config.py`](file:///Users/parulaggarwal/Desktop/Agentic_AI/config.py) | **Global Settings**: Paths, active LLM configuration, and default parameters. |

---

## 🎯 7. Quick Viva Cheat Sheet (Sir Ke Expected Questions)

### Q1: *"Aapke 6 agents kahan hain code mein?"*
> **Answer:** "Sir, `agents/` folder ke andar saare 6 agents independently defined hain:
> 1. `agents/orchestrator_agent.py` — Orchestrator Agent (Planning)
> 2. `agents/code_analysis_agent.py` — Code Analysis Agent (AST Symbols)
> 3. `agents/history_rag_agent.py` — History RAG Agent (Git History)
> 4. `agents/issue_pr_agent.py` — Issue & PR Agent (GitHub Discussions)
> 5. `agents/evidence_verification_agent.py` — Verification Agent (Gatekeeper)
> 6. `agents/reviewer_agent.py` — Reviewer Agent (Citation Synthesis)"

### Q2: *"Merging wala part kahan implement kiya hai?"*
> **Answer:** "Sir, merging wala part `agents/merged_agents.py` aur `evaluation/ablation_cases.py` mein hai. Humne 4 merging combinations test kiye hain:
> - **Code + History Merged:** Code Analysis aur History RAG ko 1 agent mein merge kiya (causes tool contention & 0.8ms latency).
> - **History + Issue/PR Merged:** Commit diffs aur PR discussions ko 1 temporal worker mein merge kiya.
> - **Orchestrator + Verifier Merged:** Orchestrator khud ko verify karta hai (causes self-confirmation bias).
> - **Verifier + Reviewer Merged:** Gatekeeper aur synthesis ko merge kiya."

### Q3: *"4 architectures kaunsi hain aur kahan hain?"*
> **Answer:** "Sir, `architectures/` directory mein charon topology patterns implemented hain:
> - `sequential.py` (Strict linear pipeline)
> - `hierarchical.py` (Master controller with parallel worker thread pool)
> - `event_driven.py` (Decoupled pub/sub message bus)
> - `workflow_dag.py` (Directed Acyclic Graph with fork/join barriers)"

### Q4: *"Agents aapas mein data kaise share karte hain?"*
> **Answer:** "Sir, `agents/blackboard.py` mein **Blackboard Architecture** implement ki hai. Saare agents timestamped `EvidenceFragment` objects blackboard par post karte hain, jise Verification aur Reviewer agent audit karte hain."

### Q5: *"Frontend aur Backend kaise connect hote hain?"*
> **Answer:** "Sir, backend `server.py` mein FastAPI server hai jo `http://localhost:8080` par chalta hai. Frontend `static/app.js` se REST API `/api/run-query` call karta hai aur structured JSON response (answer, citations, latency, and methodology trace) render karta hai."
