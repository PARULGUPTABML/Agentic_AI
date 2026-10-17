"""Programmatic citation checker for Reviewer Agent.

Verifies that every cited evidence ID in claims and answer text
strictly corresponds to an approved evidence ID in the input evidence set.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Set, Tuple

# Matches [id], [id1, id2], etc.
CITATION_REGEX = re.compile(r"\[([a-zA-Z0-9_\-\./:,\s]+)\]")


def extract_citations(text: str) -> List[str]:
    """Extract citation IDs from strings like '[commit:ab12cd3]' or '[commit:1, pr:2]'."""
    found = []
    matches = CITATION_REGEX.findall(text)
    for m in matches:
        parts = [p.strip() for p in m.split(",")]
        for p in parts:
            if p:
                found.append(p)
    return found


def check_citations(
    claims: List[Dict[str, Any]],
    valid_evidence_ids: Set[str]
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Validate claim citations against known evidence IDs.

    Returns:
        (sanitized_claims, citation_check_metrics)
    """
    valid_count = 0
    invalid_count = 0
    removed_citations = []
    sanitized_claims = []

    for claim in claims:
        citations = claim.get("citations", [])
        verified_citations = []
        for cite_id in citations:
            if cite_id in valid_evidence_ids:
                verified_citations.append(cite_id)
                valid_count += 1
            else:
                invalid_count += 1
                removed_citations.append(cite_id)

        clean_claim = dict(claim)
        clean_claim["citations"] = verified_citations
        # If all citations were invalid for an evidence-type claim, convert to inference
        if claim.get("type") == "evidence" and not verified_citations:
            clean_claim["type"] = "inference"
            clean_claim["warning"] = "Citations invalid; converted to inference"

        sanitized_claims.append(clean_claim)

    check_stats = {
        "valid": valid_count,
        "invalid": invalid_count,
        "removed": removed_citations
    }
    return sanitized_claims, check_stats
