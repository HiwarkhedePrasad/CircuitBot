"""Unified command-line interface for the KiCad-RAG pipeline.

Usage::

    python -m kicad_rag build                          # full build
    python -m kicad_rag build --limit 2                # smoke test
    python -m kicad_rag build --fts-only               # retrofit BM25 only

    python -m kicad_rag search "AMS1117-3.3" -k 3     # hybrid (default)
    python -m kicad_rag search "AMS1117" --mode dense  # dense only
    python -m kicad_rag search "AMS1117" --mode bm25   # lexical only

    python -m kicad_rag fetch "Regulator_Linear:AMS1117-3.3"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from kicad_rag import KicadRAG
from kicad_rag.constants import SYMBOLS_ROOT
from kicad_rag.store import fetch_sexpr
from kicad_rag.builder import build_full, build_fts_only


# ── helpers ──────────────────────────────────────────────────────────────────


def _parse_symbols_path(s: str) -> Path:
    p = Path(s)
    if not p.is_dir():
        raise ValueError(f"not a directory: {s}")
    return p


# ── sub-commands ─────────────────────────────────────────────────────────────


def cmd_build(args: argparse.Namespace) -> int:
    if args.fts_only:
        return build_fts_only()
    return build_full()


def cmd_search(args: argparse.Namespace) -> int:
    rag = KicadRAG(mode=args.mode)
    results = rag.search(query=args.query, k=args.k)

    if args.json:
        import dataclasses
        out = [{
            "id_str": r.id_str,
            "text": r.text,
            "score": r.score,
            "rank": r.rank,
            "pins": r.pins,
        } for r in results]
        json.dump(out, sys.stdout, indent=2)
        print()
        return 0

    print(f'\nquery: "{args.query}"  (mode={args.mode})\n')
    if not results:
        print("  no results")
        return 0
    for r in results:
        print(f"  [{r.rank}] {r.id_str}   score={r.score:.4f}   "
              f"({len(r.pins)} pins)")
        print(f"        {r.text}")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    raw = fetch_sexpr(args.id_str)
    print(raw, end="")
    return 0


def cmd_pins(args: argparse.Namespace) -> int:
    rag = KicadRAG()
    pins = rag.pins(args.id_str)
    json.dump(pins, sys.stdout, indent=2)
    print()
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """List all categories or all symbols matching a filter."""
    symdirs = [
        d.name.removesuffix(".kicad_symdir")
        for d in sorted(SYMBOLS_ROOT.iterdir())
        if d.suffix == ".kicad_symdir"
    ]
    if args.pattern:
        import fnmatch
        symdirs = [c for c in symdirs if fnmatch.fnmatch(c, args.pattern)]
    for c in symdirs:
        print(c)
    return 0


# ── main ─────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="kicad-rag",
        description="KiCad component RAG pipeline — "
                    "embed, index, search, and retrieve S-expressions.",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    # build
    bp = sub.add_parser("build", help="Build the corpus, SQLite, and index")
    bp.add_argument("--fts-only", action="store_true",
                    help="Only rebuild FTS5 table (no re-embedding)")
    bp.set_defaults(func=cmd_build)

    # search
    sp = sub.add_parser("search", help="Hybrid / dense / BM25 search")
    sp.add_argument("query", help="Part number or natural-language description")
    sp.add_argument("-k", type=int, default=5, help="Number of results")
    sp.add_argument("--mode", choices=("hybrid", "dense", "bm25"),
                    default="hybrid")
    sp.add_argument("--json", action="store_true",
                    help="Output results as JSON")
    sp.set_defaults(func=cmd_search)

    # fetch
    fp = sub.add_parser("fetch", help="Print raw S-expression for a component")
    fp.add_argument("id_str", metavar="ID",
                    help="e.g. Regulator_Linear:AMS1117-3.3")
    fp.set_defaults(func=cmd_fetch)

    # pins
    pp = sub.add_parser("pins", help="Fetch JSON pin data for a component")
    pp.add_argument("id_str", metavar="ID",
                    help="e.g. Regulator_Linear:AMS1117-3.3")
    pp.set_defaults(func=cmd_pins)

    # list
    lp = sub.add_parser("list", help="List categories (kicad_symdir names)")
    lp.add_argument("pattern", nargs="?", default="*",
                    help="Wildcard pattern to filter (e.g. *Regulator*)")
    lp.set_defaults(func=cmd_list)

    return ap


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
