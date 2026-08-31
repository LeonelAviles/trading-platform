"""Local embeddings — sentence-transformers `all-MiniLM-L6-v2` when the model is
available, else a deterministic hashing embedder — so no second API vendor is
needed (PLATFORM-SPEC.md §4.8). Also exposes a Graphiti `EmbedderClient`."""

from __future__ import annotations

import hashlib
import math
import os
import re
import threading

DIM_HASH = 256
_MODEL = None
_LOCK = threading.Lock()
_DISABLED = os.environ.get("KNOWLEDGE_EMBEDDER", "") == "hash"


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def hash_embed(text: str, dim: int = DIM_HASH) -> list[float]:
    vec = [0.0] * dim
    toks = _tokens(text)
    for i, t in enumerate(toks):
        for gram in (t, f"{toks[i - 1]}_{t}" if i else None):
            if not gram:
                continue
            h = int(hashlib.md5(gram.encode()).hexdigest(), 16)
            vec[h % dim] += 1.0 if (h >> 8) % 2 else -1.0
    n = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / n for v in vec]


def _model():
    global _MODEL
    if _DISABLED:
        return None
    with _LOCK:
        if _MODEL is None:
            try:
                from sentence_transformers import SentenceTransformer

                _MODEL = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
            except Exception:
                _MODEL = False
    return _MODEL or None


def embed(texts: list[str]) -> list[list[float]]:
    m = _model()
    if m is None:
        return [hash_embed(t) for t in texts]
    return [list(map(float, v)) for v in m.encode(texts, normalize_embeddings=True, batch_size=32)]


def backend_name() -> str:
    return "sentence-transformers/all-MiniLM-L6-v2" if _model() is not None else "hash-256"


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return float(sum(x * y for x, y in zip(a, b)))


def graphiti_embedder():
    """Graphiti EmbedderClient over the local model."""
    from graphiti_core.embedder.client import EmbedderClient

    class LocalEmbedderClient(EmbedderClient):
        async def create(self, input_data):
            texts = [input_data] if isinstance(input_data, str) else list(input_data)
            vecs = embed(texts)
            return vecs[0] if isinstance(input_data, str) else vecs

        async def create_batch(self, input_data_list):
            return embed(list(input_data_list))

    return LocalEmbedderClient()


def graphiti_cross_encoder():
    """Graphiti CrossEncoderClient over the local embeddings — Graphiti's default
    is an OpenAI reranker, which this platform has no key for."""
    from graphiti_core.cross_encoder.client import CrossEncoderClient

    class LocalCrossEncoder(CrossEncoderClient):
        async def rank(self, query, passages):
            if not passages:
                return []
            vecs = embed([query] + list(passages))
            scored = [(p, float(cosine(vecs[0], v))) for p, v in zip(passages, vecs[1:])]
            scored.sort(key=lambda x: -x[1])
            return scored

    return LocalCrossEncoder()
