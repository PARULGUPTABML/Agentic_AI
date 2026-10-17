"""Output template renderer for Reviewer / Final Response Agent."""
from __future__ import annotations

from typing import Any, Dict, List


def render_template(
    answer: str,
    claims: List[Dict[str, Any]],
    inference: str,
    confidence: str,
    gaps: List[str]
) -> str:
    """Render the standard human-readable Reviewer output template."""
    lines = []
    lines.append("Answer")
    lines.append(answer.strip())
    lines.append("")

    lines.append("Supporting Evidence")
    evidence_claims = [c for c in claims if c.get("type") == "evidence"]
    if evidence_claims:
        for c in evidence_claims:
            cites = ", ".join(c.get("citations", []))
            lines.append(f"- {c['text']} [{cites}]")
    else:
        lines.append("- No direct factual evidence available in corpus.")
    lines.append("")

    lines.append("Inference (not directly stated in the evidence)")
    inference_claims = [c for c in claims if c.get("type") == "inference"]
    if inference and inference.strip().lower() != "none":
        lines.append(f"- {inference.strip()}")
    elif inference_claims:
        for c in inference_claims:
            lines.append(f"- {c['text']}")
    else:
        lines.append("- None")
    lines.append("")

    lines.append("Gaps and Confidence")
    lines.append(f"- Confidence: {confidence.lower()}")
    if gaps:
        gap_str = "; ".join(gaps)
        lines.append(f"- Not found in evidence: {gap_str}")
    else:
        lines.append("- Not found in evidence: none")

    return "\n".join(lines)
