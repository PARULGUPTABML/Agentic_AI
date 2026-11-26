"""End-to-end evaluation: experiment matrix, LLM-as-Judge, cost and running time (requirement 10).

Conditions are ``pattern|messaging|variant``. The default plan compares three factors on the
same questions:

    patterns   single_react, sequential, parallel, reflection, hierarchical   (messaging=blackboard)
    messaging  blackboard vs p2p vs broadcast                                 (pattern=sequential)
    prompts    default, react, few_shot, persona_maintainer, persona_onboarding (pattern=sequential)

Every run is graded by the team's LLM-as-Judge (``evaluation.judge.Judge``: Gemini when a key is
set, deterministic heuristic otherwise), scored for retrieval and citations, and written as one
JSON line compatible with ``dashboard.build_dashboard``.
"""
from __future__ import annotations

import json
import math
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from readmind.prompts import VARIANTS
from readmind.settings import ROOT, get_settings

PATTERNS = ["single_react", "sequential", "parallel", "reflection", "hierarchical"]


@dataclass(frozen=True)
class Condition:
    pattern: str
    messaging: str = "blackboard"
    variant: str = "default"

    @property
    def label(self) -> str:
        return f"{self.pattern}|{self.messaging}|{self.variant}"

    @property
    def factor(self) -> str:
        if self.variant != "default":
            return "prompt"
        if self.messaging != "blackboard":
            return "messaging"
        return "pattern"


def default_plan(patterns: Iterable[str] = PATTERNS, messaging: Iterable[str] = ("p2p", "broadcast"),
                 variants: Iterable[str] = ("react", "few_shot", "persona_maintainer", "persona_onboarding"),
                 messaging_pattern: str = "sequential", variant_pattern: str = "sequential") -> list[Condition]:
    plan = [Condition(p) for p in patterns]
    plan += [Condition(messaging_pattern, m) for m in messaging]
    plan += [Condition(variant_pattern, "blackboard", v) for v in variants]
    seen, out = set(), []
    for c in plan:
        if c.label not in seen:
            seen.add(c.label)
            out.append(c)
    return out


def load_questions(path: Path | None = None, split: str = "dev", limit: int = 0, qids: list[str] | None = None) -> list[dict]:
    path = path or ROOT / "eval_set" / "questions.jsonl"
    items = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    if qids:
        items = [i for i in items if i["qid"] in qids]
    elif split != "all":
        items = [i for i in items if i.get("split") == split]
    return items[:limit] if limit else items


# --------------------------------------------------------------------------- #
#  Judge                                                                       #
# --------------------------------------------------------------------------- #
class JudgeUsage:
    calls = 0
    prompt = 0
    completion = 0


def make_judge(n_votes: int = 1):
    """Team Judge with a Gemini backend (rate-limited, usage-tracked) or the heuristic fallback."""
    from evaluation.judge import Judge
    s = get_settings()
    if s.is_mock:
        return Judge(llm_fn=None)
    from google import genai
    from google.genai import types
    from readmind.llm import _retry_options, limiter
    client = genai.Client(api_key=s.api_key, http_options=types.HttpOptions(retry_options=_retry_options()))

    def fn(system: str, prompt: str) -> str:
        limiter().acquire()
        r = client.models.generate_content(model=s.judge_model, contents=prompt, config=types.GenerateContentConfig(
            system_instruction=system, temperature=0.0, response_mime_type="application/json"))
        um = getattr(r, "usage_metadata", None)
        JudgeUsage.calls += 1
        JudgeUsage.prompt += getattr(um, "prompt_token_count", 0) or 0
        JudgeUsage.completion += getattr(um, "candidates_token_count", 0) or 0
        return r.text or ""
    return Judge(llm_fn=fn, n_votes=n_votes)


# --------------------------------------------------------------------------- #
#  One run -> row                                                              #
# --------------------------------------------------------------------------- #
def _gold_texts(item: dict) -> list[str]:
    from readmind.tools.repo_tools import _store
    st = _store()
    out = []
    for g in item.get("ground_truth_evidence", []):
        rid = st.resolve_id(g)
        if not rid and g.startswith("commit:"):
            rid = next((d for d in st.docs if d.startswith(g.lower())), None)
        if rid and rid in st.docs:
            out.append(st.docs[rid].get("text", "")[:1500])
    return out


def run_row(rm, item: dict, cond: Condition, judge, repeat: int = 0, k: int = 8) -> dict:
    from evaluation.metrics import citation_metrics, retrieval_metrics
    t0 = time.perf_counter()
    res = rm.ask(item["question"], pattern=cond.pattern, messaging=cond.messaging, variant=cond.variant,
                 meta={"qid": item["qid"], "condition": cond.label, "repeat": repeat})
    wall = (time.perf_counter() - t0) * 1000
    trace = json.loads(Path(res.trace_file).read_text())
    prov = sorted(trace["provenance"], key=lambda p: p["t_ms"])
    retrieved = [p["evidence_id"] for p in prov]
    gold = item.get("ground_truth_evidence", [])
    answer_md = res.final.to_markdown()
    ev_texts = [res.final.answer] + [e.snippet for e in res.final.evidence]
    jstart = (JudgeUsage.calls, JudgeUsage.prompt, JudgeUsage.completion)
    try:
        judged = judge.grade(item, answer_md, gold_texts=_gold_texts(item), retrieved=retrieved, evidence_texts=ev_texts)
    except Exception as e:   # judge failure must not lose the run
        judged = {"judge_method": "error", "judge_rationale": str(e)[:200]}
    m = res.metrics
    jc = JudgeUsage.calls - jstart[0]
    jp, jcmp = JudgeUsage.prompt - jstart[1], JudgeUsage.completion - jstart[2]
    s = get_settings()
    row = {
        "run_id": res.run_id, "question_id": item["qid"], "question": item["question"],
        "split": item.get("split", ""), "question_type": item.get("question_type", ""), "repeat": repeat,
        "condition": cond.label, "factor": cond.factor, "config": "R", "architecture": cond.pattern,
        "protocol": res.messaging, "variant": cond.variant, "variant_dimension": VARIANTS[cond.variant]["dimension"],
        "llm": res.model, "status": res.status,
        "final_answer": answer_md, "final": res.final.model_dump(),
        "judge": judged, "retrieval": retrieval_metrics(retrieved, gold, k),
        "citations": citation_metrics(answer_md, retrieved, gold, item.get("reference_answer", ""), ev_texts),
        "total_latency_ms": m.latency_ms, "wall_ms": round(wall, 1), "coordination_overhead_ms": 0.0,
        "llm_calls": m.llm_calls, "llm_calls_by_framework": m.llm_calls_by_framework,
        "prompt_tokens": m.prompt_tokens, "completion_tokens": m.completion_tokens, "total_tokens": m.total_tokens,
        "tokens_estimated": m.tokens_estimated, "cost_usd": m.cost_usd,
        "judge_calls": jc, "judge_cost_usd": round(jp / 1e6 * s.price_in_per_m + jcmp / 1e6 * s.price_out_per_m, 6),
        "tool_calls": m.tool_calls, "tool_errors": m.tool_errors, "messages": m.messages,
        "message_bytes": m.message_bytes, "a2a_calls": m.a2a_calls, "guardrail_blocks": m.guardrail_blocks,
        "retries": m.retries, "rounds": 1 + trace["metrics"].get("retries", 0),
        "per_agent_latency_ms": m.per_agent_latency_ms,
        "agents_invoked": res.agents_invoked,
        "retrieved_evidence": [{"id": p["evidence_id"], "type": p["type"], "source_agent": p["agent"]} for p in prov],
        "tools_used": [{"agent": sp["agent"], "tool": sp["name"].replace("tool:", ""), "ms": sp.get("duration_ms") or 0,
                        "results": len(sp["attributes"].get("evidence_ids", []))}
                       for sp in trace["spans"] if sp["kind"] == "tool"],
        "errors": [{"agent": e.split(":", 1)[0], "error": e[:300]} for e in res.errors],
        "output_guard": res.output_guard.categories if res.output_guard else [],
        "trace_file": res.trace_file,
    }
    return row


def run_matrix(rm, items: list[dict], plan: list[Condition], out_path: Path, repeats: int = 1, judge=None,
               progress: bool = True, max_runs: int = 0) -> list[dict]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out_path.exists():
        for l in out_path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(l)
                done.add((r["question_id"], r["condition"], r.get("repeat", 0), r.get("llm")))
            except Exception:
                continue
    judge = judge or make_judge()
    model = "mock" if get_settings().is_mock else get_settings().model
    total = len(items) * len(plan) * repeats
    n = new = 0
    t_start = time.time()
    for rep in range(repeats):
        for cond in plan:
            for item in items:
                n += 1
                if (item["qid"], cond.label, rep, model) in done:
                    continue
                if max_runs and new >= max_runs:
                    break
                row = run_row(rm, item, cond, judge, rep)
                with out_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                new += 1
                if progress:
                    j = row["judge"]
                    el = time.time() - t_start
                    eta = el / new * (total - n) if new else 0
                    print(f"[{n:>3}/{total}] {item['qid']} {cond.label:<42} {row['status']:<8} "
                          f"acc={j.get('accuracy', '-')} hall={j.get('hallucination', '-')} "
                          f"calls={row['llm_calls']:>2} ${row['cost_usd']:.4f} {row['total_latency_ms'] / 1000:.1f}s "
                          f"(eta {eta / 60:.1f} min)", flush=True)
    return load_rows(out_path)


def load_rows(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


# --------------------------------------------------------------------------- #
#  Cost / time estimation                                                      #
# --------------------------------------------------------------------------- #
# Real-model assumptions used before any real run exists (overridden by pilot measurements).
ASSUMED_PROMPT_TOKENS_PER_CALL = 3500
ASSUMED_COMPLETION_TOKENS_PER_CALL = 450
ASSUMED_SECONDS_PER_CALL = 4.0


def estimate(plan: list[Condition], n_questions: int, calib_rows: list[dict] | None = None, repeats: int = 1,
             judge_calls_per_run: int = 1) -> dict:
    """Pre-run estimate of LLM calls, tokens, USD and wall time for the plan.

    Call counts come from calibration rows (mock or real runs of the same conditions); token sizes
    and per-call latency come from real rows when available, otherwise from the assumptions above.
    """
    s = get_settings()
    calib = calib_rows or []
    by_cond: dict[str, list[dict]] = {}
    for r in calib:
        by_cond.setdefault(r["condition"], []).append(r)
    real = [r for r in calib if not r.get("tokens_estimated") and r.get("llm") != "mock" and r.get("llm_calls")]
    p_call = statistics.fmean([r["prompt_tokens"] / r["llm_calls"] for r in real]) if real else ASSUMED_PROMPT_TOKENS_PER_CALL
    c_call = statistics.fmean([r["completion_tokens"] / r["llm_calls"] for r in real]) if real else ASSUMED_COMPLETION_TOKENS_PER_CALL
    sec_call = statistics.fmean([r["total_latency_ms"] / 1000 / r["llm_calls"] for r in real]) if real else ASSUMED_SECONDS_PER_CALL
    rows, tot_calls, tot_cost, tot_s = [], 0, 0.0, 0.0
    spacing = 60.0 / max(1, s.rpm)
    for c in plan:
        rs = by_cond.get(c.label, [])
        calls = statistics.fmean([r["llm_calls"] for r in rs]) if rs else 10.0
        runs = n_questions * repeats
        calls_total = (calls + judge_calls_per_run) * runs
        cost = calls_total * (p_call / 1e6 * s.price_in_per_m + c_call / 1e6 * s.price_out_per_m)
        secs = calls_total * max(sec_call, spacing)
        rows.append({"condition": c.label, "runs": runs, "llm_calls_per_run": round(calls, 1),
                     "llm_calls_total": round(calls_total), "est_cost_usd": round(cost, 3),
                     "est_minutes": round(secs / 60, 1)})
        tot_calls += calls_total
        tot_cost += cost
        tot_s += secs
    return {"rows": rows, "total_runs": n_questions * repeats * len(plan), "total_llm_calls": round(tot_calls),
            "total_cost_usd": round(tot_cost, 2), "total_minutes": round(tot_s / 60, 1),
            "assumptions": {"prompt_tokens_per_call": round(p_call), "completion_tokens_per_call": round(c_call),
                            "seconds_per_call": round(sec_call, 2), "rpm": s.rpm, "price_in_per_m": s.price_in_per_m,
                            "price_out_per_m": s.price_out_per_m, "source": "measured" if real else "assumed"}}


# --------------------------------------------------------------------------- #
#  Summaries                                                                   #
# --------------------------------------------------------------------------- #
def _mean(xs):
    xs = [float(x) for x in xs if isinstance(x, (int, float)) and not (isinstance(x, float) and math.isnan(x))]
    return round(statistics.fmean(xs), 4) if xs else None


def _pct(xs, q):
    xs = sorted(float(x) for x in xs if isinstance(x, (int, float)))
    if not xs:
        return None
    i = (len(xs) - 1) * q
    lo, hi = math.floor(i), math.ceil(i)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (i - lo), 1)


def summarize_rows(rows: list[dict], group: str = "condition") -> list[dict]:
    from evaluation.stats import bootstrap_ci
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(str(r.get(group)), []).append(r)
    out = []
    for g, rs in groups.items():
        acc = [r["judge"].get("accuracy") for r in rs if isinstance(r.get("judge"), dict)]
        acc_vals = [a for a in acc if isinstance(a, (int, float))]
        correct = sum(1 for a in acc_vals if a >= 1.0)
        cost = sum(r["cost_usd"] for r in rs)
        out.append({
            group: g, "runs": len(rs), "accuracy": _mean(acc_vals),
            "accuracy_ci95": bootstrap_ci([float(a) for a in acc_vals]) if len(acc_vals) > 1 else None,
            "completeness": _mean(r["judge"].get("completeness") for r in rs),
            "hallucination": _mean(r["judge"].get("hallucination") for r in rs),
            "judge_score": _mean(r["judge"].get("judge_score") for r in rs),
            "recall_at_k": _mean(r["retrieval"].get("recall_at_k") for r in rs),
            "evidence_precision": _mean(r["retrieval"].get("evidence_precision") for r in rs),
            "citation_precision": _mean(r["citations"].get("citation_precision") for r in rs),
            "latency_p50_s": (_pct([r["total_latency_ms"] for r in rs], 0.5) or 0) / 1000,
            "latency_p95_s": (_pct([r["total_latency_ms"] for r in rs], 0.95) or 0) / 1000,
            "llm_calls": _mean(r["llm_calls"] for r in rs), "tokens": _mean(r["total_tokens"] for r in rs),
            "cost_per_run_usd": round(cost / len(rs), 5),
            "cost_per_correct_usd": round(cost / correct, 5) if correct else None,
            "tool_calls": _mean(r["tool_calls"] for r in rs), "a2a_calls": _mean(r["a2a_calls"] for r in rs),
            "messages": _mean(r["messages"] for r in rs), "message_kb": _mean(r["message_bytes"] / 1024 for r in rs),
            "errors": sum(len(r["errors"]) for r in rs), "degraded_or_failed": sum(r["status"] != "ok" for r in rs),
        })
    return sorted(out, key=lambda d: d[group])


def factor_tables(rows: list[dict]) -> dict[str, list[dict]]:
    """Pattern / messaging / prompt comparisons, each on its own slice of the matrix."""
    pat = [r for r in rows if r["protocol"] in ("blackboard", "manager", "none") and r["variant"] == "default"]
    msg = [r for r in rows if r["architecture"] == "sequential" and r["variant"] == "default"]
    var = [r for r in rows if r["architecture"] == "sequential" and r["protocol"] == "blackboard"]
    return {"by_pattern": summarize_rows(pat, "architecture"), "by_messaging": summarize_rows(msg, "protocol"),
            "by_prompt_variant": summarize_rows(var, "variant"), "by_condition": summarize_rows(rows, "condition")}


def significance(rows: list[dict], baseline: str, metric: str = "accuracy") -> list[dict]:
    from evaluation.stats import compare, holm
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["condition"], []).append({"question_id": r["question_id"],
                                                      metric: r["judge"].get(metric)})
    if baseline not in groups:
        return []
    out = [{"a": baseline, "b": c, **compare(groups[baseline], rs, metric)} for c, rs in groups.items() if c != baseline]
    for c, p in zip(out, holm([c["p_value"] for c in out])):
        c["p_holm"] = p
    return out


def to_markdown(rows: list[dict]) -> str:
    if not rows:
        return "_no rows_"
    cols = [c for c in rows[0] if c not in ("accuracy_ci95",)]
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join("–" if r[c] is None else (f"{r[c]:.3f}" if isinstance(r[c], float) else str(r[c]))
                                     for c in cols) + " |")
    return "\n".join(out)
