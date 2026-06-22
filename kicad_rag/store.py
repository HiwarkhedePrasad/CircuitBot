"""Thin wrapper around SQLite for ground-truth lookups and BM25.

Holds no long-lived connections — each function opens / closes so the
module stays importable without side effects.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from kicad_rag.constants import (
    FOOTPRINTS_ROOT,
    SQLITE_PATH,
    SYMBOLS_ROOT,
)


def _con() -> sqlite3.Connection:
    if not SQLITE_PATH.is_file():
        sys.exit(f"sqlite not found at {SQLITE_PATH}; run `kicad-rag build` first")
    con = sqlite3.connect(SQLITE_PATH)
    con.execute("PRAGMA busy_timeout = 5000")
    return con


# ── public helpers ──────────────────────────────────────────────────────────


def lookup_pins(id_str: str) -> list[dict]:
    """Return the ground-truth pin list for a component like
    ``"Regulator_Linear:AMS1117-3.3"``."""
    con = _con()
    try:
        row = con.execute(
            "SELECT pins_json FROM symbols WHERE id_str = ?", (id_str,)
        ).fetchone()
    finally:
        con.close()
    return json.loads(row[0]) if row else []


def lookup_batch(id_ints: list[int]) -> dict[int, tuple]:
    """Fetch ``(id_int, id_str, text, datasheet, pins_json, footprint, fp_filters, pads_json)`` for a batch of uint64 ids."""
    con = _con()
    try:
        ph = ",".join("?" for _ in id_ints)
        rows = {
            r[0]: r for r in con.execute(
                f"SELECT id_int, id_str, text, datasheet, pins_json, "
                f"footprint, fp_filters, pads_json "
                f"FROM symbols WHERE id_int IN ({ph})", id_ints
            )
        }
    finally:
        con.close()
    return rows


def lookup_footprint(id_str: str) -> dict | None:
    """Return footprint info for a component: ``{footprint, fp_filters, pads}`` or ``None``."""
    con = _con()
    try:
        row = con.execute(
            "SELECT footprint, fp_filters, pads_json FROM symbols WHERE id_str = ?",
            (id_str,),
        ).fetchone()
    finally:
        con.close()
    if not row or not row[0]:
        return None
    return {
        "footprint": row[0],
        "fp_filters": json.loads(row[1]) if row[1] else [],
        "pads": json.loads(row[2]) if row[2] else [],
    }


def footprint_path_for(footprint_str: str) -> Path:
    """Derive the ``.kicad_mod`` file path from a footprint reference string.

    ``footprint_str`` shape: ``"<category>:<name>"`` which maps to
    ``kicad-footprints/<category>.pretty/<name>.kicad_mod``.
    """
    cat, _, name = footprint_str.partition(":")
    return FOOTPRINTS_ROOT / f"{cat}.pretty" / f"{name}.kicad_mod"


def sexpr_path_for(id_str: str) -> Path:
    """Derive the ``.kicad_sym`` file path from a dataset ``id_str``.

    ``id_str`` shape: ``"<category>:<symbol_name>"`` which maps 1:1 to the
    on-disk KiCad layout ``<category>.kicad_symdir/<symbol_name>.kicad_sym``.
    """
    cat, _, name = id_str.partition(":")
    return SYMBOLS_ROOT / f"{cat}.kicad_symdir" / f"{name}.kicad_sym"


def fetch_sexpr(id_str: str) -> str:
    """Read and return the raw S‑expression from disk for *id_str*."""
    p = sexpr_path_for(id_str)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    return p.read_text(encoding="utf-8")