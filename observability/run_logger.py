"""Run record schema + JSONL run log.

One line per run in ``results/runs.jsonl``.  Fields follow the synopsis'
observability spec: run ID, question, agents invoked, tools used, retrieved
evidence, per-agent latency, LLM-call count, token usage, final response,
LLM-as-Judge score, and errors / retries.
"""
from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"


@dataclass
class RunRecord:
    run_id: str
    question_id: str
    question: str
    config: str                      # "A" no-context | "B" code-only | "C" full multi-agent
    condition: str                   # e.g. "A_no_context", "C_sequential"
    architecture: str = ""           # sequential | hierarchical | event_driven | workflow_dag | ""
    protocol: str = ""               # blackboard | p2p | broadcast | mcp | ""
    llm: str = ""                    # model label of the answering LLM
    split: str = ""
    question_type: str = ""
    repeat: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    schema_version: str = SCHEMA_VERSION

    agents_invoked: list[str] = field(default_factory=list)
    tools_used: list[dict[str, Any]] = field(default_factory=list)
    retrieved_evidence: list[dict[str, Any]] = field(default_factory=list)
    per_agent_latency_ms: dict[str, float] = field(default_factory=dict)
    total_latency_ms: float = 0.0
    coordination_overhead_ms: float = 0.0
    llm_calls: int = 0
    llm_calls_by_agent: dict[str, int] = field(default_factory=dict)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    tokens_estimated: bool = True
    rounds: int = 1                  # orchestrator / verification rounds
    retrieval_iterations: int = 0    # total retrieval-agent invocations
    final_answer: str = ""
    answer_confidence: str = ""
    gaps: list[str] = field(default_factory=list)

    # evaluation (filled by evaluation.metrics)
    judge: dict[str, Any] = field(default_factory=dict)
    retrieval: dict[str, Any] = field(default_factory=dict)
    citations: dict[str, Any] = field(default_factory=dict)

    errors: list[dict[str, str]] = field(default_factory=list)
    retries: int = 0
    status: str = "ok"               # ok | degraded | failed

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    # Flat view used by stats / dashboard
    def flat(self) -> dict[str, Any]:
        d = {k: v for k, v in self.to_dict().items()
             if not isinstance(v, (list, dict))}
        d.update({k: v for k, v in self.judge.items() if not isinstance(v, (list, dict))})
        d.update(self.retrieval)
        d.update({f"citation_{k}": v for k, v in self.citations.items() if not isinstance(v, (list, dict))})
        return d


def build_record(
    *,
    run_id: str,
    item: dict[str, Any],
    config: str,
    condition: str,
    output: dict[str, Any],
    recorder_snapshot: dict[str, Any] | None = None,
    llm_summary: dict[str, Any] | None = None,
    evidence: list[dict[str, Any]] | None = None,
    architecture: str = "",
    protocol: str = "",
    llm: str = "",
    repeat: int = 0,
) -> RunRecord:
    """Assemble a :class:`RunRecord` from an architecture / baseline output dict."""
    snap = recorder_snapshot or {"agent_stats": {}, "tool_calls": [], "errors": [], "verification_rounds": 0}
    llm_summary = llm_summary or {}

    per_agent = {a: round(s["latency_ms"], 2) for a, s in snap["agent_stats"].items()}
    # Orchestrator / reviewer durations also appear in the Blackboard log; use them if not instrumented.
    for log in output.get("logs", []):
        a = log.get("agent")
        if a in ("orchestrator", "reviewer") and a not in snap["agent_stats"]:
            per_agent[a] = round(per_agent.get(a, 0.0) + float(log.get("duration_ms") or 0.0), 2)

    retrieval_agents = {"code_analysis", "history_rag", "issue_pr"}
    retrieval_iterations = sum(s["calls"] for a, s in snap["agent_stats"].items() if a in retrieval_agents)
    errors = list(snap.get("errors", [])) + list(output.get("errors", []))
    status = "ok" if not errors else ("failed" if not output.get("answer") else "degraded")

    return RunRecord(
        run_id=run_id,
        question_id=item.get("qid") or item.get("id", ""),
        question=item.get("question", ""),
        config=config,
        condition=condition,
        architecture=architecture,
        protocol=protocol,
        llm=llm,
        split=item.get("split", ""),
        question_type=item.get("question_type") or item.get("category", ""),
        repeat=repeat,
        agents_invoked=list(output.get("agents_invoked", [])),
        tools_used=snap.get("tool_calls", []),
        retrieved_evidence=[
            {"id": e.get("id"), "type": e.get("type") or e.get("evidence_type"),
             "source_agent": e.get("source_agent", ""), "confidence": e.get("confidence")}
            for e in (evidence or [])
        ],
        per_agent_latency_ms=per_agent,
        total_latency_ms=float(output.get("total_latency_ms", 0.0)),
        coordination_overhead_ms=float(output.get("coordination_overhead_ms", 0.0)),
        llm_calls=int(llm_summary.get("calls", 0)),
        llm_calls_by_agent={a: int(v.get("calls", 0)) for a, v in llm_summary.get("by_agent", {}).items()},
        prompt_tokens=int(llm_summary.get("prompt_tokens", 0)),
        completion_tokens=int(llm_summary.get("completion_tokens", 0)),
        total_tokens=int(llm_summary.get("total_tokens", 0)),
        tokens_estimated=bool(llm_summary.get("tokens_estimated", True)),
        rounds=int(output.get("rounds", 1)),
        retrieval_iterations=retrieval_iterations,
        final_answer=output.get("answer", ""),
        answer_confidence=str(output.get("confidence", "")),
        gaps=list(output.get("gaps", [])),
        errors=errors,
        retries=max(0, int(output.get("rounds", 1)) - 1) + int(llm_summary.get("errors", 0)),
        status=status,
    )


class RunLogger:
    """Append-only, thread-safe JSONL log with resume support."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, record: RunRecord | dict) -> None:
        row = record.to_dict() if isinstance(record, RunRecord) else record
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue          # tolerate a truncated last line after a crash
        return rows

    def done_keys(self) -> set[tuple]:
        return {(r.get("question_id"), r.get("condition"), r.get("llm"), r.get("repeat", 0)) for r in self.read()}
