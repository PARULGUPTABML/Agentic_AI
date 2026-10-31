"""LLM-as-Judge.

Design choices (after Zheng et al., 2023, "Judging LLM-as-a-Judge"):

* **Single-answer grading against a reference** (not pairwise), so there is no
  position bias; the judge never sees which configuration produced the answer.
* **Rubric with atomic key facts** — completeness is the fraction of key facts the
  judge marks covered, which is far more stable than a holistic 1-10 score.
* **Programmatic checks override the model where they can**: fabricated evidence
  IDs are detected by code (``metrics.citation_metrics``), never by the judge.
* **Temperature 0 + optional majority vote** (``n_votes``) for stability.
* **Deterministic heuristic fallback** when no judge model is configured
  (``LLM_PROVIDER=mock``) so the whole pipeline and tests run offline; the
  output records ``judge_method`` so heuristic and LLM grades are never mixed
  silently in the dashboard.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable

from evaluation import rubric
from evaluation.metrics import (abstains, citation_metrics, fact_covered, is_unanswerable,
                                separates_evidence_inference)

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "judge_system.md"
MAX_EVIDENCE_CHARS = 1500


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except OSError:
        return "You are an impartial judge. Return JSON with key_facts, accuracy, unsupported_claims, contradictions, abstained, separates_evidence_and_inference, rationale."


def parse_judge_json(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from a model response (tolerates ```json fences)."""
    if not text:
        return None
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start = t.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(t)):
        if t[i] == "{":
            depth += 1
        elif t[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


class Judge:
    def __init__(self, llm_fn: Callable[[str, str], str] | str | None = "auto", n_votes: int = 1):
        if llm_fn == "auto":
            from evaluation.llm import get_judge_llm
            llm_fn = get_judge_llm()
        self.llm_fn = llm_fn if callable(llm_fn) else None
        self.n_votes = max(1, int(n_votes))
        self.system_prompt = _load_system_prompt()

    # ------------------------------------------------------------------ #
    def build_prompt(self, item: dict, answer: str, gold_texts: list[str]) -> str:
        facts = item.get("key_facts") or []
        evidence = "\n\n".join(f"[{i + 1}] {t[:MAX_EVIDENCE_CHARS]}" for i, t in enumerate(gold_texts)) or "(none)"
        return (
            f"QUESTION:\n{item.get('question', '')}\n\n"
            f"REFERENCE ANSWER:\n{item.get('reference_answer') or item.get('answer', '')}\n\n"
            f"KEY FACTS:\n" + ("\n".join(f"- {f}" for f in facts) or "(none)") + "\n\n"
            f"GOLD EVIDENCE:\n{evidence}\n\n"
            f"ANSWERABLE: {'no' if is_unanswerable(item) else 'yes'}\n\n"
            f"CANDIDATE ANSWER:\n{answer.strip() or '(empty)'}\n\n"
            "Return the JSON object now."
        )

    def _llm_verdict(self, item: dict, answer: str, gold_texts: list[str]) -> dict[str, Any] | None:
        prompt = self.build_prompt(item, answer, gold_texts)
        votes = []
        for _ in range(self.n_votes):
            try:
                v = parse_judge_json(self.llm_fn(self.system_prompt, prompt))
            except Exception:
                v = None
            if v and v.get("accuracy") in rubric.ACCURACY_POINTS:
                votes.append(v)
        if not votes:
            return None
        if len(votes) == 1:
            return votes[0]
        # Majority vote per field; key facts by per-fact majority.
        label = Counter(v["accuracy"] for v in votes).most_common(1)[0][0]
        merged = next(v for v in votes if v["accuracy"] == label)
        facts = merged.get("key_facts", [])
        for i, f in enumerate(facts):
            yes = sum(1 for v in votes if i < len(v.get("key_facts", [])) and v["key_facts"][i].get("covered"))
            f["covered"] = yes * 2 > len(votes)
        merged["abstained"] = sum(bool(v.get("abstained")) for v in votes) * 2 > len(votes)
        merged["separates_evidence_and_inference"] = sum(
            bool(v.get("separates_evidence_and_inference")) for v in votes) * 2 > len(votes)
        merged["contradictions"] = [c for c in merged.get("contradictions", [])] if sum(
            bool(v.get("contradictions")) for v in votes) * 2 > len(votes) else []
        merged["votes"] = len(votes)
        return merged

    def _heuristic_verdict(self, item: dict, answer: str, fabricated: list[str]) -> dict[str, Any]:
        facts = item.get("key_facts") or []
        unanswerable = is_unanswerable(item)
        abst = abstains(answer)
        if unanswerable:
            label = "correct" if abst and not fabricated else "incorrect"
            covered = [{"fact": f, "covered": label == "correct"} for f in facts]
            contradictions = [] if abst else ["answer asserts content for an unanswerable question"]
        else:
            covered = [{"fact": f, "covered": fact_covered(f, answer)} for f in facts]
            frac = (sum(c["covered"] for c in covered) / len(covered)) if covered else 0.0
            if frac >= 0.67:
                label = "correct"
            elif frac >= 0.34:
                label = "partial"
            else:
                label = "incorrect"
            contradictions = []
        return {
            "key_facts": covered, "accuracy": label, "unsupported_claims": [],
            "contradictions": contradictions, "abstained": abst,
            "separates_evidence_and_inference": separates_evidence_inference(answer),
            "rationale": "heuristic: lexical key-fact coverage + programmatic ID checks",
        }

    # ------------------------------------------------------------------ #
    def grade(self, item: dict[str, Any], answer: str, gold_texts: list[str] | None = None,
              retrieved: list[str] | None = None, evidence_texts: Iterable[str] = ()) -> dict[str, Any]:
        gold_texts = gold_texts or []
        retrieved = retrieved or []
        gold = item.get("ground_truth_evidence", item.get("gold_evidence", [])) or []
        cites = citation_metrics(answer, retrieved, gold, item.get("reference_answer", ""), evidence_texts)

        verdict, method = None, "heuristic"
        if self.llm_fn is not None:
            verdict = self._llm_verdict(item, answer, gold_texts)
            method = "llm" if verdict else "heuristic_after_llm_failure"
        if verdict is None:
            verdict = self._heuristic_verdict(item, answer, cites["fabricated_ids"])

        facts = verdict.get("key_facts") or []
        unanswerable = is_unanswerable(item)
        if unanswerable:
            completeness = 1.0 if verdict.get("accuracy") == "correct" else 0.0
        else:
            completeness = (sum(1 for f in facts if f.get("covered")) / len(facts)) if facts else (
                1.0 if verdict.get("accuracy") == "correct" else 0.5 if verdict.get("accuracy") == "partial" else 0.0)

        label = verdict.get("accuracy", "incorrect")
        # Fabricated identifiers are a hard groundedness failure: downgrade one level.
        if cites["fabricated_ids"] and label != "incorrect":
            label = "partial" if label == "correct" else "incorrect"

        out = {
            "accuracy_label": label,
            "accuracy": rubric.ACCURACY_VALUE[label],
            "completeness": round(completeness, 4),
            "key_facts_covered": sum(1 for f in facts if f.get("covered")),
            "key_facts_total": len(facts),
            "unsupported_claims": verdict.get("unsupported_claims", []) or [],
            "contradictions": verdict.get("contradictions", []) or [],
            "fabricated_ids": cites["fabricated_ids"],
            "abstained": bool(verdict.get("abstained")),
            "unanswerable": unanswerable,
            "separates_evidence_and_inference": bool(verdict.get("separates_evidence_and_inference")),
            "judge_method": method,
            "judge_rationale": verdict.get("rationale", ""),
        }
        out["hallucination"] = 1.0 if (out["contradictions"] or out["fabricated_ids"]) else 0.0
        out["hallucination_lenient"] = 1.0 if (out["hallucination"] or out["unsupported_claims"]) else 0.0
        out["judge_score"] = rubric.composite_score(out)
        out["citation_precision"] = cites["citation_precision"]
        out["gold_citation_recall"] = cites["gold_citation_recall"]
        out["n_cited"] = cites["n_cited"]
        return out
