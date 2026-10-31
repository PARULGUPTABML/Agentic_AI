"""Statistical summaries for experiment results (stdlib only).

* Per-condition means with seeded percentile-bootstrap 95% CIs.
* Latency percentiles (p50 / p95).
* Paired comparisons between conditions on the same questions:
  mean difference, bootstrap CI of the difference, two-sided paired
  sign-flip permutation test, Holm-Bonferroni correction across comparisons.

``summarize(path_or_rows)`` returns ``{"conditions": {...}, "comparisons": [...]}``
and accepts both the run-record JSONL from ``run_experiments.py`` and the rows
written by ``run_eval_multi_agent.py``.
"""
from __future__ import annotations

import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

SEED = 2026
N_BOOT = 2000
N_PERM = 5000

METRICS = [
    "accuracy", "completeness", "hallucination", "hallucination_lenient", "judge_score",
    "precision_at_k", "recall_at_k", "hit_at_k", "mrr", "evidence_precision", "evidence_recall",
    "citation_precision", "gold_citation_recall", "total_latency_ms", "coordination_overhead_ms",
    "llm_calls", "total_tokens", "retrieval_iterations", "rounds", "evidence_count",
]
COMPARE_METRICS = ["accuracy", "hallucination", "judge_score"]
ALIASES = {"latency_total_ms": "total_latency_ms", "qid": "question_id"}


# ---------------------------------------------------------------------- #
def load_rows(source: str | Path | Iterable[dict]) -> list[dict[str, Any]]:
    if isinstance(source, (str, Path)):
        rows = [json.loads(l) for l in Path(source).read_text(encoding="utf-8").splitlines() if l.strip()]
    else:
        rows = list(source)
    return [flatten(r) for r in rows]


def flatten(r: dict[str, Any]) -> dict[str, Any]:
    out = {ALIASES.get(k, k): v for k, v in r.items() if not isinstance(v, (dict, list))}
    for nested in ("judge", "retrieval", "citations"):
        for k, v in (r.get(nested) or {}).items():
            if not isinstance(v, (dict, list)):
                out.setdefault(k, v)
    if "evidence_count" not in out and isinstance(r.get("retrieved_evidence"), list):
        out["evidence_count"] = len(r["retrieved_evidence"])
    if isinstance(r.get("errors"), list):
        out["n_errors"] = len(r["errors"])
    return out


def _vals(rows: list[dict], metric: str) -> list[float]:
    out = []
    for r in rows:
        v = r.get(metric)
        if isinstance(v, bool):
            v = float(v)
        if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
            out.append(float(v))
    return out


def percentile(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (pos - lo)


def bootstrap_ci(xs: list[float], n_boot: int = N_BOOT, alpha: float = 0.05, seed: int = SEED) -> tuple | None:
    if len(xs) < 2:
        return None
    rng = random.Random(seed)
    n = len(xs)
    means = sorted(sum(rng.choice(xs) for _ in range(n)) / n for _ in range(n_boot))
    return (round(means[int(alpha / 2 * n_boot)], 4), round(means[int((1 - alpha / 2) * n_boot) - 1], 4))


def paired_permutation_p(diffs: list[float], n_perm: int = N_PERM, seed: int = SEED) -> float | None:
    """Two-sided sign-flip test of H0: mean paired difference = 0."""
    diffs = [d for d in diffs if d is not None]
    if len(diffs) < 2:
        return None
    obs = abs(sum(diffs) / len(diffs))
    if obs == 0:
        return 1.0
    rng = random.Random(seed)
    hits = 0
    for _ in range(n_perm):
        s = sum(d if rng.random() < 0.5 else -d for d in diffs) / len(diffs)
        if abs(s) >= obs - 1e-12:
            hits += 1
    return round((hits + 1) / (n_perm + 1), 5)


def holm(pvals: list[float | None]) -> list[float | None]:
    idx = [i for i, p in enumerate(pvals) if p is not None]
    order = sorted(idx, key=lambda i: pvals[i])
    m = len(order)
    adj: list[float | None] = [None] * len(pvals)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvals[i]))
        adj[i] = round(running, 5)
    return adj


# ---------------------------------------------------------------------- #
def describe(rows: list[dict]) -> dict[str, Any]:
    d: dict[str, Any] = {"n": len(rows), "n_questions": len({r.get("question_id") for r in rows})}
    for m in METRICS:
        xs = _vals(rows, m)
        if not xs:
            continue
        d[m] = round(statistics.fmean(xs), 4)
        if m in ("accuracy", "completeness", "hallucination", "judge_score", "evidence_precision", "recall_at_k"):
            d[f"{m}_ci95"] = bootstrap_ci(xs)
    lat = _vals(rows, "total_latency_ms")
    if lat:
        d["latency_p50_ms"] = round(percentile(lat, 0.5), 2)
        d["latency_p95_ms"] = round(percentile(lat, 0.95), 2)
    d["hallucination_rate"] = d.get("hallucination")
    d["error_rate"] = round(sum(1 for r in rows if r.get("n_errors") or r.get("status") in ("failed", "degraded"))
                            / len(rows), 4) if rows else None
    methods = {r.get("judge_method") for r in rows if r.get("judge_method")}
    d["judge_methods"] = sorted(methods)
    return d


def _per_question_means(rows: list[dict], metric: str) -> dict[str, float]:
    acc: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        v = _vals([r], metric)
        if v:
            acc[str(r.get("question_id"))].append(v[0])
    return {q: statistics.fmean(v) for q, v in acc.items()}


def compare(rows_a: list[dict], rows_b: list[dict], metric: str) -> dict[str, Any]:
    a, b = _per_question_means(rows_a, metric), _per_question_means(rows_b, metric)
    common = sorted(set(a) & set(b))
    diffs = [b[q] - a[q] for q in common]
    return {
        "metric": metric,
        "n_pairs": len(common),
        "mean_a": round(statistics.fmean([a[q] for q in common]), 4) if common else None,
        "mean_b": round(statistics.fmean([b[q] for q in common]), 4) if common else None,
        "mean_diff": round(statistics.fmean(diffs), 4) if diffs else None,
        "diff_ci95": bootstrap_ci(diffs),
        "p_value": paired_permutation_p(diffs),
    }


def summarize(source: str | Path | Iterable[dict], group_by: str = "condition",
              baselines: list[str] | None = None, metrics: list[str] | None = None) -> dict[str, Any]:
    rows = load_rows(source)
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[str(r.get(group_by, "all"))].append(r)
    conditions = {g: describe(rs) for g, rs in sorted(groups.items())}

    comparisons = []
    names = sorted(groups)
    if baselines is None:
        baselines = [n for n in names if n.startswith(("A_", "B_"))] or names[:1]
    for base in baselines:
        if base not in groups:
            continue
        for other in names:
            if other == base or other in baselines and names.index(other) < names.index(base):
                continue
            for m in metrics or COMPARE_METRICS:
                c = compare(groups[base], groups[other], m)
                if c["n_pairs"]:
                    comparisons.append({"a": base, "b": other, **c})
    for adj, c in zip(holm([c["p_value"] for c in comparisons]), comparisons):
        c["p_holm"] = adj
        c["significant_0_05"] = adj is not None and adj < 0.05
    return {"group_by": group_by, "n_rows": len(rows), "conditions": conditions, "comparisons": comparisons}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--group-by", default="condition")
    a = ap.parse_args()
    print(json.dumps(summarize(a.path, a.group_by), indent=2))
