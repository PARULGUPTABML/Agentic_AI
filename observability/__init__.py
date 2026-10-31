"""End-to-end observability for multi-agent runs.

    tracker.py      LLMCallTracker — counts LLM calls / tokens and attributes them to agents
    instrument.py   RunRecorder + wrappers that time agents, capture tool calls and errors
    run_logger.py   RunRecord schema and the JSONL run log consumed by the dashboard
"""
from observability.tracker import LLMCallTracker, current_agent, set_current_agent
from observability.instrument import RunRecorder, InstrumentedAgent, InstrumentedVerifier, instrument_method
from observability.run_logger import RunLogger, RunRecord, build_record

__all__ = [
    "LLMCallTracker", "current_agent", "set_current_agent",
    "RunRecorder", "InstrumentedAgent", "InstrumentedVerifier", "instrument_method",
    "RunLogger", "RunRecord", "build_record",
]
