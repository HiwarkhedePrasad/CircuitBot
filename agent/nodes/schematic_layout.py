"""Schematic layout node — replaces old placement + routing nodes.

Orchestrates the full deterministic layout pipeline:
    SynthesisGraph → Analyze → Detect motifs → Build blocks
    → Place blocks → Expand → Optimize → Score → Wire
    → Label → Beautify → Export

This is a single LangGraph node that replaces placement_node,
routing_node, connectivity_validate_node, and connectivity_repair_node.
"""

from __future__ import annotations

from typing import Any

from agent.schematic.schematic_types import LayoutContext
from agent.schematic.analyzer import analyze_circuit
from agent.schematic.detector import detect_motifs, find_orphan_components
from agent.schematic.blocks import build_block_graph
from agent.schematic.placement import place_blocks, generate_constraints
from agent.schematic.expander import Expander
from agent.schematic.optimizer import optimize
from agent.schematic.scoring import score_layout, generate_candidates, pick_best
from agent.schematic.wires import generate_wires
from agent.schematic.labels import place_labels
from agent.schematic.beautify import beautify
from agent.schematic.export_adapter import export


def _build_synthesis_graph(state: dict) -> Any:
    """Build or retrieve a SynthesisGraph from agent state."""
    # If already serialized in state, reconstruct it
    serialised = state.get("synthesis_graph")
    if serialised:
        from agent.synthesis.graph import SynthesisGraph
        g = SynthesisGraph()
        for ref, cd in serialised.get("components", {}).items():
            comp_node = g.add_component(cd)
            for pk, pd in cd.get("pins", {}).items():
                g.add_pin(ref, pk, pd)
        for net_name, nd in serialised.get("nets", {}).items():
            net = g.get_or_create_net(net_name)
            net.pins = set(nd.get("pins", []))
        g.llm_nets = list(serialised.get("llm_nets", []) or [])
        from agent.synthesis.classifier import classify_all
        classify_all(g)
        return g

    # Otherwise build from raw state fields
    comps = state.get("selected_components", [])
    netlist = state.get("netlist", [])
    pin_matrix = state.get("pin_matrix", {})
    power_pins = state.get("power_pins", [])

    from agent.synthesis.graph import SynthesisGraph
    from agent.synthesis.classifier import classify_all
    g = SynthesisGraph()
    for c in comps:
        g.add_component(c)
    for pk, pd in pin_matrix.items():
        ref = pk.split(":")[0] if ":" in pk else ""
        g.add_pin(ref, pk, pd)
    g.import_llm_nets(netlist)
    g.import_power_pins(power_pins)
    classify_all(g)
    return g


def schematic_layout_node(state: dict, config: Any = None) -> dict:
    """Main schematic layout node — replaces placement + routing.

    Args:
        state: AgentState dict with synthesis_graph or raw fields.

    Returns:
        Updated AgentState dict with component_placements, wire_paths,
        power_labels, and _placement_locked set.
    """
    result: dict[str, Any] = {}

    # 1. Build SynthesisGraph
    graph = _build_synthesis_graph(state)
    if not graph.components:
        return {"error": "No components in synthesis graph"}

    # 2. Initialize layout context
    ctx = LayoutContext()
    ctx.synthesis_graph = graph

    # 3. Run pipeline
    try:
        ctx.semantic_model = analyze_circuit(graph)

        ctx.motifs = detect_motifs(graph)
        ctx.resolved_motifs = ctx.motifs

        build_block_graph(ctx)
        place_blocks(ctx)
        _ = generate_constraints(ctx)

        expander = Expander()
        expander.expand_all(ctx)

        optimize(ctx)

        # Score + pick best if we have candidates
        candidates = generate_candidates(ctx, count=1)
        best = pick_best(candidates)
        ctx.placements = best.placements
        ctx.metadata = best.metadata

        generate_wires(ctx)
        place_labels(ctx)
        beautify(ctx)

        output = export(ctx)

        result = {
            "component_placements": output.component_placements,
            "wire_paths": output.wire_paths,
            "power_labels": output.power_labels,
            "_placement_locked": True,
            "_stage": "schematic_layout",
        }

    except Exception as exc:
        import logging
        logging.getLogger(__name__).error(
            "Schematic layout failed: %s", exc, exc_info=True,
        )
        result = {
            "error": f"Schematic layout failed: {exc}",
            "_stage": "schematic_layout_error",
        }

    return result
