"""Confidence Network — structured confidence for every AI proposal.

Replaces bare "Confidence: 93%" with an evidence chain that the user
can inspect to understand why the AI is confident and what could go wrong.

Thread-safe: all operations are stateless (no shared mutable state).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class Evidence:
    """A piece of evidence supporting a confidence assessment."""
    source: str = ""      # "datasheet", "rule", "simulation", "user_input"
    statement: str = ""
    weight: float = 1.0   # 0.0 = irrelevant, 1.0 = decisive

    def to_dict(self) -> dict:
        return {"source": self.source, "statement": self.statement, "weight": self.weight}


@dataclass
class Assumption:
    """An assumption made during the assessment."""
    description: str = ""
    impact: str = ""      # what changes if this assumption is wrong
    confidence: float = 1.0

    def to_dict(self) -> dict:
        return {"description": self.description, "impact": self.impact, "confidence": self.confidence}


@dataclass
class Risk:
    """A risk factor identified in the assessment."""
    description: str = ""
    probability: str = ""  # "low", "medium", "high"
    severity: str = ""     # "low", "medium", "high"
    mitigation: str = ""

    def to_dict(self) -> dict:
        return {
            "description": self.description, "probability": self.probability,
            "severity": self.severity, "mitigation": self.mitigation,
        }


@dataclass
class ConfidenceAnalysis:
    """Structured confidence assessment for an AI decision."""
    id: str = field(default_factory=lambda: uuid4().hex[:8])
    overall: float = 0.0  # 0.0 - 1.0

    # Evidence chain
    evidence: list[Evidence] = field(default_factory=list)
    assumptions: list[Assumption] = field(default_factory=list)
    risks: list[Risk] = field(default_factory=list)

    # Input quality
    context_freshness: str = "same_revision"  # "same_revision", "one_revision_behind", "stale"
    data_completeness: str = "full"  # "full", "partial", "minimal"

    # Mitigations
    mitigations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "overall": round(self.overall, 3),
            "evidence": [e.to_dict() for e in self.evidence],
            "assumptions": [a.to_dict() for a in self.assumptions],
            "risks": [r.to_dict() for r in self.risks],
            "context_freshness": self.context_freshness,
            "data_completeness": self.data_completeness,
            "mitigations": self.mitigations,
        }

    @property
    def summary(self) -> str:
        """Human-readable confidence summary."""
        pct = int(self.overall * 100)
        parts = [f"{pct}% confidence"]
        if self.evidence:
            parts.append(f"{len(self.evidence)} evidence items")
        if self.risks:
            high_risks = [r for r in self.risks if r.severity == "high"]
            if high_risks:
                parts.append(f"{len(high_risks)} high-risk factors")
        return ", ".join(parts)


class ConfidenceNetwork:
    """Computes structured confidence for AI decisions.

    Stateless — no caching, no side effects. Each call computes fresh.
    """

    def __init__(self, projections: dict | None = None):
        self._projections = projections or {}

    def set_projections(self, projections: dict) -> None:
        """Update projection data."""
        self._projections = projections

    def assess_component_selection(self, component_id: str,
                                    selection_rationale: str = "") -> ConfidenceAnalysis:
        """Assess confidence in a component selection decision."""
        analysis = ConfidenceAnalysis()

        design = self._projections.get("design", {})
        comps = design.get("selected_components", [])
        comp = next((c for c in comps if c.get("id_str") == component_id
                     or c.get("ref_des") == component_id), None)

        if not comp:
            analysis.overall = 0.0
            analysis.data_completeness = "minimal"
            return analysis

        # Evidence from component data
        if comp.get("description"):
            analysis.evidence.append(Evidence(
                source="component_data",
                statement=f"Component described as: {comp['description'][:80]}",
                weight=0.3,
            ))

        # Evidence from device knowledge
        from agent.component_knowledge import lookup_device
        device = lookup_device(comp.get("id_str", ""), comp.get("description", ""))
        if device:
            analysis.evidence.append(Evidence(
                source="device_knowledge",
                statement=f"Known device: {device}",
                weight=0.4,
            ))
            if device.get("voltage"):
                analysis.assumptions.append(Assumption(
                    description=f"Operating voltage is {device['voltage']}V",
                    impact="Incorrect voltage assumption may cause circuit malfunction",
                    confidence=0.9,
                ))

        # Evidence from user preferences
        prefs = self._projections.get("user_preferences", {})
        if prefs:
            preferred = prefs.get("preferred_parts", {})
            rejected = prefs.get("rejected_parts", [])
            if comp.get("ref_des") in rejected:
                analysis.risks.append(Risk(
                    description="User previously rejected this component",
                    probability="high",
                    severity="medium",
                    mitigation="Consider alternative component",
                ))
                analysis.overall -= 0.2

        # Data completeness
        has_footprint = bool(comp.get("footprint"))
        has_pads = bool(comp.get("pads"))
        if has_footprint and has_pads:
            analysis.data_completeness = "full"
            analysis.overall += 0.2
        elif has_footprint or has_pads:
            analysis.data_completeness = "partial"
            analysis.overall += 0.1
        else:
            analysis.data_completeness = "minimal"

        # Base confidence from evidence
        evidence_score = sum(e.weight for e in analysis.evidence) / max(len(analysis.evidence), 1)
        analysis.overall += evidence_score * 0.5

        # Clamp to [0, 1]
        analysis.overall = max(0.0, min(1.0, analysis.overall))

        return analysis

    def assess_netlist(self, netlist: list[dict],
                       pin_matrix: dict) -> ConfidenceAnalysis:
        """Assess confidence in the generated netlist."""
        analysis = ConfidenceAnalysis()

        if not netlist:
            analysis.overall = 0.0
            return analysis

        # Evidence: connection count
        analysis.evidence.append(Evidence(
            source="netlist_generation",
            statement=f"Generated {len(netlist)} connections",
            weight=0.3,
        ))

        # Check for unconnected pins
        connected_pins = set()
        for edge in netlist:
            connected_pins.add(edge.get("source", ""))
            connected_pins.add(edge.get("target", ""))
        all_pins = set(pin_matrix.keys())
        unconnected = all_pins - connected_pins - {""}

        if unconnected:
            analysis.risks.append(Risk(
                description=f"{len(unconnected)} pins unconnected: {', '.join(list(unconnected)[:5])}",
                probability="medium",
                severity="high",
                mitigation="Run ERC to identify and fix unconnected pins",
            ))
            analysis.overall -= 0.3
        else:
            analysis.evidence.append(Evidence(
                source="connectivity_check",
                statement="All pins connected",
                weight=0.5,
            ))
            analysis.overall += 0.3

        # Power net validation
        power_pins = [k for k, v in pin_matrix.items()
                      if v.get("etype", "") in ("power_in", "power_out")]
        connected_power = [p for p in power_pins if p in connected_pins]
        if power_pins:
            power_ratio = len(connected_power) / len(power_pins)
            if power_ratio < 0.8:
                analysis.risks.append(Risk(
                    description=f"Only {len(connected_power)}/{len(power_pins)} power pins connected",
                    probability="high",
                    severity="high",
                    mitigation="Verify power net assignments",
                ))
                analysis.overall -= 0.2

        analysis.overall = max(0.0, min(1.0, analysis.overall + 0.5))
        return analysis

    def assess_placement(self, placements: list[dict],
                         board_model: dict | None = None) -> ConfidenceAnalysis:
        """Assess confidence in component placement."""
        analysis = ConfidenceAnalysis()

        if not placements:
            analysis.overall = 0.0
            return analysis

        analysis.evidence.append(Evidence(
            source="placement_engine",
            statement=f"Placed {len(placements)} components using force-directed algorithm",
            weight=0.4,
        ))

        # Check for overlaps (rough: components too close)
        if board_model:
            components = board_model.get("components", [])
            overlaps = 0
            for i, c1 in enumerate(components):
                for c2 in components[i+1:]:
                    dx = abs(c1.get("x", 0) - c2.get("x", 0))
                    dy = abs(c1.get("y", 0) - c2.get("y", 0))
                    if dx < 2.0 and dy < 2.0:  # less than 2mm apart
                        overlaps += 1
            if overlaps:
                analysis.risks.append(Risk(
                    description=f"{overlaps} potential component overlaps detected",
                    probability="medium",
                    severity="medium",
                    mitigation="Run DRC to verify clearances",
                ))
                analysis.overall -= 0.1

        analysis.overall = max(0.0, min(1.0, analysis.overall + 0.5))
        return analysis

    def assess_routing(self, traces: list[dict],
                       nets: list[dict]) -> ConfidenceAnalysis:
        """Assess confidence in trace routing."""
        analysis = ConfidenceAnalysis()

        if not traces:
            analysis.overall = 0.0
            return analysis

        analysis.evidence.append(Evidence(
            source="routing_engine",
            statement=f"Routed {len(traces)} traces",
            weight=0.3,
        ))

        # Check for unrouted nets
        routed_nets = set(t.get("net", "") for t in traces)
        all_nets = set(n.get("net", "") for n in nets)
        unrouted = all_nets - routed_nets

        if unrouted:
            analysis.risks.append(Risk(
                description=f"{len(unrouted)} nets unrouted: {', '.join(list(unrouted)[:5])}",
                probability="high",
                severity="high",
                mitigation="Complete routing for all nets",
            ))
            analysis.overall -= 0.3
        else:
            analysis.evidence.append(Evidence(
                source="routing_completeness",
                statement="All nets routed",
                weight=0.5,
            ))
            analysis.overall += 0.3

        analysis.overall = max(0.0, min(1.0, analysis.overall + 0.5))
        return analysis

    def combine(self, analyses: list[ConfidenceAnalysis]) -> ConfidenceAnalysis:
        """Combine multiple confidence analyses into one."""
        if not analyses:
            return ConfidenceAnalysis()

        combined = ConfidenceAnalysis()
        combined.overall = sum(a.overall for a in analyses) / len(analyses)
        for a in analyses:
            combined.evidence.extend(a.evidence)
            combined.assumptions.extend(a.assumptions)
            combined.risks.extend(a.risks)
            combined.mitigations.extend(a.mitigations)

        # Deduplicate
        seen_evidence = set()
        unique_evidence = []
        for e in combined.evidence:
            key = e.statement[:50]
            if key not in seen_evidence:
                seen_evidence.add(key)
                unique_evidence.append(e)
        combined.evidence = unique_evidence

        seen_risks = set()
        unique_risks = []
        for r in combined.risks:
            key = r.description[:50]
            if key not in seen_risks:
                seen_risks.add(key)
                unique_risks.append(r)
        combined.risks = unique_risks

        return combined
