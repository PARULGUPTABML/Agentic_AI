# Held-Out Evaluation Question Set & Ground Truth: Methodology and Guide

**Owner:** Parul Aggarwal (230540)  
**Deliverable Window:** Weeks 2-3  
**Artifacts:** `parul_aggarwal/eval_set/questions.jsonl`, `validator.py`, `summary.py`, `generate_eval_set.py`, `heldout_hash.sha256`, `labelling_guide.md`

---

## 1. Purpose and Guiding Principles

The evaluation question set provides the empirical benchmark against which all multi-agent pipelines and coordination architectures are evaluated.
1. **Answerable from Real Git History**: Every answerable question is grounded in genuine commits from the official `seL4` repository (`seL4/seL4`). Zero synthetic or fabricated commit IDs are used.
2. **Not Answerable from Code Snapshot Alone**: Questions test historical rationale ("why was PAE removed in commit 3e57e647e"), timelines ("when was lock.c moved in commit 904a13ac1"), authorship ("who authored Spectre IBRS in commit f0594ac91"), and superseded implementations ("what did moving cache flush in commit 03ad568c8 supersede"). Grepping a single current snapshot cannot answer these without git logs and diff context.
3. **Strict Dev vs. Held-Out Separation**: 
   - 7 dev questions (20.0%) for prompt calibration and agent testing.
   - 28 held-out questions (80.0%) strictly frozen for benchmark evaluations.
4. **Verifiable Citations**: All ground truth references cite actual git commit SHAs (e.g. `commit:3e57e647e`, `commit:904a13ac1`), real PR numbers (e.g. `pr:420`, `pr:5`), and real issues (e.g. `issue:SELFOUR-501`).
5. **Hallucination Diagnostics**: Includes 3 unanswerable questions (Kyber in bootloader, neural network schedulers, blockchain smart contracts) where the correct model behavior is to report lack of evidence rather than guessing.

---

## 2. Dataset Distribution

| Category | Dimension | Count | Percentage |
|---|---|---|---|
| **Split** | Dev | 7 | 20.0% |
| | Held-Out | 28 | 80.0% |
| **Question Type** | Rationale | 12 | 34.3% |
| | Timeline | 9 | 25.7% |
| | Authorship | 7 | 20.0% |
| | Superseded Implementation | 7 | 20.0% |
| **Difficulty** | Easy | 15 | 42.9% |
| | Medium | 17 | 48.6% |
| | Hard | 3 | 8.6% |
| **Diagnostic** | Multi-Source Ground Truth | 3 | 8.6% |
| | Unanswerable (Gap Honesty) | 3 | 8.6% |

---

## 3. Tooling and Quality Verification

### 3.1 Validator (`validator.py`)
The validator enforces:
1. All 15 required schema fields with strict type checking.
2. Unique QIDs across the dataset.
3. Zero question text leakage between dev and held-out splits.
4. Strict regex validation for all evidence IDs (`commit:[a-f0-9]+`, `pr:\d+`, `issue:[A-Za-z0-9_\-]+`).
5. **Real Git Existence Check**: Every cited commit SHA is verified against the `seL4.git` bare repository using `git rev-parse --verify <sha>^{commit}`.
6. **Heldout Hash Verification**: Computes the canonical SHA-256 digest of held-out records and compares against `heldout_hash.sha256`.

### 3.2 Canonical Frozen Digest
- **Heldout SHA-256 Digest**: `42cb9ac563d8e3fae23c5c9f302d274ad6af2b347da572d305adc2dc077a014c`
- Generated deterministically by `generate_eval_set.py` using canonical sorted-key JSON serialization.

---

## 4. Definition of Done Checklist

- [x] Target repository agreed and verified as `seL4`
- [x] 35 questions created adhering strictly to the JSON schema
- [x] Every ground truth backed by real seL4 commit SHAs verified against git
- [x] Canonical heldout hash generated and verified
- [x] Unanswerable questions included for hallucination testing
- [x] Zero leakage between dev and held-out splits
- [x] `validator.py` passes with zero errors
