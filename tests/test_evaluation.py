"""Tests for the LLM-as-Judge evaluation framework."""
from __future__ import annotations

import json

import pytest

from evaluation import rubric
from evaluation.baselines import run_code_only, run_no_context
from evaluation.judge import Judge, parse_judge_json
from evaluation.metrics import (abstains, citation_metrics, extract_citations, extract_ids, fact_covered,
                                ids_match, is_unanswerable, judge_answer, normalize_id, retrieval_metrics)
from evaluation.spot_check import agreement, cohen_kappa, export_sample
from evaluation.stats import bootstrap_ci, holm, paired_permutation_p, summarize

ITEM = {
    "qid": "Q001", "question": "Why was PAE support removed from x86 32-bit under SELFOUR-501?",
    "reference_answer": "In commit:3e57e647e (merged via PR:420, tracked under issue:SELFOUR-501), PAE support "
                        "was removed by deleting src/arch/x86/32/kernel/vspace_pae.c.",
    "key_facts": ["Removed x86 PAE support under issue:SELFOUR-501 in commit:3e57e647e",
                  "Deleted src/arch/x86/32/kernel/vspace_pae.c",
                  "Merged into master by Hesham Almatary in PR:420"],
    "ground_truth_evidence": ["commit:3e57e647e", "pr:420", "issue:SELFOUR-501"],
}
UNANSWERABLE = {"qid": "Q007", "question": "Why was Kyber added?", "reference_answer": "No evidence exists.",
                "key_facts": ["No Kyber implementation exists"], "ground_truth_evidence": []}


class TestIds:
    def test_normalize(self):
        assert normalize_id("PR:420") == "pr:420"
        assert normalize_id("issue:selfour-501") == "issue:SELFOUR-501"
        assert normalize_id("commit:3E57E647E") == "commit:3e57e647e"

    def test_commit_prefix_match(self):
        assert ids_match("commit:3e57e647e", "commit:3e57e647e0")
        assert not ids_match("commit:3e57e6", "commit:3e57e647e0")          # < 7 chars
        assert not ids_match("pr:420", "issue:420")

    def test_extract(self):
        text = "See [commit:abc1234, pr:12] and PR #13; commit deadbeef1 did it [code:src/a.c]."
        assert extract_citations(text) == ["commit:abc1234", "pr:12", "code:src/a.c"]
        assert {"pr:13", "commit:deadbeef1"} <= set(extract_ids(text))


class TestRetrievalMetrics:
    def test_basic(self):
        m = retrieval_metrics(["code:x", "commit:3e57e647e0", "pr:420"], ITEM["ground_truth_evidence"], k=2)
        assert m["precision_at_k"] == 0.5 and m["recall_at_k"] == pytest.approx(1 / 3, abs=1e-3)
        assert m["mrr"] == 0.5 and m["evidence_recall"] == pytest.approx(2 / 3, abs=1e-3)

    def test_no_gold_excluded(self):
        m = retrieval_metrics(["code:x"], [], 5)
        assert m["recall_at_k"] is None and m["n_gold"] == 0


class TestCitations:
    def test_fabricated_ids(self):
        c = citation_metrics("Done in [commit:3e57e647e0] and [pr:999].", ["commit:3e57e647e0"], ["pr:420"])
        assert c["fabricated_ids"] == ["pr:999"] and c["citation_precision"] == 0.5

    def test_ids_in_evidence_text_are_allowed(self):
        c = citation_metrics("Merged as commit deadbeef12.", ["pr:1"], [], evidence_texts=["Linked changes: commit:deadbeef12"])
        assert c["fabricated_ids"] == []


class TestHeuristicJudge:
    def test_reference_answer_scores_well(self):
        r = judge_answer(ITEM, ITEM["reference_answer"] + " Merged by Hesham Almatary.",
                         retrieved=ITEM["ground_truth_evidence"], llm_fn=None)
        assert r["accuracy_label"] == "correct" and r["hallucination"] == 0.0
        assert r["judge_method"] == "heuristic"

    def test_fabricated_id_downgrades_and_flags(self):
        good = ITEM["reference_answer"] + " Merged by Hesham Almatary [pr:777]."
        r = judge_answer(ITEM, good, retrieved=["commit:3e57e647e0", "pr:420"], llm_fn=None)
        assert r["hallucination"] == 1.0 and r["fabricated_ids"] == ["pr:777"]
        assert r["accuracy_label"] == "partial"

    def test_unanswerable(self):
        assert judge_answer(UNANSWERABLE, "There is no evidence for this in the repository.", llm_fn=None)["accuracy"] == 1.0
        bad = judge_answer(UNANSWERABLE, "Kyber was added for post-quantum security.", llm_fn=None)
        assert bad["accuracy"] == 0.0 and bad["hallucination"] == 1.0

    def test_helpers(self):
        assert is_unanswerable(UNANSWERABLE) and not is_unanswerable(ITEM)
        assert abstains("The available evidence does not explain this.")
        assert not abstains("Answer [pr:1]\n- Not found in evidence: none")
        assert fact_covered("Deleted src/arch/x86/32/kernel/vspace_pae.c", "they deleted vspace_pae.c from src/arch/x86/32/kernel")
        assert not fact_covered("Removed PAE in commit:3e57e647e", "Removed PAE in commit:1111111aa")

    def test_score_bounds(self):
        r = judge_answer(ITEM, "", llm_fn=None)
        assert 0 <= r["judge_score"] <= rubric.MAX_SCORE


class TestLLMJudge:
    def _llm(self, payload):
        calls = []

        def fn(system, prompt):
            calls.append(prompt)
            assert "CANDIDATE ANSWER" in prompt and "KEY FACTS" in prompt
            return payload if isinstance(payload, str) else payload.pop(0)
        return fn, calls

    def test_parses_fenced_json(self):
        verdict = {"key_facts": [{"fact": f, "covered": True} for f in ITEM["key_facts"]], "accuracy": "correct",
                   "unsupported_claims": [], "contradictions": [], "abstained": False,
                   "separates_evidence_and_inference": True, "rationale": "ok"}
        fn, calls = self._llm("```json\n" + json.dumps(verdict) + "\n```")
        r = Judge(llm_fn=fn).grade(ITEM, "answer [pr:420]", retrieved=["pr:420"])
        assert r["judge_method"] == "llm" and r["accuracy"] == 1.0 and r["judge_score"] == 10.0
        assert len(calls) == 1

    def test_majority_vote(self):
        mk = lambda acc, cov: json.dumps({"key_facts": [{"fact": "f", "covered": cov}], "accuracy": acc,
                                          "unsupported_claims": [], "contradictions": [], "abstained": False,
                                          "separates_evidence_and_inference": True})
        fn, calls = self._llm([mk("partial", False), mk("correct", True), mk("correct", True)])
        r = Judge(llm_fn=fn, n_votes=3).grade(ITEM, "x", retrieved=[])
        assert len(calls) == 3 and r["accuracy_label"] == "correct" and r["completeness"] == 1.0

    def test_malformed_output_falls_back(self):
        fn, _ = self._llm("I think it is fine")
        r = Judge(llm_fn=fn).grade(ITEM, ITEM["reference_answer"], retrieved=ITEM["ground_truth_evidence"])
        assert r["judge_method"] == "heuristic_after_llm_failure"

    def test_parse_judge_json(self):
        assert parse_judge_json('noise {"a": {"b": 1}} trailing') == {"a": {"b": 1}}
        assert parse_judge_json("nothing") is None


class TestStats:
    def test_bootstrap_and_permutation(self):
        lo, hi = bootstrap_ci([0, 1, 1, 1, 0, 1, 1, 1])
        assert 0 <= lo <= 0.75 <= hi <= 1
        assert paired_permutation_p([1.0] * 10) < 0.01
        assert paired_permutation_p([0.0] * 5) == 1.0

    def test_holm(self):
        assert holm([0.01, 0.04, None, 0.03]) == [0.03, 0.06, None, 0.06]

    def test_summarize_pairs_by_question(self):
        rows = []
        for q in range(8):
            rows.append({"question_id": f"Q{q}", "condition": "A_no_context", "judge": {"accuracy": 0.0, "hallucination": 1.0},
                         "total_latency_ms": 10})
            rows.append({"question_id": f"Q{q}", "condition": "C_sequential", "judge": {"accuracy": 1.0, "hallucination": 0.0},
                         "total_latency_ms": 100})
        s = summarize(rows)
        assert s["conditions"]["C_sequential"]["accuracy"] == 1.0
        assert s["conditions"]["A_no_context"]["hallucination_rate"] == 1.0
        acc = next(c for c in s["comparisons"] if c["metric"] == "accuracy")
        assert acc["n_pairs"] == 8 and acc["mean_diff"] == 1.0 and acc["significant_0_05"]


class TestSpotCheck:
    def test_kappa(self):
        assert cohen_kappa(["a", "b", "a"], ["a", "b", "a"]) == 1.0
        assert cohen_kappa(["a", "a"], ["b", "b"]) == 0.0

    def test_export_and_agree(self, tmp_path):
        runs = tmp_path / "runs.jsonl"
        runs.write_text("\n".join(json.dumps({"run_id": f"r{i}", "question_id": f"Q{i}", "condition": c,
                                               "final_answer": "x", "judge": {"accuracy_label": "correct", "hallucination": 0}})
                                   for i, c in enumerate(["A", "B", "C", "A"])))
        out = tmp_path / "s.csv"
        assert export_sample(str(runs), 3, str(out)) == 3
        import csv
        rows = list(csv.DictReader(out.open()))
        for r in rows:                                   # a human labeller agrees on every row
            r["human_accuracy"], r["human_hallucination"] = "correct", "0"
        with out.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        res = agreement(str(out))
        assert res["n_labelled"] == 3 and res["accuracy_agreement"] == 1.0 and res["hallucination_agreement"] == 1.0


class TestBaselines:
    def test_no_context_mock(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "mock")
        r = run_no_context("Why?")
        assert r["condition"] == "A_no_context" and r["evidence"] == [] and abstains(r["answer"])

    def test_code_only_uses_code_agent(self):
        from agents.blackboard import EvidenceFragment

        class Code:
            def run(self, q, repo_slug=None):
                return [EvidenceFragment("code:src/a.c", "code_analysis", "code", "src/a.c", "int a;", 0.8)]
        seen = {}
        r = run_code_only("Where is a?", Code(), llm_fn=lambda s, p: seen.setdefault("p", p) and "a [code:src/a.c]")
        assert "[code:src/a.c]" in seen["p"] and r["evidence"][0]["id"] == "code:src/a.c"
