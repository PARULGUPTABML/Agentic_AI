"""Scoring rubric for the LLM-as-Judge (0–10 composite).

| Dimension      | Points | Definition                                                                 |
|----------------|--------|----------------------------------------------------------------------------|
| Accuracy       | 4      | correct = 4, partial = 2, incorrect = 0                                     |
| Completeness   | 3      | 3 x (key facts covered / key facts); correct abstention on unanswerable = 3 |
| Groundedness   | 2      | 2 if no hallucination; 1 if only unsupported (not contradicting) claims;   |
|                |        | 0 if a contradiction or a fabricated identifier                            |
| Separation     | 1      | evidence vs. inference clearly separated (or no inference made)            |

Hallucination (binary, used for the hallucination *rate*):
    any contradiction OR any fabricated identifier (commit SHA / PR / issue / file
    cited that is in neither the retrieved evidence, the gold evidence nor the
    reference answer).  Unsupported-but-not-contradicting claims are reported
    separately (``unsupported_claims``) so the strict and lenient rates can both
    be shown.
"""
from __future__ import annotations

from typing import Any

ACCURACY_POINTS = {"correct": 4.0, "partial": 2.0, "incorrect": 0.0}
ACCURACY_VALUE = {"correct": 1.0, "partial": 0.5, "incorrect": 0.0}
MAX_SCORE = 10.0

RUBRIC = {
    "accuracy": {"points": 4, "levels": ACCURACY_POINTS},
    "completeness": {"points": 3},
    "groundedness": {"points": 2},
    "separation": {"points": 1},
}


def groundedness_points(contradictions: list, fabricated_ids: list, unsupported: list) -> float:
    if contradictions or fabricated_ids:
        return 0.0
    if unsupported:
        return 1.0
    return 2.0


def composite_score(verdict: dict[str, Any]) -> float:
    acc = ACCURACY_POINTS.get(verdict.get("accuracy_label", "incorrect"), 0.0)
    comp = 3.0 * float(verdict.get("completeness", 0.0))
    grounded = groundedness_points(verdict.get("contradictions", []), verdict.get("fabricated_ids", []),
                                   verdict.get("unsupported_claims", []))
    sep = 1.0 if verdict.get("separates_evidence_and_inference") else 0.0
    return round(acc + comp + grounded + sep, 2)
