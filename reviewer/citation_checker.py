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


def sanitize_text_artifacts(text: str, removed_ids: List[str]) -> str:
    """Strip removed citation tags and cleanup stray punctuation or hanging clauses."""
    for rid in removed_ids:
        text = re.sub(rf"\[\s*{re.escape(rid)}\s*\]", "", text)
        text = re.sub(rf"{re.escape(rid)}", "", text)
    # Remove hanging prepositions and conjunctions before end of sentence
    text = re.sub(r",?\s*(?:but|and|or)(?:\s+also)?\s*\.", ".", text, flags=re.I)
    text = re.sub(r"(?:by|at|in|for|from|with|affected\s+by)\s*\.", ".", text, flags=re.I)
    text = re.sub(r",\s*,+", ",", text)
    text = re.sub(r",\s*\.", ".", text)
    text = re.sub(r"\s+([.,;:!?])", r"", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


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

        # Strip invalid citations from the claim text itself
        text = clean_claim.get("text", "")
        if claim.get("citations"):
            invalid_in_claim = [c for c in claim.get("citations", []) if c not in valid_evidence_ids]
            if invalid_in_claim:
                text = sanitize_text_artifacts(text, invalid_in_claim)
        clean_claim["text"] = text

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
