# Orchestrator / Manager Agent Documentation

**Owner:** Parul Aggarwal (230540)  
**Deliverable Window:** Weeks 3-5  
**Artifacts:** `parul_aggarwal/orchestrator/agent.py`, `schema.py`, `prompts/orchestrator_system.md`

---

## 1. Role and Architecture

The Orchestrator is the initial entry point of the multi-agent pipeline. It performs:
1. **Intent Classification:** Classifies incoming questions into `rationale`, `timeline`, `authorship`, `superseded_implementation`, or `other`.
2. **Entity Extraction:** Extracts source files, kernel functions, and domain features without hallucinating non-existent entities.
3. **Task Decomposition:** Builds self-contained sub-queries assigned to specialized retrieval agents (`code_analysis`, `history_rag`, `issue_pr`).
4. **Re-planning:** Reacts to gap feedback from the verification stage by refining only the missing aspects.
5. **Termination Control:** Hard stops re-planning at `MAX_ROUNDS = 2` to avoid unbounded loops.
6. **Graceful Fallback:** Automatically reverts to an all-agent fallback plan if LLM parsing fails twice.

---

## 2. Verification & Testing

- 12 unit and dev evaluation tests passing in `parul_aggarwal/tests/test_orchestrator.py`.
- Achieves 100% intent classification accuracy across dev benchmark questions.
- Verified deterministic output across repeat runs.
