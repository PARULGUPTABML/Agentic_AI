"""Validator for evaluation question set.

Checks schema conformity, unique IDs, valid enum values, citation formats,
and ensures no overlap between dev and heldout splits.
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path
from typing import Tuple, List, Set, Dict, Any

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
    "notes": str
}

ALLOWED_SPLITS = {"dev", "heldout"}
ALLOWED_QUESTION_TYPES = {"rationale", "timeline", "authorship", "superseded_implementation"}
ALLOWED_DIFFICULTIES = {"easy", "medium", "hard"}
ALLOWED_INTENTS = {"rationale", "timeline", "authorship", "superseded_implementation", "other"}
ALLOWED_AGENTS = {"code_analysis", "history_rag", "issue_pr"}

EVIDENCE_REGEX = re.compile(r"^(commit:[a-f0-9]+|pr:\d+|issue:\d+|review:\d+|code:[\w\./\-]+(:\d+(-\d+)?)?|doc:[\w\./\-]+)$")


def validate_file(file_path: str | Path) -> Tuple[bool, List[str]]:
    path = Path(file_path)
    if not path.exists():
        return False, [f"File not found: {path}"]

    errors = []
    seen_qids: Set[str] = set()
    dev_questions: Set[str] = set()
    heldout_questions: Set[str] = set()

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

            q_text = record.get("question", "").strip().lower()
            if split == "dev":
                dev_questions.add(q_text)
            elif split == "heldout":
                heldout_questions.add(q_text)

    # Check for leakage between dev and heldout
    leakage = dev_questions.intersection(heldout_questions)
    if leakage:
        for leaked in leakage:
            errors.append(f"Leakage detected: Question appears in both dev and heldout splits: '{leaked}'")

    is_valid = len(errors) == 0
    return is_valid, errors


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "parul_aggarwal/eval_set/questions.jsonl"
    print(f"Validating dataset at: {target}")
    valid, errs = validate_file(target)
    if valid:
        print("Dataset validation passed! 100% compliant with schema.")
        sys.exit(0)
    else:
        print(f"Validation FAILED with {len(errs)} error(s):")
        for err in errs:
            print(f"  - {err}")
        sys.exit(1)
