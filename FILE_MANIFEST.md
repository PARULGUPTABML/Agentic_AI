# Arpita Bansal (230546) — files to add to Agentic_AI

Copy each file to the path shown, relative to the repository root. All files are new; no existing file is modified.

| Destination path | Purpose |
|---|---|
| `agents/code_analysis_agent.py` | Code Analysis Agent (code-search tools, current snapshot evidence) |
| `agents/issue_pr_agent.py` | Issue / PR Agent (issues, PRs, reviews + commit linking) |
| `dashboard/__init__.py` | package init |
| `dashboard/analysis.py` | Comparison tables (Markdown/CSV/JSON) |
| `dashboard/build_dashboard.py` | Self-contained HTML results dashboard |
| `docs/code_and_issue_pr_agents.md` | Docs: the two agents + corpus extraction |
| `docs/evaluation_framework.md` | Docs: judge, rubric, metrics, stats, observability, dashboard |
| `evaluation/__init__.py` | package init |
| `evaluation/baselines.py` | Config A (no context) and B (code only) |
| `evaluation/judge.py` | LLM-as-Judge |
| `evaluation/llm.py` | Judge model selection (shared / Gemini / OpenAI-compatible / heuristic) |
| `evaluation/metrics.py` | Retrieval / citation / hallucination metrics, judge_answer() |
| `evaluation/rubric.py` | Scoring rubric |
| `evaluation/spot_check.py` | Manual spot-check export + Cohen's kappa |
| `evaluation/stats.py` | Bootstrap CIs, paired permutation tests, Holm; summarize() |
| `ingestion/__init__.py` | package init |
| `ingestion/corpus_store.py` | BM25 corpus store + link graph |
| `ingestion/github_client.py` | GitHub REST client (pagination, rate limits, cache, offline) |
| `ingestion/issue_pr_extractor.py` | Issue/PR/review corpus extraction CLI |
| `ingestion/linker.py` | Commit <-> issue/PR link extraction |
| `observability/__init__.py` | package init |
| `observability/instrument.py` | Per-agent latency, tool calls, error capture |
| `observability/run_logger.py` | Run record schema + JSONL logger |
| `observability/tracker.py` | LLM call + token tracking per agent |
| `prompts/judge_system.md` | Judge system prompt |
| `run_experiments.py` | Experiment runner: A/B/C x architectures x LLMs x protocols |
| `tests/fixtures/__init__.py` | package init |
| `tests/fixtures/github_fixture.py` | SYNTHETIC GitHub API fixture for offline tests |
| `tests/test_code_issue_agents.py` | tests |
| `tests/test_evaluation.py` | tests |
| `tests/test_ingestion.py` | tests |
| `tests/test_observability_dashboard.py` | tests |

## Run

```bash
python3 -m pytest tests/ -q        # 133 tests (61 existing + 72 new)
python3 run_experiments.py --offline --repo-path ../repos/seL4__seL4 --limit 5   # plumbing check, no API key
python3 -m ingestion.issue_pr_extractor --repo seL4/seL4 --git-repo ../repos/seL4__seL4   # needs GITHUB_TOKEN
python3 run_experiments.py --repo-path ../repos/seL4__seL4 --llm-label <model>   # real runs (rag backend)
python3 -m dashboard.build_dashboard results/runs.jsonl
```

Suggested `.gitignore` additions: `results/`, `data/cache/`.

Details: `docs/code_and_issue_pr_agents.md`, `docs/evaluation_framework.md`.
