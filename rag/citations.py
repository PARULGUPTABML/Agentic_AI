"""Citation checking. Answers must cite evidence ids like [commit:1a2b3c4d5e] or [issue:42].
A citation is valid only if that id was actually in the evidence given to the LLM, which gives
an automatic, model-independent signal for fabricated references."""
import re

CITE_RE = re.compile(r"\[([^\[\]]+)\]")
ID_RE = re.compile(r"^(code|commit|issue|pr|review|doc):\S+$")
SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
NON_CLAIM_RE = re.compile(r"^(recommendation|note|in summary|overall)\b|evidence does not|not certain|i am not sure",
                          re.I)


def extract_citations(text: str) -> list[str]:
    out = []
    for m in CITE_RE.finditer(text or ""):
        for part in re.split(r"[,;]\s*", m.group(1)):
            part = part.strip().strip("`")
            if ID_RE.match(part) and part not in out:
                out.append(part)
    return out


def _matches(cited: str, evidence_ids: list[str]) -> bool:
    if cited in evidence_ids:
        return True
    if cited.startswith("commit:"):
        return any(e.startswith("commit:") and (e.startswith(cited) or cited.startswith(e)) for e in evidence_ids)
    return False


def split_claims(text: str) -> list[str]:
    """Sentences, with a citation that starts a segment glued back to the previous sentence."""
    segments = [s.strip() for s in SENT_SPLIT_RE.split((text or "").strip()) if s.strip()]
    merged = []
    for s in segments:
        if merged and s.startswith("[") and CITE_RE.match(s):
            cite_end = CITE_RE.match(s).end()
            merged[-1] += " " + s[:cite_end]
            s = s[cite_end:].strip()
            if not s:
                continue
        merged.append(s)
    return merged


def verify_citations(answer: str, evidence_ids: list[str]) -> dict:
    cited = extract_citations(answer)
    valid = [c for c in cited if _matches(c, evidence_ids)]
    invalid = [c for c in cited if c not in valid]
    claims = [s for s in split_claims(answer) if len(s.split()) >= 6 and not NON_CLAIM_RE.search(s)]
    uncited = [s for s in claims if not extract_citations(s)]
    return {
        "cited": cited,
        "valid": valid,
        "invalid": invalid,
        "citation_precision": round(len(valid) / len(cited), 3) if cited else None,
        "total_claims": len(claims),
        "uncited_claims": len(uncited),
        "grounded_ratio": round((len(claims) - len(uncited)) / len(claims), 3) if claims else None,
    }
