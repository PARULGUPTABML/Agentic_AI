"""Results report: comparison tables, charts and the HTML dashboard.

    build_report(runs_path, out_dir) ->
        out_dir/report.md            all comparison tables + significance + cost/time
        out_dir/tables/*.csv         one CSV per table
        out_dir/charts/*.png         accuracy / hallucination / latency / cost / messaging charts
        out_dir/dashboard.html       the team dashboard (dashboard.build_dashboard) over the same runs
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from readmind.evaluate import factor_tables, load_rows, significance, to_markdown

PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]


def _bar(ax, labels, values, title, xlabel, fmt="{:.2f}"):
    vals = [v if isinstance(v, (int, float)) else 0 for v in values]
    ax.barh(labels, vals, color=PALETTE[0], height=0.55)
    for i, v in enumerate(values):
        ax.text((vals[i] or 0) + max(vals + [1e-9]) * 0.01, i, "–" if v is None else fmt.format(v), va="center",
                fontsize=8, color="#333")
    top = max(vals) if vals else 0
    ax.set_xlim(0, top * 1.22 if top > 0 else 1.0)
    ax.set_title(title, fontsize=10, loc="left")
    ax.set_xlabel(xlabel, fontsize=8)
    ax.tick_params(labelsize=8)
    ax.invert_yaxis()
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.grid(axis="x", color="#e5e5e5", linewidth=0.6)
    ax.set_axisbelow(True)


def charts(tables: dict, out: Path) -> list[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    specs = [("by_pattern", "architecture", "Agentic design pattern"), ("by_messaging", "protocol", "Messaging architecture"),
             ("by_prompt_variant", "variant", "Prompt variant")]
    for key, col, label in specs:
        t = tables.get(key) or []
        if not t:
            continue
        labels = [r[col] for r in t]
        fig, axes = plt.subplots(1, 4, figsize=(15, 0.55 * len(t) + 1.6))
        _bar(axes[0], labels, [r["accuracy"] for r in t], f"Accuracy — {label}", "judge accuracy (0–1)")
        _bar(axes[1], labels, [r["hallucination"] for r in t], "Hallucination rate", "share of answers")
        _bar(axes[2], labels, [r["latency_p50_s"] for r in t], "Median latency", "seconds", "{:.1f}")
        _bar(axes[3], labels, [r["cost_per_run_usd"] for r in t], "Cost per run", "USD", "{:.4f}")
        for ax in axes[1:]:
            ax.set_yticklabels([])
        fig.tight_layout()
        p = out / f"{key}.png"
        fig.savefig(p, dpi=130)
        plt.close(fig)
        paths.append(p)
    t = tables.get("by_condition") or []
    if t:
        fig, ax = plt.subplots(figsize=(7, 4.5))
        xs = [r["latency_p50_s"] for r in t]
        ys = [r["accuracy"] or 0 for r in t]
        ax.scatter(xs, ys, s=40, color=PALETTE[0], edgecolor="white", linewidth=1.5, zorder=3)
        for r, x, y in zip(t, xs, ys):
            ax.annotate(r["condition"].replace("|default", "").replace("|blackboard", ""), (x, y), fontsize=7,
                        xytext=(4, 3), textcoords="offset points")
        ax.set_xlabel("median latency (s)", fontsize=9)
        ax.set_ylabel("accuracy", fontsize=9)
        ax.set_title("Accuracy vs latency per condition (up-left is better)", fontsize=10, loc="left")
        ax.grid(color="#e5e5e5", linewidth=0.6)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        fig.tight_layout()
        p = out / "accuracy_vs_latency.png"
        fig.savefig(p, dpi=130)
        plt.close(fig)
        paths.append(p)
    m = tables.get("by_messaging") or []
    if m:
        fig, axes = plt.subplots(1, 2, figsize=(10, 2.6))
        _bar(axes[0], [r["protocol"] for r in m], [r["message_kb"] for r in m], "Message volume per run", "KB", "{:.1f}")
        _bar(axes[1], [r["protocol"] for r in m], [r["recall_at_k"] for r in m], "Evidence recall@8", "recall")
        axes[1].set_yticklabels([])
        fig.tight_layout()
        p = out / "messaging_cost.png"
        fig.savefig(p, dpi=130)
        plt.close(fig)
        paths.append(p)
    return paths


def build_report(runs_path: str | Path, out_dir: str | Path, estimate: dict | None = None,
                 security: dict | None = None) -> dict:
    runs_path, out = Path(runs_path), Path(out_dir)
    rows = load_rows(runs_path)
    out.mkdir(parents=True, exist_ok=True)
    tables = factor_tables(rows)
    (out / "tables").mkdir(exist_ok=True)
    for name, t in tables.items():
        if t:
            with open(out / "tables" / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(t[0]))
                w.writeheader()
                w.writerows(t)
    base = "sequential|blackboard|default"
    sig = significance(rows, base) if rows else []
    chart_paths = charts(tables, out / "charts") if rows else []
    totals = {
        "runs": len(rows), "questions": len({r["question_id"] for r in rows}), "conditions": len({r["condition"] for r in rows}),
        "llm_calls": sum(r["llm_calls"] for r in rows), "tokens": sum(r["total_tokens"] for r in rows),
        "cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
        "judge_cost_usd": round(sum(r.get("judge_cost_usd", 0) for r in rows), 4),
        "wall_minutes": round(sum(r.get("wall_ms", r["total_latency_ms"]) for r in rows) / 60000, 2),
        "models": sorted({r["llm"] for r in rows}),
        "judge_methods": sorted({(r.get("judge") or {}).get("judge_method", "?") for r in rows}),
        "tokens_estimated": any(r.get("tokens_estimated") for r in rows),
    }
    mock = "mock" in totals["models"]
    md = ["# ReadMind — evaluation report\n",
          f"{totals['runs']} runs · {totals['questions']} questions · {totals['conditions']} conditions · models "
          f"{', '.join(totals['models'])} · judge {', '.join(totals['judge_methods'])}\n"]
    if mock:
        md.append("> **Offline (mock) run.** These numbers validate the pipeline end to end. The answering model is a "
                  "deterministic extractive mock and the judge is heuristic, so accuracy/hallucination here are NOT "
                  "findings — rerun with a Gemini key for reportable results.\n")
    md.append(f"**Totals:** {totals['llm_calls']} LLM calls, {totals['tokens']:,} tokens"
              f"{' (estimated)' if totals['tokens_estimated'] else ''}, agent cost ${totals['cost_usd']}, "
              f"judge cost ${totals['judge_cost_usd']}, {totals['wall_minutes']} min of run time.\n")
    if estimate:
        md.append("## Pre-run estimate\n")
        md.append(f"{estimate['total_runs']} runs, ~{estimate['total_llm_calls']} LLM calls, "
                  f"~${estimate['total_cost_usd']}, ~{estimate['total_minutes']} min "
                  f"(assumptions: {json.dumps(estimate['assumptions'])})\n")
    titles = {"by_pattern": "Agentic design patterns (messaging = blackboard, default prompt)",
              "by_messaging": "Messaging architectures (sequential pattern, default prompt)",
              "by_prompt_variant": "Prompt variants (sequential pattern, blackboard)",
              "by_condition": "All conditions"}
    for k, t in tables.items():
        md.append(f"\n## {titles[k]}\n")
        md.append(to_markdown(t))
    if sig:
        md.append(f"\n## Paired significance vs `{base}` (accuracy; permutation test, Holm-corrected)\n")
        md.append(to_markdown([{k: v for k, v in s.items() if k in ("b", "n_pairs", "mean_a", "mean_b", "mean_diff",
                                                                     "p_value", "p_holm")} for s in sig]))
    if security:
        md.append("\n## Guardrail red-team results\n")
        md.append(f"- input: attack block rate {security['input']['attack_block_rate']}, benign false-positive rate "
                  f"{security['input']['benign_false_positive_rate']}, secret redaction {security['input']['secret_redaction_rate']}")
        md.append(f"- tools: attack block rate {security['tool']['tool_attack_block_rate']}")
        md.append(f"- output: {', '.join(security['output']['categories'])}")
        md.append("- caveat: the attack set is self-authored and the input filter is pattern-based; paraphrased or "
                  "obfuscated attacks can evade it.")
    (out / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    dash = None
    try:
        from dashboard.build_dashboard import main as build_dash
        build_dash([str(runs_path), "--out", str(out / "dashboard.html"), "--tables-dir", str(out / "dashboard_tables")])
        dash = out / "dashboard.html"
    except Exception as e:
        md.append(f"\n(dashboard build failed: {e})")
    summary = {"totals": totals, "tables": tables, "significance": sig, "charts": [str(p) for p in chart_paths],
               "dashboard": str(dash) if dash else None, "report": str(out / "report.md")}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary
