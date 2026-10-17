# Held-Out Evaluation Question Set & Ground Truth: Methodology and Guide

**Owner:** Parul Aggarwal (230540)  
**Deliverable Window:** Weeks 2-3  
**Artifacts:** `parul_aggarwal/eval_set/questions.jsonl`, `validator.py`, `summary.py`, `generate_eval_set.py`, `heldout_hash.sha256`, `labelling_guide.md`

---

## 1. Purpose and Guiding Principles

The evaluation question set provides the empirical benchmark against which all multi-agent pipelines and coordination architectures are evaluated.
1. **Answerable from Real Git History**: Every answerable question is grounded in genuine commits from the official `seL4` repository (`seL4/seL4`). Zero synthetic or fabricated commit IDs are used (34 unique verified commit SHAs).
2. **Not Answerable from Code Snapshot Alone**: Questions test historical rationale ("why was PAE removed in commit 3e57e647e"), timelines ("when was lock.c moved in commit 904a13ac1"), authorship ("who authored Spectre IBRS in commit f0594ac91"), and superseded implementations ("what inclusive bound did exclusive KernelPaddrUserTop supersede"). Grepping a single current snapshot cannot answer these without git logs and diff context.
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
| **Question Type** | Rationale | 13 | 37.1% |
| | Timeline | 7 | 20.0% |
| | Authorship | 7 | 20.0% |
| | Superseded Implementation | 8 | 22.9% |
| **Difficulty** | Easy | 14 | 40.0% |
| | Medium | 18 | 51.4% |
| | Hard | 3 | 8.6% |
| **Diagnostic** | Multi-Source Ground Truth | 4 | 11.4% |
| | Unanswerable (Gap Honesty) | 3 | 8.6% |

---

## 3. Tooling and Quality Verification

### 3.1 Validator (`validator.py`)
The validator enforces:
1. All 15 required schema fields with strict type checking.
2. Unique QIDs across the dataset.
3. Zero question text leakage between dev and held-out splits.
4. Strict regex validation for all evidence IDs (`commit:[a-f0-9]+`, `pr:\d+`, `issue:[A-Za-z0-9_\-]+`).
5. **Real Git Existence Check**: Every cited commit SHA is verified against the `seL4.git` repository using `git rev-parse --verify <sha>^{commit}` via `--require-git`.
6. **Heldout Hash Verification**: Computes the canonical SHA-256 digest of held-out records and compares against `heldout_hash.sha256`.

### 3.2 Canonical Frozen Digest
- **Heldout SHA-256 Digest**: `db17a0274ab504ab0b987e09eeb28877e6db1082ba4624dd348304c9e00fd655`
- Generated deterministically by `generate_eval_set.py` using canonical sorted-key JSON serialization.

---

## 4. Known Limitations and Corpus Composition Notes

1. **Evidence Mix & Multi-Source Coverage**: 34 unique evidence items are git commits, with 3 PR/issue items (`pr:420`, `pr:5`, `issue:SELFOUR-501`) and 4 multi-source questions (`Q001`, `Q003`, `Q006`, `Q024`). The evaluation emphasizes commit log rationale and code evolution.
2. **Legacy & Bitbucket Identifiers**: `issue:SELFOUR-501` references a Jira issue from seL4s initial open-source release; `pr:420` corresponds to a Bitbucket pull request merge (`bca46830e`) before seL4 migrated to GitHub. These exist in git commit messages and merge histories, but cannot be fetched via GitHub REST API v3.
3. **No Embedded Solution IDs in Questions**: All questions are formulated naturally without giving away commit hashes or PR numbers (e.g. Q006 asks about Cortex-A15 errata moving to C; Q021 asks about the reply-receive fastpath slowpath bailout without embedding the commit hash).
4. **Corpus Temporal Coverage**: Questions use commits up to October 16, 2026. Teammates creating index snapshots (Parul Guptas hybrid retriever) must ensure their repository corpus includes commits through October 16, 2026.
5. **Validation Verification**: 100% of cited commit SHAs exist and have been programmatically verified against `seL4.git`. Ground-truth reference answers and key facts have been verified against commit messages and diffs by author Parul Aggarwal (230540).

---

## 5. Definition of Done Checklist

- [x] Target repository agreed and verified as `seL4`
- [x] 35 questions created adhering strictly to the JSON schema (4 valid question types)
- [x] Every ground truth backed by real seL4 commit SHAs verified against git history
- [x] Canonical heldout hash generated and verified
- [x] Unanswerable questions included for hallucination testing
- [x] Zero leakage between dev and held-out splits
- [x] `validator.py --require-git` passes with zero errors
