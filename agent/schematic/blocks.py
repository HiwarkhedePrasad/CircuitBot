"""Functional block builder.

Groups motifs and orphan components into FunctionalBlocks based on:
  - Motif membership
  - Connectivity to major ICs (controller, regulators, interface ICs)
  - Semantic roles from the analyzer
  - Power domain membership

Produces a BlockGraph with directed edges between blocks.
"""

from __future__ import annotations

from typing import Any, Optional

from agent.schematic.schematic_types import (
    BlockEdge,
    BlockGraph,
    BlockRole,
    FunctionalBlock,
    Motif,
    MotifType,
)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _net_for_pin(graph: Any, pin_key: str) -> Optional[str]:
    for net in graph.nets.values():
        if pin_key in net.pins:
            return net.name
    return None


def _component_pins(graph: Any, ref_des: str) -> set[str]:
    comp = graph.components.get(ref_des)
    if comp is None:
        return set()
    return set(comp.pins.keys())


def _ic_refs_by_class(graph: Any, class_name: str) -> list[str]:
    return [
        ref for ref, comp in graph.components.items()
        if comp.metadata.get("component_class") == class_name
    ]


def _find_connected_ics(graph: Any, ref_des: str) -> dict[str, float]:
    """Find ICs connected to a component, returning {ref_des: connection_weight}."""
    connections: dict[str, float] = {}
    pin_keys = _component_pins(graph, ref_des)

    for net in graph.nets.values():
        shared = pin_keys & net.pins
        if not shared:
            continue
        other_pins = net.pins - shared
        for opk in other_pins:
            other_ref = opk.split(":")[0] if ":" in opk else ""
            if other_ref and other_ref != ref_des and other_ref in graph.components:
                connections[other_ref] = connections.get(other_ref, 0) + 1.0

    return connections


def _is_major_ic(graph: Any, ref_des: str) -> bool:
    """Check if a component should anchor its own block."""
    comp = graph.components.get(ref_des)
    if comp is None:
        return False
    cls = comp.metadata.get("component_class", "")
    return cls in (
        "microcontroller", "linear_regulator", "switching_regulator",
        "interface_ic", "sensor", "amplifier", "comparator", "connector",
    )


# ── Block building ──────────────────────────────────────────────────────────


def _find_components_for_motif(motif: Motif, all_motifs: list[Motif]) -> set[str]:
    """Get all component ref_des in a motif and its submotifs."""
    return set(motif.components)


def build_block_graph(ctx: Any) -> None:
    """Build BlockGraph from motifs, semantic model, and graph connectivity.

    Mutates ctx.block_graph in place.
    """
    graph = ctx.synthesis_graph
    semantic = ctx.semantic_model
    motifs = ctx.resolved_motifs or ctx.motifs

    claimed: set[str] = set()
    block_graph = BlockGraph()
    all_motif_comps: set[str] = set()
    for m in motifs:
        all_motif_comps.update(m.components)

    # ── Step 1: Controller block ──────────────────────────────────────
    controller_ref = semantic.controller
    controller_block: Optional[FunctionalBlock] = None
    if controller_ref:
        controller_motifs: list[str] = []
        ctrl_component_refs: set[str] = set()

        for motif in motifs:
            if _belongs_to_ic(motif, controller_ref, graph):
                controller_motifs.append(motif.id)
                claimed.update(motif.components)
                ctrl_component_refs.update(motif.components)

        comp = graph.components.get(controller_ref)

        controller_block = FunctionalBlock(
            name=comp.description if comp and comp.description else "Controller",
            role=BlockRole.CONTROLLER,
            motifs=controller_motifs,
            orphan_components=[controller_ref],
            anchor=controller_ref,
        )
        controller_block.component_refs = ctrl_component_refs | {controller_ref}
        block_graph.add_block(controller_block)
        claimed.add(controller_ref)

    # ── Step 2: Power blocks ─────────────────────────────────────────
    regulators = _ic_refs_by_class(graph, "linear_regulator") + \
                 _ic_refs_by_class(graph, "switching_regulator")

    for reg_ref in regulators:
        if reg_ref in claimed:
            continue
        reg_motifs: list[str] = []
        reg_component_refs: set[str] = set()

        for motif in motifs:
            if _belongs_to_ic(motif, reg_ref, graph):
                reg_motifs.append(motif.id)
                claimed.update(motif.components)
                reg_component_refs.update(motif.components)

        power_block = FunctionalBlock(
            name=f"Power_{reg_ref}",
            role=BlockRole.POWER_CONDITIONING,
            motifs=reg_motifs,
            orphan_components=[reg_ref],
            anchor=reg_ref,
            signal_flow="top_bottom",
        )
        power_block.component_refs = reg_component_refs | {reg_ref}
        block_graph.add_block(power_block)
        claimed.add(reg_ref)

    # ── Step 3: Interface blocks ─────────────────────────────────────
    connectors = _ic_refs_by_class(graph, "connector")
    interface_ics = _ic_refs_by_class(graph, "interface_ic")
    sensors = _ic_refs_by_class(graph, "sensor")

    for ic_ref in connectors + interface_ics + sensors:
        if ic_ref in claimed:
            continue
        ic_motifs: list[str] = []
        ic_component_refs: set[str] = set()
        for motif in motifs:
            if _belongs_to_ic(motif, ic_ref, graph):
                ic_motifs.append(motif.id)
                claimed.update(motif.components)
                ic_component_refs.update(motif.components)

        comp = graph.components.get(ic_ref)
        cls = comp.metadata.get("component_class", "") if comp else ""
        role = BlockRole.INTERFACE if cls in ("connector", "interface_ic") else BlockRole.SENSOR

        iface_block = FunctionalBlock(
            name=f"{role.value}_{ic_ref}",
            role=role,
            motifs=ic_motifs,
            orphan_components=[ic_ref],
            anchor=ic_ref,
        )
        iface_block.component_refs = ic_component_refs | {ic_ref}
        block_graph.add_block(iface_block)
        claimed.add(ic_ref)

    # ── Step 4: Major ICs with no motifs → their own block ──────────
    for ref in list(graph.components.keys()):
        if ref in claimed:
            continue
        if _is_major_ic(graph, ref):
            ic_block = FunctionalBlock(
                name=f"IC_{ref}",
                role=_role_for_ic(graph, ref),
                orphan_components=[ref],
                anchor=ref,
            )
            ic_block.component_refs = {ref}
            block_graph.add_block(ic_block)
            claimed.add(ref)

    # ── Step 5: Remaining motifs as their own blocks ─────────────────
    for motif in motifs:
        if motif.components and not set(motif.components) & claimed:
            comps = set(motif.components)
            block = FunctionalBlock(
                name=f"motif_{motif.motif_type.value}",
                role=_role_for_motif(motif),
                motifs=[motif.id],
                anchor=motif.anchor,
            )
            block.component_refs = comps
            block_graph.add_block(block)
            claimed.update(comps)

    # ── Step 5: Orphan components → attach to connected blocks ──────
    orphans = [ref for ref in graph.components if ref not in claimed]
    for ref in orphans:
        connected = _find_connected_ics(graph, ref)
        best_block: Optional[str] = None
        best_score = 0.0

        for c_ref, weight in connected.items():
            for bid, block in block_graph.blocks.items():
                if c_ref in block.orphan_components:
                    if weight > best_score:
                        best_score = weight
                        best_block = bid

        if best_block and best_score > 0:
            block_graph.blocks[best_block].orphan_components.append(ref)
            block_graph.blocks[best_block].component_refs.add(ref)
            claimed.add(ref)

    # ── Step 6: Remaining orphans → One passive network block ───────
    remaining = [ref for ref in graph.components if ref not in claimed]
    if remaining:
        passive_block = FunctionalBlock(
            name="Passive_Network",
            role=BlockRole.PASSIVE_NETWORK,
            orphan_components=remaining,
            anchor=remaining[0],
        )
        passive_block.component_refs = set(remaining)
        block_graph.add_block(passive_block)
        claimed.update(remaining)

    # ── Step 7: Detect edges between blocks ──────────────────────────
    _detect_block_edges(block_graph, graph)

    ctx.block_graph = block_graph


def _belongs_to_ic(motif: Motif, ic_ref: str, graph: Any) -> bool:
    """Check if a motif's components should be grouped with a specific IC.

    Rules:
      1. If the motif's anchor IS the IC → belongs.
      2. If the motif's anchor is a different major IC → does NOT belong
         (prevents regulators from being absorbed by controllers).
      3. If connected to the IC → only belongs if the motif type is
         compatible with the IC's role (a USB motif doesn't belong to
         a regulator just because they share a power net).
    """
    if motif.anchor == ic_ref:
        return True
    if _is_major_ic(graph, motif.anchor):
        return False
    for comp_ref in motif.components:
        if comp_ref == ic_ref:
            return True
        connected = _find_connected_ics(graph, comp_ref)
        if ic_ref in connected:
            if _motif_compatible_with_ic(motif, ic_ref, graph):
                return True
    return False


_MOTIF_IC_COMPAT: dict[str, set[MotifType]] = {
    "linear_regulator": {
        MotifType.DECOUPLING_CAP, MotifType.PULL_UP, MotifType.PULL_DOWN,
        MotifType.POWER_ENTRY, MotifType.RC_FILTER, MotifType.PI_FILTER,
    },
    "switching_regulator": {
        MotifType.DECOUPLING_CAP, MotifType.PULL_UP, MotifType.PULL_DOWN,
        MotifType.POWER_ENTRY, MotifType.RC_FILTER, MotifType.PI_FILTER,
    },
    "microcontroller": {
        MotifType.DECOUPLING_CAP, MotifType.PULL_UP, MotifType.PULL_DOWN,
        MotifType.CRYSTAL, MotifType.RESET_CIRCUIT, MotifType.LED_INDICATOR,
        MotifType.RC_FILTER, MotifType.VOLTAGE_DIVIDER, MotifType.I2C_BUS,
        MotifType.PROGRAMMING_HEADER,
    },
    "connector": {
        MotifType.USB_INTERFACE, MotifType.POWER_ENTRY,
    },
    "interface_ic": {
        MotifType.USB_INTERFACE, MotifType.PROGRAMMING_HEADER,
        MotifType.I2C_BUS,
    },
    "sensor": {
        MotifType.RC_FILTER, MotifType.PULL_UP, MotifType.PULL_DOWN,
    },
}


def _motif_compatible_with_ic(motif: Motif, ic_ref: str, graph: Any) -> bool:
    """Check if a motif type is compatible with an IC's role."""
    comp = graph.components.get(ic_ref)
    if comp is None:
        return True
    ic_class = comp.metadata.get("component_class", "")
    allowed = _MOTIF_IC_COMPAT.get(ic_class)
    if allowed is None:
        return True  # unknown IC class, allow all
    return motif.motif_type in allowed


def _role_for_ic(graph: Any, ref: str) -> BlockRole:
    """Assign a block role based on an IC's component class."""
    comp = graph.components.get(ref)
    if comp is None:
        return BlockRole.UNKNOWN
    cls = comp.metadata.get("component_class", "")
    if cls in ("linear_regulator", "switching_regulator"):
        return BlockRole.POWER_CONDITIONING
    if cls in ("microcontroller",):
        return BlockRole.CONTROLLER
    if cls in ("interface_ic",):
        return BlockRole.INTERFACE
    if cls in ("sensor",):
        return BlockRole.SENSOR
    if cls in ("amplifier", "comparator"):
        return BlockRole.SIGNAL_CONDITIONING
    return BlockRole.UNKNOWN


def _role_for_motif(motif: Motif) -> BlockRole:
    """Assign a block role based on motif type."""
    power_types = {
        MotifType.DECOUPLING_CAP, MotifType.PULL_UP, MotifType.PULL_DOWN,
        MotifType.POWER_ENTRY, MotifType.BATTERY_CHARGER,
    }
    interface_types = {
        MotifType.USB_INTERFACE, MotifType.PROGRAMMING_HEADER,
        MotifType.I2C_BUS, MotifType.CRYSTAL, MotifType.RESET_CIRCUIT,
    }
    active_types = {MotifType.OPAMP, MotifType.MOSFET_DRIVER}
    signal_types = {
        MotifType.RC_FILTER, MotifType.PI_FILTER,
        MotifType.VOLTAGE_DIVIDER, MotifType.LED_INDICATOR,
    }

    if motif.motif_type in power_types:
        return BlockRole.POWER_CONDITIONING
    if motif.motif_type in interface_types:
        return BlockRole.INTERFACE
    if motif.motif_type in active_types:
        return BlockRole.SIGNAL_CONDITIONING
    if motif.motif_type in signal_types:
        return BlockRole.PASSIVE_NETWORK
    return BlockRole.UNKNOWN


def _detect_block_edges(block_graph: BlockGraph, graph: Any) -> None:
    """Detect directed edges between blocks based on shared nets."""
    block_pins: dict[str, set[str]] = {}
    for bid, block in block_graph.blocks.items():
        pins: set[str] = set()
        for ref in block.orphan_components:
            comp = graph.components.get(ref)
            if comp:
                pins.update(comp.pins.keys())
        block_pins[bid] = pins

    nets_by_role: dict[str, list[str]] = {}
    for net in graph.nets.values():
        role = str(net.role.value) if hasattr(net.role, "value") else str(net.role)
        if role not in nets_by_role:
            nets_by_role[role] = []
        nets_by_role[role].append(net.name)

    power_net_names: set[str] = set(nets_by_role.get("power", []))
    ground_net_names: set[str] = set(nets_by_role.get("ground", []))

    for net in graph.nets.values():
        net_pins = net.pins
        if not net_pins:
            continue

        net_power = net.name in power_net_names
        net_ground = net.name in ground_net_names

        connected_blocks: list[str] = []
        for bid, pins in block_pins.items():
            if net_pins & pins:
                connected_blocks.append(bid)

        if len(connected_blocks) >= 2:
            for i in range(len(connected_blocks) - 1):
                src = connected_blocks[i]
                tgt = connected_blocks[i + 1]
                if net_power or net_ground:
                    signal_type = "power" if net_power else "ground"
                    if net_power:
                        block_graph.add_edge(BlockEdge(
                            source_id=src, target_id=tgt,
                            net=net.name, signal_type="power",
                        ))
                else:
                    block_graph.add_edge(BlockEdge(
                        source_id=src, target_id=tgt,
                        net=net.name, signal_type="signal",
                    ))
