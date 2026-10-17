# Multi-Agent Repository History Explainer

A multi-agent system that uses RAG (Retrieval-Augmented Generation) to explain **why** a software repository looks the way it does, by retrieving and synthesizing evidence from commits, issues, PRs, and code reviews.

## Team

| Member | Roll No. | Agents Owned | Infrastructure |
|--------|----------|-------------|----------------|
| **Parul Gupta** | 230506 | History/RAG Agent, Evidence Verification Agent | Blackboard schema, Messaging-protocol comparison |
| **Arpita Bansal** | 230546 | Code Analysis Agent, Issue/PR Agent | LLM-as-Judge evaluation, Observability dashboard |
| **Parul Aggarwal** | 230540 | Orchestrator Agent, Reviewer Agent | 4 Architecture-pattern variants, Evaluation question set |

## Project Structure

```
Agentic_AI/
├── agents/
│   ├── blackboard.py                  # Shared evidence store (Blackboard pattern)
│   ├── history_rag_agent.py           # ReAct-based RAG agent for repo history
│   ├── evidence_verification_agent.py # CoT-based evidence sufficiency checker
│   ├── messaging.py                   # 4 messaging protocols (Blackboard/P2P/Broadcast/MCP)
│   ├── multi_agent_pipeline.py        # End-to-end multi-agent pipeline
│   └── _llm_retry.py                 # LLM retry utility
├── tests/
│   └── test_agents.py                # Unit + integration tests
├── demo.py                           # Demo script
├── agent_trace_runner.py             # Trace runner for experiments
├── agent_visualizer.html             # Visual trace viewer
├── run_eval_multi_agent.py           # Evaluation runner for 40+ experiments
└── test_real_pipeline.py             # Real pipeline test
```

## Components (Parul Gupta — 230506)

### 1. Repository History / RAG Agent
- **ReAct loop** (Reason → Act → Observe) with up to 5 iterations
- 6 tools: `search_commits`, `search_issues`, `search_prs`, `search_all_history`, `get_linked`, `finish`
- Hybrid retrieval: BM25 + vector search + RRF + graph expansion

### 2. Evidence Verification Agent
- **Chain-of-Thought** prompting for evidence sufficiency checking
- Checks 5 dimensions: WHAT, WHY, WHO/WHEN, CONTEXT, COMPLETENESS
- Produces `retry_queries` for re-retrieval when evidence is insufficient

### 3. Blackboard Schema
- Thread-safe shared evidence store
- Lifecycle: `GATHERING → VERIFYING → RE-GATHERING → SYNTHESIZING → DONE`
- Evidence deduplication with confidence-based replacement
- Built-in observability logging

### 4. Messaging Protocol Comparison
Four inter-agent communication patterns for empirical comparison:
- **Blackboard** — shared-state communication
- **Peer-to-Peer** — direct agent-to-agent calls
- **Broadcast** — pub/sub message bus
- **MCP-Style** — agents as callable tools

## Setup

```bash
# Clone the repo
git clone https://github.com/<username>/Agentic_AI.git
cd Agentic_AI

# Install dependencies
pip install -r requirements.txt

# Set environment variables
cp .env.example .env
# Edit .env with your API keys
```

## Running Tests

```bash
# Run tests with mock LLM (no API keys needed)
LLM_PROVIDER=mock EMBED_MODEL=hash python -m pytest tests/test_agents.py -v
```

## References

1. P. Lewis et al., "Retrieval-Augmented Generation," NeurIPS, 2020
2. S. Yao et al., "ReAct: Synergizing Reasoning and Acting," ICLR, 2023
3. J. Wei et al., "Chain-of-Thought Prompting," NeurIPS, 2022
4. H. P. Nii, "The Blackboard Model of Problem Solving," AI Magazine, 1986
5. Model Context Protocol, documentation and specification, 2026
