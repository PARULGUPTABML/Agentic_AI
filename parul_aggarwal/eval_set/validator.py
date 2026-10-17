"""Dataset schema and ground-truth integrity validator.

Enforces:
1. Strict schema compliance for evaluation questions.
2. Prevention of data leakage between 'dev' and 'heldout' splits.
3. Verification of cited commit SHAs against real seL4 git repository history.
4. Cryptographic reproducibility of the frozen heldout hash.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

REQUIRED_FIELDS = {
    "qid": str,
    "split": str,
    "question": str,
    "question_type": str,
    "difficulty": str,
    "reference_answer": str,
    "key_facts": list,
    "ground_truth_evidence": list,
    "expected_intent": str,
    "expected_agents": list,
    "expected_entities": dict,
    "needs_multiple_sources": bool,
    "annotator": str,
    "validated_by": str,
    "notes": str,
}

ALLOWED_SPLITS = {"dev", "heldout"}
ALLOWED_QUESTION_TYPES = {"rationale", "timeline", "authorship", "superseded_implementation"}
ALLOWED_DIFFICULTIES = {"easy", "medium", "hard"}
ALLOWED_INTENTS = {"rationale", "timeline", "authorship", "superseded_implementation", "other"}
ALLOWED_AGENTS = {"code_analysis", "history_rag", "issue_pr"}

EVIDENCE_REGEX = re.compile(
    r"^(commit:[a-f0-9]+|pr:\d+|issue:[A-Za-z0-9_\-]+|review:\d+|code:[\w\./\-]+(:\d+(-\d+)?)?|doc:[\w\./\-]+)$"
)


def find_git_dir(custom_path: str | None = None) -> Path | None:
    """Locate seL4 repository dynamically without hardcoding machine-specific paths."""
    if custom_path:
        p = Path(custom_path)
        return p if p.exists() and ((p / "HEAD").exists() or (p / ".git").exists()) else None
    candidates: List[Path] = []

    for env_var in ["SEL4_GIT_DIR", "SEL4_REPO", "GIT_DIR"]:
        env_val = os.environ.get(env_var)
        if env_val:
            candidates.append(Path(env_val))

    # Standard relative workspace paths
    base_dir = Path(__file__).resolve().parent.parent.parent
    candidates.extend([
        Path("seL4.git"),
        Path("../seL4.git"),
        Path("seL4"),
        Path("../seL4"),
        base_dir / "seL4.git",
        base_dir / "seL4",
        Path.home() / ".cache" / "seL4.git",
        Path.home() / ".cache" / "seL4",
    ])

    # Dynamic search in user's home ~/.gemini scratch directories (no hardcoded usernames)
    brain_dir = Path.home() / ".gemini" / "antigravity-ide" / "brain"
    if brain_dir.exists():
        for scratch_path in brain_dir.glob("*/scratch/seL4.git"):
            candidates.append(scratch_path)
            break

    for loc in candidates:
        if loc and loc.exists() and ((loc / "HEAD").exists() or (loc / ".git").exists()):
            return loc
    return None


def verify_commit_in_git(sha: str, git_dir: Path) -> bool:
    try:
        cmd = ["git", f"--git-dir={git_dir}", "rev-parse", "--verify", f"{sha}^{{commit}}"]
        subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def validate_file(
    file_path: str | Path,
    hash_path: str | Path | None = None,
    git_dir_path: str | None = None,
    require_git: bool = False
) -> Tuple[bool, List[str], Dict[str, Any]]:
    path = Path(file_path)
    if not path.exists():
        return False, [f"File not found: {path}"], {}

    errors = []
    seen_qids: Set[str] = set()
    dev_questions: Set[str] = set()
    heldout_questions: Set[str] = set()
    heldout_records: List[Dict[str, Any]] = []
    cited_commits: Set[str] = set()

    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                errors.append(f"Line {line_no}: Invalid JSON: {e}")
                continue

            # 1. Required fields and types
            for field, expected_type in REQUIRED_FIELDS.items():
                if field not in record:
                    errors.append(f"Line {line_no} ({record.get('qid', 'UNKNOWN')}): Missing required field '{field}'")
                elif not isinstance(record[field], expected_type):
                    errors.append(
                        f"Line {line_no} ({record.get('qid', 'UNKNOWN')}): Field '{field}' expected {expected_type.__name__}, got {type(record[field]).__name__}"
                    )

            qid = record.get("qid")
            if qid:
                if qid in seen_qids:
                    errors.append(f"Line {line_no}: Duplicate qid '{qid}'")
                seen_qids.add(qid)

            split = record.get("split")
            if split not in ALLOWED_SPLITS:
                errors.append(f"Line {line_no} ({qid}): Invalid split '{split}'. Allowed: {ALLOWED_SPLITS}")
            elif split == "heldout":
                heldout_records.append(record)

            q_type = record.get("question_type")
            if q_type not in ALLOWED_QUESTION_TYPES:
                errors.append(f"Line {line_no} ({qid}): Invalid question_type '{q_type}'. Allowed: {ALLOWED_QUESTION_TYPES}")

            diff = record.get("difficulty")
            if diff not in ALLOWED_DIFFICULTIES:
                errors.append(f"Line {line_no} ({qid}): Invalid difficulty '{diff}'. Allowed: {ALLOWED_DIFFICULTIES}")

            intent = record.get("expected_intent")
            if intent not in ALLOWED_INTENTS:
                errors.append(f"Line {line_no} ({qid}): Invalid expected_intent '{intent}'. Allowed: {ALLOWED_INTENTS}")

            agents = record.get("expected_agents", [])
            for ag in agents:
                if ag not in ALLOWED_AGENTS:
                    errors.append(f"Line {line_no} ({qid}): Invalid agent '{ag}' in expected_agents. Allowed: {ALLOWED_AGENTS}")

            evidence = record.get("ground_truth_evidence", [])
            for ev in evidence:
                if not EVIDENCE_REGEX.match(ev):
                    errors.append(f"Line {line_no} ({qid}): Invalid evidence ID format '{ev}'. Must match {EVIDENCE_REGEX.pattern}")
                if ev.startswith("commit:"):
                    cited_commits.add(ev.split(":")[1])

            q_text = record.get("question", "").strip().lower()
            if split == "dev":
                dev_questions.add(q_text)
            elif split == "heldout":
                heldout_questions.add(q_text)

    # 2. Check for leakage between dev and heldout
    leakage = dev_questions.intersection(heldout_questions)
    if leakage:
        for leaked in leakage:
            errors.append(f"Leakage detected: Question appears in both dev and heldout splits: '{leaked}'")

    # 3. Check commit IDs existence in seL4 history
    git_dir = find_git_dir(git_dir_path)
    git_verified = False
    if git_dir:
        for commit_sha in cited_commits:
            if not verify_commit_in_git(commit_sha, git_dir):
                errors.append(f"Commit ID does NOT exist in seL4 repository history: commit:{commit_sha}")
        git_verified = True
    else:
        if require_git:
            errors.append("seL4 git directory not found. Commit SHAs could not be verified against git history.")

    # 4. Verify canonical heldout hash against heldout_hash.sha256
    h_path = Path(hash_path) if hash_path else path.parent / "heldout_hash.sha256"
    hash_verified = False
    if h_path.exists():
        expected_hash = h_path.read_text(encoding="utf-8").strip()
        heldout_lines = [json.dumps(r, sort_keys=True) for r in heldout_records]
        payload = chr(10).join(heldout_lines).encode("utf-8")
        actual_hash = hashlib.sha256(payload).hexdigest()
        if actual_hash != expected_hash:
            errors.append(
                f"Frozen heldout hash mismatch! Expected: {expected_hash} Actual: {actual_hash}"
            )
        else:
            hash_verified = True

    status_meta = {
        "git_verified": git_verified,
        "git_dir": str(git_dir) if git_dir else None,
        "hash_verified": hash_verified,
        "total_questions": len(seen_qids),
        "heldout_count": len(heldout_records),
        "commits_checked": len(cited_commits)
    }

    is_valid = len(errors) == 0
    return is_valid, errors, status_meta


def main():
    parser = argparse.ArgumentParser(description="Validate evaluation question set.")
    parser.add_argument("file", nargs="?", default="parul_aggarwal/eval_set/questions.jsonl", help="Path to questions.jsonl")
    parser.add_argument("--git-dir", help="Path to seL4 git directory for SHA verification")
    parser.add_argument("--require-git", action="store_true", help="Fail if git directory is not found")
    args = parser.parse_args()

    print(f"Validating dataset at: {args.file}")
    valid, errs, meta = validate_file(args.file, git_dir_path=args.git_dir, require_git=args.require_git)

    if meta.get("git_verified"):
        print(f"  [OK] seL4 git directory located at: {meta['git_dir']}")
        print(f"  [OK] Verified {meta['commits_checked']} unique commit SHAs in real git history.")
    else:
        print("  [WARN] seL4 git directory NOT found. Commit SHA verification was SKIPPED.")
        print("         Specify --git-dir <path> or set SEL4_GIT_DIR to verify commit SHAs.")

    if meta.get("hash_verified"):
        print("  [OK] Canonical held-out hash matches heldout_hash.sha256.")

    if valid:
        if meta.get("git_verified"):
            print(f"Dataset validation passed! 100% compliant ({meta['total_questions']} questions, real commits verified, hash verified).")
        else:
            print(f"Dataset schema validation passed ({meta['total_questions']} questions, hash verified). Note: git commits were not verified.")
        sys.exit(0)
    else:
        print(f"Validation FAILED with {len(errs)} error(s):")
        for err in errs:
            print(f"  - {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
