# Code Analysis Agent and Issue / PR Agent

Both agents supply evidence that complements the History/RAG Agent:

| Agent | Answers | Evidence types | IDs |
|---|---|---|---|
| Code Analysis (`agents/code_analysis_agent.py`) | **What** the code looks like *now* | `code` | `code:<path>`, `code:absent:<path>` |
| Issue / PR (`agents/issue_pr_agent.py`) | **What was discussed** and which change it triggered | `issue`, `pr`, `review` (+ linked `commit`) | `issue:<n>` / `issue:<JIRA-KEY>`, `pr:<n>`, `review:<pr>-<id>` |

Both are tool-driven and deterministic (no LLM call). That keeps them cheap, reproducible and easy to evaluate, and leaves the LLM budget to the ReAct History agent, the Verifier and the Reviewer.

## Shared contract

Both agents work unchanged in all four architecture variants:

```python
agent.run("sub-query from the orchestrator", repo_slug="seL4__seL4")  # -> list[EvidenceFragment]
agent.run(blackboard)                                                 # posts + logs every tool call
agent.last_trace                                                      # [{tool, arg, results, ms}, ...] for observability
```

Orchestrator sub-query templates (`"Analyze code structure ... for: <question>"`) are stripped so template words never become search terms. All evidence IDs pass `reviewer/citation_checker.py`'s citation regex.

## Code Analysis Agent

**Source** (first found): `repo_path=` → `$CODE_REPO_PATH` → `repos/<slug>`, `data/repos/<slug>`, `../repos/<slug>` → shared `rag.retriever` index (`code`/`doc` types).

**Pipeline**

1. `analyse_query` — file references (`lock.c`, `src/arch/x86/32/kernel/vspace_pae.c`), code symbols (`seL4_*`, snake_case, camelCase, `name()`), architecture acronyms (reuses the Orchestrator's entity extractor so both agents agree), and content keywords (generic question/change verbs removed).
2. `find_files` — exact path (+12), unique basename (+6), path-segment keyword hits (≤ +4.5).
3. `find_definitions` — C functions (return type on the previous line, seL4 style), `#define` macros, `struct/enum/union/typedef`, `.bf` bitfield blocks, Python `def/class`.
4. `grep_keywords` — whole-word hits per file, IDF-weighted and saturating, so rare terms (e.g. `ibrs`, `errata`) dominate common ones (`x86`).
5. Rank; docs/build config (`.md`, `.cmake`, `CMakeLists.txt`, …) are down-weighted ×0.5 for “what” questions.
6. Evidence: a numbered snippet around the best definition or the densest keyword window, the file outline, last-change date (`git log -1`), and a GitHub permalink.

**Confidence** = `0.2 + 0.7 × strength × √(score / best)`, where *strength* is absolute (keyword coverage, a definition hit, or an explicit path match). A weak best-of-a-bad-lot match therefore stays low — on the unanswerable Kyber probe the top code hit scores ≈ 0.36 instead of 0.9.

**Absent files.** A path named in the question that is not in the snapshot yields `code:absent:<path>` (confidence 0.3). For “superseded implementation” questions this is real evidence that the file was removed/moved and tells the History agent what to look for.

Measured on the real seL4 tree (1,038 files): 0.1–0.5 s per query; for the dev questions the expected file ranks first (e.g. `src/smp/lock.c`, `src/arch/arm/machine/errata.c`, `src/plat/pc99/machine/ioapic.c`, `src/fastpath/fastpath.c`).

## Issue / PR Agent

**Source** (first found): `store=` → `data/corpus/<slug>/` (built by `ingestion/issue_pr_extractor.py`) → shared `rag.retriever` index (`issue`, `pr`, `review` types; `neighbours()` for links).

**Tools**

| Tool | What it does |
|---|---|
| `lookup` | Direct fetch of explicit references: `PR #420`, `#88`, `pull request 77`, Jira keys like `SELFOUR-501` (resolved through `referenced_by` links to the PRs that cite them). Confidence 0.95. |
| `search_prs` / `search_issues` / `search_reviews` | BM25 per document type (titles weighted ×3). |
| (fusion) | Reciprocal Rank Fusion across the three lists; documents covering < 20 % of the query terms are dropped. |
| `link_to_changes` | Two-hop walk over the link graph: discussion → PR → commits. Result goes into `metadata.triggered_changes` / `related_discussions` and a `Linked changes:` line in the content. |

Linked commits that the source actually holds (e.g. a shared index, or a `commits.jsonl` dropped into the corpus folder) are also posted as `commit` fragments at 0.8 × the discussion's confidence; the Blackboard keeps the higher-confidence copy if the History agent posts the same commit.

## Issue / PR corpus extraction (`ingestion/`)

```bash
export GITHUB_TOKEN=ghp_...   # 5,000 req/h instead of 60
python -m ingestion.issue_pr_extractor --repo seL4/seL4 --max-prs 500 --max-issues 500 \
       --git-repo ../repos/seL4__seL4      # or --commits-jsonl <commit corpus from the History stream>
```

* `github_client.py` — stdlib REST client: pagination via `Link` headers, primary/secondary rate-limit back-off, 5xx retry, on-disk cache (`data/cache/github/`) and `--offline` replay for reproducible re-extraction.
* `issue_pr_extractor.py` — issues (+ comments; PRs returned by `/issues` are skipped), PRs (+ discussion, commits, changed files, merge SHA), reviews (+ inline comments with path and diff-hunk tail). Bot accounts are dropped; approval-only reviews are kept because they answer “who approved?”.
* `linker.py` — `closes`/`fixes`/`resolves #n`, `#n`, `GH-n`, `owner/repo#n` (other repos ignored), `Merge pull request #n`, squash suffix `(#n)`, `PR n`, Jira keys; PR `merged_as` / `contains` commits; reverse edges (`discussed_in`, `part_of`, `reviewed_by`, `referenced_by`) for traversal. Short SHAs use the History agent's 10-char convention; evaluation matches commit IDs by prefix.
* `corpus_store.py` — BM25 store with `resolve_id`, `linked`, and a `RepoIndex`-compatible surface (`docs`, `retrieve`, `neighbours`, `resolve_id`, `stats`).

Output: `issues.jsonl`, `prs.jsonl`, `reviews.jsonl`, `links.jsonl`, `manifest.json` (counts, link-relation histogram, parameters, errors).
