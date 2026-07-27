"""Design Knowledge Service — queryable facade over existing extractors.

Provides a single interface to:
- Component knowledge (pin roles, interfaces, requirements)
- Net knowledge (voltage, classification)
- Trace knowledge (impedance, current capacity)
- Library knowledge (RAG search)
- User preferences

Read-only: never mutates design state. Deterministic queries are instant;
non-deterministic queries are cached per (design_id, query_hash).

Thread-safe: all public methods are safe for concurrent access.
No memory leaks: bounded cache with TTL.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PartSummary:
    """Summary of a component from the library."""
    id_str: str
    text: str = ""
    footprint: str = ""
    pins: list[dict] = field(default_factory=list)
    score: float = 0.0


class DesignKnowledgeService:
    """Queryable knowledge service backed by existing extractors.

    Properties:
        max_cache: maximum cached query results (default 128)
        ttl: cache TTL in seconds (default 600 = 10 min)
    """

    def __init__(self, projections: dict | None = None, *,
                 max_cache: int = 128, ttl: float = 600.0):
        self._projections = projections or {}
        self._cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._max_cache = max_cache
        self._ttl = ttl
        self._lock = threading.Lock()

    def set_projections(self, projections: dict) -> None:
        """Update the projections data source."""
        self._projections = projections

    # ── Component Knowledge ────────────────────────────────────────────

    def component_interfaces(self, component_id: str) -> list[str]:
        """Detect interfaces for a component (UART, I2C, SPI, USB, etc.)."""
        cache_key = f"iface:{component_id}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        knowledge = self._extract_component_knowledge(component_id)
        result = knowledge.get("interfaces", [])
        self._set_cached(cache_key, result)
        return result

    def component_pin_roles(self, component_id: str) -> dict[str, str]:
        """Get pin roles for a component."""
        knowledge = self._extract_component_knowledge(component_id)
        return knowledge.get("pin_roles", {})

    def component_requirements(self, component_id: str) -> dict:
        """Get component requirements (voltage, decoupling, etc.)."""
        from agent.component_knowledge import lookup_device
        knowledge = self._extract_component_knowledge(component_id)
        device_info = lookup_device(component_id)
        return {
            "power_rails": knowledge.get("power_rails", []),
            "programming_pins": knowledge.get("programming_pins", {}),
            "device_info": device_info,
        }

    def component_alternatives(self, component_id: str,
                                constraints: dict | None = None) -> list[PartSummary]:
        """Find alternative components matching constraints."""
        cache_key = f"alt:{component_id}:{hashlib.sha256(str(constraints).encode()).hexdigest()[:8]}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            from agent.tools import search_components
            results = search_components(component_id, k=5)
            alternatives = [
                PartSummary(id_str=r.id_str, text=getattr(r, "text", ""),
                            footprint=getattr(r, "footprint", ""), score=r.score)
                for r in results if r.id_str != component_id
            ]
            self._set_cached(cache_key, alternatives)
            return alternatives
        except Exception:
            return []

    # ── Net Knowledge ──────────────────────────────────────────────────

    def net_classification(self, net_id: str) -> str:
        """Classify a net (POWER, GROUND, SIGNAL, CLOCK, etc.)."""
        from agent.synthesis.graph import NetRole
        role = NetRole.from_net_name(net_id)
        return role.value

    def net_voltage(self, net_id: str) -> float | None:
        """Get the voltage of a net from design data."""
        design = self._projections.get("design", {})
        power_labels = design.get("power_labels", [])
        for label in power_labels:
            if label.get("net", "").upper() == net_id.upper():
                return label.get("voltage")
        return None

    # ── Trace Knowledge ────────────────────────────────────────────────

    def trace_impedance(self, trace_id: str) -> float | None:
        """Calculate trace impedance using IPC formulas."""
        try:
            from agent.tools import calculate_microstrip_impedance
            board = self._projections.get("board_model", {})
            traces = board.get("traces", [])
            for t in traces:
                if t.get("net") == trace_id:
                    width = t.get("width", 0.254)
                    return calculate_microstrip_impedance(width, 0.035, 1.5, 4.5)
        except Exception:
            pass
        return None

    def trace_current_capacity(self, trace_id: str) -> float | None:
        """Calculate max current capacity for a trace width."""
        try:
            from agent.tools import calculate_max_current
            board = self._projections.get("board_model", {})
            traces = board.get("traces", [])
            for t in traces:
                if t.get("net") == trace_id:
                    width = t.get("width", 0.254)
                    return calculate_max_current(width, 0.035, 25.0)
        except Exception:
            pass
        return None

    # ── Library Knowledge ──────────────────────────────────────────────

    def lookup_part(self, id_str: str) -> PartSummary | None:
        """Look up a component in the KiCad library."""
        cache_key = f"part:{id_str}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            from agent.tools import search_components, fetch_pins, fetch_footprint
            results = search_components(id_str, k=1)
            if results:
                r = results[0]
                pins = fetch_pins(id_str) or []
                fp = fetch_footprint(id_str) or {}
                part = PartSummary(
                    id_str=r.id_str, text=getattr(r, "text", ""),
                    footprint=fp.get("footprint", ""), pins=pins,
                    score=r.score,
                )
                self._set_cached(cache_key, part)
                return part
        except Exception:
            pass
        return None

    def search_parts(self, query: str, constraints: dict | None = None) -> list[PartSummary]:
        """Search the KiCad library."""
        cache_key = f"search:{query}:{hashlib.sha256(str(constraints).encode()).hexdigest()[:8]}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        try:
            from agent.tools import search_components
            results = search_components(query, k=5)
            parts = [
                PartSummary(id_str=r.id_str, text=getattr(r, "text", ""),
                            footprint=getattr(r, "footprint", ""), score=r.score)
                for r in results
            ]
            self._set_cached(cache_key, parts)
            return parts
        except Exception:
            return []

    # ── User Preferences ───────────────────────────────────────────────

    def user_preferences(self) -> dict:
        """Get user preferences from projections."""
        return self._projections.get("user_preferences", {})

    # ── Engineering Rules ──────────────────────────────────────────────

    def applicable_rules(self, entity_type: str, entity_id: str) -> list[dict]:
        """Get applicable topology/layout rules for an entity."""
        rules = []
        try:
            from agent.schematic.catalog import MOTIF_CATALOG
            for sig in MOTIF_CATALOG:
                if entity_type in ("component",):
                    from agent.schematic.matcher import matches_meta
                    comp = self._get_component(entity_id)
                    if comp and matches_meta(comp, sig.primary_meta):
                        rules.append({
                            "name": sig.name,
                            "category": sig.category.value,
                            "motif_type": sig.motif_type.value,
                        })
        except Exception:
            pass
        return rules

    def companion_requirements(self, component_id: str) -> list[dict]:
        """Get companion component requirements (decoupling caps, pull-ups, etc.)."""
        try:
            from agent.support_rules import get_supporting_components
            comps = self._projections.get("design", {}).get("selected_components", [])
            comp = next((c for c in comps if c.get("id_str") == component_id), None)
            if comp:
                return get_supporting_components(comp, comps)
        except Exception:
            pass
        return []

    # ── Cache Management ───────────────────────────────────────────────

    def _get_cached(self, key: str) -> Any | None:
        """Get from cache if not expired."""
        now = time.monotonic()
        with self._lock:
            if key in self._cache:
                expiry, value = self._cache[key]
                if now < expiry:
                    self._cache.move_to_end(key)
                    return value
                else:
                    del self._cache[key]
        return None

    def _set_cached(self, key: str, value: Any) -> None:
        """Store in cache with eviction."""
        now = time.monotonic()
        with self._lock:
            self._cache[key] = (now + self._ttl, value)
            self._cache.move_to_end(key)
            # Evict oldest if over capacity
            while len(self._cache) > self._max_cache:
                self._cache.popitem(last=False)

    def clear_cache(self) -> int:
        """Clear the cache. Returns count of entries removed."""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            return count

    # ── Internal Helpers ───────────────────────────────────────────────

    def _extract_component_knowledge(self, component_id: str) -> dict:
        """Extract knowledge for a component from projections or live extraction."""
        # Check if knowledge_db is in projections
        knowledge_db = self._projections.get("knowledge_db", {})
        if component_id in knowledge_db:
            return knowledge_db[component_id]

        # Live extraction
        try:
            from agent.knowledge_extractor import extract_knowledge
            design = self._projections.get("design", {})
            comps = design.get("selected_components", [])
            pin_matrix = design.get("pin_matrix", {})
            comp = next((c for c in comps if c.get("id_str") == component_id), None)
            if comp:
                comp_pins = {k: v for k, v in pin_matrix.items()
                             if k.startswith(comp["ref_des"] + ":")}
                return extract_knowledge(comp, comp_pins)
        except Exception:
            pass
        return {}

    def _get_component(self, component_id: str) -> dict | None:
        """Get a component dict from projections."""
        design = self._projections.get("design", {})
        comps = design.get("selected_components", [])
        return next((c for c in comps if c.get("id_str") == component_id), None)
