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
        raise FileNotFoundError(
            f"SQLite database not found at {SQLITE_PATH}. "
            "Run `kicad-rag build` first to create the index."
        )
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
    """Return footprint info for a component: ``{footprint, fp_filters, pads}`` or ``None``.

    Returns None only if the component is not found in the database.
    If the component exists but has an empty footprint, returns the dict
    with empty footprint and fp_filters (for later resolution).
    """
    con = _con()
    try:
        row = con.execute(
            "SELECT footprint, fp_filters, pads_json FROM symbols WHERE id_str = ?",
            (id_str,),
        ).fetchone()
    finally:
        con.close()
    if not row:
        return None
    return {
        "footprint": row[0] or "",
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


_FOOTPRINT_INDEX: dict[str, list[str]] | None = None

def _build_footprint_index() -> dict[str, list[str]]:
    """Build an index of all footprints grouped by name prefix."""
    global _FOOTPRINT_INDEX
    if _FOOTPRINT_INDEX is not None:
        return _FOOTPRINT_INDEX

    import fnmatch
    index: dict[str, list[str]] = {}

    for category_dir in FOOTPRINTS_ROOT.iterdir():
        if not category_dir.is_dir() or not category_dir.name.endswith(".pretty"):
            continue
        category_name = category_dir.name.replace(".pretty", "")
        for footprint_file in category_dir.glob("*.kicad_mod"):
            footprint_name = footprint_file.stem
            candidate = f"{category_name}:{footprint_name}"
            # Index by first part of name (e.g., "R_0805" -> "R")
            prefix = footprint_name.split("_")[0] if "_" in footprint_name else footprint_name
            if prefix not in index:
                index[prefix] = []
            index[prefix].append(candidate)

    _FOOTPRINT_INDEX = index
    return index


def resolve_footprint_from_filters(id_str: str) -> str | None:
    """When footprint is empty, try to resolve from fp_filters.

    This function queries the database for fp_filters and matches them
    against available footprints in the library. Returns the first
    matching footprint string or None if no match found.

    Example:
        Device:R has fp_filters: ["R_*"]
        This function finds Resistor_SMD:R_0805_2012Metric as a match.
    """
    con = _con()
    try:
        row = con.execute(
            "SELECT footprint, fp_filters FROM symbols WHERE id_str = ?",
            (id_str,),
        ).fetchone()
    finally:
        con.close()

    if not row:
        return None

    footprint, fp_filters = row
    if footprint:
        return footprint  # Already has a footprint

    if not fp_filters:
        return None

    filters = json.loads(fp_filters)
    if not filters:
        return None

    import fnmatch
    index = _build_footprint_index()

    # Priority categories for common components
    PRIORITY_CATEGORIES = {
        "R": ["Resistor_SMD", "Resistor_THT"],
        "C": ["Capacitor_SMD", "Capacitor_THT"],
        "L": ["Inductor_SMD", "Inductor_THT"],
        "LED": ["LED_SMD", "LED_THT"],
        "D": ["Diode_SMD", "Diode_THT"],
    }

    # Get component name from id_str
    _, _, comp_name = id_str.partition(":")
    comp_base = comp_name.split("_")[0] if comp_name else ""
    priority_cats = PRIORITY_CATEGORIES.get(comp_base, [])

    for filter_str in filters:
        patterns = filter_str.split()
        for pattern in patterns:
            # Check if pattern has category prefix (e.g., "Connector*:*_1x??_*")
            if ":" in pattern:
                cat_pattern, _, name_pattern = pattern.partition(":")
                # Search matching categories first
                for category_dir in FOOTPRINTS_ROOT.iterdir():
                    if not category_dir.is_dir() or not category_dir.name.endswith(".pretty"):
                        continue
                    category_name = category_dir.name.replace(".pretty", "")
                    if fnmatch.fnmatch(category_name, cat_pattern):
                        for footprint_file in category_dir.glob("*.kicad_mod"):
                            footprint_name = footprint_file.stem
                            candidate = f"{category_name}:{footprint_name}"
                            if fnmatch.fnmatch(footprint_name, name_pattern):
                                if footprint_path_for(candidate).is_file():
                                    return candidate
            else:
                # Simple pattern without category prefix
                # Search priority categories first
                for cat_name in priority_cats:
                    category_dir = FOOTPRINTS_ROOT / f"{cat_name}.pretty"
                    if not category_dir.is_dir():
                        continue
                    for footprint_file in category_dir.glob("*.kicad_mod"):
                        footprint_name = footprint_file.stem
                        candidate = f"{cat_name}:{footprint_name}"
                        if fnmatch.fnmatch(footprint_name, pattern):
                            if footprint_path_for(candidate).is_file():
                                return candidate

                # Try prefix-based lookup (fast)
                prefix = pattern.split("*")[0].split("_")[0] if "*" in pattern else pattern.split("_")[0]
                if prefix in index:
                    for candidate in index[prefix]:
                        footprint_name = candidate.split(":")[1]
                        cat_name = candidate.split(":")[0]
                        # Skip priority categories already searched
                        if cat_name in priority_cats:
                            continue
                        if fnmatch.fnmatch(footprint_name, pattern):
                            if footprint_path_for(candidate).is_file():
                                return candidate

                # Fallback: search all remaining footprints
                for category_dir in FOOTPRINTS_ROOT.iterdir():
                    if not category_dir.is_dir() or not category_dir.name.endswith(".pretty"):
                        continue
                    category_name = category_dir.name.replace(".pretty", "")
                    if category_name in priority_cats:
                        continue
                    for footprint_file in category_dir.glob("*.kicad_mod"):
                        footprint_name = footprint_file.stem
                        candidate = f"{category_name}:{footprint_name}"
                        if fnmatch.fnmatch(footprint_name, pattern):
                            if footprint_path_for(candidate).is_file():
                                return candidate

    return None


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