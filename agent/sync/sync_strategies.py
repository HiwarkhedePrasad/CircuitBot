"""Component matching strategies for schematic synchronization.

Adapted from circuit-synth's sync_strategies.py to work with CircuitBot's
internal data model (dicts from parsed S-expressions instead of kicad_sch_api).
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import Any

from .schematic_importer import ImportedComponent


class SyncStrategy(ABC):
    """Base class for component matching strategies."""

    @abstractmethod
    def match_components(
        self,
        circuit_components: dict[str, dict],
        kicad_components: dict[str, ImportedComponent],
    ) -> dict[str, str]:
        """Match circuit components to KiCad components.

        Args:
            circuit_components: Dict mapping circuit_id -> component dict
                with keys: ref_des, id_str, footprint, value, etc.
            kicad_components: Dict mapping ref -> ImportedComponent

        Returns:
            Dict mapping circuit_id -> kicad_ref for matched components.
        """
        pass


class UUIDMatchStrategy(SyncStrategy):
    """Match components by UUID — most reliable identifier."""

    def match_components(
        self,
        circuit_components: dict[str, dict],
        kicad_components: dict[str, ImportedComponent],
    ) -> dict[str, str]:
        matches = {}

        for circuit_id, circuit_comp in circuit_components.items():
            circuit_uuid = circuit_comp.get("uuid", "")
            if not circuit_uuid:
                continue

            for kicad_ref, kicad_comp in kicad_components.items():
                if kicad_comp.uuid == circuit_uuid:
                    matches[circuit_id] = kicad_ref
                    break

        return matches


class ReferenceMatchStrategy(SyncStrategy):
    """Match components by reference designator."""

    def match_components(
        self,
        circuit_components: dict[str, dict],
        kicad_components: dict[str, ImportedComponent],
    ) -> dict[str, str]:
        matches = {}
        used_refs: set[str] = set()

        for circuit_id, circuit_comp in circuit_components.items():
            ref = circuit_comp.get("ref_des", "")
            if not ref:
                continue

            if ref in kicad_components and ref not in used_refs:
                matches[circuit_id] = ref
                used_refs.add(ref)

        return matches


class PositionRenameStrategy(SyncStrategy):
    """Detect renames by matching position + properties but different ref.

    If a KiCad component matches a circuit component on position, symbol,
    value, and footprint but has a different reference, it's a rename.
    """

    POSITION_TOLERANCE = 2.54  # mm (one KiCad grid unit)

    def match_components(
        self,
        circuit_components: dict[str, dict],
        kicad_components: dict[str, ImportedComponent],
    ) -> dict[str, str]:
        matches = {}
        used_refs: set[str] = set()

        for circuit_id, circuit_comp in circuit_components.items():
            circuit_ref = circuit_comp.get("ref_des", "")

            # Skip if already matched by exact reference
            if circuit_ref in kicad_components:
                continue

            # Need position data from the circuit component
            cx = circuit_comp.get("x", circuit_comp.get("at_x", 0.0))
            cy = circuit_comp.get("y", circuit_comp.get("at_y", 0.0))
            if cx == 0.0 and cy == 0.0:
                continue

            circuit_symbol = circuit_comp.get("id_str", "")
            circuit_value = circuit_comp.get("value", "")
            circuit_footprint = circuit_comp.get("footprint", "")

            for kicad_ref, kicad_comp in kicad_components.items():
                if kicad_ref in used_refs:
                    continue

                # Position match
                dx = abs(cx - kicad_comp.x)
                dy = abs(cy - kicad_comp.y)
                if dx > self.POSITION_TOLERANCE or dy > self.POSITION_TOLERANCE:
                    continue

                # Property match
                if circuit_symbol and circuit_symbol != kicad_comp.lib_id:
                    continue
                if circuit_value and circuit_value != kicad_comp.value:
                    continue
                if circuit_footprint and circuit_footprint != kicad_comp.footprint:
                    continue

                # Match found — different ref means rename
                matches[circuit_id] = kicad_ref
                used_refs.add(kicad_ref)
                break

        return matches


class ConnectionMatchStrategy(SyncStrategy):
    """Match components by their net connections.

    Uses a simplified approach: compare which nets each pin connects to.
    """

    def match_components(
        self,
        circuit_components: dict[str, dict],
        kicad_components: dict[str, ImportedComponent],
    ) -> dict[str, str]:
        # Build a mapping of component -> set of connected nets from circuit
        circuit_nets: dict[str, set[str]] = {}
        for circuit_id, circuit_comp in circuit_components.items():
            nets = set()
            # Check if component has pin->net mapping in its data
            pin_nets = circuit_comp.get("pin_nets", {})
            if isinstance(pin_nets, dict):
                nets.update(pin_nets.values())
            circuit_nets[circuit_id] = nets

        # For kicad components, we don't have net info directly from the importer
        # This strategy is a placeholder — in practice, UUID and Reference strategies
        # handle most cases. Connection matching would require wire tracing which
        // is complex and deferred to a future iteration.
        return {}


class ValueFootprintStrategy(SyncStrategy):
    """Match components by value and footprint — least reliable fallback."""

    def match_components(
        self,
        circuit_components: dict[str, dict],
        kicad_components: dict[str, ImportedComponent],
    ) -> dict[str, str]:
        matches = {}
        used_refs: set[str] = set()

        for circuit_id, circuit_comp in circuit_components.items():
            if circuit_id in matches:
                continue

            value = circuit_comp.get("value", "")
            footprint = circuit_comp.get("footprint", "")
            id_str = circuit_comp.get("id_str", "")

            if not value and not id_str:
                continue

            for kicad_ref, kicad_comp in kicad_components.items():
                if kicad_ref in used_refs:
                    continue

                # Match on lib_id (symbol) + value
                if id_str and id_str == kicad_comp.lib_id and value and value == kicad_comp.value:
                    matches[circuit_id] = kicad_ref
                    used_refs.add(kicad_ref)
                    break

                # Match on value + footprint
                if value and value == kicad_comp.value and footprint and footprint == kicad_comp.footprint:
                    matches[circuit_id] = kicad_ref
                    used_refs.add(kicad_ref)
                    break

        return matches


# Strategy order: most reliable first
ALL_STRATEGIES: list[type[SyncStrategy]] = [
    UUIDMatchStrategy,
    ReferenceMatchStrategy,
    PositionRenameStrategy,
    ConnectionMatchStrategy,
    ValueFootprintStrategy,
]


def match_all_strategies(
    circuit_components: dict[str, dict],
    kicad_components: dict[str, ImportedComponent],
) -> dict[str, str]:
    """Run all matching strategies in order and return combined matches.

    Each strategy is tried in order. First match wins — a circuit_id or
    kicad_ref matched by an earlier strategy is not re-matched.

    Returns:
        Dict mapping circuit_id -> kicad_ref
    """
    all_matches: dict[str, str] = {}
    used_circuit_ids: set[str] = set()
    used_kicad_refs: set[str] = set()

    for strategy_cls in ALL_STRATEGIES:
        strategy = strategy_cls()
        matches = strategy.match_components(circuit_components, kicad_components)

        for circuit_id, kicad_ref in matches.items():
            if circuit_id not in used_circuit_ids and kicad_ref not in used_kicad_refs:
                all_matches[circuit_id] = kicad_ref
                used_circuit_ids.add(circuit_id)
                used_kicad_refs.add(kicad_ref)

    return all_matches
