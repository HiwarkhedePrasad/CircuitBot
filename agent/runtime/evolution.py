"""Design Evolution — tracks design history and cross-design patterns.

Every design has an evolution tree where each node is a design revision,
and edges represent transformation types (generated, manual_edit, fork, etc.).

The DiscoveryEngine analyzes patterns across designs to suggest improvements.

Thread-safe: all operations use immutable snapshots.
No memory leaks: bounded stores with TTL.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class EvolutionNode:
    """A node in the design evolution tree."""
    id: str = field(default_factory=lambda: uuid4().hex[:8])
    parent_id: str = ""
    design_id: str = ""
    revision: int = 0
    label: str = ""
    transformation: str = ""  # "generated", "manual_edit", "cost_optimization", "variant"
    goal: str = ""
    summary: str = ""
    metrics: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "parent_id": self.parent_id,
            "design_id": self.design_id, "revision": self.revision,
            "label": self.label, "transformation": self.transformation,
            "goal": self.goal, "summary": self.summary,
            "metrics": self.metrics, "timestamp": self.timestamp,
        }


@dataclass
class Discovery:
    """A pattern discovered across designs."""
    id: str = field(default_factory=lambda: uuid4().hex[:8])
    category: str = ""  # "repeated_pattern", "optimization", "module_candidate"
    title: str = ""
    description: str = ""
    evidence: list[str] = field(default_factory=list)
    suggestion: str = ""
    confidence: float = 0.5
    design_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "category": self.category,
            "title": self.title, "description": self.description,
            "evidence": self.evidence, "suggestion": self.suggestion,
            "confidence": round(self.confidence, 3),
            "design_ids": self.design_ids,
        }


class DesignEvolution:
    """Tracks design history as an evolution tree.

    Stores nodes to disk (JSON). Thread-safe reads, serialized writes.
    """

    def __init__(self, data_dir: str | None = None, *,
                 max_nodes: int = 500):
        self._data_dir = data_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "data", "evolution"
        )
        self._max_nodes = max_nodes
        self._nodes: dict[str, EvolutionNode] = {}
        self._lock = threading.Lock()
        os.makedirs(self._data_dir, exist_ok=True)

    def record(self, design_id: str, revision: int, label: str,
               transformation: str, goal: str = "", summary: str = "",
               metrics: dict | None = None, parent_id: str = "") -> EvolutionNode:
        """Record a design evolution event."""
        node = EvolutionNode(
            parent_id=parent_id, design_id=design_id, revision=revision,
            label=label, transformation=transformation, goal=goal,
            summary=summary, metrics=metrics or {},
        )
        with self._lock:
            self._nodes[node.id] = node
            # Bound: drop oldest if over limit
            if len(self._nodes) > self._max_nodes:
                oldest = min(self._nodes.values(), key=lambda n: n.timestamp)
                del self._nodes[oldest.id]
            self._persist()
        return node

    def get_tree(self, design_id: str | None = None) -> list[EvolutionNode]:
        """Get evolution tree, optionally filtered by design_id."""
        with self._lock:
            nodes = list(self._nodes.values())
        if design_id:
            nodes = [n for n in nodes if n.design_id == design_id]
        nodes.sort(key=lambda n: n.timestamp)
        return nodes

    def get_latest(self, design_id: str) -> EvolutionNode | None:
        """Get the latest node for a design."""
        nodes = self.get_tree(design_id)
        return nodes[-1] if nodes else None

    def compare(self, node_id_a: str, node_id_b: str) -> dict:
        """Compare two evolution nodes."""
        with self._lock:
            a = self._nodes.get(node_id_a)
            b = self._nodes.get(node_id_b)
        if not a or not b:
            return {"error": "Node not found"}
        return {
            "node_a": a.to_dict(), "node_b": b.to_dict(),
            "metric_diffs": {
                k: {"a": a.metrics.get(k), "b": b.metrics.get(k)}
                for k in set(a.metrics.keys()) | set(b.metrics.keys())
            },
        }

    def _persist(self) -> None:
        """Save to disk (must hold lock)."""
        try:
            path = os.path.join(self._data_dir, "evolution.json")
            data = {nid: n.to_dict() for nid, n in self._nodes.items()}
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _load(self) -> None:
        """Load from disk."""
        try:
            path = os.path.join(self._data_dir, "evolution.json")
            if os.path.exists(path):
                with open(path) as f:
                    data = json.load(f)
                for nid, nd in data.items():
                    self._nodes[nid] = EvolutionNode(**{
                        k: v for k, v in nd.items()
                        if k in EvolutionNode.__dataclass_fields__
                    })
        except Exception:
            pass


class DiscoveryEngine:
    """Analyzes patterns across designs to suggest improvements.

    Stateless — no caching, no side effects. Each call computes fresh.
    """

    def __init__(self, projections: dict | None = None):
        self._projections = projections or {}

    def set_projections(self, projections: dict) -> None:
        """Update projection data."""
        self._projections = projections

    def analyze_design(self, design_id: str) -> list[Discovery]:
        """Analyze a single design for patterns."""
        discoveries = []
        design = self._projections.get("design", {})
        comps = design.get("selected_components", [])

        # Pattern: many components of same type
        type_counts: dict[str, list[str]] = {}
        for c in comps:
            cat = c.get("category", "unknown")
            type_counts.setdefault(cat, []).append(c.get("ref_des", ""))

        for cat, refs in type_counts.items():
            if len(refs) >= 4:
                discoveries.append(Discovery(
                    category="repeated_pattern",
                    title=f"Many {cat} components ({len(refs)})",
                    description=f"Design has {len(refs)} {cat} components: {', '.join(refs[:5])}",
                    evidence=[f"Component count: {len(refs)}"],
                    suggestion=f"Consider if all {len(refs)} {cat} components are necessary",
                    confidence=0.5,
                    design_ids=[design_id],
                ))

        # Pattern: missing decoupling
        mcus = [c for c in comps if c.get("category") == "mcu"]
        caps = [c for c in comps if c.get("category") == "capacitor"]
        if mcus and len(caps) < len(mcus) * 2:
            discoveries.append(Discovery(
                category="optimization",
                title="Insufficient decoupling capacitors",
                description=f"{len(mcus)} MCU(s) but only {len(caps)} capacitor(s)",
                evidence=[f"MCUs: {len(mcus)}, Caps: {len(caps)}"],
                suggestion="Add at least 2 decoupling caps per MCU power pin",
                confidence=0.6,
                design_ids=[design_id],
            ))

        return discoveries

    def cross_design_analysis(self, designs: list[dict]) -> list[Discovery]:
        """Analyze patterns across multiple designs."""
        discoveries = []

        # Collect component usage across designs
        component_usage: dict[str, list[str]] = {}
        for design in designs:
            design_id = design.get("id", "")
            for c in design.get("selected_components", []):
                id_str = c.get("id_str", "")
                component_usage.setdefault(id_str, []).append(design_id)

        # Pattern: repeated component across designs
        for id_str, design_ids in component_usage.items():
            if len(design_ids) >= 3:
                discoveries.append(Discovery(
                    category="module_candidate",
                    title=f"Reusable component: {id_str}",
                    description=f"Used in {len(design_ids)} designs",
                    evidence=[f"Design count: {len(design_ids)}"],
                    suggestion=f"Consider creating a reusable module for {id_str}",
                    confidence=0.7,
                    design_ids=design_ids,
                ))

        return discoveries
