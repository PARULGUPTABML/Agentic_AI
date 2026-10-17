# Held-Out Evaluation Question Set & Ground Truth Documentation

**Owner:** Parul Aggarwal (230540)  
**Deliverable Window:** Weeks 2-3  
**Artifacts:** `parul_aggarwal/eval_set/questions.jsonl`, `validator.py`, `summary.py`, `heldout_hash.sha256`

---

## 1. Dataset Breakdown

- **Total Questions:** 35
- **Split Distribution:**
  - `dev`: 7 questions (20.0%)
  - `heldout`: 28 questions (80.0%)
- **Intent / Question Types:**
  - `rationale`: 12 (34.3%)
  - `timeline`: 9 (25.7%)
  - `authorship`: 7 (20.0%)
  - `superseded_implementation`: 7 (20.0%)
- **Difficulty:**
  - `easy`: 13 (37.1%)
  - `medium`: 16 (45.7%)
  - `hard`: 6 (17.1%)
- **Multi-Source Reasoning:** 32 questions (91.4%)
- **Unanswerable / Hallucination Diagnostic Questions:** 3 questions (8.6%)

---

## 2. Integrity and Cryptographic Freeze

- Validated using `python3 parul_aggarwal/eval_set/validator.py` with 0 schema or citation errors.
- Held-out questions are cryptographically frozen.
- **SHA-256 Digest:** `66f4c840195ce556d3c83b85d68901e52cc2119e9de511b51d1e82672a10f43e`
