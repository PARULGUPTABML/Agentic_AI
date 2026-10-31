"""SYNTHETIC GitHub API fixture for offline tests.

These payloads imitate the *shape* of GitHub REST responses for a few seL4-like
items so the extractor, linker, corpus store and Issue/PR Agent can be tested
without network access.  Titles echo topics from eval_set/questions.jsonl, but
bodies, review text, review IDs and full SHAs are invented test data — they are
NOT real seL4 history and must not be used as evaluation ground truth.
"""
from __future__ import annotations

from ingestion.github_client import GitHubClient

REPO = "seL4/seL4"
SHA_PAE = "3e57e647e0aa11bb22cc33dd44ee55ff66778899"     # synthetic full SHA (prefix as in eval set)
SHA_PAE_2 = "aa00bb11cc22dd33ee44ff5566778899aabbccdd"
SHA_LOCK = "904a13ac1ffeeddccbbaa99887766554433221100"
SHA_IBRS = "7c1d2e3f40112233445566778899aabbccddeeff"


def _user(login, bot=False):
    return {"login": login, "type": "Bot" if bot else "User"}


ISSUES = [
    {"number": 88, "title": "x86: IBRS based Spectre v2 mitigation",
     "body": "We should support hardware IBRS on x86 as an alternative Spectre v2 mitigation.",
     "user": _user("alice"), "state": "closed", "labels": [{"name": "x86"}], "comments": 1,
     "created_at": "2018-02-01T10:00:00Z", "closed_at": "2018-03-01T10:00:00Z",
     "html_url": "https://github.com/seL4/seL4/issues/88"},
    {"number": 91, "title": "Question about build docs", "body": "How do I build for qemu?",
     "user": _user("carol"), "state": "closed", "labels": [], "comments": 0,
     "created_at": "2018-04-01T10:00:00Z", "closed_at": "2018-04-02T10:00:00Z",
     "html_url": "https://github.com/seL4/seL4/issues/91"},
    # The issues endpoint also returns PRs; the extractor must skip these.
    {"number": 420, "title": "x86: remove PAE", "pull_request": {}, "user": _user("bob"),
     "created_at": "2017-01-01T00:00:00Z"},
]

PRS = [
    {"number": 420, "title": "x86: remove PAE support for ia32 (SELFOUR-501)",
     "body": "Removes the Physical Address Extension (PAE) paging mode from 32-bit x86. "
             "Maintaining two page-table formats doubled the verification and test burden "
             "and PAE was unused. Tracked in SELFOUR-501.",
     "user": _user("bob"), "state": "closed", "merged_at": "2017-02-10T12:00:00Z",
     "merge_commit_sha": SHA_PAE, "base": {"ref": "master"}, "labels": [],
     "created_at": "2017-02-01T12:00:00Z", "closed_at": "2017-02-10T12:00:00Z",
     "html_url": "https://github.com/seL4/seL4/pull/420"},
    {"number": 433, "title": "smp: move lock.c to generic smp directory",
     "body": "lock.c is architecture independent; move it out of src/arch/x86 so ARM SMP can share it.",
     "user": _user("bob"), "state": "closed", "merged_at": "2017-02-21T09:00:00Z",
     "merge_commit_sha": SHA_LOCK, "base": {"ref": "master"}, "labels": [],
     "created_at": "2017-02-20T09:00:00Z", "closed_at": "2017-02-21T09:00:00Z",
     "html_url": "https://github.com/seL4/seL4/pull/433"},
    {"number": 455, "title": "x86: add IBRS spectre mitigation", "body": "Fixes #88. Adds hardware IBRS support.",
     "user": _user("alice"), "state": "closed", "merged_at": "2018-03-01T09:00:00Z",
     "merge_commit_sha": SHA_IBRS, "base": {"ref": "master"}, "labels": [],
     "created_at": "2018-02-10T09:00:00Z", "closed_at": "2018-03-01T09:00:00Z",
     "html_url": "https://github.com/seL4/seL4/pull/455"},
]

PR_COMMITS = {420: [SHA_PAE_2], 433: [SHA_LOCK], 455: [SHA_IBRS]}
PR_FILES = {420: ["src/arch/x86/32/kernel/vspace_pae.c", "src/arch/x86/32/head.S"],
            433: ["src/arch/x86/kernel/lock.c", "src/smp/lock.c"],
            455: ["src/arch/x86/machine/hardware.c"]}
REVIEWS = {
    420: [{"id": 9001, "user": _user("dave"), "state": "APPROVED",
           "body": "Agree: dropping PAE removes a whole page-table format from the proofs.",
           "submitted_at": "2017-02-09T12:00:00Z"},
          {"id": 9002, "user": _user("ci-bot[bot]", bot=True), "state": "COMMENTED", "body": "CI passed"}],
    433: [{"id": 9003, "user": _user("erin"), "state": "APPROVED", "body": "", "submitted_at": "2017-02-21T08:00:00Z"}],
    455: [{"id": 9004, "user": _user("dave"), "state": "CHANGES_REQUESTED",
           "body": "Please gate IBRS behind a config option.", "submitted_at": "2018-02-20T09:00:00Z"}],
}
REVIEW_COMMENTS = {
    420: [{"pull_request_review_id": 9001, "path": "src/arch/x86/32/head.S", "line": 40,
           "body": "PAE setup code can go too.", "diff_hunk": "@@ -30,6 +30,0 @@\n-    /* enable PAE */"}],
    433: [], 455: [],
}
ISSUE_COMMENTS = {
    88: [{"user": _user("dave"), "body": "IBRS is expensive on older CPUs; make it optional.",
          "created_at": "2018-02-02T10:00:00Z"}],
    420: [{"user": _user("dave"), "body": "Looks good, no users of PAE remain.", "created_at": "2017-02-05T10:00:00Z"}],
    433: [], 455: [],
}

COMMITS = [
    {"sha": SHA_PAE, "message": "Merge pull request #420 from bob/remove-pae\n\nx86: remove PAE (SELFOUR-501)"},
    {"sha": SHA_PAE_2, "message": "x86: remove PAE support\n\nSELFOUR-501: delete vspace_pae.c"},
    {"sha": SHA_LOCK, "message": "smp: move lock.c to src/smp (#433)"},
    {"sha": SHA_IBRS, "message": "x86: IBRS spectre mitigation\n\nFixes #88"},
]


class FakeGitHubClient(GitHubClient):
    """Serves the fixture through the real pagination code path."""

    def __init__(self):
        super().__init__(token="test", cache_dir=None)

    def _fetch(self, url):
        path = url.split("api.github.com", 1)[1].split("?", 1)[0]
        base = f"/repos/{REPO}"
        table = {f"{base}/issues": ISSUES, f"{base}/pulls": PRS}
        for n in PR_COMMITS:
            table[f"{base}/pulls/{n}/commits"] = [{"sha": s} for s in PR_COMMITS[n]]
            table[f"{base}/pulls/{n}/files"] = [{"filename": f} for f in PR_FILES[n]]
            table[f"{base}/pulls/{n}/reviews"] = REVIEWS[n]
            table[f"{base}/pulls/{n}/comments"] = REVIEW_COMMENTS[n]
        for n, cs in ISSUE_COMMENTS.items():
            table[f"{base}/issues/{n}/comments"] = cs
        self.requests_made += 1
        return table.get(path, []), None
