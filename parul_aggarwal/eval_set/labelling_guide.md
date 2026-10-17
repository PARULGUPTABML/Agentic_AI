# Evaluation Question Set Labelling Guide

**Owner:** Parul Aggarwal (230540)  
**Project:** Repository-Aware Multi-Agent AI for Software History and Development Decisions

---

## 1. Overview and Core Principles

This benchmark dataset evaluates how effectively multi-agent AI systems reason about software repository history, development decisions, and architectural evolution compared to single-agent or code-snapshot-only baselines.

### Guiding Principles
1. **Answerable from History**: Every answerable question must have ground truth rooted in commits, pull requests, issues, reviews, or design discussions.
2. **Not Answerable from Current Code Snapshot Alone**: Questions must probe the *why*, *when*, *who*, or *what was superseded* that cannot be determined simply by grepping or reading current source files without historical context.
3. **Strict Dev vs. Held-Out Separation**: 
   - `dev` split (~20%): Used for prompt tuning, agent development, and threshold calibration.
   - `heldout` split (~80%): Strictly frozen for final evaluation. Never inspect or tune models on held-out questions.
4. **Verifiable Citations**: Every claim in the reference answer must cite concrete artifact IDs (`commit:<sha>`, `pr:<id>`, `issue:<id>`).
5. **Unanswerable Questions**: Includes deliberately unanswerable questions to rigorously test hallucination detection and gap honesty.

---

## 2. Schema Specification

Each line in `questions.jsonl` is a valid, single JSON object conforming to:

```json
{
  "qid": "Q001",
  "split": "dev | heldout",
  "question": "Why was the kernel memory allocation mechanism changed to untyped memory?",
  "question_type": "rationale | timeline | authorship | superseded_implementation",
  "difficulty": "easy | medium | hard",
  "reference_answer": "In commit:7a81b2c and PR:142, kernel memory allocation was changed to untyped memory to enable capability-based spatial isolation and eliminate dynamic allocations inside the microkernel core.",
  "key_facts": [
    "Kernel memory shifted to user-managed untyped memory capabilities",
    "Eliminated dynamic memory allocation in the microkernel core",
    "Enabled formal verification of physical memory bounds"
  ],
  "ground_truth_evidence": [
    "commit:7a81b2c",
    "pr:142",
    "issue:118"
  ],
  "expected_intent": "rationale",
  "expected_agents": [
    "code_analysis",
    "history_rag",
    "issue_pr"
  ],
  "expected_entities": {
    "files": ["src/kernel/untyped.c", "include/object/untyped.h"],
    "functions": ["invokeUntyped_Retype"],
    "features": ["untyped memory", "spatial isolation"]
  },
  "needs_multiple_sources": true,
  "annotator": "Parul Aggarwal",
  "validated_by": "Team Lead / Peer Reviewer",
  "notes": "Covers transition to seL4 untyped memory architecture"
}
```

---

## 3. Taxonomy & Labelling Rules

### 3.1 Question Types and Intent Mapping

| Question Type | Description | Expected Intent | Primary Agents |
|---|---|---|---|
| `rationale` | "Why" decisions were made, reasons for architectural redesigns or trade-offs | `rationale` | `code_analysis`, `history_rag`, `issue_pr` |
| `timeline` | "When" or in what sequence features, APIs, or deprecations occurred | `timeline` | `history_rag`, `code_analysis` |
| `authorship` | "Who" authored, reviewed, or spearheaded major architectural changes | `authorship` | `history_rag`, `issue_pr` |
| `superseded_implementation` | "What" implementation was replaced, deprecated, or removed and why | `superseded_implementation` | `history_rag`, `code_analysis` |

### 3.2 Difficulty Ratings

- **`easy`**: The answer is found directly in a single artifact (e.g., a single commit message or single issue response).
- **`medium`**: Requires synthesizing information across 2 distinct artifacts (e.g., a commit message and an associated pull request discussion).
- **`hard`**: Requires cross-referencing 3 or more artifacts across different modalities (e.g., an issue identifying a bottleneck, a pull request debating alternatives, and a commit refactoring the code).

### 3.3 Key Facts

A **key fact** is a necessary proposition for a complete and faithful answer. If an AI response misses a key fact or contradicts it, the answer score must be penalized by LLM-as-judge or human evaluation.

### 3.4 Artifact ID Syntax
Stable prefixes must be used strictly:
- `commit:<sha7_or_sha40>`
- `pr:<number>`
- `issue:<number>`
- `review:<number>`
- `code:<file_path>:<line_range>`

---

## 4. Conflict Resolution & Quality Assurance

1. **Disagreements between Annotators**:
   - If two annotators disagree on an answer or intent, the ground truth artefact text is authoritative.
   - If evidence remains ambiguous, the question is rewritten or flagged with lower annotator confidence.
2. **Leakage Prevention**:
   - Never use question text that reveals commit hashes, author names, or specific PR IDs (e.g., write *"Why was IPC buffer alignment updated?"* instead of *"Why did commit 3b4f1 update IPC buffer alignment?"*).
3. **Freezing**:
   - Once validated, the `heldout` questions are cryptographically hashed using SHA-256 (`heldout_hash.sha256`). Any modification invalidates the benchmark baseline.
