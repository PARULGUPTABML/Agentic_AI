# Orchestrator / Manager Agent: Methodology and Build Guide

**Component:** Orchestrator / Manager Agent  
**Artifacts:** `orchestrator/agent.py`, `schema.py`, `prompts/orchestrator_system.md`, `tests/test_orchestrator.py`

---

## 1. Purpose & Scope

The Orchestrator is the initial entry point of the multi-agent system:
- Takes raw developer questions and decomposes them into independent subtasks.
- Selects the target agents (`code_analysis`, `history_rag`, `issue_pr`) according to an intent policy.
- Handles gap feedback and re-planning without nesting sub-queries.
- Strictly bounds execution (`MAX_ROUNDS = 2`, `MAX_LLM_CALLS = 4`).
- Logs execution telemetry to the shared Blackboard.

**Out of Scope:** Does not perform static analysis, git log retrieval, issue searches, or final answer writing.

---

## 2. Intent Classification and Policy

Intents are classified with strict word-boundary matching (`\b` regex):
- `rationale` ("why", "reason", "motivation", "purpose") -> assigns `code_analysis`, `history_rag`, `issue_pr`
- `timeline` ("when", "timeline", "date", "sequence", "introduced", "merged") -> assigns `history_rag`, `code_analysis`
- `authorship` ("who", "author", "authored", "spearheaded", "approved by") -> assigns `history_rag`, `issue_pr` (word boundaries prevent "whole" or "whose" from matching)
- `superseded_implementation` ("supersede", "replace", "deprecated", "predecessor") -> assigns `history_rag`, `code_analysis`
- `other` -> broad fallback plan across all three retrieval agents

---

## 3. Dynamic Entity Extraction

Entities are extracted dynamically from the question text without hardcoded candidate lists:
- **Files:** Regex matching valid source and build filenames (`*.c`, `*.h`, `*.S`, `*.cmake`, `*.bf`, `*.thy`, etc.).
- **Functions & Symbols:** Identifies identifiers matching `seL4_*`, `c_handle_*`, `sys_*`, or CamelCase/snake_case functions.
- **Architectural Tokens:** Captures uppercase system acronyms (`SMP`, `PAE`, `ASID`, `IOAPIC`, `GIC`, `MCS`, `TCB`).

---

## 4. Resilience: Retry and Fallback Architecture

1. **LLM Path**:
   - Generates plan using `prompts/orchestrator_system.md`.
   - Validates generated JSON against schema via `validate_plan_dict()`.
   - If invalid, executes **one automatic retry** with the schema validation error appended to the prompt.
   - If the retry also fails or an exception occurs, automatically invokes `create_fallback_plan()`.
2. **Deterministic Offline Fallback**:
   - When running offline (`LLM_PROVIDER=mock`), uses rule-based planner guaranteeing 100% schema compliance and determinism.
3. **Re-planning**:
   - Method `replan(previous_plan, feedback)` preserves the original user question without string-nesting, appends a targeted query for the specific gap, increments the round counter, and stops strictly at `MAX_ROUNDS = 2`.

---

## 5. Verification Checklist

- [x] Plan schema strictly defined and validated in code (`schema.py`)
- [x] System prompt maintained in separate versioned file (`prompts/orchestrator_system.md`)
- [x] Intent classifier and entity extraction tested on real seL4 queries
- [x] Validation, retry, and fallback logic verified
- [x] Re-plan loop stops at `MAX_ROUNDS` without query nesting
- [x] Telemetry logged to Blackboard (duration, actions, tokens)
- [x] 13 unit and dev-set tests passing in `tests/test_orchestrator.py`
