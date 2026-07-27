"""Discovery Engine — cross-design pattern analysis.

Analyzes patterns across multiple designs to suggest:
- Reusable modules from repeated circuits
- Layout improvements from historical data
- Component preferences from usage patterns
- Process improvements from design history

Thread-safe: all operations are stateless.
No memory leaks: results are computed on-demand.
"""

from __future__ import annotations

import threading
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class DiscoveryType(Enum):
    REUSABLE_MODULE = "reusable_module"
    LAYOUT_IMPROVEMENT = "layout_improvement"
    COMPONENT_PREFERENCE = "component_preference"
    PROCESS_IMPROVEMENT = "process_improvement"
    COST_OPTIMIZATION = "cost_optimization"


@dataclass
class Discovery:
    """A discovery from cross-design analysis."""
    id: str = field(default_factory=lambda: uuid4().hex[:8])
    discovery_type: DiscoveryType = DiscoveryType.REUSABLE_MODULE
    title: str = ""
    description: str = ""
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.0
    suggested_action: str = ""
    affected_designs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.discovery_type.value,
            "title": self.title,
            "description": self.description,
            "evidence": self.evidence,
            "confidence": round(self.confidence, 3),
            "suggested_action": self.suggested_action,
            "affected_designs": self.affected_designs,
        }


class DiscoveryEngine:
    """Analyzes patterns across designs to suggest improvements.

    Stateless — no caching, no side effects. Each call computes fresh.
    Thread-safe: all operations are pure functions of input data.
    """

    def __init__(self):
        self._lock = threading.Lock()

    def analyze_design(self, design_id: str, design_data: dict) -> list[Discovery]:
        """Analyze a single design for reusable patterns."""
        discoveries = []

        comps = design_data.get("selected_components", [])
        if not comps:
            return discoveries

        # Discover repeated component families
        family_counts = Counter()
        for c in comps:
            id_str = c.get("id_str", "")
            family = id_str.split(":")[0] if ":" in id_str else id_str
            family_counts[family] += 1

        for family, count in family_counts.items():
            if count >= 3:
                discoveries.append(Discovery(
                    discovery_type=DiscoveryType.REUSABLE_MODULE,
                    title=f"Repeated component family: {family}",
                    description=f"{count} components from {family} family. Consider creating a reusable module.",
                    evidence=[f"Found {count} components with prefix {family}"],
                    confidence=0.6,
                    suggested_action=f"Create reusable module for {family} family",
                    affected_designs=[design_id],
                ))

        # Discover common IC + passive combinations
        ic_refs = [c.get("ref_des", "") for c in comps if c.get("category") in ("mcu", "ic", "regulator")]
        passive_count = sum(1 for c in comps if c.get("category") in ("passive", "resistor", "capacitor"))
        if ic_refs and passive_count > len(ic_refs) * 3:
            discoveries.append(Discovery(
                discovery_type=DiscoveryType.REUSABLE_MODULE,
                title="High passive-to-IC ratio",
                description=f"{passive_count} passives for {len(ic_refs)} ICs. Common decoupling/pull-up patterns may be modularizable.",
                evidence=[f"Passive:IC ratio = {passive_count}:{len(ic_refs)}"],
                confidence=0.5,
                suggested_action="Review passive components for module extraction",
                affected_designs=[design_id],
            ))

        return discoveries

    def cross_design_analysis(self, designs: list[dict]) -> list[Discovery]:
        """Analyze patterns across multiple designs."""
        discoveries = []
        if len(designs) < 2:
            return discoveries

        # Find common components across designs
        component_usage: dict[str, list[str]] = defaultdict(list)
        for design in designs:
            design_id = design.get("id", "unknown")
            for c in design.get("selected_components", []):
                id_str = c.get("id_str", "")
                if id_str:
                    component_usage[id_str].append(design_id)

        # Discover frequently used components
        for comp_id, used_in in component_usage.items():
            if len(used_in) >= 3:
                discoveries.append(Discovery(
                    discovery_type=DiscoveryType.COMPONENT_PREFERENCE,
                    title=f"Frequently used: {comp_id}",
                    description=f"Used in {len(used_in)} designs. Strong candidate for a reusable module or template.",
                    evidence=[f"Found in {len(used_in)} designs: {', '.join(used_in[:5])}"],
                    confidence=min(0.5 + len(used_in) * 0.1, 0.9),
                    suggested_action=f"Create template for {comp_id}",
                    affected_designs=used_in,
                ))

        # Discover common circuit patterns
        pattern_counts: dict[str, int] = Counter()
        for design in designs:
            comps = design.get("selected_components", [])
            families = set()
            for c in comps:
                id_str = c.get("id_str", "")
                family = id_str.split(":")[0] if ":" in id_str else ""
                if family:
                    families.add(family)
            pattern_key = ",".join(sorted(families))
            pattern_counts[pattern_key] += 1

        for pattern, count in pattern_counts.items():
            if count >= 3:
                families = [f for f in pattern.split(",") if f]
                discoveries.append(Discovery(
                    discovery_type=DiscoveryType.REUSABLE_MODULE,
                    title=f"Common pattern: {', '.join(families[:4])}",
                    description=f"This component combination appears in {count} designs.",
                    evidence=[f"Pattern found {count} times"],
                    confidence=min(0.4 + count * 0.1, 0.8),
                    suggested_action=f"Create template for {', '.join(families[:3])} combination",
                    affected_designs=[],
                ))

        # Discover cost optimization opportunities
        for design in designs:
            comps = design.get("selected_components", [])
            if len(comps) > 15:
                discoveries.append(Discovery(
                    discovery_type=DiscoveryType.COST_OPTIMIZATION,
                    title=f"Complex design: {len(comps)} components",
                    description=f"Design has {len(comps)} components. Review for consolidation opportunities.",
                    evidence=[f"Component count: {len(comps)}"],
                    confidence=0.4,
                    suggested_action="Review for component consolidation",
                    affected_designs=[design.get("id", "unknown")],
                ))

        return discoveries

    def suggest_from_history(self, evolution_nodes: list[dict]) -> list[Discovery]:
        """Suggest improvements based on design evolution history."""
        discoveries = []
        if not evolution_nodes:
            return discoveries

        # Find designs that went through cost optimization
        cost_optimizations = [n for n in evolution_nodes
                              if n.get("transformation") == "cost_optimization"]
        if cost_optimizations:
            total_savings = sum(n.get("metrics", {}).get("savings", 0) for n in cost_optimizations)
            discoveries.append(Discovery(
                discovery_type=DiscoveryType.COST_OPTIMIZATION,
                title="Historical cost optimizations",
                description=f"Found {len(cost_optimizations)} cost optimizations saving ${total_savings:.2f} total.",
                evidence=[f"{len(cost_optimizations)} optimizations, ${total_savings:.2f} total savings"],
                confidence=0.7,
                suggested_action="Apply similar optimizations to new designs",
                affected_designs=[],
            ))

        # Find designs with many edits (potential complexity issues)
        edit_counts: dict[str, int] = Counter()
        for node in evolution_nodes:
            if node.get("transformation") == "manual_edit":
                design_id = node.get("design_id", "unknown")
                edit_counts[design_id] += 1

        for design_id, count in edit_counts.items():
            if count >= 5:
                discoveries.append(Discovery(
                    discovery_type=DiscoveryType.PROCESS_IMPROVEMENT,
                    title=f"Frequently edited design: {design_id}",
                    description=f"Design had {count} manual edits. Consider improving initial generation quality.",
                    evidence=[f"{count} manual edits on {design_id}"],
                    confidence=0.5,
                    suggested_action="Review edit patterns to improve initial generation",
                    affected_designs=[design_id],
                ))

        return discoveries
