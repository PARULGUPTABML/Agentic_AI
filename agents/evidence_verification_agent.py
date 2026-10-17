"""Evidence Verification Agent — Chain-of-Thought sufficiency check.

After the retrieval agents have posted their evidence to the Blackboard,
this agent uses Chain-of-Thought (CoT) prompting to reason step-by-step
about whether the collected evidence is sufficient to answer the
developer's question.  If the evidence has gaps, the agent produces
refined queries so the retrieval agents can re-gather.

References:
    [3] J. Wei et al., "Chain-of-Thought Prompting Elicits Reasoning
        in Large Language Models," NeurIPS, 2022.
"""
from __future__ import annotations

import json
import time
from typing import Any

from agents.blackboard import Blackboard, BlackboardStatus, EvidenceFragment
from agents._llm_retry import generate_with_retry as generate
from rag.llm import parse_json

# ------------------------------------------------------------------ #
#  Prompts                                                            #
# ------------------------------------------------------------------ #

COT_SYSTEM_PROMPT = """You are the Evidence Verification Agent in a multi-agent system that explains software design decisions.

Your job is to carefully examine the collected evidence and determine whether it is SUFFICIENT to answer the developer's question.

You MUST think step-by-step using Chain-of-Thought reasoning. For each step, explicitly check one aspect of the evidence.

Think about these dimensions:
1. WHAT — Does the evidence explain what changed in the code? (files, functions, specific code changes)
2. WHY — Does the evidence explain the motivation or reasoning behind the change? (issues, discussions, PR descriptions)
3. WHO/WHEN — Does the evidence identify who made the change and when? (commit authors, dates, PR timelines)
4. CONTEXT — Does the evidence provide broader context? (related issues, prior attempts, rejected alternatives)
5. COMPLETENESS — Are there obvious gaps? Is there a commit but no associated issue/PR? Is there an issue but no resolution?

After your reasoning, output a JSON verdict with this exact format:
```json
{
    "sufficient": true/false,
    "confidence": 0.0-1.0,
    "reasoning": "Your overall assessment in 2-3 sentences",
    "covered_aspects": ["what", "why", ...],
    "gaps": ["description of gap 1", "description of gap 2"],
    "retry_queries": ["refined search query 1", "refined search query 2"]
}
```

RULES:
- retry_queries should be empty if sufficient is true.
- retry_queries should contain 1-3 specific, targeted queries to fill the identified gaps.
- Be strict: if the evidence only shows WHAT changed but not WHY, mark as insufficient.
- If there are at least 2 complementary evidence types (e.g. commit + issue, or PR + review), that's a good sign.
"""

COT_USER_PROMPT = """Developer's Question:
{question}

Repository: {repo}

Evidence collected ({count} pieces):

{evidence_text}

---

Now reason step-by-step about whether this evidence is sufficient to answer the question.
Then output your JSON verdict."""


# ------------------------------------------------------------------ #
#  Evidence formatting                                                #
# ------------------------------------------------------------------ #

def _format_evidence_for_verification(fragments: list[EvidenceFragment]) -> str:
    """Format Blackboard evidence into a readable block for the LLM."""
    if not fragments:
        return "(No evidence collected)"

    blocks = []
    for i, frag in enumerate(fragments, 1):
        date_str = f" | {frag.date[:10]}" if frag.date else ""
        header = (
            f"[{i}] ID: {frag.id}\n"
            f"    Type: {frag.evidence_type} | Agent: {frag.source_agent} | "
            f"Confidence: {frag.confidence:.2f}{date_str}"
        )
        # Trim content to keep within context limits
        content = frag.content[:1500]
        blocks.append(f"{header}\n    Title: {frag.title}\n    Content:\n    {content}")

    return "\n\n---\n\n".join(blocks)


def _count_types(fragments: list[EvidenceFragment]) -> dict[str, int]:
    """Count how many evidence pieces of each type we have."""
    counts: dict[str, int] = {}
    for f in fragments:
        counts[f.evidence_type] = counts.get(f.evidence_type, 0) + 1
    return counts


# ------------------------------------------------------------------ #
#  Main agent class                                                   #
# ------------------------------------------------------------------ #

class EvidenceVerificationAgent:
    """Chain-of-Thought agent that verifies evidence sufficiency.

    Usage::

        agent = EvidenceVerificationAgent()
        result = agent.run(blackboard)
        # result["sufficient"] tells you if evidence is enough
    """

    def __init__(self):
        self.agent_name = "evidence_verification"

    def run(self, blackboard: Blackboard) -> dict[str, Any]:
        """Verify the evidence on the Blackboard using CoT reasoning.

        Returns a dict with:
            - sufficient (bool): whether the evidence answers the question
            - confidence (float): 0.0–1.0
            - reasoning (str): explanation
            - gaps (list[str]): identified gaps
            - retry_queries (list[str]): refined queries for re-retrieval
            - cot_trace (str): full chain-of-thought reasoning
        """
        blackboard.set_status(BlackboardStatus.VERIFYING)
        t_start = time.perf_counter()

        evidence = blackboard.get_evidence()
        type_counts = _count_types(evidence)

        blackboard.log(
            self.agent_name, "start",
            f"Verifying {len(evidence)} evidence fragments: {type_counts}",
        )

        # ── Quick heuristic checks before calling the LLM ──
        # If we have zero evidence, no need to ask the LLM
        if not evidence:
            result = {
                "sufficient": False,
                "confidence": 0.0,
                "reasoning": "No evidence was collected by any retrieval agent.",
                "covered_aspects": [],
                "gaps": ["No evidence at all"],
                "retry_queries": [blackboard.question],
                "cot_trace": "No evidence to verify.",
            }
            blackboard.verification_result = result
            blackboard.log(self.agent_name, "verdict", "INSUFFICIENT: no evidence")
            return result

        # ── Build the CoT prompt ──
        evidence_text = _format_evidence_for_verification(evidence)
        prompt = COT_USER_PROMPT.format(
            question=blackboard.question,
            repo=blackboard.repo_slug.replace("__", "/"),
            count=len(evidence),
            evidence_text=evidence_text,
        )

        # ── Call the LLM with CoT ──
        t_llm = time.perf_counter()
        cot_response = generate(
            COT_SYSTEM_PROMPT,
            prompt,
            temperature=0.0,
        )
        llm_ms = (time.perf_counter() - t_llm) * 1000

        blackboard.log(
            self.agent_name, "cot_reasoning",
            f"CoT response: {cot_response[:500]}",
            duration_ms=llm_ms,
        )

        # ── Parse the verdict ──
        result = self._parse_verdict(cot_response, evidence, type_counts)
        result["cot_trace"] = cot_response

        # ── Post-processing: add heuristic signals ──
        result = self._apply_heuristics(result, evidence, type_counts)

        # ── Store on Blackboard ──
        blackboard.verification_result = result

        total_ms = (time.perf_counter() - t_start) * 1000
        verdict_str = "SUFFICIENT" if result["sufficient"] else "INSUFFICIENT"
        blackboard.log(
            self.agent_name, "verdict",
            f"{verdict_str} (confidence={result['confidence']:.2f}): {result['reasoning'][:200]}",
            duration_ms=total_ms,
        )

        return result

    def _parse_verdict(self, cot_response: str, evidence: list[EvidenceFragment],
                       type_counts: dict[str, int]) -> dict[str, Any]:
        """Extract the structured JSON verdict from the CoT response."""
        try:
            parsed = parse_json(cot_response)
            return {
                "sufficient": bool(parsed.get("sufficient", False)),
                "confidence": float(parsed.get("confidence", 0.5)),
                "reasoning": str(parsed.get("reasoning", "")),
                "covered_aspects": list(parsed.get("covered_aspects", [])),
                "gaps": list(parsed.get("gaps", [])),
                "retry_queries": list(parsed.get("retry_queries", [])),
            }
        except (json.JSONDecodeError, ValueError, TypeError):
            # Fallback: try to infer from the text
            text_lower = cot_response.lower()
            is_sufficient = (
                "sufficient" in text_lower
                and "insufficient" not in text_lower
                and "not sufficient" not in text_lower
            )
            return {
                "sufficient": is_sufficient,
                "confidence": 0.6 if is_sufficient else 0.3,
                "reasoning": "Could not parse structured verdict from CoT response.",
                "covered_aspects": [],
                "gaps": ["Verdict parsing failed — treating as uncertain"],
                "retry_queries": [] if is_sufficient else [evidence[0].title if evidence else ""],
            }

    def _apply_heuristics(self, result: dict[str, Any],
                          evidence: list[EvidenceFragment],
                          type_counts: dict[str, int]) -> dict[str, Any]:
        """Add heuristic signals to complement the LLM's judgement.

        These heuristics catch obvious cases the LLM might miss:
        - Only code evidence, no history → probably insufficient for "why" questions
        - Multiple evidence types → good diversity signal
        - Very low average confidence → retrieval probably missed the target
        """
        history_types = {"commit", "issue", "pr", "review"}
        has_history = bool(set(type_counts.keys()) & history_types)
        type_diversity = len(type_counts)
        avg_confidence = (
            sum(e.confidence for e in evidence) / len(evidence) if evidence else 0.0
        )

        # If LLM says sufficient but we only have code evidence (no history),
        # downgrade for "why" questions
        if result["sufficient"] and not has_history:
            result["sufficient"] = False
            result["confidence"] = min(result["confidence"], 0.4)
            result["gaps"].append(
                "Only code/doc evidence found — no historical context (commits, issues, PRs) "
                "to explain WHY the code is this way."
            )
            if not result["retry_queries"]:
                result["retry_queries"] = ["related commits and issues"]

        # Boost confidence if we have diverse evidence types
        if type_diversity >= 3 and result["sufficient"]:
            result["confidence"] = min(1.0, result["confidence"] + 0.1)

        # Lower confidence if retrieval scores are very low
        if avg_confidence < 0.2:
            result["confidence"] = min(result["confidence"], avg_confidence + 0.2)

        return result
