"""Context Engine — cached, revisioned, token-budgeted context builder.

Builds structured context from projections. Caches results keyed by
(design_id, revision, scope_hash) with TTL expiry and size bounds.

Thread-safe: all public methods acquire a lock before cache mutation.
No memory leaks: bounded cache with LRU eviction + TTL expiry.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkScope:
    """Defines what the context should cover."""
    stage: str                              # "netlist", "placement", "routing", etc.
    component_refs: list[str] = field(default_factory=list)
    net_names: list[str] = field(default_factory=list)
    include_research: bool = False
    include_validation: bool = False

    def cache_key(self) -> str:
        parts = [self.stage, ",".join(sorted(self.component_refs)),
                 ",".join(sorted(self.net_names)),
                 str(self.include_research), str(self.include_validation)]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


@dataclass
class Context:
    """Structured context returned by the engine."""
    design_summary: str = ""
    entities: dict[str, Any] = field(default_factory=dict)
    constraints: list[dict] = field(default_factory=list)
    design_intent: dict = field(default_factory=dict)
    user_preferences: dict = field(default_factory=dict)
    change_context: str = ""
    open_findings: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    token_estimate: int = 0


class ContextEngine:
    """Cached context builder with bounded LRU cache and TTL expiry.

    Cache properties:
        max_entries: maximum number of cached contexts (default 64)
        ttl_seconds: time-to-live for cache entries (default 300s = 5 min)
    """

    def __init__(self, projections: dict | None = None, *,
                 max_entries: int = 64, ttl_seconds: float = 300.0):
        self._projections = projections or {}
        self._cache: OrderedDict[str, tuple[float, Context]] = OrderedDict()
        self._max_entries = max_entries
        self._ttl = ttl_seconds
        self._lock = threading.Lock()

    def build(self, design_id: str, revision: int, scope: WorkScope,
              budget: int = 8000) -> Context:
        """Build or retrieve cached context.

        Args:
            design_id: unique design identifier
            revision: current design revision number
            scope: what the context should cover
            budget: max token estimate for the context

        Returns:
            Context with entities truncated to fit budget.
        """
        cache_key = f"{design_id}:{revision}:{scope.cache_key()}"
        now = time.monotonic()

        with self._lock:
            # Check cache
            if cache_key in self._cache:
                expiry, ctx = self._cache[cache_key]
                if now < expiry:
                    self._cache.move_to_end(cache_key)
                    return ctx
                else:
                    del self._cache[cache_key]

            # Build fresh context
            ctx = self._build_context(design_id, revision, scope, budget)

            # Store in cache with eviction
            self._cache[cache_key] = (now + self._ttl, ctx)
            self._cache.move_to_end(cache_key)
            self._evict_expired(now)
            self._evict_oldest()

        return ctx

    def invalidate(self, design_id: str | None = None, revision: int | None = None) -> int:
        """Invalidate cache entries. Returns count of entries removed.

        Args:
            design_id: if provided, only invalidate entries for this design
            revision: if provided with design_id, only invalidate up to this revision
        """
        removed = 0
        with self._lock:
            keys_to_remove = []
            for key in self._cache:
                parts = key.split(":")
                if design_id and parts[0] != design_id:
                    continue
                if revision is not None and len(parts) > 1:
                    try:
                        if int(parts[1]) > revision:
                            continue
                    except ValueError:
                        pass
                keys_to_remove.append(key)
            for key in keys_to_remove:
                del self._cache[key]
                removed += 1
        return removed

    def clear(self) -> None:
        """Clear entire cache."""
        with self._lock:
            self._cache.clear()

    @property
    def cache_size(self) -> int:
        """Current number of cached entries."""
        with self._lock:
            return len(self._cache)

    def _build_context(self, design_id: str, revision: int,
                       scope: WorkScope, budget: int) -> Context:
        """Build context from projections."""
        ctx = Context()

        # Design summary from projections
        design_data = self._projections.get("design", {})
        ctx.design_summary = self._summarize_design(design_data, scope)

        # Entity summaries (token-budgeted)
        entities = self._extract_entities(design_data, scope)
        ctx.entities = self._fit_to_budget(entities, budget // 2)

        # Constraints from synthesis graph
        graph_data = self._projections.get("synthesis_graph")
        if graph_data and isinstance(graph_data, dict):
            ctx.constraints = graph_data.get("constraints", [])
        elif graph_data and hasattr(graph_data, "constraints"):
            ctx.constraints = [
                {"type": ct.type.value, "source_pin": ct.source_pin,
                 "target_pin": ct.target_pin, "metadata": ct.metadata}
                for ct in graph_data.constraints
            ]

        # Design intent
        ctx.design_intent = self._projections.get("design_intent", {})

        # User preferences
        ctx.user_preferences = self._projections.get("user_preferences", {})

        # Open findings
        ctx.open_findings = self._projections.get("findings", [])

        # Decisions
        ctx.decisions = self._projections.get("decisions", [])

        # Token estimate
        ctx.token_estimate = self._estimate_tokens(ctx)

        return ctx

    def _summarize_design(self, design_data: dict, scope: WorkScope) -> str:
        """Generate a compact design summary."""
        parts = []
        comps = design_data.get("selected_components", [])
        if comps:
            refs = [c.get("ref_des", "?") for c in comps[:10]]
            parts.append(f"{len(comps)} components: {', '.join(refs)}")
        nets = design_data.get("nets", [])
        if nets:
            parts.append(f"{len(nets)} nets")
        return "; ".join(parts) if parts else "No design data"

    def _extract_entities(self, design_data: dict, scope: WorkScope) -> dict[str, Any]:
        """Extract entity summaries for the requested scope."""
        entities = {}
        comps = design_data.get("selected_components", [])
        for c in comps:
            ref = c.get("ref_des", "")
            if scope.component_refs and ref not in scope.component_refs:
                continue
            entities[ref] = {
                "id_str": c.get("id_str", ""),
                "category": c.get("category", ""),
                "description": c.get("description", "")[:100],
            }
        return entities

    def _fit_to_budget(self, entities: dict, half_budget: int) -> dict:
        """Truncate entity summaries to fit within token budget."""
        if not entities:
            return entities
        # Rough estimate: 4 tokens per entity summary
        max_entities = max(half_budget // 4, 1)
        if len(entities) <= max_entities:
            return entities
        # Keep most important entities (shortest descriptions first = most specific)
        sorted_items = sorted(entities.items(), key=lambda x: len(str(x[1])))
        return dict(sorted_items[:max_entities])

    def _estimate_tokens(self, ctx: Context) -> int:
        """Rough token estimate for the context."""
        total = 0
        total += len(ctx.design_summary.split()) * 2
        for entity in ctx.entities.values():
            total += len(str(entity).split()) * 2
        total += len(ctx.constraints) * 10
        total += len(ctx.change_context.split()) * 2
        return total

    def _evict_expired(self, now: float) -> None:
        """Remove expired entries (must hold lock)."""
        expired = [k for k, (exp, _) in self._cache.items() if now >= exp]
        for k in expired:
            del self._cache[k]

    def _evict_oldest(self) -> None:
        """Remove oldest entries if over capacity (must hold lock)."""
        while len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)
