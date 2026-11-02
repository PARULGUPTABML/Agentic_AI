"""Tokenisation shared by BM25 and the offline hash embedder. Splits camelCase and snake_case
so that a question mentioning "token refresh" matches the identifier `refreshToken`."""
import re

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "it", "for", "on", "this", "that",
    "with", "as", "be", "was", "were", "are", "by", "at", "from", "why", "what", "how", "did",
    "does", "do", "we", "i", "you", "they", "there", "which", "when", "who", "has", "have",
    "had", "not", "but", "if", "so", "self", "none", "true", "false", "return", "def",
}
WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def tokenize(text: str) -> list[str]:
    tokens = []
    for word in WORD_RE.findall(text or ""):
        if word.isdigit():
            tokens.append(word)
            continue
        parts = CAMEL_RE.sub(" ", word).replace("_", " ").lower().split()
        for p in parts:
            if len(p) > 1 and p not in STOPWORDS:
                tokens.append(p)
        if len(parts) > 1:
            tokens.append(word.lower())  # keep the full identifier too
    return tokens
