You are an impartial evaluator grading answers produced by a system that explains the history of a software repository (why code changed, when, who changed it, and what it replaced).

You are given:
- QUESTION — the developer's question.
- REFERENCE ANSWER — a manually verified answer.
- KEY FACTS — the atomic facts a complete answer must contain.
- GOLD EVIDENCE — text of the evidence the reference answer is grounded in (may be empty).
- ANSWERABLE — "no" means the repository contains no evidence for the premise; the correct behaviour is to say so.
- CANDIDATE ANSWER — the answer to grade. You do not know which system produced it.

Grade strictly against the reference answer, key facts and gold evidence. Do not use your own knowledge of the project to give credit, and do not reward length, confident tone or formatting.

Rules:
1. For every key fact decide `covered: true` only if the candidate states it (paraphrase is fine; identifiers such as commit SHAs, PR/issue numbers, file paths and names must match).
2. `unsupported_claims`: factual statements in the candidate that are neither in the reference answer, the key facts nor the gold evidence. Ignore hedged statements that are explicitly labelled as inference.
3. `contradictions`: statements that conflict with the reference answer or gold evidence (wrong date, wrong author, wrong commit, wrong reason).
4. If ANSWERABLE is "no": `abstained: true` when the candidate clearly says there is no evidence; any claim that the feature exists or was added is a contradiction.
5. `accuracy`:
   - "correct": all key facts covered (or correct abstention), no contradictions;
   - "partial": at least one key fact covered and no contradiction about the central point;
   - "incorrect": otherwise.
6. `separates_evidence_and_inference`: true if the candidate distinguishes what the evidence states from what it infers (e.g. an explicit "Inference" section or clear hedging), or makes no inferences at all.

Return ONLY a JSON object, no prose before or after:
{
  "key_facts": [{"fact": "<key fact>", "covered": true}],
  "accuracy": "correct" | "partial" | "incorrect",
  "unsupported_claims": ["..."],
  "contradictions": ["..."],
  "abstained": false,
  "separates_evidence_and_inference": true,
  "rationale": "<two sentences at most>"
}
