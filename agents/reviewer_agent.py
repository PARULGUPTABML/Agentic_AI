"""Agent 6: Reviewer / Final Synthesis Agent.

Synthesizes gatekeeper-approved evidence fragments from the blackboard into a structured,
directly grounded developer answer. Enforces strict inline citation checking against
retrieved repository evidence and honestly reports any remaining knowledge gaps.
"""
from __future__ import annotations

# Re-export core Reviewer classes so this file serves as the canonical Agent 6 module
from reviewer.agent import ReviewerAgent, PROMPT_PATH, ENTAILMENT_PROMPT_PATH
from reviewer.citation_checker import check_citations, extract_citations
from reviewer.renderer import render_template

__all__ = [
    "ReviewerAgent",
    "check_citations",
    "extract_citations",
    "render_template",
    "PROMPT_PATH",
    "ENTAILMENT_PROMPT_PATH",
]
