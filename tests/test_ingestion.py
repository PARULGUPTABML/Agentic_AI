"""Tests for issue/PR corpus extraction, linking and the corpus store."""
from __future__ import annotations

import json

import pytest

from ingestion.corpus_store import IssuePRStore, tokenize
from ingestion.github_client import GitHubClient, GitHubError
from ingestion.issue_pr_extractor import IssuePRExtractor, write_corpus
from ingestion.linker import build_links, commit_id, extract_refs, resolve_number
from tests.fixtures.github_fixture import COMMITS, REPO, SHA_IBRS, SHA_PAE, FakeGitHubClient


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    out = tmp_path_factory.mktemp("corpus") / "seL4__seL4"
    ex = IssuePRExtractor(FakeGitHubClient(), REPO, verbose=False)
    issues = ex.extract_issues(None)
    prs, reviews = ex.extract_prs(None)
    manifest = write_corpus(out, REPO, issues, prs, reviews, COMMITS)
    return out, manifest


class TestLinker:
    def test_merge_and_closing_refs(self):
        refs = dict(extract_refs("Merge pull request #420 from x/y\n\nFixes #12, see #13 and SELFOUR-501"))
        assert refs["pr:420"] == "merges"
        assert refs["num:12"] == "closes"
        assert refs["num:13"] == "references"
        assert refs["issue:SELFOUR-501"] == "references"
        assert "num:420" not in refs          # merged PR number is not also an issue ref

    def test_squash_suffix_and_pr_word(self):
        assert ("pr:77", "merges") in extract_refs("smp: move lock (#77)")
        assert ("pr:5", "references") in extract_refs("follow-up to PR 5")

    def test_cross_repo_refs_dropped(self):
        refs = dict(extract_refs("closes other/repo#5, fixes seL4/seL4#6", own_repo="seL4/seL4"))
        assert "num:5" not in refs and refs["num:6"] == "closes"

    def test_denylisted_tokens_not_jira(self):
        assert extract_refs("UTF-8 and SHA-256 support") == []

    def test_resolve_number(self):
        assert resolve_number("num:7", {7}, set()) == "pr:7"
        assert resolve_number("num:8", {7}, {8}) == "issue:8"
        assert resolve_number("issue:ABC-1", set(), set()) == "issue:ABC-1"

    def test_build_links_has_reverse_edges(self):
        prs = [{"id": "pr:1", "number": 1, "title": "x", "body": "Fixes #2", "merge_commit_sha": "a" * 40,
                "commit_shas": ["b" * 40]}]
        issues = [{"id": "issue:2", "number": 2, "body": ""}]
        links = {(l.src, l.dst, l.rel) for l in build_links([], issues, prs, [])}
        assert ("pr:1", "issue:2", "closes") in links
        assert ("issue:2", "pr:1", "discussed_in") in links
        assert ("pr:1", commit_id("a" * 40), "merged_as") in links
        assert (commit_id("b" * 40), "pr:1", "part_of") in links


class TestExtractor:
    def test_counts_and_pr_filtering(self, corpus):
        _, manifest = corpus
        assert manifest["counts"]["issues"] == 2       # PR #420 listed by /issues is skipped
        assert manifest["counts"]["prs"] == 3
        assert manifest["counts"]["reviews"] == 3      # bot review dropped, empty approval kept

    def test_documents_have_required_fields(self, corpus):
        out, _ = corpus
        for name in ("issues.jsonl", "prs.jsonl", "reviews.jsonl"):
            for line in (out / name).read_text().splitlines():
                d = json.loads(line)
                for f in ("id", "type", "title", "text", "date", "url", "files"):
                    assert f in d, (name, f)

    def test_pr_merge_sha_and_files(self, corpus):
        out, _ = corpus
        prs = {json.loads(l)["id"]: json.loads(l) for l in (out / "prs.jsonl").read_text().splitlines()}
        assert prs["pr:420"]["merge_commit_sha"] == SHA_PAE
        assert "src/arch/x86/32/kernel/vspace_pae.c" in prs["pr:420"]["files"]
        assert "Looks good" in prs["pr:420"]["text"]          # discussion thread included

    def test_review_inline_comment_attached(self, corpus):
        out, _ = corpus
        reviews = [json.loads(l) for l in (out / "reviews.jsonl").read_text().splitlines()]
        r = next(x for x in reviews if x["id"] == "review:420-9001")
        assert "PAE setup code" in r["text"] and r["files"] == ["src/arch/x86/32/head.S"]
        assert not any("ci-bot" in x["author"] for x in reviews)


class TestStore:
    def test_search_and_types(self, corpus):
        st = IssuePRStore.load(corpus[0])
        hits = st.search("PAE physical address extension removed", {"pr"}, 3)
        assert hits[0]["id"] == "pr:420" and hits[0]["score"] == 1.0
        assert all(h["type"] == "pr" for h in hits)

    def test_resolve_id_variants(self, corpus):
        st = IssuePRStore.load(corpus[0])
        assert st.resolve_id("PR:420") == "pr:420"
        assert st.resolve_id("#88") == "issue:88"
        assert st.resolve_id("pull 455") == "pr:455"
        assert st.resolve_id("issue:9999") is None

    def test_links_reach_commits(self, corpus):
        st = IssuePRStore.load(corpus[0])
        assert commit_id(SHA_IBRS) in st.neighbours("pr:455")
        assert ("pr:455", "discussed_in") in st.linked("issue:88")
        assert ("pr:420", "referenced_by") in st.linked("issue:SELFOUR-501")

    def test_repoindex_surface(self, corpus):
        st = IssuePRStore.load(corpus[0])
        hits, timings = st.retrieve("IBRS spectre", types={"issue", "pr"}, k=2, expand=True)
        assert {h["id"] for h in hits} >= {"pr:455", "issue:88"} and timings == {}


def test_tokenize_identifiers():
    toks = set(tokenize("seL4_TCB_SetPriority handleSyscall lock.c"))
    assert {"sel4", "tcb", "setpriority", "priority", "handlesyscall", "syscall", "lock"} <= toks


class TestGitHubClient:
    def test_next_link_parsing(self):
        h = '<https://api.github.com/x?page=2>; rel="next", <https://api.github.com/x?page=5>; rel="last"'
        assert GitHubClient._next_link(h) == "https://api.github.com/x?page=2"
        assert GitHubClient._next_link(None) is None

    def test_rate_limit_wait(self):
        assert GitHubClient._rate_limit_wait({"Retry-After": "7"}, 2.0) == 7.0
        assert GitHubClient._rate_limit_wait({}, 3.0) == 3.0

    def test_offline_cache(self, tmp_path):
        c = GitHubClient(token="t", cache_dir=tmp_path)
        url = c.url("/repos/a/b/issues")
        c._cache_put(url, [{"number": 1}], None)
        off = GitHubClient(token="t", cache_dir=tmp_path, offline=True)
        assert list(off.paginate("/repos/a/b/issues")) == [{"number": 1}]
        with pytest.raises(GitHubError):
            off.get("/repos/a/b/pulls")
