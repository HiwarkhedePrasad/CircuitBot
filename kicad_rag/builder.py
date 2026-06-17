"""Corpus extraction (S-expression → JSON) and full index building.

These are ``kicad-rag build`` internals.  Typical end-users only ever need
to run the CLI::

    python -m kicad_rag build
    python -m kicad_rag build --limit 5   # smoke-test
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

from kicad_rag.constants import (
    DATASET_PATH,
    DATA_DIR,
    EMBED_BATCH,
    EMBED_MODEL,
    FOOTPRINTS_ROOT,
    INDEX_PATH,
    SQLITE_PATH,
    SYMBOLS_ROOT,
    UTILS_ROOT,
)


# ── dataset extraction (S-expressions → JSON corpus) ────────────────────────


def _syspath() -> None:
    """Make sure the KiCad *kicad_sym* parser is importable."""
    p = str(UTILS_ROOT / "common")
    if p not in sys.path:
        sys.path.insert(0, p)


def _props_to_dict(sym) -> dict[str, str]:
    """``symbol.properties`` is ``list[Property]`` — flatten to ``{name: value}``."""
    return {p.name: p.value for p in sym.properties}


def _parse_footprint_pads(footprint_str: str) -> str:
    """Parse a ``.kicad_mod`` file and return JSON string of pad data.

    Returns ``'[]'`` if no footprint string, file missing, or parse error.
    """
    if not footprint_str:
        return "[]"
    cat, _, name = footprint_str.partition(":")
    mod_path = FOOTPRINTS_ROOT / f"{cat}.pretty" / f"{name}.kicad_mod"
    if not mod_path.is_file():
        return "[]"
    try:
        _syspath()
        import kicad_mod  # noqa: E402

        mod = kicad_mod.KicadMod(str(mod_path))
        pads = []
        for p in mod.pads:
            pads.append({
                "number": p["number"],
                "type": p["type"],
                "shape": p["shape"],
                "x": round(p["pos"]["x"], 4),
                "y": round(p["pos"]["y"], 4),
                "ox": round(p["pos"].get("orientation", 0), 4),
                "sx": round(p["size"]["x"], 4),
                "sy": round(p["size"]["y"], 4),
                "layers": p["layers"],
            })
        return json.dumps(pads, separators=(",", ":"))
    except Exception as exc:
        print(f"  ! footprint parse failed {footprint_str}: {exc}", file=sys.stderr)
        return "[]"


def _resolve_inherited(sym):
    """Walk the ``_inheritance`` chain; return the root parent (the one
    that actually carries the pins)."""
    if sym.extends and sym._inheritance:
        return sym._inheritance[-1]
    return sym


def build_dataset(limit: int = 0) -> list[dict]:
    """Parse every ``.kicad_symdir`` in *kicad-symbols* and return the
    list of records ready for embedding and SQLite.

    Parameters
    ----------
    limit:
        Stop after parsing *limit* symbols (not symdirs).  0 = no limit.
    """
    _syspath()
    import kicad_sym  # noqa: E402

    records: list[dict] = []
    symdirs = sorted(
        p for p in SYMBOLS_ROOT.iterdir() if p.suffix == ".kicad_symdir"
    )
    total = len(symdirs)

    for idx, symdir in enumerate(symdirs, 1):
        category = symdir.stem
        print(f"[{idx:3d}/{total}] {category}", flush=True)
        try:
            library = kicad_sym.KicadLibrary.from_dir(str(symdir))
        except Exception as exc:
            print(f"  ! skip {category}: {exc}", file=sys.stderr)
            continue

        for symbol in library.symbols:
            if limit and len(records) >= limit:
                break
            props = _props_to_dict(symbol)
            pin_source = _resolve_inherited(symbol)

            # ki_fp_filters can appear multiple times — collect all
            fp_filters = []
            for p in symbol.properties:
                if p.name == "ki_fp_filters":
                    fp_filters.append(p.value)

            records.append({
                "id": f"{category}:{symbol.name}",
                "text_for_embedding": (
                    f"Component: {symbol.name}. "
                    f"Category: {category}. "
                    f"Description: {props.get('Description', '')}. "
                    f"Keywords: {props.get('ki_keywords', '')}."
                ),
                "pins_ground_truth": [
                    {"num": p.number, "name": p.name, "type": p.etype}
                    for p in pin_source.pins
                ],
                "datasheet": props.get("Datasheet", ""),
                "extends": symbol.extends,
                "footprint": props.get("Footprint", ""),
                "fp_filters": fp_filters,
            })
        if limit and len(records) >= limit:
            break

    return records


def _build_fts(con: sqlite3.Connection) -> None:
    """Create / rebuild the FTS5 virtual table for BM25."""
    con.execute("DROP TABLE IF EXISTS symbols_fts")
    con.execute(
        """CREATE VIRTUAL TABLE symbols_fts USING fts5(
            text, id_str,
            content='symbols', content_rowid='id_int',
            tokenize="unicode61 remove_diacritics 2 tokenchars '.-_'"
        )"""
    )
    con.execute(
        "INSERT INTO symbols_fts(rowid, text, id_str) "
        "SELECT id_int, text, id_str FROM symbols"
    )
    con.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('optimize')")
    con.commit()
    n = con.execute("SELECT count(*) FROM symbols_fts").fetchone()[0]
    print(f"  fts5  : {n:,} rows indexed (BM25)")


def _init_sqlite(records: list[dict]) -> None:
    """Write the ground-truth SQLite store (symbols table + FTS5)."""
    if SQLITE_PATH.exists():
        SQLITE_PATH.unlink()
    con = sqlite3.connect(SQLITE_PATH)
    con.execute(
        """CREATE TABLE symbols (
            id_int    INTEGER PRIMARY KEY,
            id_str    TEXT UNIQUE NOT NULL,
            text      TEXT NOT NULL,
            datasheet TEXT,
            extends   TEXT,
            pins_json TEXT NOT NULL,
            footprint TEXT DEFAULT '',
            fp_filters TEXT DEFAULT '[]',
            pads_json TEXT DEFAULT '[]'
        )"""
    )
    con.execute("CREATE INDEX idx_id_str ON symbols(id_str)")

    rows = [
        (
            i + 1,
            r["id"],
            r["text_for_embedding"],
            r.get("datasheet", ""),
            r.get("extends"),
            json.dumps(r["pins_ground_truth"], separators=(",", ":")),
            r.get("footprint", ""),
            json.dumps(r.get("fp_filters", []), separators=(",", ":")),
            _parse_footprint_pads(r.get("footprint", "")),
        )
        for i, r in enumerate(records)
    ]
    con.executemany("INSERT INTO symbols VALUES (?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    _build_fts(con)
    con.close()
    print(f"  sqlite: {len(rows):,} rows -> {SQLITE_PATH.name}")


def _embed_all(texts: list[str]) -> np.ndarray:
    from fastembed import TextEmbedding

    print(f"  loading model {EMBED_MODEL} ...", flush=True)
    model = TextEmbedding(model_name=EMBED_MODEL)
    print(f"  embedding {len(texts):,} texts (batch={EMBED_BATCH}) ...",
          flush=True)

    t0 = time.time()
    chunks: list[np.ndarray] = []
    done = 0
    for vec in model.embed(texts, batch_size=EMBED_BATCH):
        chunks.append(np.asarray(vec, dtype=np.float32))
        done += 1
        if done % 1000 == 0:
            elapsed = time.time() - t0
            rate = done / elapsed if elapsed else 0
            eta = (len(texts) - done) / rate if rate else 0
            print(f"    {done:>6,}/{len(texts):,}  "
                  f"({rate:.0f}/s  eta {eta:0.0f}s)", flush=True)

    arr = np.vstack(chunks).astype(np.float32, copy=False)
    if not arr.flags["C_CONTIGUOUS"]:
        arr = np.ascontiguousarray(arr)
    print(f"  embedded {arr.shape[0]:,} x {arr.shape[1]} dims  "
          f"in {time.time()-t0:.1f}s", flush=True)
    return arr


def _build_index(vectors: np.ndarray, ids: np.ndarray) -> None:
    from turbovec import IdMapIndex

    print("  building IdMapIndex (bit_width=4) ...", flush=True)
    idx = IdMapIndex(bit_width=4)
    idx.add_with_ids(vectors, ids)
    idx.prepare()
    idx.write(str(INDEX_PATH))
    raw_mb = vectors.nbytes / 1048576
    disk_mb = INDEX_PATH.stat().st_size / 1048576
    print(f"  wrote {INDEX_PATH.name}: {disk_mb:.2f} MB  "
          f"({raw_mb:.2f} MB raw -> {raw_mb / disk_mb:.1f}x compression)")


# ── public entry points ──────────────────────────────────────────────────────


def build_full(limit: int = 0) -> int:
    """Full pipeline: parse *.kicad_sym → embed → index.

    Parameters
    ----------
    limit:
        Stop after *limit* symbols (0 = no limit).

    Produces ``turbovec_dataset.json``, ``circuitbot.sqlite`` (with FTS5),
    and ``circuitbot.tvim`` in *data/*.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print("[1/3] parsing symbols …")
    records = build_dataset(limit=limit)
    print(f"  {len(records):,} records")

    print("[2/3] writing ground-truth sqlite …")
    _init_sqlite(records)

    print("[3/3] embedding + indexing …")
    texts = [r["text_for_embedding"] for r in records]
    vectors = _embed_all(texts)
    ids = np.arange(1, len(records) + 1, dtype=np.uint64)
    _build_index(vectors, ids)

    # persist the intermediate corpus so the user can inspect it
    DATASET_PATH.write_text(
        json.dumps(records, indent=2), encoding="utf-8"
    )
    print(f"  corpus: {len(records):,} records -> {DATASET_PATH.name}")

    print()
    for f in (INDEX_PATH, SQLITE_PATH, DATASET_PATH):
        print(f"  {f.name}  ({f.stat().st_size / 1048576:.2f} MB)")
    return 0


def build_fts_only() -> int:
    """Retrofit FTS5 onto an already-built sqlite (no re-embedding)."""
    if not SQLITE_PATH.is_file():
        print("no sqlite found; run full build first", file=sys.stderr)
        return 1
    con = sqlite3.connect(SQLITE_PATH)
    _build_fts(con)
    con.close()
    return 0