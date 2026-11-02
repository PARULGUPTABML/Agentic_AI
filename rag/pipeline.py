"""Answering pipeline for the three experimental conditions (+ ablation), runnable through ReadMind's
native pipeline or the LangChain / LlamaIndex engines in rag/frameworks/."""
import time

from config import LLM_MODEL, LLM_PROVIDER, TOP_K_FINAL
from rag.citations import verify_citations
from rag.guardrails import run_guardrails
from rag.llm import generate

CONDITIONS = {
    "llm_only": {"label": "LLM only", "types": None},
    "code_only": {"label": "LLM + current code", "types": {"code", "doc"}},
    "full_history": {"label": "LLM + code + history (RAG)",
                     "types": {"code", "doc", "commit", "issue", "pr", "review"}},
}
# Ablation (not shown in the UI): full history but without history-graph expansion.
ABLATIONS = {
    "full_no_graph": {"label": "History RAG without graph expansion",
                      "types": {"code", "doc", "commit", "issue", "pr", "review"}, "expand": False},
}
ALL_CONDITIONS = {**CONDITIONS, **ABLATIONS}

SYSTEM_RAG = """You are a senior engineer who explains why a software repository looks the way it does.
Rules:
- Use ONLY the evidence provided. Do not use outside knowledge about this project.
- End every factual sentence with citations using the evidence IDs exactly as written, e.g. [commit:1a2b3c4d5e] or [issue:482] or [pr:77].
- Never invent commit hashes, issue numbers or PR numbers.
- If the evidence does not answer the question, say "The available evidence does not explain this." and then say what the evidence does show.
- Prefer history evidence (commits, issues, PRs, reviews) to explain WHY; use code evidence to explain WHAT.
- Be concise: 3-8 sentences. If the developer is planning a change, finish with one line starting "Recommendation:"."""

SYSTEM_NO_CONTEXT = """You are a senior engineer answering a question about the open-source repository {repo}.
You have no access to its code or history in this conversation. Answer from what you know.
If you are not sure, say so explicitly rather than guessing. Never invent commit hashes, issue numbers or PR numbers.
Be concise: 3-8 sentences."""


def format_evidence(evidence: list[dict], max_chars=1800) -> str:
    blocks = []
    for e in evidence:
        head = f"[{e['id']}] ({e['type']}{', ' + e['date'][:10] if e.get('date') else ''}) {e.get('title', '')}"
        blocks.append(f"{head}\n{e['text'][:max_chars]}")
    return "\n\n---\n\n".join(blocks)


ENGINES = {
    "native": "ReadMind native pipeline",
    "langchain": "LangChain (LCEL chain over the ReadMind retriever)",
    "llamaindex": "LlamaIndex (RetrieverQueryEngine over the ReadMind retriever)",
}


def build_result(slug, question, condition, answer, evidence, timings, t0, t1, t2, engine="native") -> dict:
    """Package an answer into the response shape the API, UI and evaluation all expect.
    Shared by the native pipeline and the LangChain / LlamaIndex engines so results are comparable."""
    spec = ALL_CONDITIONS[condition]
    evidence_cleaned = [{**{k2: v for k2, v in e.items() if k2 != "text"}, "snippet": e["text"][:500]}
                        for e in evidence]
    verification = verify_citations(answer, [e["id"] for e in evidence])
    guardrails = run_guardrails(
        question=question,
        answer=answer,
        evidence=evidence,
        verification=verification,
        condition=condition
    )
    return {
        "repo": slug,
        "condition": condition,
        "condition_label": spec["label"],
        "engine": engine,
        "question": question,
        "answer": answer,
        "evidence": evidence_cleaned,
        "verification": verification,
        "guardrails": guardrails,
        "latency_ms": {"retrieval": round((t1 - t0) * 1000, 1), "generation": round((t2 - t1) * 1000, 1),
                       "total": round((t2 - t0) * 1000, 1), **{k2: round(v, 1) for k2, v in timings.items()}},
        "model": f"{LLM_PROVIDER}:{LLM_MODEL}",
    }


def get_index(slug):
    """Imported lazily so this module (and the framework engines) load without chromadb in CI."""
    from rag.retriever import get_index as _get_index
    return _get_index(slug)


def answer_question(slug: str, question: str, condition: str = "full_history", k: int = TOP_K_FINAL,
                    engine: str = "native") -> dict:
    if condition not in ALL_CONDITIONS:
        raise ValueError(f"Unknown condition '{condition}'")
    if engine not in ENGINES:
        raise ValueError(f"Unknown engine '{engine}'. Choose one of: {', '.join(ENGINES)}")
    if engine == "langchain":
        from rag.frameworks.langchain_rag import answer_with_langchain
        return answer_with_langchain(slug, question, condition, k)
    if engine == "llamaindex":
        from rag.frameworks.llamaindex_rag import answer_with_llamaindex
        return answer_with_llamaindex(slug, question, condition, k)

    spec = ALL_CONDITIONS[condition]
    index = get_index(slug)
    repo_name = index.stats.get("github", slug)
    types = spec["types"]

    t0 = time.perf_counter()
    if types is None:
        evidence, timings = [], {}
        system = SYSTEM_NO_CONTEXT.format(repo=repo_name)
        prompt = f"Question: {question}"
    else:
        evidence, timings = index.retrieve(question, types, k=k, expand=spec.get("expand", True))
        system = SYSTEM_RAG
        prompt = (f"Repository: {repo_name}\n\nEvidence:\n\n{format_evidence(evidence)}\n\n"
                  f"Question: {question}\n\nAnswer with citations:")
    t1 = time.perf_counter()
    answer = generate(system, prompt)
    t2 = time.perf_counter()
    return build_result(slug, question, condition, answer, evidence, timings, t0, t1, t2, engine="native")
