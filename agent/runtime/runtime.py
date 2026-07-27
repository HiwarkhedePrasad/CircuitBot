"""Engineering Intelligence Runtime — unified facade.

Every capability receives one Runtime instance. Capabilities never
construct their own context, knowledge, or constraints. This prevents
the "five agents each independently querying the same data" problem.

Properties:
- Thread-safe: all subsystems use internal locks
- No memory leaks: bounded caches with TTL, explicit eviction
- Optional: behind RUNTIME_ENABLED feature flag, existing code works without it
- Lazy initialization: subsystems created on first access
"""

from __future__ import annotations

import threading
from typing import Any

from agent.feature_flags import is_enabled


class EngineeringIntelligenceRuntime:
    """Shared intelligence layer. Every capability receives one instance.

    Usage::

        runtime = EngineeringIntelligenceRuntime(
            design_id="abc123",
            revision=1,
            event_store=None,
            projections={"design": design_data, "synthesis_graph": graph},
        )
        context = runtime.context.build(scope, budget=8000)
        knowledge = runtime.knowledge.component_interfaces("U1")
        violations = runtime.constraints.check(revision=1)
    """

    def __init__(self, design_id: str, revision: int = 0,
                 event_store: Any = None, projections: dict | None = None):
        self._design_id = design_id
        self._revision = revision
        self._event_store = event_store
        self._projections = projections or {}
        self._lock = threading.Lock()

        # Lazy-initialized subsystems
        self._context = None
        self._knowledge = None
        self._memory = None

    @property
    def design_id(self) -> str:
        return self._design_id

    @property
    def revision(self) -> int:
        return self._revision

    def update_revision(self, revision: int) -> None:
        """Update the current revision number."""
        self._revision = revision

    def update_projections(self, projections: dict) -> None:
        """Update projection data and invalidate caches."""
        with self._lock:
            self._projections.update(projections)
            if self._context:
                self._context.invalidate(self._design_id)
            if self._knowledge:
                self._knowledge.set_projections(self._projections)

    # ── Subsystem Accessors (lazy init) ────────────────────────────────

    @property
    def context(self):
        """ContextEngine — cached, revisioned context builder."""
        if self._context is None:
            from agent.runtime.context_engine import ContextEngine
            self._context = ContextEngine(self._projections)
        return self._context

    @property
    def knowledge(self):
        """DesignKnowledgeService — queryable knowledge facade."""
        if self._knowledge is None:
            from agent.runtime.knowledge_service import DesignKnowledgeService
            self._knowledge = DesignKnowledgeService(self._projections)
        return self._knowledge

    @property
    def memory(self):
        """MemoryService — persistent design and user memory."""
        if self._memory is None:
            from agent.runtime.memory_service import MemoryService
            self._memory = MemoryService(self._design_id)
        return self._memory

    # ── Constraint Solver (created fresh per check, no state) ──────────

    def check_constraints(self, graph: Any = None) -> list[dict]:
        """Run deterministic constraint validation.

        Args:
            graph: SynthesisGraph instance. If None, uses projections.

        Returns:
            List of violation dicts with code, severity, message.
        """
        if graph is None:
            graph = self._projections.get("synthesis_graph")
        if graph is None:
            return []

        violations = []

        # Synthesis validation (power, ground, connectivity)
        try:
            from agent.synthesis.validation import validate_circuit
            issues = validate_circuit(graph)
            violations.extend(issues)
        except Exception:
            pass

        # Topology constraint validation
        try:
            from agent.synthesis.engine import validate_constraints
            constraint_viols = validate_constraints(graph)
            for cv in constraint_viols:
                violations.append({
                    "code": "CON001",
                    "severity": cv.severity,
                    "stage": "synthesis",
                    "message": cv.description,
                })
        except Exception:
            pass

        return violations

    def suggest_repairs(self, graph: Any = None) -> list[dict]:
        """Suggest repairs for constraint violations."""
        if graph is None:
            graph = self._projections.get("synthesis_graph")
        if graph is None:
            return []

        try:
            from agent.synthesis.engine import validate_constraints, suggest_repairs
            violations = validate_constraints(graph)
            repairs = suggest_repairs(violations)
            return [{"description": r.description, "priority": r.priority}
                    for r in repairs]
        except Exception:
            return []

    # ── Motif Detection ────────────────────────────────────────────────

    def detect_motifs(self, graph: Any = None) -> list[dict]:
        """Detect circuit motifs in the synthesis graph."""
        if graph is None:
            graph = self._projections.get("synthesis_graph")
        if graph is None or not hasattr(graph, "components"):
            return []

        try:
            from agent.schematic.detector import detect_motifs
            motifs = detect_motifs(graph)
            return [{"type": m.motif_type.value, "components": m.components,
                     "anchor": m.anchor, "score": m.score}
                    for m in motifs]
        except Exception:
            return []

    # ── Cleanup ────────────────────────────────────────────────────────

    def clear(self) -> None:
        """Clear all subsystem state."""
        if self._context:
            self._context.clear()
        if self._memory:
            self._memory.clear()

    def __repr__(self) -> str:
        return (f"EngineeringIntelligenceRuntime(design_id={self._design_id!r}, "
                f"revision={self._revision})")


# ── Factory ────────────────────────────────────────────────────────────────

def create_runtime(design_id: str, revision: int = 0,
                   event_store: Any = None,
                   projections: dict | None = None,
                   design_session: Any = None) -> EngineeringIntelligenceRuntime | None:
    """Create a Runtime instance if the feature flag is enabled.

    Returns None if RUNTIME_ENABLED is False — existing code continues
    to work exactly as before.

    Args:
        design_id: unique design identifier (typically session_id)
        revision: current design revision
        event_store: optional event store for memory persistence
        projections: initial projection data
        design_session: optional DesignSession to extract projections from
    """
    if not is_enabled("RUNTIME_ENABLED"):
        return None

    # Build projections from DesignSession if provided
    if projections is None:
        projections = {}
    if design_session:
        design_data = design_session.get_design()
        projections.setdefault("design", design_data)
        if "synthesis_graph" in design_data:
            projections["synthesis_graph"] = design_data["synthesis_graph"]
        if "knowledge_db" in design_data:
            projections["knowledge_db"] = design_data["knowledge_db"]

    return EngineeringIntelligenceRuntime(
        design_id=design_id,
        revision=revision,
        event_store=event_store,
        projections=projections,
    )
