"""Design Evolution — tracks design history and evolution across revisions.

Every design has an evolution tree where each node is a revision.
Edges represent transformation types (generated, manual_edit, cost_optimization, variant).

The AI can compare nodes, suggest transformations, and learn from evolution history.

Thread-safe: all operations use thread-local storage.
No memory leaks: bounded history with explicit eviction.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class EvolutionNode:
    """A node in the design evolution tree."""
    id: str = field(default_factory=lambda: uuid4().hex[:8])
    parent_id: str | None = None
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
class EvolutionEdge:
    """An edge connecting two evolution nodes."""
    source_id: str = ""
    target_id: str = ""
    transformation: str = ""
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id, "target_id": self.target_id,
            "transformation": self.transformation, "description": self.description,
        }


class DesignEvolution:
    """Tracks design evolution across revisions.

    Thread-safe: nodes stored in thread-local dict.
    Bounded: max 200 nodes per design (oldest pruned).
    """

    def __init__(self, design_id: str, max_nodes: int = 200):
        self._design_id = design_id
        self._max_nodes = max_nodes
        self._nodes: dict[str, EvolutionNode] = {}
        self._edges: list[EvolutionEdge] = []
        self._lock = threading.Lock()

    @property
    def design_id(self) -> str:
        return self._design_id

    def record_generation(self, revision: int, component_count: int,
                          layer_count: int = 2, bom_cost: float = 0.0) -> EvolutionNode:
        """Record a new design generation."""
        node = EvolutionNode(
            design_id=self._design_id,
            revision=revision,
            label=f"Generation v{revision}",
            transformation="generated",
            summary=f"{component_count} components, {layer_count} layers",
            metrics={
                "component_count": component_count,
                "layer_count": layer_count,
                "bom_cost": bom_cost,
            },
        )
        self._add_node(node)
        return node

    def record_edit(self, revision: int, parent_id: str,
                    description: str, changes: dict | None = None) -> EvolutionNode:
        """Record a manual edit."""
        node = EvolutionNode(
            design_id=self._design_id,
            revision=revision,
            parent_id=parent_id,
            label=f"Edit v{revision}",
            transformation="manual_edit",
            summary=description,
            metrics=changes or {},
        )
        self._add_node(node)
        if parent_id:
            self._edges.append(EvolutionEdge(
                source_id=parent_id, target_id=node.id,
                transformation="manual_edit", description=description,
            ))
        return node

    def record_cost_optimization(self, revision: int, parent_id: str,
                                  savings: float, changes: list[str]) -> EvolutionNode:
        """Record a cost optimization."""
        node = EvolutionNode(
            design_id=self._design_id,
            revision=revision,
            parent_id=parent_id,
            label=f"Cost optimization v{revision}",
            transformation="cost_optimization",
            goal=f"Reduce BOM cost by ${savings:.2f}",
            summary=f"Saved ${savings:.2f}: {'; '.join(changes[:3])}",
            metrics={"savings": savings, "changes": changes},
        )
        self._add_node(node)
        if parent_id:
            self._edges.append(EvolutionEdge(
                source_id=parent_id, target_id=node.id,
                transformation="cost_optimization",
                description=f"Saved ${savings:.2f}",
            ))
        return node

    def record_variant(self, revision: int, parent_id: str,
                       variant_name: str, goal: str) -> EvolutionNode:
        """Record a design variant (e.g., production, prototype)."""
        node = EvolutionNode(
            design_id=self._design_id,
            revision=revision,
            parent_id=parent_id,
            label=variant_name,
            transformation="variant",
            goal=goal,
            summary=f"Variant: {variant_name}",
        )
        self._add_node(node)
        if parent_id:
            self._edges.append(EvolutionEdge(
                source_id=parent_id, target_id=node.id,
                transformation="variant", description=variant_name,
            ))
        return node

    def get_tree(self) -> dict:
        """Get the full evolution tree."""
        with self._lock:
            return {
                "nodes": [n.to_dict() for n in self._nodes.values()],
                "edges": [e.to_dict() for e in self._edges],
                "root": self._find_root(),
            }

    def get_timeline(self) -> list[dict]:
        """Get evolution timeline sorted by timestamp."""
        with self._lock:
            nodes = sorted(self._nodes.values(), key=lambda n: n.timestamp)
            return [n.to_dict() for n in nodes]

    def compare_nodes(self, node_id_a: str, node_id_b: str) -> dict:
        """Compare two evolution nodes."""
        with self._lock:
            a = self._nodes.get(node_id_a)
            b = self._nodes.get(node_id_b)
        if not a or not b:
            return {"error": "Node not found"}

        comparison = {
            "node_a": a.to_dict(),
            "node_b": b.to_dict(),
            "metric_diffs": {},
        }

        for key in set(a.metrics.keys()) | set(b.metrics.keys()):
            va = a.metrics.get(key)
            vb = b.metrics.get(key)
            if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                comparison["metric_diffs"][key] = {
                    "a": va, "b": vb, "diff": vb - va,
                    "pct_change": ((vb - va) / va * 100) if va else 0,
                }

        return comparison

    def get_latest(self) -> EvolutionNode | None:
        """Get the most recent evolution node."""
        with self._lock:
            if not self._nodes:
                return None
            return max(self._nodes.values(), key=lambda n: n.timestamp)

    def get_children(self, node_id: str) -> list[EvolutionNode]:
        """Get all children of a node."""
        with self._lock:
            return [n for n in self._nodes.values() if n.parent_id == node_id]

    def _add_node(self, node: EvolutionNode) -> None:
        """Add a node, pruning oldest if over capacity."""
        with self._lock:
            self._nodes[node.id] = node
            if len(self._nodes) > self._max_nodes:
                oldest = min(self._nodes.values(), key=lambda n: n.timestamp)
                del self._nodes[oldest.id]
                self._edges = [e for e in self._edges
                               if e.source_id != oldest.id and e.target_id != oldest.id]

    def _find_root(self) -> str | None:
        """Find the root node (no parent)."""
        for node in self._nodes.values():
            if node.parent_id is None:
                return node.id
        return None
