"""Issue / PR / review corpus extraction (data-preparation stream).

Modules:
    github_client         Minimal, dependency-free GitHub REST client with
                          pagination, rate-limit back-off and an on-disk cache.
    linker                Commit <-> issue / PR reference extraction and
                          link-graph construction.
    issue_pr_extractor    CLI that pulls issues, PRs, reviews and review
                          comments into JSONL corpus files.
    corpus_store          Loads the corpus and serves BM25 search + link
                          lookups to the Issue/PR Agent.
"""
