"""Functional block detection via signal-name seeds + Louvain communities.

Identifies groups of components that form a functional unit (e.g. USB block,
reset block, crystal oscillator block) by combining heuristic signal-name
matching with NetworkX Louvain community detection.

Usage::

    from agent.placement.community import detect_blocks, seed_block_assignments

    block_of = detect_blocks(graph, netlist)
    # -> {ref_des: block_name, ...}
"""

from __future__ import annotations

import networkx as nx

SMALL_CIRCUIT_MAX_COMPONENTS = 20

_BLOCK_SEEDS: dict[str, str] = {
    "RESET": "RESET_BLOCK",
    "EN":    "RESET_BLOCK",
    "ENABLE": "RESET_BLOCK",
    "RST":   "RESET_BLOCK",
    "NRST":  "RESET_BLOCK",
    "BOOT0": "BOOT_BLOCK",
    "BOOT1": "BOOT_BLOCK",
    "MODE":  "BOOT_BLOCK",
    "USB_DP": "USB_BLOCK",
    "USB_DM": "USB_BLOCK",
    "VBUS":  "USB_BLOCK",
    "XTAL":  "CRYSTAL_BLOCK",
    "XIN":   "CRYSTAL_BLOCK",
    "XOUT":  "CRYSTAL_BLOCK",
    "XI":    "CRYSTAL_BLOCK",
    "XO":    "CRYSTAL_BLOCK",
    "OSC":   "CRYSTAL_BLOCK",
    "OSC_IN":  "CRYSTAL_BLOCK",
    "OSC_OUT": "CRYSTAL_BLOCK",
    "MOSI":  "SPI_BLOCK",
    "MISO":  "SPI_BLOCK",
    "SCK":   "SPI_BLOCK",
    "CS":    "SPI_BLOCK",
    "SS":    "SPI_BLOCK",
    "NSS":   "SPI_BLOCK",
    "SCL":   "I2C_BLOCK",
    "SDA":   "I2C_BLOCK",
    "TX":    "UART_BLOCK",
    "RX":    "UART_BLOCK",
    "TXD":   "UART_BLOCK",
    "RXD":   "UART_BLOCK",
    "CANH":  "CAN_BLOCK",
    "CANL":  "CAN_BLOCK",
    "SWDIO": "DEBUG_BLOCK",
    "SWCLK": "DEBUG_BLOCK",
    "SWO":   "DEBUG_BLOCK",
    "TMS":   "DEBUG_BLOCK",
    "TCK":   "DEBUG_BLOCK",
    "TDI":   "DEBUG_BLOCK",
    "TDO":   "DEBUG_BLOCK",
}

_BLOCK_ROLE: dict[str, str] = {
    "POWER_BLOCK":  "power",
    "USB_BLOCK":    "power",
    "REGULATOR_BLOCK": "regulator",
    "CRYSTAL_BLOCK": "mcu",
    "RESET_BLOCK":  "mcu",
    "BOOT_BLOCK":   "mcu",
    "MCU_BLOCK":    "mcu",
    "RF_MODULE_BLOCK": "mcu",
    "SPI_BLOCK":    "peripheral",
    "I2C_BLOCK":    "peripheral",
    "UART_BLOCK":   "peripheral",
    "CAN_BLOCK":    "peripheral",
    "DEBUG_BLOCK":  "mcu",
    "SENSOR_BLOCK": "peripheral",
    "DISPLAY_BLOCK": "peripheral",
    "LED_BLOCK":    "peripheral",
    "DECOUPLING_BLOCK": "mcu",
    "ORPHAN_BLOCK": "peripheral",
}


def seed_block_assignments(netlist: list) -> dict[str, str]:
    """Tag component pairs with a block ID based on signal names.

    Scans the netlist for pin names matching ``_BLOCK_SEEDS`` keys and
    tags *both* endpoints of the net with the corresponding block ID.
    """
    block_of: dict[str, str] = {}
    for conn in netlist:
        for side in ("source", "target"):
            pin_key = conn.get(side, "")
            pin_name = pin_key.split(":")[-1] if ":" in pin_key else pin_key
            pin_up = pin_name.upper().replace(" ", "_")
            block_id = None
            for kw, bid in _BLOCK_SEEDS.items():
                if kw == pin_up or pin_up.startswith(kw + "_") or pin_up.endswith("_" + kw):
                    block_id = bid
                    break
            if block_id is None:
                continue
            sr = conn["source"].split(":")[0]
            tr = conn["target"].split(":")[0]
            block_of.setdefault(sr, block_id)
            block_of.setdefault(tr, block_id)
    return block_of


def detect_blocks(graph: nx.Graph, netlist: list) -> dict[str, str]:
    """Detect functional blocks via Louvain modularity + seed signals.

    Returns a dict ``{ref_des: block_name}`` for every component in *graph*.
    Seed-named signals (RESET, USB, XTAL, …) are matched first;
    Louvain partitions fill in the remaining components.
    """
    if graph.number_of_nodes() <= SMALL_CIRCUIT_MAX_COMPONENTS:
        return {ref: "SMALL_CIRCUIT_BLOCK" for ref in graph.nodes}

    block_of: dict[str, str] = {}
    seeded = seed_block_assignments(netlist)
    block_of.update(seeded)

    assigned = set(seeded)
    unassigned = [n for n in graph.nodes if n not in assigned]
    if len(unassigned) >= 3:
        sub = graph.subgraph(unassigned).copy()
        try:
            communities = nx.algorithms.community.louvain_communities(
                sub, weight="weight", seed=42
            )
        except AttributeError:
            communities = nx.algorithms.community.greedy_modularity_communities(
                sub, weight="weight"
            )
        for i, comm in enumerate(communities):
            block_name = f"LOUVAIN_BLOCK_{i}"
            for r in comm:
                if r in seeded:
                    block_name = seeded[r]
                    break
            for ref in comm:
                block_of[ref] = block_name

    for ref in graph.nodes:
        if ref not in block_of:
            block_of[ref] = "ORPHAN_BLOCK"

    return block_of
