# Reviewer / Final Response Agent: Methodology and Build Guide

**Owner:** Parul Aggarwal (230540)  
**Deliverable Window:** Weeks 5-6  
**Artifacts:** `parul_aggarwal/reviewer/agent.py`, `citation_checker.py`, `renderer.py`, `prompts/reviewer_system.md`, `prompts/reviewer_entailment.md`, `tests/test_reviewer.py`

---

## 1. Purpose & Scope

The Reviewer Agent is the final step of the multi-agent pipeline:
- Turns approved repository evidence into a clear, factual explanation for the developer.
- Ensures every factual claim carries inline citations pointing to valid evidence IDs.
- Strictly isolates inferences from factual statements.
- Honestly reports evidence gaps and sets confidence accordingly.
- Does NOT retrieve evidence or judge sufficiency; writes strictly from supplied fragments.

---

## 2. Methodology Steps

1. **Intake & Sanitization:** Reads input data; drops any evidence item missing an ID.
2. **Empty Evidence Detection:** If no evidence is present, reports gap honestly, sets confidence to `low`, inference to `None`, and writes no hallucinated claims.
3. **Grounded Drafting:** Synthesizes claims directly from evidence text. In LLM mode, uses `prompts/reviewer_system.md`; in offline/mock mode, extracts factual statements with inline citations without inventing inferences.
4. **Separation of Inference:** Only draws an inference if evidence demonstrates a genuine causal link. Never hardcodes blanket assumptions. If none exists, records `Inference: None`.
5. **Entailment Spot Check:** When `enable_entailment_check=True`, verifies each claim against cited evidence text using `prompts/reviewer_entailment.md` (or lexical verification fallback). Unverified claims are downgraded to inference.
6. **Pure-Code Programmatic Citation Checker:** `check_citations()` scans all claims, verifies that every cited ID exists in the approved evidence set, strips invalid IDs, and records statistics in `citation_check`.
7. **Gap and Confidence Evaluation:** Evaluates confidence based on evidence count and agreement; handles the `incomplete: true` flag by explicitly noting the retrieval cutoff.
8. **Template Rendering:** Renders output with standard `Answer`, `Supporting Evidence`, `Inference`, and `Gaps and Confidence` headers.
9. **Blackboard Integration:** Updates `blackboard.final_answer = rendered`, transitions status to `BlackboardStatus.DONE`, and logs telemetry.

---

## 3. Output Schema & Template Format

```
Answer
<short direct answer with inline citations like [commit:3e57e647e]>

Supporting Evidence
- <claim> [id]

Inference (not directly stated in the evidence)
- <inference, or "None">

Gaps and Confidence
- Confidence: high | medium | low
- Not found in evidence: <list, or "none">
```

---

## 4. Definition of Done Checklist

- [x] Citation validity is 100% after programmatic checking (invalid citations removed or downgraded)
- [x] Zero invented inferences on unrelated or empty evidence items
- [x] Template renderer conforms strictly to section 5 format
- [x] Adversarial tests pass (empty evidence, incomplete flag, dropped malformed items)
- [x] Entailment check flag supported
- [x] Blackboard integration tested (`BlackboardStatus.DONE`, final answer, logs)
- [x] 8 unit and adversarial tests passing in `tests/test_reviewer.py`
