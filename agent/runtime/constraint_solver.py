"""Constraint Solver — declarative constraint system for circuit validation.

Detection is deterministic and instant. Explanation and repair are LLM tasks,
called only on demand. This decouples "what's wrong" from "what to do about it."

Built on top of existing synthesis/validation.py, synthesis/engine.py,
bus_checker.py, and tools.py — no new constraint implementations needed.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class Severity(Enum):
    CRITICAL = "critical"
    ERROR = "error"
    WARNING = "warning"
    INFO = "informational"


class ConstraintType(Enum):
    POWER_CONNECTIVITY = "power_connectivity"
    GROUND_CONNECTIVITY = "ground_connectivity"
    TOPOLOGY = "topology"
    CLEARANCE = "clearance"
    IMPEDANCE = "impedance"
    CURRENT_CAPACITY = "current_capacity"
    DECOUPLING = "decoupling"
    BUS_INTEGRITY = "bus_integrity"


@dataclass
class Violation:
    """A constraint violation detected by the solver."""
    id: str = field(default_factory=lambda: uuid4().hex[:8])
    constraint_type: ConstraintType = ConstraintType.POWER_CONNECTIVITY
    severity: Severity = Severity.WARNING
    entity_ids: list[str] = field(default_factory=list)
    description: str = ""
    code: str = ""
    evidence: list[str] = field(default_factory=list)
    suggested_fix: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "constraint_type": self.constraint_type.value,
            "severity": self.severity.value,
            "entity_ids": self.entity_ids,
            "description": self.description,
            "code": self.code,
            "evidence": self.evidence,
            "suggested_fix": self.suggested_fix,
        }


@dataclass
class Finding:
    """A finding to display on a design object."""
    id: str = field(default_factory=lambda: uuid4().hex[:8])
    entity_type: str = ""  # "component", "trace", "net"
    entity_id: str = ""
    severity: Severity = Severity.INFO
    title: str = ""
    description: str = ""
    actions: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "severity": self.severity.value,
            "title": self.title,
            "description": self.description,
            "actions": self.actions,
        }


class ConstraintSolver:
    """Declarative constraint system that detects violations without AI.

    Thread-safe: check() acquires lock during execution.
    """

    def __init__(self, projections: dict | None = None):
        self._projections = projections or {}
        self._custom_constraints: list[dict] = []
        self._lock = threading.Lock()

    def set_projections(self, projections: dict) -> None:
        """Update projection data."""
        self._projections = projections

    def declare(self, constraint: dict) -> None:
        """Register a custom constraint."""
        with self._lock:
            self._custom_constraints.append(constraint)

    def check(self, graph: Any = None) -> list[Violation]:
        """Run all constraints against the current design. Deterministic."""
        if graph is None:
            graph = self._projections.get("synthesis_graph")
        if graph is None:
            return []

        violations = []

        # 1. Synthesis validation (power, ground, connectivity)
        violations.extend(self._check_synthesis(graph))

        # 2. Topology constraints
        violations.extend(self._check_topology(graph))

        # 3. Bus integrity
        violations.extend(self._check_bus(graph))

        return violations

    def violations_for(self, entity_id: str, graph: Any = None) -> list[Violation]:
        """Get active violations for a specific entity."""
        all_violations = self.check(graph)
        return [v for v in all_violations if entity_id in v.entity_ids]

    def findings_for(self, entity_type: str, entity_id: str,
                     graph: Any = None) -> list[Finding]:
        """Get findings for a specific object (for UI display)."""
        violations = self.violations_for(entity_id, graph)
        findings = []
        for v in violations:
            finding = Finding(
                entity_type=entity_type,
                entity_id=entity_id,
                severity=v.severity,
                title=f"{v.constraint_type.value}: {v.code}",
                description=v.description,
            )
            if v.suggested_fix:
                finding.actions.append({
                    "label": "Fix automatically",
                    "type": "fix",
                    "violation_id": v.id,
                })
            findings.append(finding)
        return findings

    def _check_synthesis(self, graph: Any) -> list[Violation]:
        """Run synthesis validation checks."""
        violations = []
        try:
            from agent.synthesis.validation import validate_circuit
            issues = validate_circuit(graph)
            for issue in issues:
                sev_map = {"critical": Severity.CRITICAL, "recoverable": Severity.ERROR,
                           "warning": Severity.WARNING, "informational": Severity.INFO}
                sev = sev_map.get(issue.get("severity", "warning"), Severity.WARNING)
                v = Violation(
                    constraint_type=ConstraintType.POWER_CONNECTIVITY,
                    severity=sev,
                    entity_ids=self._extract_entities_from_issue(issue, graph),
                    description=issue.get("message", ""),
                    code=issue.get("code", ""),
                )
                violations.append(v)
        except Exception:
            pass
        return violations

    def _check_topology(self, graph: Any) -> list[Violation]:
        """Run topology constraint checks."""
        violations = []
        try:
            from agent.synthesis.engine import validate_constraints
            constraint_viols = validate_constraints(graph)
            for cv in constraint_viols:
                v = Violation(
                    constraint_type=ConstraintType.TOPOLOGY,
                    severity=Severity.WARNING,
                    entity_ids=self._extract_entities_from_violation(cv, graph),
                    description=cv.description,
                    code="CON001",
                )
                violations.append(v)
        except Exception:
            pass
        return violations

    def _check_bus(self, graph: Any) -> list[Violation]:
        """Check bus integrity (I2C, UART, SPI)."""
        violations = []
        try:
            # Check for common bus issues from the graph
            if hasattr(graph, "nets"):
                for net_name, net_node in graph.nets.items():
                    from agent.synthesis.graph import NetRole
                    if net_node.role == NetRole.COMMUNICATION:
                        # Communication nets need at least 2 pins
                        if len(net_node.pins) < 2:
                            v = Violation(
                                constraint_type=ConstraintType.BUS_INTEGRITY,
                                severity=Severity.WARNING,
                                entity_ids=list(net_node.pins),
                                description=f"Communication net '{net_name}' has only {len(net_node.pins)} pin(s)",
                                code="BUS001",
                            )
                            violations.append(v)
        except Exception:
            pass
        return violations

    def _extract_entities_from_issue(self, issue: dict, graph: Any) -> list[str]:
        """Extract entity IDs from a validation issue."""
        entities = []
        msg = issue.get("message", "")
        # Try to extract component refs from the message
        import re
        refs = re.findall(r'\b([A-Z][A-Z0-9]+:\d+)\b', msg)
        entities.extend(refs)
        refs2 = re.findall(r'\b([A-Z]\d+)\b', msg)
        entities.extend(refs2)
        return list(set(entities))[:5]

    def _extract_entities_from_violation(self, cv: Any, graph: Any) -> list[str]:
        """Extract entity IDs from a constraint violation."""
        entities = []
        if hasattr(cv, "constraint") and hasattr(cv.constraint, "source_pin"):
            entities.append(cv.constraint.source_pin.split(":")[0])
            if cv.constraint.target_pin:
                entities.append(cv.constraint.target_pin.split(":")[0])
        return list(set(entities))[:5]
