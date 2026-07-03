"""Data-driven topology matching.

Topology rules define functional motifs by pin roles and component metadata,
never by referenced component names.  A matcher walks the synthesis graph,
finds motifs, and emits ConstraintEdges.

Each rule describes:
  - Which component type to look for (by metadata keys, not names)
  - Which pin roles define the motif
  - What relationships (constraints) exist between them
"""

from __future__ import annotations

from typing import Any

from agent.synthesis.graph import (
    ComponentNode,
    ConstraintEdge,
    ConstraintType,
    NetRole,
    PinRole,
    SynthesisGraph,
)


# ── Rule definitions (data-driven, not circuit-specific) ───────────────────

class TopologyRule:
    """A topology rule expressed in terms of pin roles and component metadata.

    Fields:
      name:          Human-readable name (e.g. "indicator_led")
      comp_meta:     Metadata predicates the target component must satisfy.
                     Keys are metadata field names; values are matched sets
                     (any-of).  E.g. {"passive_class": {"led"}}
      pin_roles:     Set of PinRoles the component must have to match.
      net_role_map:  Dict mapping pin role → expected NetRole of the net
                     it connects to.
      constraints:   List of constraint specs, each a dict with keys:
                       type: ConstraintType
                       source_role: PinRole (on the matched component)
                       target_role: optional PinRole (on another component)
                       target_meta: optional metadata predicate
                       net_role:    optional NetRole (for net-directed constraints)
                     At least one of target_role / target_meta / net_role.
    """
    __slots__ = ("name", "comp_meta", "pin_roles", "net_role_map", "constraints")

    def __init__(
        self,
        name: str,
        comp_meta: dict[str, set[str]],
        pin_roles: set[PinRole],
        net_role_map: dict[PinRole, NetRole] | None = None,
        constraints: list[dict[str, Any]] | None = None,
    ):
        self.name = name
        self.comp_meta = comp_meta
        self.pin_roles = pin_roles
        self.net_role_map = net_role_map or {}
        self.constraints = constraints or []


# ── Built-in rule catalog ──────────────────────────────────────────────────

# These rules are defined by pin roles and electrical metadata only.
# Adding new topologies means adding a new TopologyRule entry here —
# no code changes needed in the matcher.

BUILTIN_TOPOLOGIES: list[TopologyRule] = [
    TopologyRule(
        name="indicator_led",
        comp_meta={"passive_class": {"led"}},
        pin_roles={PinRole.ANODE, PinRole.CATHODE},
        net_role_map={
            PinRole.CATHODE: NetRole.GROUND,
        },
        constraints=[
            {
                "type": ConstraintType.LOAD,
                "source_role": PinRole.ANODE,
                "target_meta": {"passive_class": {"resistor"}},
            },
            {
                "type": ConstraintType.GROUNDED_BY,
                "source_role": PinRole.CATHODE,
                "net_role": NetRole.GROUND,
            },
        ],
    ),
    TopologyRule(
        name="bypass_capacitor",
        comp_meta={"passive_class": {"capacitor"}},
        pin_roles=set(),  # any capacitor with two pins is a candidate
        net_role_map={},
        constraints=[
            {
                "type": ConstraintType.DECOUPLES,
                "source_role": None,  # applies to both pins
                "net_role": NetRole.POWER,
            },
            {
                "type": ConstraintType.GROUNDED_BY,
                "source_role": None,
                "net_role": NetRole.GROUND,
            },
        ],
    ),
    TopologyRule(
        name="pull_up_resistor",
        comp_meta={"passive_class": {"resistor"}},
        pin_roles=set(),
        net_role_map={},
        constraints=[
            {
                "type": ConstraintType.PULLED_UP,
                "source_role": None,
                "net_role": NetRole.POWER,
            },
        ],
    ),
    TopologyRule(
        name="pull_down_resistor",
        comp_meta={"passive_class": {"resistor"}},
        pin_roles=set(),
        net_role_map={},
        constraints=[
            {
                "type": ConstraintType.PULLED_DOWN,
                "source_role": None,
                "net_role": NetRole.GROUND,
            },
        ],
    ),
    TopologyRule(
        name="linear_regulator",
        comp_meta={"component_class": {"linear_regulator"}},
        pin_roles={PinRole.VIN, PinRole.VOUT, PinRole.GND},
        net_role_map={
            PinRole.VIN:  NetRole.POWER,
            PinRole.GND:  NetRole.GROUND,
        },
        constraints=[
            {
                "type": ConstraintType.POWERED_BY,
                "source_role": PinRole.VIN,
                "net_role": NetRole.POWER,
            },
            {
                "type": ConstraintType.GROUNDED_BY,
                "source_role": PinRole.GND,
                "net_role": NetRole.GROUND,
            },
            {
                "type": ConstraintType.SERIES,
                "source_role": PinRole.VOUT,
                "target_meta": {"component_class": {"microcontroller"}},
            },
        ],
    ),
]


# ── Matcher ─────────────────────────────────────────────────────────────────


def _matches_meta(comp: ComponentNode, predicates: dict[str, set[str]]) -> bool:
    """Check if a component satisfies all metadata predicates."""
    if not predicates:
        return True
    for key, expected in predicates.items():
        actual = comp.metadata.get(key)
        if isinstance(actual, str):
            if actual.lower() not in {v.lower() for v in expected}:
                return False
        elif isinstance(actual, (list, tuple)):
            if not any(a.lower() in {v.lower() for v in expected} for a in actual):
                return False
        else:
            return False
    return True


def _find_connected_components(
    graph: SynthesisGraph,
    comp: ComponentNode,
    pin_role: PinRole | None,
) -> list[ComponentNode]:
    """Find components connected to the given component via nets on the given pin role."""
    matched = set()
    target_pins: set[str] = set()
    for pin_key, pin in comp.pins.items():
        if pin_role is not None and pin.role != pin_role:
            continue
        target_pins.add(pin_key)

    if not target_pins:
        return []

    for net in graph.nets.values():
        connected = target_pins & net.pins
        if not connected:
            continue
        other_pins = net.pins - connected
        for op in other_pins:
            other_ref = op.split(":")[0] if ":" in op else ""
            other_comp = graph.components.get(other_ref)
            if other_comp and other_comp != comp:
                matched.add(other_comp)

    # Also check power_pins
    for pp in graph.power_pins:
        pin_key = pp.get("pin", "")
        if pin_key in target_pins:
            continue
        # power pins don't connect to other components directly here

    return list(matched)


def match_and_constrain(graph: SynthesisGraph, topologies: list[TopologyRule] | None = None):
    """Match topology rules against the graph and generate constraints.

    Idempotent — call as often as needed (duplicate constraints are not added).
    """
    rules = topologies or BUILTIN_TOPOLOGIES
    existing: set[tuple] = set()

    def _already(ct: ConstraintType, src: str, tgt: str | None) -> bool:
        key = (ct.value, src, tgt or "")
        if key in existing:
            return True
        existing.add(key)
        return False

    for rule in rules:
        for comp in graph.components.values():
            if not _matches_meta(comp, rule.comp_meta):
                continue

            # Check required pin roles exist
            if rule.pin_roles:
                comp_roles = {p.role for p in comp.pins.values()}
                if not rule.pin_roles.issubset(comp_roles):
                    continue

            # Generate net-role constraints (requirements, not facts)
            for src_role, expected_net_role in rule.net_role_map.items():
                for pin_key, pin in comp.pins.items():
                    if pin.role != src_role:
                        continue
                    # Find the net this pin is on (if any)
                    actual_net = None
                    for net in graph.nets.values():
                        if pin_key in net.pins:
                            actual_net = net
                            break
                    net_name = actual_net.name if actual_net else "?"
                    if _already(ConstraintType.POWERED_BY, pin_key, net_name):
                        continue
                    graph.add_constraint(ConstraintEdge(
                        type=ConstraintType.POWERED_BY
                            if expected_net_role == NetRole.POWER
                            else ConstraintType.GROUNDED_BY,
                        source_pin=pin_key,
                        target_pin=net_name,
                        metadata={
                            "topology": rule.name,
                            "expected_net_role": expected_net_role.value,
                            "actual_net_role": actual_net.role.value if actual_net else "none",
                        },
                    ))

            # Generate cross-component constraints
            for spec in rule.constraints:
                ct_type = spec["type"]
                src_role = spec.get("source_role")
                target_meta = spec.get("target_meta")
                target_role = spec.get("target_role")
                net_role = spec.get("net_role")

                # Find source pins
                source_pins = []
                if src_role is not None:
                    source_pins = [pk for pk, p in comp.pins.items() if p.role == src_role]
                else:
                    source_pins = list(comp.pins.keys())

                for sp in source_pins:
                    # If there's a target_meta, search for connected comps matching it
                    if target_meta:
                        neighbors = _find_connected_components(
                            graph, comp,
                            src_role if src_role else None,
                        )
                        for neigh in neighbors:
                            if not _matches_meta(neigh, target_meta):
                                continue
                            if _already(ct_type, sp, neigh.ref_des):
                                continue
                            graph.add_constraint(ConstraintEdge(
                                type=ct_type,
                                source_pin=sp,
                                target_pin=neigh.ref_des,
                                metadata={"topology": rule.name},
                            ))

                    # If there's a net_role, find nets on that role
                    if net_role is not None:
                        for net in graph.nets.values():
                            if sp not in net.pins:
                                continue
                            if net.role != net_role:
                                continue
                            if _already(ct_type, sp, net.name):
                                continue
                            graph.add_constraint(ConstraintEdge(
                                type=ct_type,
                                source_pin=sp,
                                target_pin=net.name,
                                metadata={"topology": rule.name, "net_role": net_role.value},
                            ))
