"""``KicadRAG`` — single-entry-point class for the CircuitBot agent.

Typical usage in an an LLM agent loop::

    from kicad_rag import KicadRAG

    rag = KicadRAG()

    # 1) search for a component by part-number or natural language
    results = rag.search("AMS1117-3.3", k=3)
    # → [Result(id_str='Regulator_Linear:AMS1117-3.3', score=0.0328, …), …]

    # 2) get ground-truth pins for netlist validation
    pins = rag.pins("Regulator_Linear:AMS1117-3.3")
    # → [{"num":"1","name":"GND","type":"power_in"}, …]

    # 3) fetch the raw KiCad S-expression to insert into a schematic
    sexpr = rag.sexpr("Regulator_Linear:AMS1117-3.3")
    # → "(kicad_symbol_lib (symbol ..."
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Optional

from kicad_rag.retrieval import dense_search, bm25_search, hybrid_search
from kicad_rag.store import (
    lookup_batch,
    lookup_footprint,
    lookup_pins,
    fetch_sexpr as _fetch_sexpr,
)
from kicad_rag.constants import FANOUT


@dataclass
class Result:
    """A single hit from a hybrid / dense / BM25 search."""

    id_str: str
    text: str
    score: float
    rank: int
    pins: List[dict]
    datasheet: str = ""
    footprint: Optional[str] = None
    fp_filters: Optional[List[str]] = None
    pads: Optional[List[dict]] = None


class KicadRAG:
    """Client for the KiCad component database.

    Does **not** persist connections — every method is a self-contained
    round-trip so the caller never has to worry about stale handles, thread
    safety, or the index file being relocated.

    Parameters
    ----------
    mode : str
        Default retriever mode: ``"hybrid"``, ``"dense"``, or ``"bm25"``.
        Can be overridden per ``search()`` call.
    """

    def __init__(self, mode: str = "hybrid") -> None:
        if mode not in ("hybrid", "dense", "bm25"):
            raise ValueError(f"unknown mode: {mode}")
        self._mode = mode

    # ── public API ──────────────────────────────────────────────────────────

    def search(self, query: str, k: int = 5,
               mode: Optional[str] = None,
               fanout: int = FANOUT) -> List[Result]:
        """Ranked results from the chosen retriever.

        Parameters
        ----------
        query:
            Natural-language description or part number
            (e.g. ``"AMS1117-3.3"``, ``"3.3 V LDO SOT-223"``).
        k:
            How many results to return.
        mode:
            Override the instance-default retriever.  ``None`` uses the
            mode passed to ``__init__``.
        fanout:
            Per-retriever depth fed into the RRF fuser (hybrid only).
        """
        mode = mode or self._mode
        if mode == "dense":
            ranked = dense_search(query, k)
        elif mode == "bm25":
            ranked = bm25_search(query, k)
        else:
            ranked = hybrid_search(query, k, fanout)

        if not ranked:
            return []

        rows = lookup_batch([rid for rid, _ in ranked])
        out: list[Result] = []
        for rank, (id_int, score) in enumerate(ranked, 1):
            row = rows.get(id_int)
            if row is None:
                continue
            _, id_str, text, datasheet, pins_json, footprint, fp_filters_json, pads_json = row
            out.append(Result(
                id_str=id_str,
                text=text,
                score=score,
                rank=rank,
                pins=json.loads(pins_json),
                datasheet=datasheet or "",
                footprint=footprint or None,
                fp_filters=json.loads(fp_filters_json) if fp_filters_json else None,
                pads=json.loads(pads_json) if pads_json else None,
            ))
        return out

    def pins(self, id_str: str) -> List[dict]:
        """Ground-truth pin list for netlist validation."""
        return lookup_pins(id_str)

    def sexpr(self, id_str: str) -> str:
        """Raw KiCad S-expression from the on-disk ``.kicad_sym`` file."""
        return _fetch_sexpr(id_str)

    def footprint(self, id_str: str) -> Optional[dict]:
        """Footprint info ``{footprint, fp_filters, pads}`` or ``None``."""
        return lookup_footprint(id_str)

    def pads(self, id_str: str) -> List[dict]:
        """Pad list for the default footprint, or empty list."""
        info = lookup_footprint(id_str)
        return info["pads"] if info else []