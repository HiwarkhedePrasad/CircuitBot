"""Low-level pattern-matching functions for motif detection.

Each function operates on a SynthesisGraph and a MotifSignature.
They are pure query functions — no side effects, no mutation.
"""

from __future__ import annotations

from typing import Any, Optional

from agent.schematic.schematic_types import (
    MotifSignature,
    PinNetConstraint,
    SecondarySpec,
)


# ── Component metadata matching ─────────────────────────────────────────────


def matches_meta(comp: Any, predicates: dict[str, set[str]]) -> bool:
    """Check if a component satisfies all metadata predicates.

    Keys are metadata field names (e.g. "component_class", "passive_class").
    Values are sets of acceptable strings (any-of match).
    """
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


# ── Pin role matching ───────────────────────────────────────────────────────


def has_pin_roles(comp: Any, required_roles: set[str]) -> bool:
    """Check if a component has pins with all the required roles.

    Pin roles are resolved as strings from PinRole.value.
    """
    if not required_roles:
        return True
    comp_roles = set()
    for pin in comp.pins.values():
        role = getattr(pin, "role", None)
        if role is not None:
            comp_roles.add(str(role.value) if hasattr(role, "value") else str(role))
    return required_roles.issubset(comp_roles)


# ── Net connectivity queries ────────────────────────────────────────────────


def _net_for_pin(graph: Any, pin_key: str) -> Optional[Any]:
    """Find the NetNode connected to a pin."""
    for net in graph.nets.values():
        if pin_key in net.pins:
            return net
    return None


def _net_role_for_pin(graph: Any, pin_key: str) -> Optional[str]:
    """Get the NetRole of the net connected to a pin, as a string."""
    net = _net_for_pin(graph, pin_key)
    if net is not None:
        role = getattr(net, "role", None)
        if role is not None:
            return str(role.value) if hasattr(role, "value") else str(role)
    return None


def _pins_on_net_by_role(graph: Any, comp: Any, target_net_role: str) -> list[str]:
    """Return pins of a component that connect to nets of a given role."""
    result = []
    for pk in comp.pins:
        nr = _net_role_for_pin(graph, pk)
        if nr == target_net_role:
            result.append(pk)
    return result


def _components_sharing_net(
    graph: Any, comp: Any, connected_pin_role: Optional[str],
) -> dict[str, set[str]]:
    """Find all components sharing a net with the given component.

    Returns dict of {ref_des: set_of_pin_keys} for connected components.
    """
    connected: dict[str, set[str]] = {}
    target_pins: set[str] = set()

    for pk in comp.pins:
        if connected_pin_role is None:
            target_pins.add(pk)
        else:
            pin = comp.pins.get(pk)
            if pin is not None:
                role = getattr(pin, "role", None)
                role_str = str(role.value) if (role and hasattr(role, "value")) else str(role or "")
                if role_str == connected_pin_role:
                    target_pins.add(pk)

    for net in graph.nets.values():
        shared = target_pins & net.pins
        if not shared:
            continue
        other_pins = net.pins - shared
        for opk in other_pins:
            ref = opk.split(":")[0] if ":" in opk else ""
            if ref and ref != comp.ref_des and ref in graph.components:
                if ref not in connected:
                    connected[ref] = set()
                connected[ref].add(opk)
    return connected


# ── Pin-net constraint checking ─────────────────────────────────────────────


def check_pin_net_constraints(
    comp: Any, constraints: list[PinNetConstraint], graph: Any,
) -> bool:
    """Verify a component satisfies all required pin-net constraints.

    Each constraint says: a pin with role X (any if empty) should connect
    to a net with role Y.  This function checks if there exists an assignment
    of constraints to pins such that all required constraints are met.
    """
    if not constraints:
        return True

    # Build list of (pin_key, net_role) available for this component
    available: list[tuple[str, str]] = []
    for pk in comp.pins:
        nr = _net_role_for_pin(graph, pk)
        if nr:
            available.append((pk, nr))

    required_constraints = [c for c in constraints if c.required]
    optional_constraints = [c for c in constraints if not c.required]

    # Try to match required constraints to available (pin, net_role) pairs
    used_pins: set[str] = set()

    def _matches_constraint(pin_key: str, net_role: str, constraint: PinNetConstraint) -> bool:
        if constraint.pin_role:
            pin = comp.pins.get(pin_key)
            if pin is not None:
                role = getattr(pin, "role", None)
                role_str = str(role.value) if (role and hasattr(role, "value")) else ""
                if role_str != constraint.pin_role:
                    return False
        if constraint.net_role and net_role != constraint.net_role:
            return False
        return True

    for constraint in required_constraints:
        matched = False
        for i, (pk, nr) in enumerate(available):
            if pk in used_pins:
                continue
            if _matches_constraint(pk, nr, constraint):
                used_pins.add(pk)
                matched = True
                break
        if not matched:
            return False

    return True


# ── Secondary component discovery ───────────────────────────────────────────


def find_secondaries(
    comp: Any, specs: list[SecondarySpec], graph: Any,
) -> dict[str, str]:
    """Find secondary components matching the given specs.

    Returns dict of {label: ref_des} for matched secondaries.
    Only required secondaries must be found; optional ones may be missing.
    """
    result: dict[str, str] = {}
    neighbors = _components_sharing_net(graph, comp, None)

    for spec in specs:
        found = False
        for ref, _ in neighbors.items():
            if ref in result.values():
                continue
            neighbor = graph.components.get(ref)
            if neighbor is None:
                continue
            if not matches_meta(neighbor, spec.meta):
                continue
            if spec.pin_roles:
                if not has_pin_roles(neighbor, spec.pin_roles):
                    continue
            result[spec.label] = ref
            found = True
            break
        if not found and spec.required:
            return {}

    return result


# ── Candidate scoring ────────────────────────────────────────────────────────


def calculate_score(
    comp: Any, secondaries: dict[str, str], graph: Any, signature: MotifSignature,
) -> float:
    """Calculate a match score for a candidate.

    Base score from signature, plus:
      - +5 per secondary found
      - +10 if all required secondaries found
      - -5 if any required secondary missing
      - +3 per pin-net constraint satisfied
      - +2 per pin role matched
    """
    score = signature.base_score

    # Bonus for finding required secondaries
    required_count = sum(1 for s in signature.secondaries if s.required)
    found_required = sum(1 for s in signature.secondaries if s.required and s.label in secondaries)
    if required_count > 0 and found_required == required_count:
        score += 10.0
    score += 5.0 * len(secondaries)

    # Bonus for matching pin-net constraints
    matched_constraints = 0
    for c in signature.pin_net_constraints:
        if c.pin_role:
            pin = next((p for p_name, p in comp.pins.items()
                        if getattr(p, "role", None) and hasattr(p.role, "value")
                        and str(p.role.value) == c.pin_role), None)
            if pin is not None:
                nr = _net_role_for_pin(graph, pin.key if hasattr(pin, "key") else "")
                if nr == c.net_role:
                    matched_constraints += 1
        else:
            pins_with_role = _pins_on_net_by_role(graph, comp, c.net_role)
            if pins_with_role:
                matched_constraints += 1
    score += 3.0 * matched_constraints

    # Bonus for matching pin roles
    if signature.primary_pin_roles:
        comp_roles = set()
        for pin in comp.pins.values():
            role = getattr(pin, "role", None)
            if role is not None:
                comp_roles.add(str(role.value) if hasattr(role, "value") else str(role))
        matched_roles = len(signature.primary_pin_roles & comp_roles)
        score += 2.0 * matched_roles

    return score


# ── Candidate generation ────────────────────────────────────────────────────


class CandidateMatch:
    """A candidate motif match before conflict resolution."""
    __slots__ = ("signature", "primary", "secondaries", "score", "all_components")

    def __init__(
        self,
        signature: MotifSignature,
        primary: str,
        secondaries: dict[str, str],
        score: float,
    ):
        self.signature = signature
        self.primary = primary
        self.secondaries = secondaries
        self.score = score
        self.all_components: set[str] = {primary}
        self.all_components.update(secondaries.values())

    def __repr__(self) -> str:
        return (f"CandidateMatch({self.signature.name}, primary={self.primary}, "
                f"score={self.score:.1f})")


def discover_candidates(
    graph: Any, signature: MotifSignature,
) -> list[CandidateMatch]:
    """Discover all candidate matches for a single signature.

    Walks every component in the graph and checks if it matches
    the signature's primary component requirements, pin roles,
    pin-net constraints, and secondaries.
    """
    candidates: list[CandidateMatch] = []

    for comp in graph.components.values():
        if not matches_meta(comp, signature.primary_meta):
            continue

        if not has_pin_roles(comp, signature.primary_pin_roles):
            continue

        if not check_pin_net_constraints(comp, signature.pin_net_constraints, graph):
            continue

        secondaries = find_secondaries(comp, signature.secondaries, graph)
        required_labels = {s.label for s in signature.secondaries if s.required}
        found_labels = set(secondaries.keys())
        if required_labels and not required_labels.issubset(found_labels):
            continue

        score = calculate_score(comp, secondaries, graph, signature)
        candidates.append(CandidateMatch(
            signature=signature,
            primary=comp.ref_des,
            secondaries=secondaries,
            score=score,
        ))

    return candidates
