"""Tests for the Code Analysis Agent and the Issue/PR Agent."""
from __future__ import annotations

import textwrap

import pytest

from agents.blackboard import Blackboard, EvidenceFragment
from agents.code_analysis_agent import CodeAnalysisAgent
from agents.issue_pr_agent import IssuePRAgent
from architectures.base import StubRetrievalAgent, StubVerificationAgent
from architectures.sequential import SequentialArchitecture
from architectures.hierarchical import HierarchicalArchitecture
from architectures.event_driven import EventDrivenArchitecture
from architectures.workflow_dag import WorkflowDAGArchitecture
from ingestion.corpus_store import IssuePRStore
from ingestion.issue_pr_extractor import IssuePRExtractor, write_corpus
from tests.fixtures.github_fixture import COMMITS, REPO, FakeGitHubClient


@pytest.fixture(scope="module")
def mini_repo(tmp_path_factory):
    root = tmp_path_factory.mktemp("repo")
    files = {
        "src/smp/lock.c": """
            #include <smp/lock.h>
            /* generic SMP lock shared by all architectures */
            void
            clh_lock_init(void)
            {
                for (int i = 0; i < CONFIG_MAX_NUM_NODES; i++) { big_kernel_lock.node[i] = 0; }
            }
        """,
        "include/smp/lock.h": """
            #define CLH_LOCK_SIZE 4
            typedef struct clh_lock { int head; } clh_lock_t;
            void clh_lock_init(void);
        """,
        "src/api/syscall.c": """
            exception_t
            handleSyscall(syscall_t syscall)
            {
                return decodeInvocation(syscall);
            }
        """,
        "tools/gen.py": """
            class BitfieldGen:
                pass
            def generate_bitfield(spec):
                return spec
        """,
        "README.md": "lock smp lock smp lock smp lock smp generic architecture",
    }
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(body))
    return root


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    out = tmp_path_factory.mktemp("c") / "seL4__seL4"
    ex = IssuePRExtractor(FakeGitHubClient(), REPO, verbose=False)
    issues = ex.extract_issues(None)
    prs, reviews = ex.extract_prs(None)
    write_corpus(out, REPO, issues, prs, reviews, COMMITS)
    return IssuePRStore.load(out)


# ---------------------------------------------------------------------- #
class TestCodeAnalysisAgent:
    def test_file_reference_ranks_first(self, mini_repo):
        a = CodeAnalysisAgent(repo_path=mini_repo)
        frags = a.run("When was SMP lock.c moved to the generic smp directory?")
        assert frags[0].id == "code:src/smp/lock.c"
        assert frags[0].evidence_type == "code" and frags[0].source_agent == "code_analysis"
        assert "clh_lock_init()" in frags[0].content           # outline
        assert " | " in frags[0].content                         # numbered snippet

    def test_source_preferred_over_docs(self, mini_repo):
        frags = CodeAnalysisAgent(repo_path=mini_repo).run("smp lock generic architecture")
        ids = [f.id for f in frags]
        assert ids.index("code:src/smp/lock.c") < ids.index("code:README.md")

    def test_c_definitions(self, mini_repo):
        a = CodeAnalysisAgent(repo_path=mini_repo)
        defs = a.find_definitions(["handleSyscall", "CLH_LOCK_SIZE", "clh_lock"])
        kinds = {(f, d["symbol"], d["kind"]) for f, ds in defs.items() for d in ds if d["kind"] != "reference"}
        assert ("src/api/syscall.c", "handleSyscall", "function") in kinds
        assert ("include/smp/lock.h", "CLH_LOCK_SIZE", "macro") in kinds
        assert ("include/smp/lock.h", "clh_lock", "type") in kinds

    def test_symbol_query_finds_definition(self, mini_repo):
        frags = CodeAnalysisAgent(repo_path=mini_repo).run("How does handleSyscall dispatch invocations?")
        assert frags[0].id == "code:src/api/syscall.c"
        assert frags[0].metadata["definitions"][0]["kind"] == "function"

    def test_python_outline(self, mini_repo):
        assert CodeAnalysisAgent(repo_path=mini_repo).outline("tools/gen.py") == ["class BitfieldGen", "generate_bitfield()"]

    def test_absent_file_reported(self, mini_repo):
        frags = CodeAnalysisAgent(repo_path=mini_repo).run("Why was src/arch/x86/32/kernel/vspace_pae.c removed?")
        assert any(f.id == "code:absent:src/arch/x86/32/kernel/vspace_pae.c" for f in frags)

    def test_unrelated_query_low_confidence(self, mini_repo):
        frags = CodeAnalysisAgent(repo_path=mini_repo).run("post-quantum Kyber lattice cryptography")
        assert all(f.confidence < 0.5 for f in frags)

    def test_template_prefix_stripped(self, mini_repo):
        a = CodeAnalysisAgent(repo_path=mini_repo)
        q = a.analyse_query("Analyze code structure and implementation trade-offs for: lock.c in smp")
        assert "analyze" not in q["keywords"] and q["files"] == ["lock.c"]

    def test_blackboard_mode_posts_and_logs(self, mini_repo):
        bb = Blackboard("Where is clh_lock_init defined?", "seL4__seL4")
        frags = CodeAnalysisAgent(repo_path=mini_repo).run(bb)
        assert bb.evidence_count() == len(frags) > 0
        actions = [l.action for l in bb.get_logs()]
        assert "tool_find_definitions" in actions and actions[-1] == "complete"

    def test_no_repo_no_index_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("CODE_REPO_PATH", raising=False)
        assert CodeAnalysisAgent(repo_slug="nope__nope").run("anything") == []

    def test_citation_safe_ids(self, mini_repo):
        from reviewer.citation_checker import extract_citations
        for f in CodeAnalysisAgent(repo_path=mini_repo).run("smp lock"):
            assert extract_citations(f"[{f.id}]") == [f.id]


# ---------------------------------------------------------------------- #
class TestIssuePRAgent:
    def test_finds_pr_and_links_commits(self, store):
        frags = IssuePRAgent(store=store).run("Why was PAE support removed from x86 32-bit?")
        top = frags[0]
        assert top.id == "pr:420" and top.evidence_type == "pr"
        assert "commit:3e57e647e0" in top.metadata["triggered_changes"]
        assert "Linked changes:" in top.content

    def test_explicit_jira_key_resolves_via_links(self, store):
        frags = IssuePRAgent(store=store).run("What happened under SELFOUR-501?")
        assert frags[0].id == "pr:420" and frags[0].confidence == 0.95
        assert frags[0].metadata["match"] == "explicit reference"

    def test_issue_linked_to_fixing_pr(self, store):
        frags = IssuePRAgent(store=store).run("Who implemented the hardware IBRS Spectre mitigation?")
        by_id = {f.id: f for f in frags}
        assert {"pr:455", "issue:88"} <= set(by_id)
        assert "pr:455" in by_id["issue:88"].metadata["related_discussions"]
        assert "commit:7c1d2e3f40" in by_id["issue:88"].metadata["triggered_changes"]

    def test_reviews_searched(self, store):
        frags = IssuePRAgent(store=store).run("gate IBRS behind a config option review")
        assert any(f.evidence_type == "review" for f in frags)

    def test_unanswerable_returns_nothing(self, store):
        assert IssuePRAgent(store=store).run("Why was Kyber post-quantum lattice crypto added?") == []

    def test_trace_records_tools(self, store):
        a = IssuePRAgent(store=store)
        a.run("smp lock move")
        tools = [t["tool"] for t in a.last_trace]
        assert tools[:4] == ["lookup", "search_prs", "search_issues", "search_reviews"]

    def test_no_source(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        a = IssuePRAgent(repo_slug="x__y")
        assert a.source is None and a.run("anything") == []

    def test_index_adapter(self):
        class FakeIndex:
            docs = {"pr:1": {"type": "pr", "title": "lock rework", "text": "lock rework details", "url": ""},
                    "commit:abcdef1234": {"type": "commit", "title": "c", "text": "commit text"}}

            def retrieve(self, q, types=None, k=6, expand=False, rerank=False):
                return [{"id": i, **d, "score": 0.9} for i, d in self.docs.items() if d["type"] in types], {}

            def neighbours(self, i):
                return ["commit:abcdef1234"] if i == "pr:1" else []

        frags = IssuePRAgent(index=FakeIndex()).run("lock rework")
        assert frags[0].id == "pr:1" and frags[0].metadata["triggered_changes"] == ["commit:abcdef1234"]
        assert any(f.id == "commit:abcdef1234" and f.source_agent == "issue_pr" for f in frags)


# ---------------------------------------------------------------------- #
@pytest.mark.parametrize("arch_cls", [SequentialArchitecture, HierarchicalArchitecture,
                                      EventDrivenArchitecture, WorkflowDAGArchitecture])
def test_agents_work_in_every_architecture(arch_cls, mini_repo, store):
    agents = {"code_analysis": CodeAnalysisAgent(repo_path=mini_repo),
              "history_rag": StubRetrievalAgent("history_rag"),
              "issue_pr": IssuePRAgent(store=store)}
    out = arch_cls().run("Why was PAE support removed under SELFOUR-501?", "t1",
                         retrieval_agents=agents, verification_agent=StubVerificationAgent())
    assert "issue_pr" in out["agents_invoked"]
    assert out["evidence_count"] >= 2
    assert out["citation_check"]["invalid"] == 0
