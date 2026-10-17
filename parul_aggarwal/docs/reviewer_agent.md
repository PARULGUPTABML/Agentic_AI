# Reviewer / Final Response Agent Documentation

**Owner:** Parul Aggarwal (230540)  
**Deliverable Window:** Weeks 5-6  
**Artifacts:** `parul_aggarwal/reviewer/agent.py`, `citation_checker.py`, `renderer.py`, `prompts/reviewer_system.md`

---

## 1. Role and Core Principles

The Reviewer Agent is the terminal synthesis node in the pipeline. It transforms verified evidence into a polished, factual developer answer:
1. **Grounded Drafting:** Only utilizes explicitly supplied evidence fragments.
2. **Pure-Code Programmatic Citation Checker:** Validates every cited evidence ID against the approved input evidence set. Strips or converts invalid citations to inferences.
3. **Strict Separation of Inference:** Clearly separates literal evidence from speculative inferences or hypotheses.
4. **Honest Gap Reporting:** Explicitly lists missing information and handles the `incomplete: true` flag when re-planning reaches its limit.
5. **Standardized Human-Readable Template:** Renders output with standard `Answer`, `Supporting Evidence`, `Inference`, and `Gaps and Confidence` blocks.

---

## 2. Verification & Testing

- 7 unit, adversarial, and Blackboard integration tests passing in `parul_aggarwal/tests/test_reviewer.py`.
- Correctly handles unanswerable queries by stating lack of evidence without hallucination.
- Integrates with `BlackboardStatus.DONE` and observability logs.
