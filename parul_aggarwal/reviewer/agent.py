"""Reviewer / Final Response Agent implementation.

Synthesizes approved evidence into a grounded explanation, enforces strict
citation checking on both LLM and rule-based outputs, separates inferences,
and honestly reports gaps and confidence.
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from parul_aggarwal.reviewer.citation_checker import check_citations, extract_citations
from parul_aggarwal.reviewer.renderer import render_template

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "reviewer_system.md"
ENTAILMENT_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "reviewer_entailment.md"


class ReviewerAgent:
    def __init__(
        self,
        prompt_path: Optional[Path] = None,
        enable_entailment_check: bool = False,
        llm_fn: Optional[Callable[[str, str], str]] = None
    ):
        self.prompt_path = prompt_path or PROMPT_PATH
        self.entailment_prompt_path = ENTAILMENT_PROMPT_PATH
        self.system_prompt = self._load_prompt(self.prompt_path)
        self.entailment_prompt = self._load_prompt(self.entailment_prompt_path)
        self.enable_entailment_check = enable_entailment_check
        self.llm_fn = llm_fn

    def _load_prompt(self, path: Path) -> str:
        if path.exists():
            return path.read_text(encoding="utf-8")
        return "Reviewer Agent: Synthesize evidence into a cited, factual response with explicit inference separation."

    def _generate_llm(self, system: str, prompt: str) -> str:
        if self.llm_fn:
            return self.llm_fn(system, prompt)
        from agents._llm_retry import generate_with_retry
        return generate_with_retry(system, prompt)

    def check_entailment(self, claim: str, evidence_text: str) -> bool:
        """Verify whether the claim is entailed by the cited evidence text."""
        use_llm = bool(self.llm_fn) or os.environ.get("LLM_PROVIDER", "").lower() not in ("mock", "none", "")
        if use_llm:
            try:
                p = self.entailment_prompt.replace("{claim}", claim).replace("{evidence_text}", evidence_text)
                res = self._generate_llm("You are an entailment verification judge. Output YES or NO only.", p)
                return "yes" in res.lower()
            except Exception as e:
                logger.warning("LLM entailment check failed (%s). Falling back to lexical verification.", e)

        # Lexical verification fallback
        claim_words = set(re.findall(r"\b[a-zA-Z]{4,}\b", claim.lower()))
        ev_words = set(re.findall(r"\b[a-zA-Z]{4,}\b", evidence_text.lower()))
        if not claim_words:
            return True
        overlap = len(claim_words.intersection(ev_words)) / len(claim_words)
        return overlap >= 0.35

    def _assess_evidence_relatedness(self, evidence: List[Dict[str, Any]]) -> bool:
        """Check whether evidence items share thematic vocabulary or are completely unrelated."""
        if len(evidence) < 2:
            return True
        word_sets = []
        for e in evidence:
            text = (e.get("title", "") + " " + (e.get("text") or e.get("content") or "")).lower()
            words = set(re.findall(r"\b[a-zA-Z]{4,}\b", text))
            word_sets.append(words)

        shared = word_sets[0].intersection(word_sets[1])
        return len(shared) >= 2

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
        valid_evidence_ids: Set[str] = set()

        for item in raw_evidence:
            eid = item.get("id")
            if not eid:
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
                inference="None",
                confidence="low",
                gaps=gaps
            )
            result = {
                "run_id": run_id,
                "answer": ans_text,
                "claims": [],
                "inference": "None",
                "gaps": gaps,
                "confidence": "low",
                "citation_check": {"valid": 0, "invalid": 0, "removed": []},
                "rendered": rendered,
                "incomplete": incomplete
            }
            self._update_blackboard(blackboard, result, time.time() - start_time)
            return result

        # 3. Grounded Drafting (LLM or Rule-Based)
        use_llm = bool(self.llm_fn) or os.environ.get("LLM_PROVIDER", "").lower() not in ("mock", "none", "")
        answer_text = ""
        claims: List[Dict[str, Any]] = []
        inference_text = "None"

        if use_llm:
            try:
                ev_summary = "\n".join([
                    f"[{e['id']}] ({e.get('title', '')}): {e.get('text', '') or e.get('content', '')}"
                    for e in valid_evidence
                ])
                prompt = (
                    f"Question: {question}\nIncomplete: {incomplete}\n\nEvidence:\n{ev_summary}\n\n"
                    "Provide a synthesized answer citing evidence IDs inline."
                )
                raw_llm = self._generate_llm(self.system_prompt, prompt)
                answer_text = raw_llm.strip()
            except Exception as e:
                logger.warning("Reviewer LLM call failed (%s). Using rule-based synthesizer.", e)

        if answer_text:
            # Parse claims and citations from LLM output
            sentences = re.split(r"(?<=[.!?])\s+", answer_text)
            for s in sentences:
                s_cites = extract_citations(s)
                if s_cites:
                    claims.append({
                        "text": s.strip(),
                        "type": "evidence",
                        "citations": s_cites
                    })
                elif len(s.strip()) > 10:
                    claims.append({
                        "text": s.strip(),
                        "type": "inference",
                        "citations": []
                    })
        else:
            # Rule-based synthesis strictly from evidence
            sorted_ev = sorted(valid_evidence, key=lambda x: x.get("confidence", 0.0), reverse=True)
            fact_statements = []
            for ev in sorted_ev[:3]:
                eid = ev["id"]
                title = ev.get("title", "")
                snippet = ev.get("text") or ev.get("content") or ev.get("snippet") or ""
                first_line = snippet.strip().split("\n")[0].strip()
                if len(first_line) > 140:
                    first_line = first_line[:137] + "..."
                stmt = f"{title}: {first_line}" if title and title not in first_line else first_line
                claims.append({
                    "text": stmt,
                    "type": "evidence",
                    "citations": [eid]
                })
                fact_statements.append(f"{stmt} [{eid}]")

            answer_text = " ".join(fact_statements)
            inference_text = "None"

        # 4. Optional Entailment Spot Check
        if self.enable_entailment_check and claims:
            ev_map = {e["id"]: e.get("text") or e.get("content") or "" for e in valid_evidence}
            sanitized_entailment_claims = []
            for c in claims:
                if c.get("type") == "evidence":
                    c_text = c.get("text", "")
                    c_ids = c.get("citations", [])
                    entailed = True
                    for cid in c_ids:
                        ev_content = ev_map.get(cid, "")
                        if not self.check_entailment(c_text, ev_content):
                            entailed = False
                            break
                    if not entailed:
                        c["type"] = "inference"
                        c["warning"] = "Entailment check failed; classified as inference"
                sanitized_entailment_claims.append(c)
            claims = sanitized_entailment_claims

        # 5. Programmatic Citation Checker (runs on LLM and rule-based outputs!)
        sanitized_claims, citation_stats = check_citations(claims, valid_evidence_ids)

        # Strip invalid citations and cleanup punctuation in answer_text
        from parul_aggarwal.reviewer.citation_checker import sanitize_text_artifacts

        evidence_claims = [c for c in sanitized_claims if c.get("type") == "evidence"]
        if evidence_claims and any(c.get("type") == "inference" for c in sanitized_claims):
            # Keep only verified evidence statements in the direct answer paragraph
            answer_text = " ".join([c["text"] for c in evidence_claims])
        else:
            answer_text = sanitize_text_artifacts(answer_text, citation_stats.get("removed", []))

        answer_text = sanitize_text_artifacts(answer_text, citation_stats.get("removed", []))

        # 6. Gaps & Confidence
        gaps = list(gap_notes)
        if incomplete:
            gaps.append("Retrieval terminated early at re-planning limit; some secondary details may be missing")

        items_related = self._assess_evidence_relatedness(valid_evidence)

        if incomplete or citation_stats["invalid"] > 0 or not items_related:
            confidence = "medium" if len(valid_evidence) >= 1 else "low"
        elif len(valid_evidence) >= 2 and all(e.get("confidence", 0.0) >= 0.8 for e in valid_evidence[:2]):
            confidence = "high"
        else:
            confidence = "medium"

        rendered = render_template(
            answer=answer_text,
            claims=sanitized_claims,
            inference=inference_text,
            confidence=confidence,
            gaps=gaps
        )

        output = {
            "run_id": run_id,
            "answer": answer_text,
            "claims": sanitized_claims,
            "inference": inference_text,
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
