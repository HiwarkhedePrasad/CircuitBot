"""Circuit validation with four severity levels.

Levels:
  Critical      — circuit cannot function (e.g. power not connected)
  Recoverable   — engine can fix automatically
  Warning       — may be intentional, but flagged
  Informational — visibility only (e.g. PCV001)
"""

from __future__ import annotations

from typing import Any

from agent.synthesis.graph import NetRole, PinRole, SynthesisGraph
from agent.synthesis.engine import validate_constraints


ValidationIssue = dict  # {"code": str, "severity": str, "stage": str, "message": str, ...}


def _make_issue(code: str, severity: str, stage: str, message: str,
                **kw: Any) -> ValidationIssue:
    return {"code": code, "severity": severity, "stage": stage,
            "message": message, **kw}


def validate_circuit(graph: SynthesisGraph) -> list[ValidationIssue]:
    """Run the full validation pipeline.

    Returns a list of issues sorted by severity (most severe first).
    """
    issues: list[ValidationIssue] = []

    # ── Critical checks ───────────────────────────────────────────────
    # 1. Every power net must have at least one source
    for net in graph.power_nets:
        if not net.pins:
            issues.append(_make_issue(
                "PWR001", "critical", "synthesis",
                f"Power net '{net.name}' has no connected pins",
            ))

    # 2. Every power pin should connect to a power net
    for comp in graph.components.values():
        for pin_key, pin in comp.pins.items():
            if pin.role not in (PinRole.POWER_IN, PinRole.POWER_OUT):
                continue
            on_power = any(
                pin_key in net.pins and net.role == NetRole.POWER
                for net in graph.nets.values()
            )
            if not on_power:
                issues.append(_make_issue(
                    "PWR002", "critical", "synthesis",
                    f"Power pin {pin_key} on {comp.ref_des} is not connected "
                    f"to any power net",
                    pin=pin_key, ref_des=comp.ref_des,
                ))

    # 3. No critical ground pins unconnected
    for comp in graph.components.values():
        for pin_key, pin in comp.pins.items():
            if pin.role != PinRole.GND:
                continue
            on_gnd = any(
                pin_key in net.pins and net.role == NetRole.GROUND
                for net in graph.nets.values()
            )
            if not on_gnd:
                issues.append(_make_issue(
                    "GND001", "critical", "synthesis",
                    f"Ground pin {pin_key} on {comp.ref_des} is not connected "
                    f"to any ground net",
                    pin=pin_key, ref_des=comp.ref_des,
                ))

    # ── Recoverable checks ────────────────────────────────────────────
    # Constraint validation
    constraint_violations = validate_constraints(graph)
    for cv in constraint_violations:
        issues.append(_make_issue(
            "CON001", "recoverable", "synthesis",
            cv.description,
        ))

    # ── Warning checks ────────────────────────────────────────────────
    # Components with no connected pins at all
    for comp in graph.components.values():
        all_pins = set(comp.pins.keys())
        connected = set()
        for net in graph.nets.values():
            connected.update(net.pins & all_pins)
        for pp in graph.power_pins:
            if pp.get("pin", "") in all_pins:
                connected.add(pp["pin"])
        if all_pins and not connected:
            issues.append(_make_issue(
                "COMP001", "warning", "synthesis",
                f"Component {comp.ref_des} ({comp.id_str}) has no connected pins",
                ref_des=comp.ref_des,
            ))

    # ── Informational checks ──────────────────────────────────────────
    # Pin coverage (how many pins are used vs total)
    for comp in graph.components.values():
        total = len(comp.pins)
        connected = set()
        for net in graph.nets.values():
            connected.update(net.pins & set(comp.pins.keys()))
        for pp in graph.power_pins:
            pp_pin = pp.get("pin", "")
            if pp_pin in comp.pins:
                connected.add(pp_pin)
        used = len(connected)
        if total > 0 and used < total:
            unused = sorted(set(comp.pins.keys()) - connected)
            issues.append(_make_issue(
                "COMP002", "informational", "synthesis",
                f"Component {comp.ref_des}: {used}/{total} pins connected; "
                f"{total - used} unused ({', '.join(unused[:6])}"
                f"{'...' if len(unused) > 6 else ''})",
                ref_des=comp.ref_des, used=used, total=total,
            ))

    # Sort: critical → recoverable → warning → info
    severity_order = {"critical": 0, "recoverable": 1, "warning": 2, "informational": 3}
    issues.sort(key=lambda i: severity_order.get(i.get("severity", "informational"), 99))

    return issues
