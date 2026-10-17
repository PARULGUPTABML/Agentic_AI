"""Reviewer / Final Response Agent implementation.

Synthesizes approved evidence into a grounded explanation, enforces strict
citation checking, separates inferences from facts, and reports gaps and confidence.
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from parul_aggarwal.reviewer.citation_checker import check_citations, extract_citations
from parul_aggarwal.reviewer.renderer import render_template

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "reviewer_system.md"
ENTAILMENT_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "reviewer_entailment.md"


class ReviewerAgent:
    def __init__(self, prompt_path: Optional[Path] = None, enable_entailment_check: bool = False):
        self.prompt_path = prompt_path or PROMPT_PATH
        self.system_prompt = self._load_prompt(self.prompt_path)
        self.enable_entailment_check = enable_entailment_check

    def _load_prompt(self, path: Path) -> str:
        if path.exists():
            return path.read_text(encoding="utf-8")
        return "Reviewer Agent: Synthesize evidence into a cited, factual response with explicit inference separation."

    def review(
        self,
        input_data: Dict[str, Any],
        blackboard: Any = None
    ) -> Dict[str, Any]:
        """Execute the reviewer workflow on input data.

        Args:
            input_data: Dict with keys: run_id, question, evidence, incomplete (optional), gap_notes (optional)
            blackboard: Optional Blackboard instance to update
        """
        start_time = time.time()
        run_id = input_data.get("run_id", "run-unknown")
        question = input_data.get("question", "")
        raw_evidence = input_data.get("evidence", [])
        incomplete = input_data.get("incomplete", False)
        gap_notes = list(input_data.get("gap_notes", []))

        # 1. Intake: Filter out malformed evidence items missing an ID
        valid_evidence: List[Dict[str, Any]] = []
        invalid_evidence_dropped = 0
        valid_evidence_ids: Set[str] = set()

        for item in raw_evidence:
            eid = item.get("id")
            if not eid:
                invalid_evidence_dropped += 1
                logger.warning("Dropped evidence item without ID: %s", item)
                continue
            valid_evidence.append(item)
            valid_evidence_ids.add(eid)

        # 2. Check for empty evidence
        if not valid_evidence:
            gaps = ["No relevant evidence found in repository history"]
            if incomplete:
                gaps.append("Pipeline reached maximum re-plan rounds without finding evidence")
            if gap_notes:
                gaps.extend(gap_notes)

            ans_text = f"Based on the repository history, there is no evidence to answer: '{question}'."
            rendered = render_template(
                answer=ans_text,
                claims=[],
                inference="No inferences can be drawn without supporting evidence.",
                confidence="low",
                gaps=gaps
            )
            result = {
                "run_id": run_id,
                "answer": ans_text,
                "claims": [],
                "inference": "No inferences can be drawn without supporting evidence.",
                "gaps": gaps,
                "confidence": "low",
                "citation_check": {"valid": 0, "invalid": 0, "removed": []},
                "rendered": rendered,
                "incomplete": incomplete
            }
            self._update_blackboard(blackboard, result, time.time() - start_time)
            return result

        # 3. Grounded Drafting & Claim Construction
        claims: List[Dict[str, Any]] = []
        answer_parts: List[str] = []

        # Sort evidence by confidence
        sorted_ev = sorted(valid_evidence, key=lambda x: x.get("confidence", 0.0), reverse=True)

        for ev in sorted_ev[:4]:  # Top relevant fragments
            eid = ev["id"]
            title = ev.get("title", "")
            snippet = ev.get("text") or ev.get("content") or ev.get("snippet") or ""
            # Clean snippet to one key statement
            first_sentence = snippet.strip().split("\n")[0].strip()
            if len(first_sentence) > 160:
                first_sentence = first_sentence[:157] + "..."

            claim_text = f"{title}: {first_sentence}" if title and title not in first_sentence else first_sentence
            claims.append({
                "text": claim_text,
                "type": "evidence",
                "citations": [eid]
            })
            answer_parts.append(f"{claim_text} [{eid}]")

        # Deduce inference from evidence if applicable
        inference = ""
        if len(valid_evidence) >= 2:
            inference = "The structural updates across these commits suggest a deliberate architectural consolidation."
            claims.append({
                "text": inference,
                "type": "inference",
                "citations": []
            })

        # 4. Programmatic Citation Checker
        sanitized_claims, citation_stats = check_citations(claims, valid_evidence_ids)

        # 5. Evaluate Gaps & Confidence
        gaps = list(gap_notes)
        if incomplete:
            gaps.append("Retrieval terminated early at re-planning limit; some secondary details may be missing")

        if incomplete or citation_stats["invalid"] > 0:
            confidence = "medium" if len(valid_evidence) >= 2 else "low"
        elif len(valid_evidence) >= 2 and all(ev.get("confidence", 0.0) >= 0.7 for ev in valid_evidence[:2]):
            confidence = "high"
        else:
            confidence = "medium"

        # Combine answer text
        joined_answer = " ".join(answer_parts)
        if incomplete:
            joined_answer += " Note: evidence retrieval was incomplete."

        rendered = render_template(
            answer=joined_answer,
            claims=sanitized_claims,
            inference=inference,
            confidence=confidence,
            gaps=gaps
        )

        output = {
            "run_id": run_id,
            "answer": joined_answer,
            "claims": sanitized_claims,
            "inference": inference or "None",
            "gaps": gaps,
            "confidence": confidence,
            "citation_check": citation_stats,
            "rendered": rendered,
            "incomplete": incomplete
        }

        self._update_blackboard(blackboard, output, time.time() - start_time)
        return output

    def _update_blackboard(self, blackboard: Any, output: Dict[str, Any], duration_s: float) -> None:
        if blackboard is None:
            return
        if hasattr(blackboard, "final_answer"):
            blackboard.final_answer = output["rendered"]
        if hasattr(blackboard, "set_status"):
            try:
                from agents.blackboard import BlackboardStatus
                blackboard.set_status(BlackboardStatus.DONE)
            except Exception:
                pass
        if hasattr(blackboard, "log"):
            blackboard.log(
                agent="reviewer",
                action="final_response",
                detail=f"Confidence: {output['confidence']}, Valid citations: {output['citation_check']['valid']}, Gaps: {len(output['gaps'])}",
                duration_ms=round(duration_s * 1000, 1),
                tokens_used=len(output["rendered"]) // 4
            )
