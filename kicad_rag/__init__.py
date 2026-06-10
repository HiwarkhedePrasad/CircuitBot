"""KiCad-RAG: semantic + lexical retrieval over the 22k-component KiCad library.

Two-stage architecture:

1. **Dense** (TurboVec / bge-small-en-v1.5) — natural-language queries like
   *"3.3 V LDO in SOT-223"*.
2. **BM25** (SQLite FTS5) — exact part-number lookups like *"AMS1117-3.3"*.

Both are fused with **weighted Reciprocal Rank Fusion** (BM25 × 2 bias) so the
LLM agent gets the right part even when dense embeddings put noise ahead.

Quick start
-----------

.. code-block:: python

    from kicad_rag import KicadRAG

    rag = KicadRAG()
    results = rag.search("AMS1117-3.3", k=3)
    pins = rag.pins("Regulator_Linear:AMS1117-3.3")
    sexpr = rag.sexpr("Regulator_Linear:AMS1117-3.3")
"""

from kicad_rag.client import KicadRAG, Result

__all__ = ["KicadRAG", "Result"]
