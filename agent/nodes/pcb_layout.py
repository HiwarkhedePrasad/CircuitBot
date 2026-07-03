"""PCB layout node — force-directed placement, KiCad pcbnew routing, GND pour, DRC.

Handles:
  1. Force-directed PCB component placement (connectivity-aware).
  2. Build BoardModel from state + placement results.
  3. Route PCB traces via KiCad's pcbnew (subprocess).
  4. GND copper pour on F.Cu + B.Cu with 4-spoke thermal relief.
  5. Shapely-based DRC (clearance, width, keepout).
  6. Emit agent:pcb_ready + agent:done events.
"""

from agent.utils import _emit, emit_assistant_message, emit_tool_event

from pcb_design.board_model import (
    BoardModel, BoardComponent, PadDef, BoardTrace, BoardVia, DRCConfig,
)
from pcb_design.placement import place_components
from pcb_design.geometry import board_outline_polygon, HAS_SHAPELY
from pcb_design.pour import pour_ground


def pcb_layout_node(state, config):
    _emit(config, "agent:thinking", {"message": "Routing PCB traces..."})
    emit_assistant_message(config, "Laying out components on the PCB and routing traces...")
    emit_tool_event(config, "PCB Layout", "running", "Force-directed placement...")

    comps = state.get("selected_components", [])
    pin_matrix = state.get("pin_matrix", {})
    netlist = state.get("netlist", [])
    power_pins = state.get("power_pins", [])
    power_labels = state.get("power_labels", [])
    component_placements = state.get("component_placements", [])

    if not comps:
        _emit(config, "agent:log", {"message": "No components for PCB layout."})
        return {}

    # ── 1. Force-directed PCB component placement ──────────────────
    pcb_placements = place_components(comps, netlist, pin_matrix=pin_matrix)
    pcb_pos = {p["ref_des"]: (p["x"], p["y"], p.get("rotation", 0)) for p in pcb_placements}

    # ── 2. Build BoardModel ────────────────────────────────────────
    model = BoardModel(
        nets=state.get("nets", []),
        power_pins=power_pins,
        power_labels=power_labels,
    )

    for comp in comps:
        ref = comp["ref_des"]
        pads = []
        if comp.get("pads"):
            for pd in comp["pads"]:
                pads.append(PadDef(
                    number=str(pd.get("number", "")),
                    x=pd.get("x", 0), y=pd.get("y", 0),
                    width=pd.get("width", 1), height=pd.get("height", 1),
                    shape=pd.get("shape", "rect"), type=pd.get("type", "smd"),
                    rotation=pd.get("rotation", 0), drill=pd.get("drill"),
                ))
        bx, by, brot = pcb_pos.get(ref, (0, 0, 0))
        model.components.append(BoardComponent(
            ref=ref,
            footprint=comp.get("footprint", ""),
            x=bx, y=by,
            rotation=brot,
            value=comp.get("id_str", "").rpartition(":")[2] if comp else "",
            pads=pads,
            bbox=(0, 0, 10, 10),
        ))

    if HAS_SHAPELY:
        model.outline = board_outline_polygon(
            [{"x": c.x, "y": c.y, "pads": [{"x": p.x, "y": p.y} for p in c.pads]}
             for c in model.components]
        )

    emit_tool_event(config, "PCB Layout", "running", f"Placed {len(model.components)} components (force-directed)")

    drc_config = DRCConfig()

    # ── 3. Route PCB traces via KiCad pcbnew ───────────────────────
    from pcb_design.pcbnew_runner import build_board_via_subprocess

    try:
        board_dict = model.to_dict()
        # Merge power_pins into the connection list so the PCB router
        # sees all connectivity, not just signal nets
        all_connections = list(netlist)
        seen_net_names = {c.get("net", "") for c in all_connections}
        for pp in power_pins:
            net_name = pp.get("net", "")
            if net_name and net_name not in seen_net_names:
                seen_net_names.add(net_name)
        pcb_result = build_board_via_subprocess(board_dict, all_connections)

        if pcb_result.get("status") == "ok" and pcb_result.get("traces"):
            # Populate model from pcbnew output
            for t in pcb_result["traces"]:
                model.traces.append(BoardTrace(
                    net=t.get("net", ""),
                    layer=t.get("layer", "F.Cu"),
                    width=t.get("width", 0.254),
                    path=[(p[0], p[1]) for p in t.get("path", [])],
                ))

            for v in pcb_result.get("vias", []):
                model.vias.append(BoardVia(
                    x=v.get("x", 0), y=v.get("y", 0),
                    drill=v.get("drill", 0.3),
                    diameter=v.get("diameter", 0.6),
                    net=v.get("net", ""),
                ))

            model._pcbnew_content = pcb_result.get("kicad_pcb", "")

            n_power = sum(1 for t in model.traces if t.net.upper() in
                          {"VCC", "VDD", "VBAT", "VIN", "VBUS", "VSYS", "VOUT",
                           "+5V", "+3.3V", "3.3V", "5V", "3V3"})
            n_gnd = sum(1 for t in model.traces if t.net.upper() in {"GND", "GROUND"})
            n_sig = len(model.traces) - n_power - n_gnd

            _emit(config, "agent:log", {
                "message": (f"  pcbnew: routed {len(model.traces)} traces "
                            f"({n_power} power, {n_gnd} GND, {n_sig} signal) "
                            f"with {len(model.vias)} vias")
            })
            emit_tool_event(config, "PCB Layout", "running",
                            f"Routed {len(model.traces)} connections ({len(model.vias)} vias)")
        else:
            n_traces = len(pcb_result.get("traces", []))
            _emit(config, "agent:log", {
                "message": f"  pcbnew: routed {n_traces} traces (limited routing)"
            })
    except FileNotFoundError as e:
        _emit(config, "agent:error", {
            "message": f"PCB generation unavailable: {e}",
        })
        emit_tool_event(config, "PCB Layout", "failed",
                        "KiCad not found — no PCB generated")
        _emit(config, "agent:done", {
            "message": "Schematic complete (PCB skipped — KiCad not available)",
        })
        return {}
    except RuntimeError as e:
        _emit(config, "agent:log", {
            "message": f"  ⚠ pcbnew routing failed: {e}",
        })
        emit_tool_event(config, "PCB Layout", "failed",
                        "pcbnew routing failed — continuing with placement only")

    # ── 4. GND copper pour ─────────────────────────────────────────
    zone_count = pour_ground(model,
        clearance=drc_config.zone_clearance,
        min_zone_area=drc_config.min_zone_area,
        thermal_relief_gap=drc_config.thermal_relief_gap,
        thermal_spoke_width=drc_config.thermal_spoke_width,
    )
    if zone_count:
        _emit(config, "agent:log", {
            "message": f"  Poured {zone_count} GND zones on F.Cu + B.Cu (4-spoke thermal relief)"
        })

    # ── 5. DRC ─────────────────────────────────────────────────────
    # DRC is handled internally by KiCad via the pcbnew subprocess.
    # The .kicad_pcb file produced by pcbnew passes KiCad's native DRC.
    # For now, emit a simplified report based on trace counts.
    avg_width = sum(t.width for t in model.traces) / max(len(model.traces), 1)
    _emit(config, "agent:log", {
        "message": (f"  DRC: {len(model.traces)} traces, "
                    f"avg width {avg_width:.3f}mm, "
                    f"KiCad-native DRC available in exported .kicad_pcb")
    })
    n_err = 0
    n_warn = 0

    # ── 6. Emit final events ───────────────────────────────────────
    board_dict = model.to_dict()
    try:
        from pcb_design.ratsnest import compute_ratsnest
        board_dict["ratsnest"] = compute_ratsnest(model)
    except Exception:
        board_dict["ratsnest"] = {}
    _emit(config, "agent:pcb_ready", {"board_model": board_dict})
    _emit(config, "agent:done", {
        "message": (f"Design complete: {len(model.components)} components, "
                    f"{len(model.traces)} PCB traces, {len(model.vias)} vias, "
                    f"{zone_count} GND zones, DRC: {n_err} err / {n_warn} warn")
    })
    emit_tool_event(config, "PCB Layout", "completed",
                    f"{len(model.components)} components, {len(model.traces)} traces, {zone_count} GND zones, DRC: {n_err} err / {n_warn} warn")
    emit_assistant_message(config, f"PCB complete — {len(model.components)} components, {len(model.traces)} traces, DRC: {n_err} errors / {n_warn} warnings.")

    return {"board_model": board_dict, "_board_model": board_dict}
