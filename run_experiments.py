"""Experiment runner: configurations A / B / C x architectures x LLMs x protocols.

Every run is instrumented (per-agent latency, tool calls, LLM calls, tokens,
errors), graded by the LLM-as-Judge, scored for retrieval / citations, and
appended to a JSONL run log that the dashboard turns into comparison tables.

Examples::

    # Offline dry run (mock LLM, no rag backend needed) — checks the plumbing end to end
    python run_experiments.py --offline --repo-path ../repos/seL4__seL4 --limit 5

    # Full matrix with the real backend (rag/ + config on PYTHONPATH, LLM_PROVIDER set)
    python run_experiments.py --configs A B C \\
        --architectures sequential hierarchical event_driven workflow_dag \\
        --repo-path ../repos/seL4__seL4 --corpus data/corpus/seL4__seL4 \\
        --llm-label gemini-2.0-flash --split heldout

    # Messaging-protocol comparison (Blackboard vs P2P vs Broadcast vs MCP)
    python run_experiments.py --configs P --protocols blackboard p2p broadcast mcp

    # Then build the dashboard
    python -m dashboard.build_dashboard results/runs.jsonl

35 questions x (A + B + 4 architectures) = 210 logged runs per LLM, well above the
40-run minimum.  Re-running the same command resumes where it stopped.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

ARCHITECTURES = ["sequential", "hierarchical", "event_driven", "workflow_dag"]


# ---------------------------------------------------------------------- #
#  Environment                                                            #
# ---------------------------------------------------------------------- #
def install_offline_backend() -> None:
    """Stand-in ``rag`` / ``config`` modules so the agents import without the backend."""
    import types
    os.environ["LLM_PROVIDER"] = "mock"
    os.environ.setdefault("JUDGE_PROVIDER", "heuristic")

    def generate(system, prompt, **kw):
        return "Thought: no backend in offline mode\nAction: finish()"

    rag = types.ModuleType("rag")
    llm = types.ModuleType("rag.llm")
    llm.generate = generate
    llm.parse_json = lambda t: json.loads(t)
    retriever = types.ModuleType("rag.retriever")

    def get_index(slug):
        raise RuntimeError("offline mode: no shared index")
    retriever.get_index = get_index
    retriever.RepoIndex = object
    citations = types.ModuleType("rag.citations")
    citations.verify_citations = lambda answer, ids: {"valid": 0, "invalid": 0}
    config = types.ModuleType("config")
    config.LLM_MODEL, config.LLM_PROVIDER, config.EVAL_DIR = "mock", "mock", ROOT / "results"
    for name, mod in {"rag": rag, "rag.llm": llm, "rag.retriever": retriever,
                      "rag.citations": citations, "config": config}.items():
        sys.modules.setdefault(name, mod)


def load_questions(path: Path, split: str, limit: int, qids: list[str] | None) -> list[dict]:
    items = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if split != "all":
        items = [i for i in items if i.get("split") == split]
    if qids:
        items = [i for i in items if i.get("qid") in qids]
    return items[:limit] if limit else items


# ---------------------------------------------------------------------- #
#  Agents                                                                 #
# ---------------------------------------------------------------------- #
def build_agents(args, recorder):
    from agents.code_analysis_agent import CodeAnalysisAgent
    from agents.issue_pr_agent import IssuePRAgent
    from observability import InstrumentedAgent, InstrumentedVerifier
    from architectures.base import StubVerificationAgent

    code = CodeAnalysisAgent(args.repo, repo_path=args.repo_path)
    issue_pr = IssuePRAgent(args.repo, corpus_dir=args.corpus)
    agents: dict[str, Any] = {
        "code_analysis": InstrumentedAgent("code_analysis", code, recorder),
        "issue_pr": InstrumentedAgent("issue_pr", issue_pr, recorder),
    }
    notes = [f"code_analysis repo_path={code.repo_path}", f"issue_pr source={issue_pr.source_name}"]

    history = None
    if not args.offline:
        try:
            from agents.history_rag_agent import HistoryRAGAgent
            history = HistoryRAGAgent(args.repo)
        except Exception as e:
            notes.append(f"history_rag unavailable: {e}")
    if history is not None:
        agents["history_rag"] = InstrumentedAgent("history_rag", history, recorder)
    else:
        notes.append("history_rag omitted (offline or backend missing)")

    verifier = None
    if not args.offline:
        try:
            from agents.evidence_verification_agent import EvidenceVerificationAgent
            verifier = EvidenceVerificationAgent()
        except Exception as e:
            notes.append(f"verification agent unavailable: {e}")
    if verifier is None:
        verifier = StubVerificationAgent()
        notes.append("verification = StubVerificationAgent")
    return agents, InstrumentedVerifier("verification", verifier, recorder), code, issue_pr, notes


def make_architecture(name: str, recorder):
    from architectures.sequential import SequentialArchitecture
    from architectures.hierarchical import HierarchicalArchitecture
    from architectures.event_driven import EventDrivenArchitecture
    from architectures.workflow_dag import WorkflowDAGArchitecture
    from observability import instrument_method
    cls = {"sequential": SequentialArchitecture, "hierarchical": HierarchicalArchitecture,
           "event_driven": EventDrivenArchitecture, "workflow_dag": WorkflowDAGArchitecture}[name]
    arch = cls()
    instrument_method(arch.orchestrator, "plan", "orchestrator", recorder)
    instrument_method(arch.orchestrator, "replan", "orchestrator", recorder)
    # Capture the evidence handed to the reviewer (architectures do not return it).
    captured: dict[str, Any] = {}
    original = arch.reviewer.review

    def review(input_data, blackboard=None):
        captured["evidence"] = list(input_data.get("evidence", []))
        return original(input_data, blackboard=blackboard)
    arch.reviewer.review = review
    instrument_method(arch.reviewer, "review", "reviewer", recorder)
    return arch, captured


# ---------------------------------------------------------------------- #
#  Gold evidence text (for the judge)                                     #
# ---------------------------------------------------------------------- #
def gold_texts(item: dict, issue_pr_agent) -> list[str]:
    out = []
    src = getattr(issue_pr_agent, "source", None)
    for g in item.get("ground_truth_evidence", []):
        try:
            rid = src.resolve_id(g) if src is not None else None
        except Exception:
            rid = None
        if rid and rid in src.docs:
            out.append(src.docs[rid].get("text", ""))
    return out


# ---------------------------------------------------------------------- #
def run_one(args, item, config, arch_name, protocol, ctx, repeat) -> dict:
    from evaluation.baselines import run_code_only, run_no_context
    from evaluation.metrics import retrieval_metrics, citation_metrics
    from evaluation.judge import Judge
    from observability import LLMCallTracker, build_record

    recorder = ctx["recorder"]
    recorder.reset()
    run_id = f"{item['qid']}-{config}{'-' + arch_name if arch_name else ''}{'-' + protocol if protocol else ''}-r{repeat}-{uuid.uuid4().hex[:6]}"
    q = item["question"]
    errors: list[dict] = []
    t0 = time.perf_counter()

    with LLMCallTracker() as llm:
        try:
            if config == "A":
                out = run_no_context(q)
                evidence = []
            elif config == "B":
                out = run_code_only(q, ctx["agents"]["code_analysis"], args.repo)
                evidence = out["evidence"]
            elif config == "C":
                arch, captured = ctx["arch"][arch_name]
                captured.clear()
                out = arch.run(q, run_id, repo_slug=args.repo, retrieval_agents=ctx["agents"],
                               verification_agent=ctx["verifier"])
                evidence = captured.get("evidence", [])
            elif config == "P":
                from agents.messaging import PROTOCOLS
                res = PROTOCOLS[protocol]["fn"](args.repo, q)
                evidence = res.get("evidence", [])
                out = {"answer": res.get("answer", ""), "agents_invoked": [protocol],
                       "total_latency_ms": res.get("latency_ms", {}).get("total", 0.0),
                       "rounds": 1 + int(res.get("multi_agent", {}).get("retry_count", 0)), "logs": []}
            else:
                raise ValueError(config)
        except Exception as e:
            errors.append({"agent": "runner", "error": f"{type(e).__name__}: {e}"})
            out, evidence = {"answer": "", "total_latency_ms": (time.perf_counter() - t0) * 1000}, []
    out.setdefault("errors", errors)

    condition = {"A": "A_no_context", "B": "B_code_only", "C": f"C_{arch_name}", "P": f"P_{protocol}"}[config]
    rec = build_record(run_id=run_id, item=item, config=config, condition=condition, output=out,
                       recorder_snapshot=recorder.snapshot(), llm_summary=llm.summary(), evidence=evidence,
                       architecture=arch_name or "", protocol=protocol or "", llm=args.llm_label, repeat=repeat)

    retrieved = [e.get("id") for e in evidence if e.get("id")]
    gold = item.get("ground_truth_evidence", [])
    ev_texts = [e.get("text", "") for e in evidence]
    rec.retrieval = retrieval_metrics(retrieved, gold, args.k)
    rec.citations = {k: v for k, v in citation_metrics(rec.final_answer, retrieved, gold,
                                                       item.get("reference_answer", ""), ev_texts).items()}
    if not args.no_judge:
        try:
            rec.judge = ctx["judge"].grade(item, rec.final_answer, gold_texts=gold_texts(item, ctx["issue_pr"]),
                                           retrieved=retrieved, evidence_texts=ev_texts)
        except Exception as e:
            rec.errors.append({"agent": "judge", "error": f"{type(e).__name__}: {e}"})
    return rec.to_dict()


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--questions", default=str(ROOT / "eval_set" / "questions.jsonl"))
    ap.add_argument("--split", default="all", choices=["all", "dev", "heldout"])
    ap.add_argument("--qids", nargs="*", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--configs", nargs="+", default=["A", "B", "C"], choices=["A", "B", "C", "P"])
    ap.add_argument("--architectures", nargs="+", default=ARCHITECTURES, choices=ARCHITECTURES)
    ap.add_argument("--protocols", nargs="+", default=["blackboard", "p2p", "broadcast", "mcp"])
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--repo", default="seL4__seL4")
    ap.add_argument("--repo-path", default=None, help="local checkout for the Code Analysis Agent")
    ap.add_argument("--corpus", default=None, help="issue/PR corpus dir (default data/corpus/<repo>)")
    ap.add_argument("--llm-label", default=os.environ.get("LLM_MODEL", "default"))
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--judge-votes", type=int, default=1)
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--offline", action="store_true", help="mock LLM + no rag backend (plumbing check only)")
    ap.add_argument("--out", default=str(ROOT / "results" / "runs.jsonl"))
    ap.add_argument("--fresh", action="store_true", help="ignore existing results instead of resuming")
    args = ap.parse_args(argv)

    if args.offline:
        install_offline_backend()
        if args.llm_label == "default":
            args.llm_label = "mock"
        if "P" in args.configs:
            print("[warn] protocol runs need the real backend; skipping P in offline mode")
            args.configs = [c for c in args.configs if c != "P"]

    from evaluation.judge import Judge
    from evaluation.llm import describe_judge
    from evaluation.stats import summarize
    from observability import RunLogger, RunRecorder

    recorder = RunRecorder()
    agents, verifier, code, issue_pr, notes = build_agents(args, recorder)
    ctx = {"recorder": recorder, "agents": agents, "verifier": verifier, "issue_pr": issue_pr,
           "judge": Judge(llm_fn="auto", n_votes=args.judge_votes),
           "arch": {a: make_architecture(a, recorder) for a in args.architectures} if "C" in args.configs else {}}
    print("Setup:", "; ".join(notes), f"; judge={describe_judge()}", file=sys.stderr)

    out_path = Path(args.out)
    if args.fresh and out_path.exists():
        out_path.unlink()
    logger = RunLogger(out_path)
    done = logger.done_keys()

    items = load_questions(Path(args.questions), args.split, args.limit, args.qids)
    plan: list[tuple[str, str, str]] = []
    for c in args.configs:
        if c == "C":
            plan += [("C", a, "") for a in args.architectures]
        elif c == "P":
            plan += [("P", "", p) for p in args.protocols]
        else:
            plan.append((c, "", ""))

    total = len(items) * len(plan) * args.repeats
    n = skipped = 0
    for item in items:
        for config, arch_name, protocol in plan:
            condition = {"A": "A_no_context", "B": "B_code_only", "C": f"C_{arch_name}", "P": f"P_{protocol}"}[config]
            for rep in range(args.repeats):
                n += 1
                if (item["qid"], condition, args.llm_label, rep) in done:
                    skipped += 1
                    continue
                row = run_one(args, item, config, arch_name, protocol, ctx, rep)
                logger.write(row)
                j = row.get("judge", {})
                print(f"[{n}/{total}] {item['qid']} {condition:<16} acc={j.get('accuracy', '-')} "
                      f"hall={j.get('hallucination', '-')} ev={len(row['retrieved_evidence'])} "
                      f"{row['total_latency_ms']:.0f}ms {row['status']}", file=sys.stderr)

    summary = summarize(out_path)
    summary_path = out_path.with_name("summary.json")
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n{n - skipped} new runs ({skipped} resumed). Log: {out_path}  Summary: {summary_path}", file=sys.stderr)
    cols = ["n", "accuracy", "completeness", "hallucination_rate", "evidence_precision", "recall_at_k",
            "latency_p50_ms", "llm_calls"]
    print(f"{'condition':<18}" + "".join(f"{c:>20}" for c in cols))
    for cond, m in summary["conditions"].items():
        print(f"{cond:<18}" + "".join(f"{str(m.get(c, '-')):>20}" for c in cols))
    return summary


if __name__ == "__main__":
    main()
