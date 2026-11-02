"""Central configuration. All values can be overridden with environment variables (.env)."""
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR.parent / ".env")
load_dotenv(BASE_DIR / ".env")

# ---------- storage ----------
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
REPOS_DIR = DATA_DIR / "repos"    # git clones
RAW_DIR = DATA_DIR / "raw"        # extracted JSON (commits, issues, PRs)
INDEX_DIR = DATA_DIR / "index"    # chunks, graph, vector DB per repo
EVAL_DIR = DATA_DIR / "eval"      # benchmark + results
for _d in (REPOS_DIR, RAW_DIR, INDEX_DIR, EVAL_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------- external services ----------
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")          # gemini | openai | ollama | mock
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3.5-flash")
JUDGE_PROVIDER = os.getenv("JUDGE_PROVIDER", LLM_PROVIDER)  # use a different/stronger model if you can
JUDGE_MODEL = os.getenv("JUDGE_MODEL", LLM_MODEL)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

# ---------- retrieval ----------
# "hash" = tiny offline embedder for tests/CI. Real runs: BAAI/bge-small-en-v1.5 or bge-base.
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
USE_RERANKER = os.getenv("USE_RERANKER", "true").lower() == "true"
RERANK_MODEL = os.getenv("RERANK_MODEL", "BAAI/bge-reranker-base")
TOP_K_CANDIDATES = int(os.getenv("TOP_K_CANDIDATES", "30"))
TOP_K_FINAL = int(os.getenv("TOP_K_FINAL", "8"))
# In the full-history condition, reserve at least this share of final slots for history evidence
HISTORY_QUOTA = float(os.getenv("HISTORY_QUOTA", "0.5"))

CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")

# ---------- Sourcegraph (semantic code search + code navigation) ----------
SOURCEGRAPH_URL = os.getenv("SOURCEGRAPH_URL", "http://localhost:7080")
SOURCEGRAPH_TOKEN = os.getenv("SOURCEGRAPH_TOKEN", "")

REPOS_FILE = Path(os.getenv("REPOS_FILE", BASE_DIR / "repos.yaml"))


def load_repos():
    """Read repos.yaml and return a list of repo dicts with owner/name/slug filled in."""
    with open(REPOS_FILE, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    defaults = cfg.get("defaults", {})
    repos = []
    for entry in cfg.get("repos", []):
        gh = entry["github"].strip()
        if "OWNER" in gh or "/" not in gh:
            continue  # unfilled placeholder
        owner, name = gh.split("/", 1)
        repos.append({
            **defaults,
            **entry,
            "owner": owner,
            "name": name,
            "slug": f"{owner}__{name}",
            "url": entry.get("url", f"https://github.com/{gh}.git"),
        })
    return repos


def get_repo(slug):
    for r in load_repos():
        if r["slug"] == slug:
            return r
    raise KeyError(f"Repo '{slug}' not found in {REPOS_FILE}")
