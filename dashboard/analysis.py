"""Turn the run log into the comparison tables the team reports on.

    python -m dashboard.analysis results/runs.jsonl --out results/analysis

Writes ``tables.md`` (all tables, Markdown), one CSV per table, and
``analysis.json`` (everything, machine-readable).

Tables
------
    configurations       A vs B vs C (each architecture) — accuracy, hallucination, evidence precision, latency, cost
    architectures        C only — adds coordination overhead, rounds, retrieval iterations
    llms                 per answering-model label (when several LLMs were run)
    protocols            P runs — Blackboard vs P2P vs Broadcast vs MCP
    question_types       accuracy per question type x condition
    agent_latency        mean per-agent latency per condition + which agent dominates
    agent_llm_calls      mean LLM calls per agent per condition
    tool_usage           tool calls per agent/tool (count, mean results, mean ms)
    errors               error / retry counts per condition and agent
    significance         paired tests vs baselines (Holm-corrected)
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from evaluation.stats import describe, load_rows, summarize

MAIN_COLS = [
    ("n", "runs"), ("accuracy", "accuracy"), ("accuracy_ci95", "accuracy 95% CI"),
    ("completeness", "completeness"), ("hallucination_rate", "hallucination"),
    ("evidence_precision", "evidence precision"), ("recall_at_k", "recall@k"),
    ("citation_precision", "citation precision"), ("judge_score", "judge score /10"),
    ("latency_p50_ms", "p50 ms"), ("latency_p95_ms", "p95 ms"),
    ("llm_calls", "LLM calls"), ("total_tokens", "tokens"),
]
ARCH_EXTRA = [("coordination_overhead_ms", "coord. overhead ms"), ("rounds", "rounds"),
              ("retrieval_iterations", "retrieval calls"), ("error_rate", "error rate")]


def _fmt(v: Any) -> str:
    if v is None:
        return "–"
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return f"[{_fmt(v[0])}, {_fmt(v[1])}]"
    if isinstance(v, float):
        return f"{v:.3f}" if abs(v) < 10 else f"{v:,.0f}"
    return str(v)


def _table(rows: list[dict], cols: list[tuple[str, str]], key: str, key_label: str) -> dict:
    return {"columns": [key_label] + [c[1] for c in cols],
            "rows": [[r[key]] + [r.get(c[0]) for c in cols] for r in rows]}


def _group(rows: list[dict], field: str, where=lambda r: True) -> dict[str, list[dict]]:
    g: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if where(r):
            g[str(r.get(field) or "–")].append(r)
    return dict(sorted(g.items()))


def analyse(runs_path: str | Path) -> dict[str, Any]:
    raw = [json.loads(l) for l in Path(runs_path).read_text(encoding="utf-8").splitlines() if l.strip()]
    flat = load_rows(raw)
    tables: dict[str, dict] = {}

    # 1. configurations
    conf_rows = [{"condition": c, **describe(rs)} for c, rs in _group(flat, "condition").items()]
    tables["configurations"] = _table(conf_rows, MAIN_COLS, "condition", "condition")

    # 2. architectures (C only)
    arch_rows = [{"architecture": a, **describe(rs)}
                 for a, rs in _group(flat, "architecture", lambda r: r.get("config") == "C").items()]
    if arch_rows:
        tables["architectures"] = _table(arch_rows, MAIN_COLS + ARCH_EXTRA, "architecture", "architecture")

    # 3. LLMs
    llm_groups = _group(flat, "llm")
    if len(llm_groups) > 1:
        llm_rows = []
        for llm, rs in llm_groups.items():
            for cond, rs2 in _group(rs, "condition").items():
                llm_rows.append({"llm/condition": f"{llm} / {cond}", **describe(rs2)})
        tables["llms"] = _table(llm_rows, MAIN_COLS, "llm/condition", "LLM / condition")

    # 4. protocols
    proto_rows = [{"protocol": p, **describe(rs)}
                  for p, rs in _group(flat, "protocol", lambda r: r.get("config") == "P").items()]
    if proto_rows:
        tables["protocols"] = _table(proto_rows, MAIN_COLS, "protocol", "protocol")

    # 5. question type x condition accuracy
    conds = sorted({r["condition"] for r in flat})
    qt_rows = []
    for qt, rs in _group(flat, "question_type").items():
        by = _group(rs, "condition")
        qt_rows.append([qt] + [describe(by[c]).get("accuracy") if c in by else None for c in conds])
    tables["question_types"] = {"columns": ["question type"] + conds, "rows": qt_rows}

    # 6/7. per-agent latency and LLM calls
    agent_lat: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    agent_llm: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    agents_seen: set[str] = set()
    for r in raw:
        for a, ms in (r.get("per_agent_latency_ms") or {}).items():
            agent_lat[r["condition"]][a].append(float(ms))
            agents_seen.add(a)
        for a, n in (r.get("llm_calls_by_agent") or {}).items():
            agent_llm[r["condition"]][a].append(float(n))
    agents_order = [a for a in ["orchestrator", "code_analysis", "history_rag", "issue_pr", "verification", "reviewer"]
                    if a in agents_seen] + sorted(agents_seen - {"orchestrator", "code_analysis", "history_rag",
                                                                  "issue_pr", "verification", "reviewer"})
    lat_rows, dominant = [], {}
    for c in conds:
        means = {a: (statistics.fmean(agent_lat[c][a]) if agent_lat[c].get(a) else None) for a in agents_order}
        tot = sum(v for v in means.values() if v) or 0.0
        dom = max((a for a in means if means[a]), key=lambda a: means[a], default=None)
        dominant[c] = {"agent": dom, "share": round(means[dom] / tot, 3) if dom and tot else None}
        lat_rows.append([c] + [means[a] for a in agents_order] + [dom, dominant[c]["share"]])
    tables["agent_latency"] = {"columns": ["condition"] + [f"{a} ms" for a in agents_order] + ["dominant agent", "share"],
                               "rows": lat_rows}
    llm_agents = sorted({a for c in agent_llm for a in agent_llm[c]})
    if llm_agents:
        tables["agent_llm_calls"] = {
            "columns": ["condition"] + llm_agents,
            "rows": [[c] + [statistics.fmean(agent_llm[c][a]) if agent_llm[c].get(a) else None for a in llm_agents]
                     for c in conds]}

    # 8. tool usage
    tool_stats: dict[tuple, dict[str, list]] = defaultdict(lambda: {"results": [], "ms": [], "errors": 0})
    for r in raw:
        for t in r.get("tools_used", []):
            k = (t.get("agent"), t.get("tool"))
            tool_stats[k]["results"].append(float(t.get("results") or 0))
            tool_stats[k]["ms"].append(float(t.get("ms") or 0))
            tool_stats[k]["errors"] += 1 if t.get("error") else 0
    tables["tool_usage"] = {
        "columns": ["agent", "tool", "calls", "mean results", "mean ms", "errors"],
        "rows": [[a, t, len(v["ms"]), statistics.fmean(v["results"]), statistics.fmean(v["ms"]), v["errors"]]
                 for (a, t), v in sorted(tool_stats.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1])))]}

    # 9. errors
    err_counter: Counter = Counter()
    for r in raw:
        for e in r.get("errors", []):
            err_counter[(r["condition"], e.get("agent", "?"))] += 1
    status = {c: Counter(r.get("status", "ok") for r in raw if r["condition"] == c) for c in conds}
    tables["errors"] = {
        "columns": ["condition", "ok", "degraded", "failed", "mean retries", "errors by agent"],
        "rows": [[c, status[c].get("ok", 0), status[c].get("degraded", 0), status[c].get("failed", 0),
                  statistics.fmean([float(r.get("retries", 0)) for r in raw if r["condition"] == c]),
                  ", ".join(f"{a}:{n}" for (cc, a), n in err_counter.items() if cc == c) or "–"] for c in conds]}

    # 10. significance
    summ = summarize(raw)
    tables["significance"] = {
        "columns": ["A (baseline)", "B (compared)", "metric", "pairs", "mean A", "mean B", "diff (B−A)",
                    "diff 95% CI", "p", "p (Holm)", "sig."],
        "rows": [[c["a"], c["b"], c["metric"], c["n_pairs"], c["mean_a"], c["mean_b"], c["mean_diff"],
                  c["diff_ci95"], c["p_value"], c["p_holm"], "yes" if c["significant_0_05"] else "no"]
                 for c in summ["comparisons"]]}

    meta = {
        "runs": len(raw),
        "questions": len({r.get("question_id") for r in raw}),
        "conditions": conds,
        "llms": sorted({r.get("llm", "") for r in raw}),
        "judge_methods": sorted({(r.get("judge") or {}).get("judge_method", "none") for r in raw}),
        "mock": any(r.get("llm") == "mock" for r in raw)
                or any((r.get("judge") or {}).get("judge_method", "").startswith("heuristic") for r in raw),
        "dominant_agent": dominant,
        "agents": agents_order,
    }
    return {"meta": meta, "tables": tables, "summary": summ, "condition_metrics": {r["condition"]: r for r in conf_rows},
            "agent_latency_means": {c: {a: (statistics.fmean(agent_lat[c][a]) if agent_lat[c].get(a) else 0.0)
                                        for a in agents_order} for c in conds}}


def to_markdown(analysis: dict) -> str:
    m = analysis["meta"]
    out = [f"# Experiment results\n",
           f"{m['runs']} runs · {m['questions']} questions · conditions: {', '.join(m['conditions'])} · "
           f"LLMs: {', '.join(m['llms'])} · judge: {', '.join(m['judge_methods'])}\n"]
    if m["mock"]:
        out.append("> **Note:** these results include mock-LLM and/or heuristic-judge runs. "
                   "They validate the pipeline but must not be reported as findings.\n")
    for name, t in analysis["tables"].items():
        out.append(f"\n## {name.replace('_', ' ').title()}\n")
        out.append("| " + " | ".join(t["columns"]) + " |")
        out.append("|" + "---|" * len(t["columns"]))
        for row in t["rows"]:
            out.append("| " + " | ".join(_fmt(v) for v in row) + " |")
    return "\n".join(out) + "\n"


def write_outputs(analysis: dict, out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "tables.md").write_text(to_markdown(analysis), encoding="utf-8")
    (out / "analysis.json").write_text(json.dumps(analysis, indent=2, default=str), encoding="utf-8")
    for name, t in analysis["tables"].items():
        with open(out / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(t["columns"])
            for row in t["rows"]:
                w.writerow([("" if v is None else (f"{v[0]}..{v[1]}" if isinstance(v, (list, tuple)) else v))
                            for v in row])
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="?", default="results/runs.jsonl")
    ap.add_argument("--out", default="results/analysis")
    a = ap.parse_args(argv)
    res = analyse(a.runs)
    out = write_outputs(res, a.out)
    print(f"Wrote {len(res['tables'])} tables to {out}")


if __name__ == "__main__":
    main()
