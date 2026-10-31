"""Tests for observability, the experiment runner (offline) and the dashboard."""
from __future__ import annotations

import json
import sys
import textwrap
import threading
import types

import pytest

from observability import (InstrumentedAgent, InstrumentedVerifier, LLMCallTracker, RunLogger, RunRecorder,
                           build_record, instrument_method, set_current_agent)


class TestTracker:
    def test_counts_and_attribution(self, monkeypatch):
        mod = types.ModuleType("fake_llm_mod")
        mod.generate = lambda system, prompt, **kw: "x" * 40
        monkeypatch.setitem(sys.modules, "fake_llm_mod", mod)
        with LLMCallTracker(targets=[("fake_llm_mod", "generate")]) as t:
            set_current_agent("history_rag")
            mod.generate("s" * 8, "p" * 8)
            set_current_agent(None)
            mod.generate("s", "p")
        s = t.summary()
        assert s["calls"] == 2 and s["by_agent"]["history_rag"]["calls"] == 1
        assert s["by_agent"]["unattributed"]["calls"] == 1 and s["completion_tokens"] == 20
        assert not hasattr(mod.generate, "__wrapped_by_tracker__")          # restored on exit

    def test_thread_local_attribution(self, monkeypatch):
        mod = types.ModuleType("fake_llm_mod2")
        mod.generate = lambda s, p, **kw: "ok"
        monkeypatch.setitem(sys.modules, "fake_llm_mod2", mod)
        with LLMCallTracker(targets=[("fake_llm_mod2", "generate")]) as t:
            def worker(name):
                set_current_agent(name)
                mod.generate("a", "b")
            ts = [threading.Thread(target=worker, args=(n,)) for n in ("code_analysis", "issue_pr")]
            [x.start() for x in ts]
            [x.join() for x in ts]
        assert set(t.summary()["by_agent"]) == {"code_analysis", "issue_pr"}


class TestInstrumentation:
    def test_typeerror_passthrough_for_blackboard_fallback(self):
        class BBOnly:
            def run(self, blackboard):
                return ["frag"]
        rec = RunRecorder()
        a = InstrumentedAgent("history_rag", BBOnly(), rec)
        with pytest.raises(TypeError):
            a.run("q", repo_slug="x")
        assert a.run(object()) == ["frag"]
        assert rec.snapshot()["agent_stats"]["history_rag"]["calls"] == 1

    def test_agent_error_degrades(self):
        class Boom:
            last_trace = [{"tool": "search", "arg": "q", "results": 0, "ms": 1.0}]

            def run(self, q, repo_slug=None):
                raise RuntimeError("index down")
        rec = RunRecorder()
        assert InstrumentedAgent("issue_pr", Boom(), rec).run("q", repo_slug="x") == []
        snap = rec.snapshot()
        assert snap["errors"][0]["agent"] == "issue_pr" and snap["tool_calls"][0]["tool"] == "search"

    def test_verifier_error_is_safe(self):
        from agents.blackboard import Blackboard

        class Bad:
            def run(self, bb):
                raise ValueError("parse")
        bb = Blackboard("q", "r")
        out = InstrumentedVerifier("verification", Bad(), RunRecorder()).run(bb)
        assert out["sufficient"] and out["error"] and bb.verification_result is out

    def test_instrument_method(self):
        class O:
            def plan(self, x):
                return x * 2
        o, rec = O(), RunRecorder()
        instrument_method(o, "plan", "orchestrator", rec)
        instrument_method(o, "plan", "orchestrator", rec)      # idempotent
        assert o.plan(3) == 6 and rec.snapshot()["agent_stats"]["orchestrator"]["calls"] == 1


def test_build_record_and_logger(tmp_path):
    rec = RunRecorder()
    rec.record("code_analysis", 12.5, 3, [{"tool": "grep_keywords", "arg": ["a"], "results": {"x": 1}, "ms": 2.0}])
    out = {"answer": "A [pr:1]", "agents_invoked": ["orchestrator_r1", "code_analysis", "reviewer"], "rounds": 2,
           "total_latency_ms": 50.0, "coordination_overhead_ms": 1.5,
           "logs": [{"agent": "reviewer", "action": "final_response", "duration_ms": 4.0}]}
    r = build_record(run_id="r1", item={"qid": "Q1", "question": "q", "split": "dev", "question_type": "rationale"},
                     config="C", condition="C_sequential", output=out, recorder_snapshot=rec.snapshot(),
                     llm_summary={"calls": 3, "total_tokens": 99, "by_agent": {"reviewer": {"calls": 1}}},
                     evidence=[{"id": "pr:1", "type": "pr", "source_agent": "issue_pr", "confidence": 0.9}],
                     architecture="sequential", llm="m")
    assert r.per_agent_latency_ms == {"code_analysis": 12.5, "reviewer": 4.0}
    assert r.retries == 1 and r.retrieval_iterations == 1 and r.tools_used[0]["results"] == 1
    log = RunLogger(tmp_path / "runs.jsonl")
    log.write(r)
    (tmp_path / "runs.jsonl").open("a").write('{"truncated')        # crash mid-write
    assert log.done_keys() == {("Q1", "C_sequential", "m", 0)}


@pytest.fixture
def tiny_repo(tmp_path):
    p = tmp_path / "repo" / "src" / "smp"
    p.mkdir(parents=True)
    (p / "lock.c").write_text(textwrap.dedent("""
        void
        lock_init(void)
        {
        }
    """))
    return tmp_path / "repo"


def test_offline_experiment_and_dashboard(tmp_path, tiny_repo, monkeypatch):
    """End-to-end: runner (offline) -> run log -> analysis tables -> HTML dashboard."""
    import run_experiments
    from dashboard.build_dashboard import main as build
    monkeypatch.setenv("JUDGE_PROVIDER", "heuristic")
    out = tmp_path / "res" / "runs.jsonl"
    summary = run_experiments.main(["--offline", "--repo-path", str(tiny_repo), "--limit", "2",
                                    "--architectures", "sequential", "workflow_dag", "--out", str(out),
                                    "--corpus", str(tmp_path / "none")])
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    assert len(rows) == 2 * 4                                    # A, B, C x2
    assert {r["condition"] for r in rows} == {"A_no_context", "B_code_only", "C_sequential", "C_workflow_dag"}
    for r in rows:
        for f in ("run_id", "agents_invoked", "tools_used", "retrieved_evidence", "per_agent_latency_ms",
                  "llm_calls", "total_tokens", "final_answer", "judge", "errors", "retries"):
            assert f in r
    assert "C_sequential" in summary["conditions"]
    # resume: nothing new is run
    run_experiments.main(["--offline", "--repo-path", str(tiny_repo), "--limit", "2",
                          "--architectures", "sequential", "workflow_dag", "--out", str(out)])
    assert len(out.read_text().splitlines()) == 8

    build([str(out)])
    html = (tmp_path / "res" / "dashboard.html").read_text()
    assert "<svg" in html and "Accuracy by condition" in html and "not</b> reportable" in html
    assert (tmp_path / "res" / "analysis" / "configurations.csv").exists()
