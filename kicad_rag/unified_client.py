"""
Unified component client — combines KiCad RAG + tscircuit data sources.

Provides a single search interface that queries both databases and merges
results, giving CircuitBot access to 50K+ KiCad parts + tscircuit registry.
"""

import json
import logging
from typing import List, Optional

from kicad_rag.client import KicadRAG, Result as KicadResult

logger = logging.getLogger(__name__)

# Lazy import to avoid circular dependencies
_tscircuit_client = None


def _get_tscircuit():
    """Lazy-load the tscircuit client."""
    global _tscircuit_client
    if _tscircuit_client is None:
        try:
            from tscircuit_data import TscircuitClient
            _tscircuit_client = TscircuitClient(cache_enabled=True)
        except ImportError:
            logger.debug("tscircuit_data not installed, using KiCad RAG only")
    return _tscircuit_client


class UnifiedClient:
    """Component search client that queries both KiCad RAG and tscircuit.

    Usage:
        client = UnifiedClient()
        results = client.search("ESP32")  # searches both sources
    """

    def __init__(self, mode: str = "hybrid"):
        self.kicad = KicadRAG(mode=mode)

    def search(self, query: str, k: int = 5,
               mode: Optional[str] = None,
               library_filter: Optional[str] = None,
               tscircuit_only: bool = False) -> List[KicadResult]:
        """Search both KiCad RAG and tscircuit registry.

        Args:
            query: Search term (part number, description, category)
            k: Max results per source
            mode: Override retriever mode
            library_filter: Optional library filter for KiCad
            tscircuit_only: If True, only search tscircuit

        Returns:
            Merged and deduplicated list of Result objects
        """
        results = []

        # Search KiCad RAG (primary source)
        if not tscircuit_only:
            try:
                kicad_results = self.kicad.search(query, k=k, mode=mode, library_filter=library_filter)
                results.extend(kicad_results)
            except Exception as e:
                logger.warning(f"KiCad RAG search failed: {e}")

        # Search tscircuit (secondary source)
        tc = _get_tscircuit()
        if tc:
            try:
                tc_components = tc.search(query, limit=k)
                for comp in tc_components:
                    # Convert tscircuit component to KicadResult for compatibility
                    pins = [{"number": p.number, "name": p.name, "type": p.type}
                            for p in comp.pins] if comp.pins else []
                    results.append(KicadResult(
                        id_str=comp.id_str,
                        text=comp.description or comp.name,
                        score=0.5,  # Default score for tscircuit results
                        rank=len(results) + 1,
                        pins=pins,
                        datasheet=comp.datasheet,
                        footprint=comp.footprint.name if comp.footprint else None,
                        pads=comp.footprint.pads if comp.footprint else None,
                    ))
            except Exception as e:
                logger.warning(f"tscircuit search failed: {e}")

        # Deduplicate by id_str (prefer KiCad results)
        seen = set()
        deduped = []
        for r in results:
            if r.id_str not in seen:
                seen.add(r.id_str)
                deduped.append(r)

        # Re-rank by score
        deduped.sort(key=lambda x: x.score, reverse=True)

        return deduped[:k * 2]  # Return up to 2x requested to give variety

    def pins(self, id_str: str) -> List[dict]:
        """Get pin list for a component."""
        # Try KiCad first
        pins = self.kicad.pins(id_str)
        if pins:
            return pins

        # Try tscircuit
        tc = _get_tscircuit()
        if tc:
            comp = tc.get_component(id_str)
            if comp and comp.pins:
                return [{"number": p.number, "name": p.name, "type": p.type}
                        for p in comp.pins]

        return []

    def sexpr(self, id_str: str) -> str:
        """Get KiCad S-expression for a component."""
        return self.kicad.sexpr(id_str)

    def footprint(self, id_str: str) -> Optional[dict]:
        """Get footprint info for a component."""
        fp = self.kicad.footprint(id_str)
        if fp:
            return fp

        # Try tscircuit
        tc = _get_tscircuit()
        if tc:
            comp = tc.get_component(id_str)
            if comp and comp.footprint:
                return {
                    "footprint": comp.footprint.name,
                    "pads": comp.footprint.pads,
                }

        return None

    def pads(self, id_str: str) -> List[dict]:
        """Get pad list for a component's default footprint."""
        pads = self.kicad.pads(id_str)
        if pads:
            return pads

        # Try tscircuit
        tc = _get_tscircuit()
        if tc:
            comp = tc.get_component(id_str)
            if comp and comp.footprint and comp.footprint.pads:
                return comp.footprint.pads

        return []
