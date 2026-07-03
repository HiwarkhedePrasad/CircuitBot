"""Graph engine — constraint validation and repair.

The engine compares the LLM netlist (stored in the graph) against generated
constraints and produces a report.  The repair module then generates missing
connections.

This is entirely generic — no circuit-specific logic, no hardcoded net names.
"""

from __future__ import annotations

from agent.synthesis.graph import (
    ConstraintEdge,
    ConstraintType,
    NetRole,
    PinRole,
    SynthesisGraph,
)


# ── Validation ─────────────────────────────────────────────────────────────


class ConstraintViolation:
    """A single constraint that is not satisfied by the LLM netlist."""
    __slots__ = ("constraint", "description", "severity")

    def __init__(self, constraint: ConstraintEdge, description: str,
                 severity: str = "warning"):
        self.constraint = constraint
        self.description = description
        self.severity = severity


def _pins_on_same_net(graph: SynthesisGraph, pin_a: str, pin_b: str) -> bool:
    """Check if two pins share a net in the LLM netlist."""
    for net in graph.nets.values():
        if pin_a in net.pins and pin_b in net.pins:
            return True
    return False


def _pin_on_net_role(graph: SynthesisGraph, pin_key: str, role: NetRole) -> bool:
    """Check if a pin is connected to any net with the given role."""
    for net in graph.nets.values():
        if pin_key in net.pins and net.role == role:
            return True
    return False


def validate_constraints(graph: SynthesisGraph) -> list[ConstraintViolation]:
    """Compare constraints against the LLM netlist and report mismatches."""
    violations: list[ConstraintViolation] = []

    for c in graph.constraints:
        src = c.source_pin
        tgt = c.target_pin

        if c.type in (ConstraintType.POWERED_BY, ConstraintType.GROUNDED_BY):
            net_role_map = {
                ConstraintType.POWERED_BY: NetRole.POWER,
                ConstraintType.GROUNDED_BY: NetRole.GROUND,
            }
            expected_role = net_role_map.get(c.type)
            if expected_role:
                if src and not _pin_on_net_role(graph, src, expected_role):
                    # Either unconnected or on wrong-role net
                    actual_net_role = c.metadata.get("actual_net_role", "unknown")
                    msg = (
                        f"Pin {src} expected on {expected_role.value} net "
                        f"(currently: {actual_net_role})"
                        f" — topology: {c.metadata.get('topology', '?')}"
                    )
                    violations.append(ConstraintViolation(
                        constraint=c,
                        description=msg,
                        severity="warning",
                    ))

        elif c.type in (ConstraintType.LOAD, ConstraintType.SERIES,
                        ConstraintType.DECOUPLES):
            # Expect src pin and tgt component to share a net
            if src and tgt and ":" not in tgt:
                # tgt is a component ref_des — find pins of that component
                target_comp = graph.components.get(tgt)
                if target_comp:
                    connected = any(
                        _pins_on_same_net(graph, src, tp)
                        for tp in target_comp.pins
                    )
                    if not connected:
                        violations.append(ConstraintViolation(
                            constraint=c,
                            description=(
                                f"Pin {src} should connect to component {tgt} "
                                f"(topology: {c.metadata.get('topology', '?')})"
                            ),
                            severity="warning",
                        ))
            elif src and tgt and ":" in tgt:
                # tgt is a pin key
                if not _pins_on_same_net(graph, src, tgt):
                    violations.append(ConstraintViolation(
                        constraint=c,
                        description=(
                            f"Pin {src} should connect to {tgt} "
                            f"(topology: {c.metadata.get('topology', '?')})"
                        ),
                        severity="warning",
                    ))

        elif c.type in (ConstraintType.PULLED_UP, ConstraintType.PULLED_DOWN):
            expected_role = NetRole.POWER if c.type == ConstraintType.PULLED_UP else NetRole.GROUND
            if src and not _pin_on_net_role(graph, src, expected_role):
                violations.append(ConstraintViolation(
                    constraint=c,
                    description=(
                        f"Pin {src} expected pull-{'up' if c.type == ConstraintType.PULLED_UP else 'down'} "
                        f"to {expected_role.value} net "
                        f"(topology: {c.metadata.get('topology', '?')})"
                    ),
                    severity="info",
                ))

    return violations


# ── Repair ──────────────────────────────────────────────────────────────────


class RepairAction:
    """A suggested repair to satisfy a constraint."""
    __slots__ = ("violation", "description", "priority")

    def __init__(self, violation: ConstraintViolation, description: str,
                 priority: int = 0):
        self.violation = violation
        self.description = description
        self.priority = priority


def suggest_repairs(violations: list[ConstraintViolation]) -> list[RepairAction]:
    """Generate repair suggestions from constraint violations."""
    repairs: list[RepairAction] = []
    for v in violations:
        c = v.constraint
        src = c.source_pin
        tgt = c.target_pin

        if c.type in (ConstraintType.POWERED_BY, ConstraintType.GROUNDED_BY):
            expected_role = (
                NetRole.POWER if c.type == ConstraintType.POWERED_BY
                else NetRole.GROUND
            )
            repairs.append(RepairAction(
                violation=v,
                description=(
                    f"Connect pin {src} to a {expected_role.value} net"
                ),
                priority=1,
            ))

        elif c.type in (ConstraintType.LOAD, ConstraintType.SERIES):
            if tgt and ":" not in tgt:
                repairs.append(RepairAction(
                    violation=v,
                    description=(
                        f"Connect pin {src} to component {tgt} "
                        f"(find appropriate pin on {tgt} by role)"
                    ),
                    priority=2,
                ))

        elif c.type in (ConstraintType.PULLED_UP, ConstraintType.PULLED_DOWN):
            repairs.append(RepairAction(
                violation=v,
                description=(
                    f"Add pull-{'up' if c.type == ConstraintType.PULLED_UP else 'down'} "
                    f"resistor between {src} and "
                    f"{'power' if c.type == ConstraintType.PULLED_UP else 'ground'}"
                ),
                priority=3,
            ))

    return repairs
