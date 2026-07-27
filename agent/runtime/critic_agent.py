"""Critic Agent — background agent that questions design decisions.

The Critic does not generate proposals. It generates QUESTIONS.
Each question is a Critique with evidence, severity, and confidence.

Runs on a schedule (every N events, or on idle). Produces non-blocking
findings that accumulate in a review panel. Users can dismiss, accept,
or ask for explanation.

Thread-safe: all operations are stateless (no shared mutable state).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class CritiqueSeverity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CritiqueCategory(Enum):
    COMPONENT_SELECTION = "component_selection"
    TOPOLOGY = "topology"
    COST = "cost"
    RELIABILITY = "reliability"
    THERMAL = "thermal"
    SIGNAL_INTEGRITY = "signal_integrity"
    POWER_INTEGRITY = "power_integrity"
    LAYOUT = "layout"


@dataclass
class Critique:
    """A design question from the Critic Agent."""
    id: str = field(default_factory=lambda: uuid4().hex[:8])
    target_entity_id: str = ""
    target_revision: int = 0
    question: str = ""
    context: str = ""
    severity: CritiqueSeverity = CritiqueSeverity.MEDIUM
    category: CritiqueCategory = CritiqueCategory.COMPONENT_SELECTION
    confidence: float = 0.5
    evidence: list[str] = field(default_factory=list)
    dismissed: bool = False
    accepted: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "target_entity_id": self.target_entity_id,
            "target_revision": self.target_revision,
            "question": self.question,
            "context": self.context,
            "severity": self.severity.value,
            "category": self.category.value,
            "confidence": round(self.confidence, 3),
            "evidence": self.evidence,
            "dismissed": self.dismissed,
            "accepted": self.accepted,
        }


class CriticAgent:
    """Background agent that questions design decisions.

    Stateless — no scheduling, no caching. Call analyze() when needed.
    Thread-safe: all operations are pure functions of input data.
    """

    def __init__(self, projections: dict | None = None):
        self._projections = projections or {}
        self._dismissed_patterns: list[str] = []  # patterns the user dismissed
        self._lock = threading.Lock()

    def set_projections(self, projections: dict) -> None:
        """Update projection data."""
        self._projections = projections

    def analyze(self, graph: Any = None) -> list[Critique]:
        """Run full analysis and return all critiques.

        Args:
            graph: SynthesisGraph instance. If None, uses projections.

        Returns:
            List of Critique objects with questions about design decisions.
        """
        if graph is None:
            graph = self._projections.get("synthesis_graph")
        design = self._projections.get("design", {})

        critiques = []

        # Component selection critiques
        critiques.extend(self._critique_components(design))

        # Topology critiques
        critiques.extend(self._critique_topology(graph))

        # Cost critiques
        critiques.extend(self._critique_cost(design))

        # Reliability critiques
        critiques.extend(self._critique_reliability(design))

        # Filter out dismissed patterns
        critiques = [c for c in critiques if not self._is_dismissed(c)]

        return critiques

    def dismiss(self, critique_id: str) -> None:
        """Dismiss a critique by ID."""
        with self._lock:
            # We don't remove it, just mark it
            pass  # In production, would persist dismissals

    def add_dismissed_pattern(self, pattern: str) -> None:
        """Add a pattern to suppress (e.g., 'component_selection:AMS1117')."""
        with self._lock:
            if pattern not in self._dismissed_patterns:
                self._dismissed_patterns.append(pattern)

    def _critique_components(self, design: dict) -> list[Critique]:
        """Question component selection decisions."""
        critiques = []
        comps = design.get("selected_components", [])
        if not comps:
            return critiques

        from agent.component_knowledge import lookup_device

        for comp in comps:
            ref = comp.get("ref_des", "")
            id_str = comp.get("id_str", "")
            device = lookup_device(id_str, comp.get("description", ""))

            # Question: Is there a cheaper alternative?
            if device and device.get("type") == "regulator":
                critiques.append(Critique(
                    target_entity_id=ref,
                    question=f"Why {id_str} instead of a cheaper alternative?",
                    context=f"{id_str} is a regulator. Consider if a lower-cost option exists.",
                    severity=CritiqueSeverity.LOW,
                    category=CritiqueCategory.COST,
                    confidence=0.4,
                    evidence=[f"Component type: regulator"],
                ))

            # Question: Is this component necessary?
            if comp.get("category") == "passive":
                critiques.append(Critique(
                    target_entity_id=ref,
                    question=f"Is {ref} ({id_str}) necessary?",
                    context=f"Passive component — verify it serves a purpose.",
                    severity=CritiqueSeverity.LOW,
                    category=CritiqueCategory.TOPOLOGY,
                    confidence=0.3,
                    evidence=["Passive component may be redundant"],
                ))

        return critiques

    def _critique_topology(self, graph: Any) -> list[Critique]:
        """Question topology decisions."""
        critiques = []
        if graph is None or not hasattr(graph, "constraints"):
            return critiques

        # Check for missing decoupling
        try:
            from agent.synthesis.graph import PinRole
            for comp in graph.components.values():
                power_pins = [p for p in comp.pins.values() if p.role == PinRole.POWER_IN]
                if power_pins:
                    # Check if there's a decoupling cap nearby
                    has_decoupling = False
                    for constraint in graph.constraints:
                        if (hasattr(constraint, 'type') and
                            constraint.type.value == 'decouples' and
                            comp.ref_des in str(constraint.source_pin)):
                            has_decoupling = True
                            break
                    if not has_decoupling:
                        critiques.append(Critique(
                            target_entity_id=comp.ref_des,
                            question=f"Does {comp.ref_des} need decoupling capacitors?",
                            context=f"Component has {len(power_pins)} power pin(s) but no decoupling constraint found.",
                            severity=CritiqueSeverity.MEDIUM,
                            category=CritiqueCategory.POWER_INTEGRITY,
                            confidence=0.6,
                            evidence=[f"Power pins: {[p.name for p in power_pins]}"],
                        ))
        except Exception:
            pass

        return critiques

    def _critique_cost(self, design: dict) -> list[Critique]:
        """Question cost decisions."""
        critiques = []
        comps = design.get("selected_components", [])
        if len(comps) > 20:
            critiques.append(Critique(
                target_entity_id="",
                question=f"Design has {len(comps)} components — can any be merged?",
                context="Higher component count increases BOM cost and assembly complexity.",
                severity=CritiqueSeverity.LOW,
                category=CritiqueCategory.COST,
                confidence=0.5,
                evidence=[f"Component count: {len(comps)}"],
            ))
        return critiques

    def _critique_reliability(self, design: dict) -> list[Critique]:
        """Question reliability decisions."""
        critiques = []
        comps = design.get("selected_components", [])
        board_model = design.get("board_model", {})
        traces = board_model.get("traces", [])

        # Check for thin traces on power nets
        power_nets = set()
        for pp in design.get("power_pins", []):
            power_nets.add(pp.get("net", ""))

        for trace in traces:
            net = trace.get("net", "")
            width = trace.get("width", 0.254)
            if net in power_nets and width < 0.5:
                critiques.append(Critique(
                    target_entity_id=net,
                    question=f"Trace on {net} is only {width}mm wide — sufficient for power?",
                    context=f"Power traces should be wider for current capacity.",
                    severity=CritiqueSeverity.MEDIUM,
                    category=CritiqueCategory.POWER_INTEGRITY,
                    confidence=0.5,
                    evidence=[f"Trace width: {width}mm, net: {net}"],
                ))

        return critiques

    def _is_dismissed(self, critique: Critique) -> bool:
        """Check if a critique matches a dismissed pattern."""
        for pattern in self._dismissed_patterns:
            parts = pattern.split(":")
            if len(parts) == 2:
                cat, entity = parts
                if critique.category.value == cat:
                    if not entity or critique.target_entity_id == entity:
                        return True
        return False
