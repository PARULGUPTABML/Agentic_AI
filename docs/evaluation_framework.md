# Evaluation framework, observability and results dashboard

This is what lets the project claim, with measured evidence, which configuration, architecture, protocol and LLM performs best.

```
run_experiments.py ──► observability (per-agent latency, tools, LLM calls, tokens, errors)
        │                         │
        ├──► evaluation (judge + retrieval + citation metrics)
        ▼                         ▼
 results/runs.jsonl  ──►  evaluation/stats.py  ──►  dashboard/  (tables.md, CSVs, dashboard.html)
```

## 1. Experiment matrix (`run_experiments.py`)

| Config | What the answering LLM gets |
|---|---|
| **A** `A_no_context` | the question only |
| **B** `B_code_only` | the current code snapshot from the Code Analysis Agent |
| **C** `C_<architecture>` | the full multi-agent pipeline under `sequential`, `hierarchical`, `event_driven`, `workflow_dag` |
| **P** `P_<protocol>` | `agents/messaging.py` protocols: `blackboard`, `p2p`, `broadcast`, `mcp` |

A and B use the same model and helper (`agents._llm_retry`) as C, so only the context changes. 35 questions × (A + B + 4 architectures) = **210 runs per LLM**; repeat with another `LLM_MODEL` / `--llm-label` to compare LLMs. Runs are resumable (keyed on question, condition, LLM, repeat).

```bash
# plumbing check, no backend or API key needed
python run_experiments.py --offline --repo-path ../repos/seL4__seL4 --limit 5

# real runs (rag/ + config on PYTHONPATH, LLM_PROVIDER/LLM_MODEL set)
python run_experiments.py --repo-path ../repos/seL4__seL4 --corpus data/corpus/seL4__seL4 --llm-label <model>
python run_experiments.py --configs P --llm-label <model>        # protocol comparison
python -m dashboard.build_dashboard results/runs.jsonl
```

## 2. LLM-as-Judge (`evaluation/judge.py`, `prompts/judge_system.md`)

* **Reference-guided single-answer grading** — the judge sees the question, reference answer, key facts, gold-evidence text and the candidate, but *not* which configuration produced it (no position or label bias).
* **Atomic key facts** — completeness = covered key facts / all key facts (more stable than a holistic score).
* **Unanswerable probes** (Q007, Q029, Q035 — empty gold evidence) are correct only when the answer abstains.
* **Code beats model where it can** — fabricated evidence IDs are detected programmatically (`metrics.citation_metrics`), never by the judge.
* Temperature 0, optional majority vote (`--judge-votes 3`).
* **Fallback** — with no judge model (`LLM_PROVIDER=mock` or `JUDGE_PROVIDER=heuristic`) a deterministic lexical judge runs instead. Every row records `judge_method`; the dashboard flags heuristic/mock results as not reportable.
* **Judge model** — `JUDGE_PROVIDER=shared|gemini|openai`, `JUDGE_MODEL`, `JUDGE_API_KEY`, `JUDGE_BASE_URL` (any OpenAI-compatible host). Prefer a different model family from the answering LLM to reduce self-preference bias.

### Rubric (0–10, `evaluation/rubric.py`)

| Dimension | Points | Rule |
|---|---|---|
| Accuracy | 4 | correct 4 · partial 2 · incorrect 0 |
| Completeness | 3 | 3 × fraction of key facts covered (correct abstention = 3) |
| Groundedness | 2 | 2 none · 1 unsupported-only · 0 contradiction or fabricated ID |
| Separation | 1 | evidence vs. inference distinguished (or no inference made) |

A fabricated identifier also downgrades accuracy one level.

**Hallucination** (binary) = any contradiction **or** any fabricated ID (cited/mentioned commit, PR, issue, review or code ID that is in neither the retrieved evidence, the evidence texts, the gold set nor the reference answer). `hallucination_lenient` additionally counts unsupported claims.

## 3. Metrics (`evaluation/metrics.py`)

| Metric | Definition |
|---|---|
| `accuracy` | 1 / 0.5 / 0 from the judge label |
| `completeness` | key-fact coverage |
| `hallucination`, `hallucination_lenient` | see above |
| `precision_at_k`, `recall_at_k`, `hit_at_k`, `mrr` | over retrieved IDs vs. `ground_truth_evidence` (`k` = 8) |
| `evidence_precision` / `evidence_recall` | over the whole retrieved set |
| `citation_precision` | cited IDs that were actually retrieved |
| `gold_citation_recall` | gold IDs the answer cites |

ID matching: kinds must agree; commits match on ≥ 7-char SHA prefix (`commit:3e57e647e` ≡ `commit:3e57e647e0`); `PR:420` ≡ `pr:420`; Jira keys upper-cased. Questions with no gold evidence report `None` for retrieval ratios so they do not drag averages to zero.

`judge_answer(item, answer, gold_texts)` and `retrieval_metrics(retrieved, gold, k)` keep the signatures `run_eval_multi_agent.py` already imports, and `evaluation.stats.summarize(path)` returns the `conditions[...]` keys it prints.

## 4. Statistics (`evaluation/stats.py`)

Per condition: mean, seeded percentile-bootstrap 95 % CI (2,000 resamples), latency p50/p95, error rate. Comparisons vs. baselines A and B: per-question paired means (repeats averaged first), mean difference with bootstrap CI, two-sided sign-flip permutation test (5,000 permutations), **Holm–Bonferroni** correction across all comparisons. Stdlib only.

## 5. Observability (`observability/`)

Every run writes one JSON line (`RunRecord`, schema v1.0):

`run_id, question_id, question, config, condition, architecture, protocol, llm, split, question_type, repeat, timestamp, agents_invoked, tools_used[{agent, tool, arg, results, ms}], retrieved_evidence[{id, type, source_agent, confidence}], per_agent_latency_ms, total_latency_ms, coordination_overhead_ms, llm_calls, llm_calls_by_agent, prompt_tokens, completion_tokens, total_tokens, tokens_estimated, rounds, retrieval_iterations, final_answer, answer_confidence, gaps, judge{…}, retrieval{…}, citations{…}, errors[], retries, status`

* `LLMCallTracker` wraps the `generate` functions the agents already use (`agents._llm_retry._raw_generate`, pipeline/messaging `generate`, `rag.llm.generate`) for the duration of a run, so no agent code changes. Calls are attributed through a **thread-local** current agent (works for the threaded hierarchical/event-driven variants). Tokens are estimated at ~4 chars/token unless a `usage_fn` is supplied.
* `InstrumentedAgent` / `InstrumentedVerifier` / `instrument_method` time each agent, copy its `last_trace` tool calls, and turn agent exceptions into logged errors so one failure degrades a run instead of crashing the experiment. `TypeError` is re-raised so the architectures' `run(blackboard)` fallback still works for the History agent.
* `RunLogger` is append-only, thread-safe, tolerates a truncated last line, and provides resume keys.

## 6. Results dashboard (`dashboard/`)

`python -m dashboard.build_dashboard results/runs.jsonl` writes `results/dashboard.html` (self-contained, inline SVG, light + dark) and `results/analysis/` (`tables.md`, one CSV per table, `analysis.json`).

Charts: accuracy by condition with CI whiskers · hallucination rate · accuracy vs. median latency · per-agent latency stack (with the **dominant agent** per condition) · accuracy heat-map by question type. Tables: configurations, architectures (+ coordination overhead, rounds, retrieval calls), LLMs, protocols, question types, per-agent latency and LLM calls, tool usage, errors/retries, significance tests.

## 7. Manual spot checks (`evaluation/spot_check.py`)

```bash
python -m evaluation.spot_check export results/runs.jsonl --n 20   # stratified across conditions
# fill human_accuracy / human_hallucination in results/spot_check.csv
python -m evaluation.spot_check agree results/spot_check.csv       # raw agreement + Cohen's kappa
```

Treat κ ≥ 0.6 as acceptable; below that, revise the judge prompt before trusting automated numbers.

## Known data note

18 of the 102 `key_facts` in `eval_set/questions.jsonl` are not stated in their own `reference_answer` (e.g. Q001's “Merged into master by Hesham Almatary in PR:420”). The judge grades against key facts, so a candidate that only paraphrases the reference answer will be marked *partial* on those questions. Either extend those reference answers or accept this as intended strictness.
