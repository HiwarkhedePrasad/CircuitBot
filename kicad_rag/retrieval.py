"""Dense (TurboVec), BM25 (SQLite FTS5), and weighted-RRF hybrid retrieval.

Each retriever returns ``list[(id_int, score)]`` where *score* is higher =
better (already negated for BM25).  The hybrid fuser uses the weighted
Reciprocal Rank Fusion formula from ``constants.py``.
"""
from __future__ import annotations

import re
import sqlite3
from typing import List, Tuple

import numpy as np

from kicad_rag.constants import (
    BIT_WIDTH,
    EMBED_BATCH,
    EMBED_MODEL,
    FANOUT,
    K_RRF,
    SQLITE_PATH,
    W_BM25,
    W_DENSE,
)

_ts = __import__("time")


# ── tokeniser ────────────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"[A-Za-z0-9][\w.\-]*")


def _fts_tokens(query: str) -> list[str]:
    """Lowercased alphanumeric groups that a FTS5 ``tokenchars '.-_'``
    tokeniser will also produce — ensures queries like ``AMS1117-3.3`` are
    kept intact."""
    return [t.lower() for t in _TOKEN_RE.findall(query) if t]


def _to_fts_match(query: str) -> str:
    """Build a high-recall FTS5 OR-expression from query tokens."""
    toks = _fts_tokens(query)
    return " OR ".join(f'"{t}"*' for t in toks) if toks else ""


# ── BM25 (SQLite FTS5) ─────────────────────────────────────────────────────


def bm25_search(query: str, k: int = FANOUT) -> list[Tuple[int, float]]:
    """Return ``[(id_int, bm25_score)]`` — higher score = better match."""
    match_expr = _to_fts_match(query)
    if not match_expr:
        return []
    con = sqlite3.connect(SQLITE_PATH)
    try:
        rows = con.execute(
            "SELECT rowid, bm25(symbols_fts) AS s "
            "FROM symbols_fts WHERE symbols_fts MATCH ? "
            "ORDER BY s LIMIT ?",
            (match_expr, k),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        con.close()
    return [(int(r[0]), -float(r[1])) for r in rows]


# ── Dense (TurboVec) ───────────────────────────────────────────────────────

_embedding_model = None
_turbovec_index = None


def _get_dense_resources():
    """Lazy-load and cache the embedding model + TurboVec index at module level."""
    global _embedding_model, _turbovec_index
    if _embedding_model is None:
        from fastembed import TextEmbedding
        _embedding_model = TextEmbedding(model_name=EMBED_MODEL)
    if _turbovec_index is None:
        from turbovec import IdMapIndex
        from kicad_rag.constants import INDEX_PATH
        _turbovec_index = IdMapIndex.load(str(INDEX_PATH))
    return _embedding_model, _turbovec_index


def dense_search(query: str, k: int = FANOUT) -> list[Tuple[int, float]]:
    """Return ``[(id_int, cosine_score)]`` from the quantised TurboVec index."""
    model, idx = _get_dense_resources()

    qvec = np.asarray(next(model.embed([query])), dtype=np.float32)[None, :]
    if not qvec.flags["C_CONTIGUOUS"]:
        qvec = np.ascontiguousarray(qvec)

    scores, ids = idx.search(qvec, k=k)
    return [(int(i), float(s)) for i, s in zip(ids[0], scores[0])]


# ── Weighted RRF fusion ──────────────────────────────────────────────────────


def hybrid_search(
    query: str,
    k: int = 5,
    fanout: int = FANOUT,
) -> list[Tuple[int, float]]:
    """Weighted Reciprocal Rank Fusion of dense + BM25.

    Returns ``[(id_int, fused_score)]`` sorted by descending fused score.
    """
    dense = dense_search(query, fanout)
    bm25 = bm25_search(query, fanout)

    fused: dict[int, float] = {}
    bm25_rank: dict[int, int] = {}

    for rank, (id_int, _) in enumerate(dense, 1):
        fused[id_int] = fused.get(id_int, 0.0) + W_DENSE / (K_RRF + rank)

    for rank, (id_int, _) in enumerate(bm25, 1):
        fused[id_int] = fused.get(id_int, 0.0) + W_BM25 / (K_RRF + rank)
        bm25_rank[id_int] = rank

    return sorted(
        fused.items(),
        key=lambda kv: (
            -kv[1],                      # primary: higher fused score
            bm25_rank.get(kv[0], 1_000_000),  # tiebreak: earlier in BM25
            kv[0],                       # final tiebreak: lower id
        ),
    )[:k]