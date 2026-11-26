"""Deploy ReadMind to a Hugging Face Space (Docker SDK) — runnable from Kaggle or any machine.

    HF_TOKEN=hf_... python deploy/hf_space.py --space <username>/readmind [--gemini-key $GEMINI_API_KEY]

Uploads the project (without data/results), adds the Space README header that selects the Docker
SDK on port 7860, and stores GEMINI_API_KEY as a Space *secret* (never in the files). The Space then
builds the Dockerfile in Hugging Face's cloud and serves the API + web UI at
https://<username>-readmind.hf.space
"""
from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADER = """---
title: ReadMind
emoji: 🧭
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
short_description: Repository-history agents (Google ADK + CrewAI, MCP, A2A)
---

"""
IGNORE = shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "results", "repos", "cache", "venv", ".venv",
                                "*.pyc", "*.ipynb_checkpoints", "static/chart.umd.js")


def main(argv=None) -> str:
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", required=True, help="<hf-username>/<space-name>")
    ap.add_argument("--gemini-key", default=os.environ.get("GEMINI_API_KEY", ""))
    ap.add_argument("--model", default=os.environ.get("READMIND_MODEL", "gemini-3.5-flash"))
    ap.add_argument("--private", action="store_true")
    a = ap.parse_args(argv)
    from huggingface_hub import HfApi
    token = os.environ.get("HF_TOKEN")
    if not token:
        raise SystemExit("HF_TOKEN is not set")
    api = HfApi(token=token)
    api.create_repo(a.space, repo_type="space", space_sdk="docker", private=a.private, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        dst = Path(tmp) / "space"
        shutil.copytree(ROOT, dst, ignore=IGNORE)
        readme = dst / "README.md"
        readme.write_text(HEADER + readme.read_text(encoding="utf-8"), encoding="utf-8")
        api.upload_folder(folder_path=str(dst), repo_id=a.space, repo_type="space",
                          commit_message="Deploy ReadMind (ADK + CrewAI)")
    if a.gemini_key:
        api.add_space_secret(a.space, "GEMINI_API_KEY", a.gemini_key)
    for k, v in {"READMIND_LLM": "gemini" if a.gemini_key else "mock", "READMIND_MODEL": a.model, "PORT": "7860"}.items():
        api.add_space_variable(a.space, k, v)
    owner, name = a.space.split("/", 1)
    url = f"https://{owner}-{name}.hf.space".lower()
    print(f"Space: https://huggingface.co/spaces/{a.space}\nApp (after the build finishes): {url}")
    return url


if __name__ == "__main__":
    main()
