"""Build a self-contained HTML results dashboard (inline SVG, no CDN, works offline).

    python -m dashboard.build_dashboard results/runs.jsonl --out results/dashboard.html

Charts
------
    1. Accuracy by condition (with 95% bootstrap CI whiskers)
    2. Hallucination rate by condition
    3. Accuracy vs. median latency (the latency-accuracy trade-off)
    4. Per-agent latency breakdown (which agent dominates end-to-end latency)
    5. Accuracy heat-map: question type x condition
followed by every analysis table (the accessible table view of each chart).

Colours follow a validated reference palette (categorical order fixed: blue, orange,
aqua, yellow, magenta, green, …; single-hue blue ramp for magnitude), with separate
light/dark values; every mark has a hover tooltip (SVG <title>).
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

from dashboard.analysis import _fmt, analyse, write_outputs

CATEGORICAL_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
CATEGORICAL_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
BLUE_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
W = 540


def esc(s: Any) -> str:
    return html.escape(str(s), quote=True)


# ---------------------------------------------------------------------- #
#  Chart primitives                                                       #
# ---------------------------------------------------------------------- #
def hbar_chart(items: list[tuple[str, float | None, tuple | None]], *, xmax: float, fmt=lambda v: f"{v:.2f}",
               title: str, unit: str = "") -> str:
    """Horizontal single-series bars; optional (lo, hi) whisker."""
    label_w, row_h, bar_h, pad_r = 128, 30, 14, 52
    h = 24 + row_h * len(items) + 24
    plot_w = W - label_w - pad_r
    xmax = xmax or 1.0
    x = lambda v: label_w + plot_w * max(0.0, min(1.0, v / xmax))
    parts = [f'<svg viewBox="0 0 {W} {h}" role="img" aria-label="{esc(title)}" class="chart">']
    for i in range(5):                                   # recessive grid
        gx = label_w + plot_w * i / 4
        parts.append(f'<line x1="{gx:.1f}" y1="16" x2="{gx:.1f}" y2="{h - 22}" class="grid"/>'
                     f'<text x="{gx:.1f}" y="{h - 6}" class="tick" text-anchor="middle">{esc(fmt(xmax * i / 4))}{esc(unit)}</text>')
    for i, (name, v, ci) in enumerate(items):
        y = 24 + i * row_h
        parts.append(f'<text x="{label_w - 8}" y="{y + bar_h / 2 + 4}" class="label" text-anchor="end">{esc(name)}</text>')
        if v is None:
            parts.append(f'<text x="{label_w + 4}" y="{y + bar_h / 2 + 4}" class="tick">no data</text>')
            continue
        bw = max(2.0, x(v) - label_w)
        tip = f"{name}: {fmt(v)}{unit}" + (f" (95% CI {fmt(ci[0])}–{fmt(ci[1])})" if ci else "")
        # bar anchored at baseline: square left edge, 4px rounded data end
        parts.append(f'<g class="mark"><title>{esc(tip)}</title>'
                     f'<path d="M{label_w},{y} h{max(0, bw - 4):.1f} q4,0 4,4 v{bar_h - 8} q0,4 -4,4 h-{max(0, bw - 4):.1f} z" class="s1"/>'
                     f'<rect x="{label_w}" y="{y - 6}" width="{plot_w}" height="{row_h}" class="hit"/></g>')
        if ci:
            lo, hi = x(ci[0]), x(ci[1])
            cy = y + bar_h / 2
            parts.append(f'<path d="M{lo:.1f},{cy - 5} v10 M{lo:.1f},{cy} H{hi:.1f} M{hi:.1f},{cy - 5} v10" class="whisker"/>')
        parts.append(f'<text x="{max(x(v), x(ci[1]) if ci else 0) + 6:.1f}" y="{y + bar_h / 2 + 4}" class="value">{esc(fmt(v))}{esc(unit)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def scatter_chart(points: list[tuple[str, float, float]], title: str) -> str:
    """x = p50 latency (ms), y = accuracy."""
    h, l, r, t, b = 300, 56, 24, 16, 40
    if not points:
        return "<p class='muted'>No data.</p>"
    xmax = max(p[1] for p in points) * 1.15 or 1.0
    x = lambda v: l + (W - l - r) * v / xmax
    y = lambda v: t + (h - t - b) * (1 - v)
    parts = [f'<svg viewBox="0 0 {W} {h}" role="img" aria-label="{esc(title)}" class="chart">']
    for i in range(5):
        gy = y(i / 4)
        parts.append(f'<line x1="{l}" y1="{gy:.1f}" x2="{W - r}" y2="{gy:.1f}" class="grid"/>'
                     f'<text x="{l - 8}" y="{gy + 4:.1f}" class="tick" text-anchor="end">{i / 4:.2f}</text>')
        gx = l + (W - l - r) * i / 4
        parts.append(f'<text x="{gx:.1f}" y="{h - 20}" class="tick" text-anchor="middle">{_fmt(xmax * i / 4)}</text>')
    parts.append(f'<text x="{(W + l) / 2}" y="{h - 4}" class="tick" text-anchor="middle">median latency (ms)</text>'
                 f'<text x="14" y="{(h - b) / 2}" class="tick" transform="rotate(-90 14 {(h - b) / 2})" text-anchor="middle">accuracy</text>')
    placed: list[tuple[float, float]] = []
    for name, lat, acc in points:
        px, py = x(lat), y(acc)
        dy = -10
        while any(abs(px - qx) < 90 and abs(py + dy - qy) < 12 for qx, qy in placed):
            dy -= 13
        placed.append((px, py + dy))
        parts.append(f'<g class="mark"><title>{esc(name)}: accuracy {acc:.3f}, p50 {lat:,.0f} ms</title>'
                     f'<circle cx="{px:.1f}" cy="{py:.1f}" r="5" class="s1 ring"/>'
                     f'<circle cx="{px:.1f}" cy="{py:.1f}" r="14" class="hit"/></g>'
                     f'<text x="{px + 8:.1f}" y="{py + dy:.1f}" class="label">{esc(name)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def stacked_chart(conds: list[str], agents: list[str], means: dict[str, dict[str, float]], title: str) -> str:
    label_w, row_h, bar_h, pad_r = 128, 30, 14, 64
    h = 24 + row_h * len(conds) + 24
    totals = {c: sum(means[c].get(a, 0.0) for a in agents) for c in conds}
    xmax = max(totals.values() or [1.0]) or 1.0
    plot_w = W - label_w - pad_r
    legend = "".join(f'<span class="key"><i class="sw s{i + 1}"></i>{esc(a)}</span>' for i, a in enumerate(agents))
    parts = [f'<div class="legend">{legend}</div><svg viewBox="0 0 {W} {h}" role="img" aria-label="{esc(title)}" class="chart">']
    for i in range(5):
        gx = label_w + plot_w * i / 4
        parts.append(f'<line x1="{gx:.1f}" y1="16" x2="{gx:.1f}" y2="{h - 22}" class="grid"/>'
                     f'<text x="{gx:.1f}" y="{h - 6}" class="tick" text-anchor="middle">{_fmt(xmax * i / 4)} ms</text>')
    for r_i, c in enumerate(conds):
        y = 24 + r_i * row_h
        parts.append(f'<text x="{label_w - 8}" y="{y + bar_h / 2 + 4}" class="label" text-anchor="end">{esc(c)}</text>')
        cx = float(label_w)
        for a_i, a in enumerate(agents):
            v = means[c].get(a, 0.0)
            if v <= 0:
                continue
            w = plot_w * v / xmax
            gap = 2 if w > 3 else 0                     # 2px surface gap between segments
            share = v / totals[c] if totals[c] else 0
            parts.append(f'<g class="mark"><title>{esc(c)} · {esc(a)}: {v:,.1f} ms ({share:.0%})</title>'
                         f'<rect x="{cx:.1f}" y="{y}" width="{max(1.0, w - gap):.1f}" height="{bar_h}" class="s{a_i + 1}"/>'
                         f'<rect x="{cx:.1f}" y="{y - 6}" width="{max(1.0, w):.1f}" height="{row_h}" class="hit"/></g>')
            cx += w
        parts.append(f'<text x="{cx + 6:.1f}" y="{y + bar_h / 2 + 4}" class="value">{_fmt(totals[c])} ms</text>')
    parts.append("</svg>")
    return "".join(parts)


def heatmap(table: dict, title: str) -> str:
    cols, rows = table["columns"][1:], table["rows"]
    if not rows:
        return "<p class='muted'>No data.</p>"
    label_w, cell_w, cell_h, head_h = 180, 96, 30, 70
    w = label_w + cell_w * len(cols) + 40
    h = head_h + cell_h * len(rows) + 8
    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{esc(title)}" class="chart" style="max-width:{w}px">']
    for j, c in enumerate(cols):
        cx = label_w + j * cell_w + cell_w / 2
        parts.append(f'<text x="{cx:.1f}" y="{head_h - 8}" class="tick" text-anchor="start" '
                     f'transform="rotate(-30 {cx:.1f} {head_h - 8})">{esc(c)}</text>')
    for i, row in enumerate(rows):
        y = head_h + i * cell_h
        parts.append(f'<text x="{label_w - 8}" y="{y + cell_h / 2 + 4}" class="label" text-anchor="end">{esc(row[0])}</text>')
        for j, v in enumerate(row[1:]):
            x0 = label_w + j * cell_w
            if v is None:
                parts.append(f'<rect x="{x0 + 1}" y="{y + 1}" width="{cell_w - 2}" height="{cell_h - 2}" rx="3" class="empty"/>')
                continue
            step = min(len(BLUE_RAMP) - 1, int(v * (len(BLUE_RAMP) - 1) + 0.5))
            ink = "#0b0b0b" if step < 3 else "#ffffff"
            parts.append(f'<g class="mark"><title>{esc(row[0])} · {esc(cols[j])}: accuracy {v:.3f}</title>'
                         f'<rect x="{x0 + 1}" y="{y + 1}" width="{cell_w - 2}" height="{cell_h - 2}" rx="3" fill="{BLUE_RAMP[step]}"/>'
                         f'<text x="{x0 + cell_w / 2}" y="{y + cell_h / 2 + 4}" text-anchor="middle" class="cell" style="fill:{ink}">{v:.2f}</text></g>')
    parts.append("</svg>")
    return "".join(parts)


def table_html(name: str, t: dict) -> str:
    head = "".join(f"<th>{esc(c)}</th>" for c in t["columns"])
    body = "".join("<tr>" + "".join(f"<td>{esc(_fmt(v))}</td>" for v in row) + "</tr>" for row in t["rows"])
    return (f'<details class="tbl"{" open" if name == "configurations" else ""}>'
            f'<summary>{esc(name.replace("_", " ").title())}</summary>'
            f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div></details>')


# ---------------------------------------------------------------------- #
def build_html(a: dict) -> str:
    meta, cm = a["meta"], a["condition_metrics"]
    conds = meta["conditions"]
    acc = [(c, cm[c].get("accuracy"), cm[c].get("accuracy_ci95")) for c in conds]
    hal = [(c, cm[c].get("hallucination_rate"), cm[c].get("hallucination_ci95")) for c in conds]
    pts = [(c, cm[c]["latency_p50_ms"], cm[c]["accuracy"]) for c in conds
           if cm[c].get("latency_p50_ms") is not None and cm[c].get("accuracy") is not None]
    best = max(conds, key=lambda c: cm[c].get("accuracy") or -1) if conds else "–"
    low_h = min(conds, key=lambda c: cm[c].get("hallucination_rate") if cm[c].get("hallucination_rate") is not None else 9) if conds else "–"
    agents = meta["agents"][:8]
    dom = meta["dominant_agent"]
    dom_note = ", ".join(f"{c}: <b>{esc(d['agent'])}</b> ({d['share']:.0%})" for c, d in dom.items() if d.get("agent") and d.get("share"))

    css_series = "\n".join(f".s{i + 1}{{fill:var(--series-{i + 1})}} .sw.s{i + 1}{{background:var(--series-{i + 1})}}"
                           for i in range(8))
    light_vars = "".join(f"--series-{i + 1}:{c};" for i, c in enumerate(CATEGORICAL_LIGHT))
    dark_vars = "".join(f"--series-{i + 1}:{c};" for i, c in enumerate(CATEGORICAL_DARK))
    warn = ('<div class="banner">These results include mock-LLM and/or heuristic-judge runs. They show the pipeline '
            'works end to end but are <b>not</b> reportable findings — rerun with the real backend and an LLM judge.</div>'
            if meta["mock"] else "")
    tables = "".join(table_html(n, t) for n, t in a["tables"].items())

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Experiment Results Dashboard</title>
<style>
:root{{--surface:#fcfcfb;--page:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;{light_vars}}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--surface:#1a1a19;--page:#0d0d0d;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;{dark_vars}}}}}
:root[data-theme="dark"]{{--surface:#1a1a19;--page:#0d0d0d;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;{dark_vars}}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--page);color:var(--ink);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1080px;margin:0 auto;padding:24px 16px 64px}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:15px;margin:0 0 4px}} .sub,.muted{{color:var(--ink2)}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:16px 0}}
.kpi,.card{{background:var(--surface);border:1px solid var(--grid);border-radius:10px;padding:14px 16px}}
.kpi b{{display:block;font-size:22px;font-variant-numeric:tabular-nums}} .kpi span{{color:var(--ink2);font-size:12px}}
.grid2{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,480px),1fr));gap:16px}}
.card p.note{{color:var(--ink2);font-size:12px;margin:4px 0 8px}}
.chart{{width:100%;height:auto;display:block}} .chart text{{font-size:12px;fill:var(--ink2)}}
.chart .tick{{fill:var(--muted);font-size:11px}} .chart .label{{fill:var(--ink)}} .chart .value{{fill:var(--ink);font-variant-numeric:tabular-nums}}
.chart .cell{{font-size:11px;font-variant-numeric:tabular-nums}}
.grid{{stroke:var(--grid);stroke-width:1}} .whisker{{stroke:var(--ink2);stroke-width:1.5;fill:none}}
.ring{{stroke:var(--surface);stroke-width:2}} .hit{{fill:transparent}} .empty{{fill:var(--grid)}}
.mark:hover rect:not(.hit),.mark:hover path,.mark:hover circle:not(.hit){{opacity:.8}}
{css_series}
.legend{{display:flex;flex-wrap:wrap;gap:12px;margin:4px 0 6px;font-size:12px;color:var(--ink2)}}
.sw{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}}
.banner{{background:var(--surface);border:1px solid var(--axis);border-left:4px solid #fab219;border-radius:8px;padding:10px 14px;margin:12px 0;color:var(--ink)}}
details.tbl{{background:var(--surface);border:1px solid var(--grid);border-radius:10px;margin:12px 0;padding:8px 14px}}
summary{{cursor:pointer;font-weight:600;padding:4px 0}} .scroll{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;font-size:12.5px;font-variant-numeric:tabular-nums}}
th,td{{padding:6px 8px;border-bottom:1px solid var(--grid);text-align:right;white-space:nowrap}}
th:first-child,td:first-child{{text-align:left}} th{{color:var(--ink2);font-weight:600}}
</style></head><body><main>
<h1>Repository-Aware Multi-Agent AI — Experiment Results</h1>
<div class="sub">{meta['runs']} runs · {meta['questions']} questions · LLMs: {esc(', '.join(meta['llms']))} · judge: {esc(', '.join(meta['judge_methods']))}</div>
{warn}
<div class="kpis">
 <div class="kpi"><span>Runs logged</span><b>{meta['runs']}</b></div>
 <div class="kpi"><span>Conditions</span><b>{len(conds)}</b></div>
 <div class="kpi"><span>Highest accuracy</span><b>{esc(_fmt(cm[best].get('accuracy')) if conds else '–')}</b><span>{esc(best)}</span></div>
 <div class="kpi"><span>Lowest hallucination</span><b>{esc(_fmt(cm[low_h].get('hallucination_rate')) if conds else '–')}</b><span>{esc(low_h)}</span></div>
</div>
<div class="grid2">
 <section class="card"><h2>Accuracy by condition</h2><p class="note">LLM-as-Judge accuracy (correct = 1, partial = 0.5). Whiskers: 95% bootstrap CI.</p>
 {hbar_chart(acc, xmax=1.0, title="Accuracy by condition")}</section>
 <section class="card"><h2>Hallucination rate by condition</h2><p class="note">Share of answers with a contradiction or a fabricated evidence ID. Lower is better.</p>
 {hbar_chart(hal, xmax=max([v or 0 for _, v, _ in hal] + [0.25]), title="Hallucination rate by condition")}</section>
 <section class="card"><h2>Accuracy vs. latency</h2><p class="note">Each point is a condition; up and to the left is better.</p>
 {scatter_chart(pts, "Accuracy vs median latency")}</section>
 <section class="card"><h2>Where the time goes</h2><p class="note">Mean per-agent latency per run. Dominant agent — {dom_note or 'n/a'}.</p>
 {stacked_chart(conds, agents, a['agent_latency_means'], "Per-agent latency")}</section>
</div>
<section class="card" style="margin-top:16px"><h2>Accuracy by question type</h2><p class="note">Darker = higher accuracy.</p>
{heatmap(a['tables']['question_types'], "Accuracy by question type and condition")}</section>
<h2 style="margin-top:28px">Tables</h2>
{tables}
</main></body></html>"""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="?", default="results/runs.jsonl")
    ap.add_argument("--out", default=None, help="HTML path (default: <runs dir>/dashboard.html)")
    ap.add_argument("--tables-dir", default=None, help="also write CSV/Markdown tables (default: <runs dir>/analysis)")
    a = ap.parse_args(argv)
    runs = Path(a.runs)
    analysis = analyse(runs)
    write_outputs(analysis, a.tables_dir or runs.parent / "analysis")
    out = Path(a.out or runs.parent / "dashboard.html")
    out.write_text(build_html(analysis), encoding="utf-8")
    print(f"Dashboard: {out}")


if __name__ == "__main__":
    main()
