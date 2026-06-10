"""Thin wrapper around SQLite for ground-truth lookups and BM25.

Holds no long-lived connections — each function opens / closes so the
module stays importable without side effects.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from kicad_rag.constants import SQLITE_PATH, SYMBOLS_ROOT


def _con() -> sqlite3.Connection:
    if not SQLITE_PATH.is_file():
        sys.exit(f"sqlite not found at {SQLITE_PATH}; run `kicad-rag build` first")
    return sqlite3.connect(SQLITE_PATH)


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
    """Fetch ``(id_int, id_str, text, pins_json)`` for a batch of uint64 ids."""
    con = _con()
    try:
        ph = ",".join("?" for _ in id_ints)
        rows = {
            r[0]: r for r in con.execute(
                f"SELECT id_int, id_str, text, pins_json "
                f"FROM symbols WHERE id_int IN ({ph})", id_ints
            )
        }
    finally:
        con.close()
    return rows


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
