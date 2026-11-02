"""Embedding helpers. Uses sentence-transformers; EMBED_MODEL=hash gives an offline fallback
(for tests/CI only, retrieval quality is much lower)."""
import hashlib
from functools import lru_cache

import numpy as np

from config import EMBED_MODEL
from rag.text_utils import tokenize

HASH_DIM = 512
MAX_EMBED_CHARS = 2000


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBED_MODEL)


def _hash_embed(texts):
    vecs = np.zeros((len(texts), HASH_DIM), dtype=np.float32)
    for i, t in enumerate(texts):
        for tok in tokenize(t):
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            vecs[i, h % HASH_DIM] += 1.0 if (h >> 12) & 1 else -1.0
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vecs / norms


def embed_documents(texts: list[str]) -> list[list[float]]:
    texts = [t[:MAX_EMBED_CHARS] for t in texts]
    if EMBED_MODEL == "hash":
        return _hash_embed(texts).tolist()
    return _model().encode(texts, normalize_embeddings=True, batch_size=32,
                           show_progress_bar=False).tolist()


def embed_query(text: str) -> list[float]:
    if EMBED_MODEL == "hash":
        return _hash_embed([text])[0].tolist()
    if "bge" in EMBED_MODEL.lower():  # BGE models expect an instruction prefix for queries
        text = "Represent this sentence for searching relevant passages: " + text
    return _model().encode([text], normalize_embeddings=True)[0].tolist()
