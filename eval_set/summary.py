"""Summary script for evaluation question set.

Prints counts by split, question type, difficulty, multi-source requirements,
and unanswerable question count.
"""
from __future__ import annotations
import json
import sys
from collections import Counter
from pathlib import Path


def summarize(file_path: str | Path) -> None:
    path = Path(file_path)
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    total = len(records)
    splits = Counter(r["split"] for r in records)
    types = Counter(r["question_type"] for r in records)
    difficulties = Counter(r["difficulty"] for r in records)
    unanswerable = sum(1 for r in records if len(r.get("ground_truth_evidence", [])) == 0)
    multi_source = sum(1 for r in records if r.get("needs_multiple_sources", False))

    print("=" * 60)
    print(" EVALUATION QUESTION SET SUMMARY")
    print("=" * 60)
    print(f"Total Questions: {total}\n")

    print("--- Splits ---")
    for s, cnt in sorted(splits.items()):
        pct = (cnt / total) * 100
        print(f"  {s:<10}: {cnt:2d} ({pct:5.1f}%)")

    print("\n--- Question Types ---")
    for t, cnt in sorted(types.items()):
        pct = (cnt / total) * 100
        print(f"  {t:<25}: {cnt:2d} ({pct:5.1f}%)")

    print("\n--- Difficulties ---")
    for d, cnt in sorted(difficulties.items()):
        pct = (cnt / total) * 100
        print(f"  {d:<10}: {cnt:2d} ({pct:5.1f}%)")

    print("\n--- Diagnostic & Adversarial Properties ---")
    print(f"  Multi-source questions      : {multi_source:2d} ({(multi_source/total)*100:5.1f}%)")
    print(f"  Unanswerable (gap/halluc)   : {unanswerable:2d} ({(unanswerable/total)*100:5.1f}%)")
    print("=" * 60)


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "eval_set/questions.jsonl"
    summarize(target)
