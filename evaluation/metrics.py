"""Evaluation metrics: retrieval, citations, hallucination and the judge entry point.

Public API (kept compatible with ``run_eval_multi_agent.py``)::

    retrieval_metrics(retrieved_ids, gold_ids, k)            -> dict
    citation_metrics(answer, retrieved_ids, gold_ids, ...)   -> dict
    judge_answer(item, answer, gold_texts, ...)              -> dict

Evidence-ID matching rules
--------------------------
* Kinds must agree (``commit``, ``pr``, ``issue``, ``review``, ``code``).
* Commits match on SHA prefix (>= 7 hex chars), so ``commit:3e57e647e`` (eval set,
  9 chars) matches ``commit:3e57e647e0`` (History agent, 10 chars).
* ``PR:420``, ``pr:420`` and ``#420``-style mentions normalise to ``pr:420``;
  Jira keys are upper-cased (``issue:SELFOUR-501``).
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from ingestion.corpus_store import tokenize

ID_PREFIXES = ("commit", "pr", "issue", "review", "code", "doc")
_ID_MENTION = re.compile(r"\b(commit|pr|issue|review|code|doc)\s*:\s*([A-Za-z0-9_\-./:]+[A-Za-z0-9_])", re.IGNORECASE)
_PR_MENTION = re.compile(r"\b(?:PR|pull request)\s*#?\s*(\d+)\b", re.IGNORECASE)
_SHA_NEAR_COMMIT = re.compile(r"\bcommit\s+([0-9a-f]{7,40})\b", re.IGNORECASE)
_ABSTAIN = re.compile(
    r"\b(no (relevant |historical )?evidence|does not (explain|contain|show)|not (found|present) in|"
    r"cannot be (determined|answered|found)|there is no (record|evidence|indication)|"
    r"no (record|mention|indication)|unable to find|insufficient evidence|not supported by the evidence)\b",
    re.IGNORECASE)


# ---------------------------------------------------------------------- #
#  ID normalisation / matching                                            #
# ---------------------------------------------------------------------- #
def normalize_id(ref: str) -> str:
    r = (ref or "").strip().strip("[]()., ")
    if ":" not in r:
        return r.lower()
    kind, key = r.split(":", 1)
    kind = kind.strip().lower()
    key = key.strip()
    if kind == "pull":
        kind = "pr"
    if kind == "commit":
        key = key.lower()
    elif kind in ("pr", "issue"):
        key = key.lstrip("#")
        key = key if key.isdigit() else key.upper()
    return f"{kind}:{key}"


def ids_match(a: str, b: str) -> bool:
    a, b = normalize_id(a), normalize_id(b)
    if a == b:
        return True
    if a.startswith("commit:") and b.startswith("commit:"):
        x, y = a[7:], b[7:]
        n = min(len(x), len(y))
        return n >= 7 and x[:n] == y[:n]
    return False


def _in(ref: str, pool: Iterable[str]) -> bool:
    return any(ids_match(ref, p) for p in pool)


def extract_ids(text: str) -> list[str]:
    """Evidence identifiers mentioned anywhere in *text* (citations or prose)."""
    found: list[str] = []
    for kind, key in _ID_MENTION.findall(text or ""):
        found.append(normalize_id(f"{kind}:{key}"))
    for n in _PR_MENTION.findall(text or ""):
        found.append(f"pr:{n}")
    for sha in _SHA_NEAR_COMMIT.findall(text or ""):
        found.append(f"commit:{sha.lower()}")
    return list(dict.fromkeys(found))


def extract_citations(text: str) -> list[str]:
    """IDs that appear inside square-bracket citations, e.g. ``[commit:abc, pr:2]``."""
    out = []
    for group in re.findall(r"\[([^\[\]]+)\]", text or ""):
        for part in group.split(","):
            p = part.strip()
            if ":" in p and p.split(":", 1)[0].lower() in ID_PREFIXES:
                out.append(normalize_id(p))
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------------- #
#  Retrieval metrics                                                      #
# ---------------------------------------------------------------------- #
def retrieval_metrics(retrieved: list[str], gold: list[str], k: int = 8) -> dict[str, Any]:
    """Precision/recall@k, hit@k, MRR and whole-list evidence precision.

    Returns ``None`` for ratio metrics when there is no gold evidence (unanswerable
    probes), so they are excluded from averages rather than counted as zero.
    """
    retrieved = [r for r in retrieved if r]
    if not gold:
        return {"precision_at_k": None, "recall_at_k": None, "hit_at_k": None, "mrr": None,
                "evidence_precision": None, "evidence_recall": None, "n_retrieved": len(retrieved), "n_gold": 0}
    top = retrieved[:k]
    rel_top = [r for r in top if _in(r, gold)]
    gold_found_top = [g for g in gold if _in(g, top)]
    gold_found_all = [g for g in gold if _in(g, retrieved)]
    rr = 0.0
    for i, r in enumerate(retrieved, 1):
        if _in(r, gold):
            rr = 1.0 / i
            break
    return {
        "precision_at_k": round(len(rel_top) / len(top), 4) if top else 0.0,
        "recall_at_k": round(len(gold_found_top) / len(gold), 4),
        "hit_at_k": 1.0 if gold_found_top else 0.0,
        "mrr": round(rr, 4),
        "evidence_precision": round(sum(1 for r in retrieved if _in(r, gold)) / len(retrieved), 4) if retrieved else 0.0,
        "evidence_recall": round(len(gold_found_all) / len(gold), 4),
        "n_retrieved": len(retrieved),
        "n_gold": len(gold),
    }


# ---------------------------------------------------------------------- #
#  Citations / fabricated identifiers                                     #
# ---------------------------------------------------------------------- #
def citation_metrics(answer: str, retrieved: list[str], gold: list[str],
                     reference_answer: str = "", evidence_texts: Iterable[str] = ()) -> dict[str, Any]:
    cited = extract_citations(answer)
    mentioned = extract_ids(answer)
    allowed = set(map(normalize_id, retrieved)) | set(map(normalize_id, gold)) | set(extract_ids(reference_answer))
    for t in evidence_texts:
        allowed |= set(extract_ids(t))
    fabricated = [m for m in dict.fromkeys(cited + mentioned) if not _in(m, allowed)]
    valid_cited = [c for c in cited if _in(c, retrieved)]
    return {
        "cited": cited,
        "n_cited": len(cited),
        "citation_precision": round(len(valid_cited) / len(cited), 4) if cited else None,
        "gold_citation_recall": (round(sum(1 for g in gold if _in(g, cited)) / len(gold), 4) if gold else None),
        "fabricated_ids": fabricated,
        "n_fabricated": len(fabricated),
    }


# ---------------------------------------------------------------------- #
#  Heuristic helpers shared with the judge                                #
# ---------------------------------------------------------------------- #
def is_unanswerable(item: dict[str, Any]) -> bool:
    if "answerable" in item:
        return not item["answerable"]
    gold = item.get("ground_truth_evidence", item.get("gold_evidence"))
    return gold is not None and len(gold) == 0


_TEMPLATE_NO_GAP = re.compile(r"not found in evidence:\s*none\.?", re.IGNORECASE)


def abstains(answer: str) -> bool:
    """True when the answer says the evidence does not support an answer.

    The Reviewer's rendered template always contains "Not found in evidence: none"
    when there is *no* gap, so that boilerplate is removed before matching.
    """
    return bool(_ABSTAIN.search(_TEMPLATE_NO_GAP.sub("", answer or "")))


def fact_covered(fact: str, answer: str) -> bool:
    """Lexical coverage of one key fact: identifiers must match, content words mostly present."""
    ans_ids = extract_ids(answer)
    fact_ids = extract_ids(fact)
    if fact_ids and not any(_in(f, ans_ids) for f in fact_ids):
        # also accept a bare SHA / number written without a prefix
        bare = [f.split(":", 1)[1] for f in fact_ids]
        if not any(b.lower() in (answer or "").lower() for b in bare):
            return False
    ft = {t for t in tokenize(re.sub(_ID_MENTION, " ", fact)) if len(t) > 2}
    if not ft:
        return bool(fact_ids)
    at = set(tokenize(answer))
    overlap = len(ft & at) / len(ft)
    return overlap >= (0.4 if fact_ids else 0.6)


def separates_evidence_inference(answer: str) -> bool:
    a = answer or ""
    if re.search(r"\binference\b", a, re.IGNORECASE):
        return True
    has_cites = bool(extract_citations(a))
    hedged = bool(re.search(r"\b(likely|probably|suggests|appears to|it seems|presumably)\b", a, re.IGNORECASE))
    return has_cites and (hedged or not re.search(r"\b(because|so that|in order to)\b", a, re.IGNORECASE))


def judge_answer(item: dict[str, Any], answer: str, gold_texts: list[str] | None = None,
                 retrieved: list[str] | None = None, evidence_texts: Iterable[str] = (),
                 llm_fn: Any = "auto", n_votes: int = 1) -> dict[str, Any]:
    """Grade one answer with the LLM judge (or the deterministic fallback).

    Returns flat fields ready to merge into a results row: ``accuracy`` (0/0.5/1),
    ``accuracy_label``, ``completeness``, ``hallucination`` (0/1), ``judge_score`` (0-10), ...
    """
    from evaluation.judge import Judge   # local import avoids a cycle
    return Judge(llm_fn=llm_fn, n_votes=n_votes).grade(
        item, answer, gold_texts=gold_texts or [], retrieved=retrieved or [], evidence_texts=evidence_texts)
