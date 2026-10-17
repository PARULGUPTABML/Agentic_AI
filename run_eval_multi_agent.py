"""Run evaluation with the multi-agent pipeline as an additional condition.

    python -m evaluation.run_eval_multi_agent                          # multi-agent condition
    python -m evaluation.run_eval_multi_agent --protocols blackboard p2p  # specific protocols
    python -m evaluation.run_eval_multi_agent --limit 10              # first 10 questions only

Results are appended to data/eval/results_multi_agent.jsonl.
"""
import argparse
import json

from tqdm import tqdm

from config import EVAL_DIR
from evaluation.metrics import judge_answer, retrieval_metrics
from evaluation.stats import summarize
from agents.multi_agent_pipeline import run_multi_agent
from agents.messaging import PROTOCOLS, compare_protocols
from rag.retriever import get_index


def load_benchmark(path, include_unverified=False):
    items = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    return [i for i in items if include_unverified or i.get("verified")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--benchmark", default=str(EVAL_DIR / "benchmark.jsonl"))
    ap.add_argument("--out", default=str(EVAL_DIR / "results_multi_agent.jsonl"))
    ap.add_argument("--protocols", nargs="+", default=["blackboard"],
                    choices=list(PROTOCOLS.keys()) + ["multi_agent"],
                    help="Which protocols to evaluate. 'multi_agent' = default pipeline.")
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--include-unverified", action="store_true")
    ap.add_argument("--no-judge", action="store_true")
    args = ap.parse_args()

    items = load_benchmark(args.benchmark, args.include_unverified)
    if args.limit:
        items = items[: args.limit]
    if not items:
        raise SystemExit("No (verified) questions found. See evaluation/benchmark.example.jsonl.")

    # Load already-done results for resume support
    done = set()
    try:
        for line in open(args.out, encoding="utf-8"):
            r = json.loads(line)
            done.add((r["qid"], r["condition"]))
    except FileNotFoundError:
        pass

    with open(args.out, "a", encoding="utf-8") as out:
        for item in tqdm(items, desc="questions"):
            index = get_index(item["repo"])
            gold = item.get("gold_evidence", [])
            gold_texts = [index.docs[g]["text"] for g in (index.resolve_id(x) for x in gold) if g]

            for protocol in args.protocols:
                cond_name = f"multi_agent_{protocol}"
                if (item["id"], cond_name) in done:
                    continue

                try:
                    if protocol == "multi_agent":
                        res = run_multi_agent(item["repo"], item["question"])
                    else:
                        fn = PROTOCOLS[protocol]["fn"]
                        res = fn(item["repo"], item["question"])
                except Exception as e:
                    print(f"\n{item['id']} [{cond_name}] failed: {e}")
                    continue

                retrieved = [e["id"] for e in res.get("evidence", [])]
                v = res.get("verification", {}).get("citations", {})
                ma = res.get("multi_agent", {})

                row = {
                    "qid": item["id"],
                    "repo": item["repo"],
                    "category": item.get("category"),
                    "condition": cond_name,
                    "protocol": protocol,
                    "question": item["question"],
                    "answer": res.get("answer", ""),
                    "retrieved": retrieved,
                    "gold_evidence": gold,
                    "latency_total_ms": res.get("latency_ms", {}).get("total", 0),
                    "latency_generation_ms": res.get("latency_ms", {}).get("generation", 0),
                    "citation_precision": v.get("citation_precision"),
                    "grounded_ratio": v.get("grounded_ratio"),
                    "invalid_citations": len(v.get("invalid", [])),
                    # Multi-agent specific metrics
                    "evidence_count": ma.get("evidence_count", 0),
                    "retry_count": ma.get("retry_count", 0),
                    "agent_log_count": len(ma.get("agent_logs", [])),
                    "evidence_sufficient": res.get("verification", {}).get(
                        "evidence_sufficiency", {}).get("sufficient"),
                }

                if retrieved:
                    row.update(retrieval_metrics(retrieved, gold, args.k))

                if not args.no_judge:
                    try:
                        row.update(judge_answer(item, res.get("answer", ""), gold_texts))
                    except Exception as e:
                        print(f"\njudge failed for {item['id']} [{cond_name}]: {e}")

                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                out.flush()

    # Generate summary
    summary = summarize(args.out)
    print("\n" + "=" * 60)
    print("  Multi-Agent Evaluation Summary")
    print("=" * 60)
    print(json.dumps(
        {c: {k: v for k, v in m.items()
             if k in ("accuracy", "completeness", "hallucination_rate",
                       "recall_at_k", "latency_p50_ms")}
         for c, m in summary.get("conditions", {}).items()},
        indent=2,
    ))


if __name__ == "__main__":
    main()
