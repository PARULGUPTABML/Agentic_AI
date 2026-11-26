"""Runtime settings (environment-driven, one place).

Environment variables
---------------------
    READMIND_LLM          gemini | mock          (default: gemini when an API key is present, else mock)
    GEMINI_API_KEY        Gemini key (copied to GOOGLE_API_KEY for ADK)
    READMIND_MODEL        answering model         (default gemini-3.5-flash, the team's config.py default)
    READMIND_JUDGE_MODEL  judge model             (default = READMIND_MODEL; prefer a different family)
    READMIND_RPM          max LLM requests/minute across BOTH frameworks (default 10 for gemini)
    READMIND_REPO_PATH    local git clone of the repository (needs history: clone with --filter=blob:none)
    READMIND_CORPUS_DIR   issue/PR/review corpus from ingestion.issue_pr_extractor
    READMIND_WORK_DIR     where runs, traces and reports are written (default results/readmind)
    READMIND_MCP_TRANSPORT  http | stdio          (default http: servers start once, index loaded once)
    READMIND_PRICE_IN / READMIND_PRICE_OUT  USD per 1M tokens (default 1.50 / 9.00 = gemini-3.5-flash
                          standard tier, Nov 2026; edit when the price sheet changes)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    llm_mode: str = "mock"
    model: str = "gemini-3.5-flash"
    judge_model: str = "gemini-3.5-flash"
    api_key: str = ""
    rpm: int = 10
    temperature: float = 0.0
    repo_github: str = "seL4/seL4"
    repo_path: Path = ROOT / "data" / "repos" / "seL4__seL4"
    corpus_dir: Path = ROOT / "data" / "corpus" / "seL4__seL4"
    work_dir: Path = ROOT / "results" / "readmind"
    max_commits: int = 20000
    mcp_transport: str = "http"
    mcp_repo_port: int = 8201
    mcp_analytics_port: int = 8202
    a2a_history_port: int = 8101
    a2a_crew_port: int = 8102
    host: str = "127.0.0.1"
    price_in_per_m: float = 1.50
    price_out_per_m: float = 9.00
    max_retries_on_quota: int = 4
    tool_result_limit: int = 6
    extra: dict = field(default_factory=dict)

    @property
    def repo_slug(self) -> str:
        return self.repo_github.replace("/", "__")

    @property
    def is_mock(self) -> bool:
        return self.llm_mode == "mock"

    @property
    def crew_model(self) -> str:
        return self.model if "/" in self.model else f"gemini/{self.model}"


def _from_env() -> Settings:
    key = _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")
    mode = _env("READMIND_LLM", "gemini" if key else "mock").lower()
    if mode not in ("gemini", "mock"):
        mode = "mock"
    if mode == "gemini" and not key:
        raise RuntimeError("READMIND_LLM=gemini but no GEMINI_API_KEY / GOOGLE_API_KEY is set")
    model = _env("READMIND_MODEL", "gemini-3.5-flash")
    if key:
        os.environ.setdefault("GOOGLE_API_KEY", key)       # ADK reads GOOGLE_API_KEY
        os.environ.setdefault("GEMINI_API_KEY", key)       # CrewAI's Gemini provider reads GEMINI_API_KEY
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")
    os.environ.setdefault("GOOGLE_GENAI_USE_ENTERPRISE", "FALSE")
    os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")   # no data leaves the run except LLM calls
    os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
    os.environ.setdefault("OTEL_SDK_DISABLED", "false")
    return Settings(
        llm_mode=mode,
        model=model,
        judge_model=_env("READMIND_JUDGE_MODEL", model),
        api_key=key,
        rpm=int(_env("READMIND_RPM", "10" if mode == "gemini" else "100000")),
        repo_github=_env("READMIND_REPO", "seL4/seL4"),
        repo_path=Path(_env("READMIND_REPO_PATH", str(ROOT / "data" / "repos" / "seL4__seL4"))),
        corpus_dir=Path(_env("READMIND_CORPUS_DIR", str(ROOT / "data" / "corpus" / "seL4__seL4"))),
        work_dir=Path(_env("READMIND_WORK_DIR", str(ROOT / "results" / "readmind"))),
        max_commits=int(_env("READMIND_MAX_COMMITS", "20000")),
        mcp_transport=_env("READMIND_MCP_TRANSPORT", "http"),
        mcp_repo_port=int(_env("READMIND_MCP_REPO_PORT", "8201")),
        mcp_analytics_port=int(_env("READMIND_MCP_ANALYTICS_PORT", "8202")),
        a2a_history_port=int(_env("READMIND_A2A_HISTORY_PORT", "8101")),
        a2a_crew_port=int(_env("READMIND_A2A_CREW_PORT", "8102")),
        price_in_per_m=float(_env("READMIND_PRICE_IN", "1.50")),
        price_out_per_m=float(_env("READMIND_PRICE_OUT", "9.00")),
    )


_SETTINGS: Settings | None = None


def get_settings() -> Settings:
    global _SETTINGS
    if _SETTINGS is None:
        _SETTINGS = _from_env()
    return _SETTINGS


def configure(**overrides) -> Settings:
    """Override settings for this process (tests, notebook). Also exported to env for subprocesses."""
    global _SETTINGS
    s = replace(get_settings(), **overrides)
    _SETTINGS = s
    env = {
        "READMIND_LLM": s.llm_mode, "READMIND_MODEL": s.model, "READMIND_REPO": s.repo_github,
        "READMIND_REPO_PATH": str(s.repo_path), "READMIND_CORPUS_DIR": str(s.corpus_dir),
        "READMIND_WORK_DIR": str(s.work_dir), "READMIND_MAX_COMMITS": str(s.max_commits),
        "READMIND_MCP_TRANSPORT": s.mcp_transport, "READMIND_RPM": str(s.rpm),
    }
    os.environ.update(env)
    return s


def reset() -> None:
    global _SETTINGS
    _SETTINGS = None
