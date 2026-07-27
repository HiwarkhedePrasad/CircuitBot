"""Dense (TurboVec), BM25 (SQLite FTS5), and weighted-RRF hybrid retrieval.

Each retriever returns ``list[(id_int, score)]`` where *score* is higher =
better (already negated for BM25).  The hybrid fuser uses the weighted
Reciprocal Rank Fusion formula from ``constants.py``.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
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
    """Build a high-recall FTS5 OR-expression from query tokens safely."""
    toks = _fts_tokens(query)
    cleaned_toks = [t.replace('"', '""') for t in toks if t]
    return " OR ".join(f'"{t}"*' for t in cleaned_toks) if cleaned_toks else ""


# ── BM25 (SQLite FTS5) ─────────────────────────────────────────────────────


def bm25_search(query: str, k: int = FANOUT, library_filter: str | None = None) -> list[Tuple[int, float]]:
    """Return ``[(id_int, bm25_score)]`` — higher score = better match."""
    match_expr = _to_fts_match(query)
    if not match_expr:
        return []
    con = sqlite3.connect(SQLITE_PATH)
    try:
        where_clauses = ["symbols_fts MATCH ?"]
        params: list = [match_expr]

        if library_filter:
            pats = [p.strip() for p in library_filter.split("|") if p.strip()]
            if pats:
                lib_conditions = []
                for p in pats:
                    lib_conditions.append("symbols.id_str LIKE ? OR symbols.id_str LIKE ?")
                    params.extend([f"{p}:%", f"{p}_%"])
                where_clauses.append(f"({' OR '.join(lib_conditions)})")

        where_sql = " AND ".join(where_clauses)
        params.append(k)

        sql = (
            f"SELECT symbols.rowid, bm25(symbols_fts) AS s "
            f"FROM symbols_fts JOIN symbols ON symbols.rowid = symbols_fts.rowid "
            f"WHERE {where_sql} "
            f"ORDER BY s LIMIT ?"
        )
        rows = con.execute(sql, params).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        con.close()
    return [(int(r[0]), -float(r[1])) for r in rows]


# ── Dense (TurboVec) ───────────────────────────────────────────────────────

_embedding_model = None
_turbovec_index = None
_dense_load_failed = False
_dense_last_attempt = 0  # timestamp of last failed attempt

_DENSE_LOAD_TIMEOUT = 120  # seconds — first download of ~80MB model can be slow
_DENSE_RETRY_COOLDOWN = 300  # seconds before retrying after failure


def _get_dense_resources():
    """Lazy-load and cache the embedding model + TurboVec index at module level."""
    global _embedding_model, _turbovec_index, _dense_load_failed, _dense_last_attempt

    if os.environ.get("DISABLE_DENSE_SEARCH", "").lower() in ("1", "true", "yes"):
        _dense_load_failed = True
        raise RuntimeError("Dense search disabled via DISABLE_DENSE_SEARCH env var")

    if _dense_load_failed:
        now = _ts.time()
        if now - _dense_last_attempt < _DENSE_RETRY_COOLDOWN:
            raise RuntimeError("Dense resources previously failed to load")
        _dense_load_failed = False

    if _embedding_model is None or _turbovec_index is None:
        t = threading.Thread(target=_init_dense_resources, daemon=True)
        t.start()
        t.join(timeout=_DENSE_LOAD_TIMEOUT)

    if _embedding_model is None or _turbovec_index is None:
        _dense_last_attempt = _ts.time()
        _dense_load_failed = True
        raise RuntimeError("Dense resources unavailable")

    return _embedding_model, _turbovec_index


def _init_dense_resources():
    global _embedding_model, _turbovec_index
    try:
        from fastembed import TextEmbedding
        _embedding_model = TextEmbedding(model_name=EMBED_MODEL)
        from turbovec import IdMapIndex
        from kicad_rag.constants import INDEX_PATH
        _turbovec_index = IdMapIndex.load(str(INDEX_PATH))
    except Exception as e:
        print(f"[retrieval] Dense embeddings unavailable ({e}) — falling back to BM25/FTS5 search.")
        _embedding_model = None
        _turbovec_index = None


def dense_search(query: str, k: int = FANOUT) -> list[Tuple[int, float]]:
    """Return ``[(id_int, cosine_score)]`` from the quantised TurboVec index."""
    try:
        model, idx = _get_dense_resources()
        if model is None or idx is None:
            return []
        qvec = np.asarray(next(model.embed([query])), dtype=np.float32)[None, :]
        if not qvec.flags["C_CONTIGUOUS"]:
            qvec = np.ascontiguousarray(qvec)

        scores, ids = idx.search(qvec, k=k)
        return [(int(i), float(s)) for i, s in zip(ids[0], scores[0])]
    except Exception:
        return []


# ── Weighted RRF fusion ──────────────────────────────────────────────────────


def hybrid_search(
    query: str,
    k: int = 5,
    fanout: int = FANOUT,
    library_filter: str | None = None,
) -> list[Tuple[int, float]]:
    """Weighted Reciprocal Rank Fusion of dense + BM25 with exact part boosting."""
    dense = dense_search(query, fanout)
    bm25 = bm25_search(query, fanout, library_filter=library_filter)

    # Post-filter dense if library_filter provided
    if library_filter and dense:
        pats = [p.strip() for p in library_filter.split("|") if p.strip()]
        if pats:
            from kicad_rag.store import lookup_batch
            dense_rows = lookup_batch([rid for rid, _ in dense])
            dense = [
                (rid, score) for rid, score in dense
                if rid in dense_rows and any(
                    dense_rows[rid][1].startswith(p + ":") or dense_rows[rid][1].startswith(p + "_")
                    for p in pats
                )
            ]

    fused: dict[int, float] = {}
    bm25_rank: dict[int, int] = {}

    for rank, (id_int, _) in enumerate(dense, 1):
        fused[id_int] = fused.get(id_int, 0.0) + W_DENSE / (K_RRF + rank)

    for rank, (id_int, _) in enumerate(bm25, 1):
        fused[id_int] = fused.get(id_int, 0.0) + W_BM25 / (K_RRF + rank)
        bm25_rank[id_int] = rank

    # Boost exact part number matches
    toks = [t.strip().upper() for t in re.findall(r'[A-Za-z0-9][\w.\-]*', query) if len(t) >= 3]
    if toks:
        from kicad_rag.store import lookup_batch
        all_ids = list(fused.keys())
        if all_ids:
            row_map = lookup_batch(all_ids)
            for id_int, rdata in row_map.items():
                id_str = rdata[1].upper()
                bare_part = id_str.rpartition(":")[2]
                for tok in toks:
                    if tok == bare_part or bare_part.startswith(tok):
                        fused[id_int] += 0.5

    # Boost canonical taxonomy symbols for query
    try:
        from kicad_rag.taxonomy import get_canonical_symbols
        canon_syms = get_canonical_symbols(query, library_filter or "")
        if canon_syms:
            from kicad_rag.store import lookup_batch
            all_ids = list(fused.keys())
            if all_ids:
                row_map = lookup_batch(all_ids)
                for id_int, rdata in row_map.items():
                    id_str = rdata[1]
                    if id_str in canon_syms:
                        fused[id_int] += 0.8
    except Exception:
        pass

    return sorted(
        fused.items(),
        key=lambda kv: (
            -kv[1],
            bm25_rank.get(kv[0], 1_000_000),
            kv[0],
        ),
    )[:k]