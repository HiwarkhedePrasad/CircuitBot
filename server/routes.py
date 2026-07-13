import copy
import json
import tempfile
import os
from pathlib import Path

from flask import request, jsonify, send_from_directory, Response

from server.state import app, socketio, rag, design_lock, session_manager
from server.chat import (
    CHAT_SESSIONS, ChatSession, _get_or_create_chat_session, _create_empty_board_model,
    _build_component_proposal_from_query, _prune_legacy_mock_history, _load_real_footprint_geometry,
)


def _deep_copy_design(design: dict) -> dict:
    """Create a deep copy of the design dict to prevent shared-state mutation."""
    return copy.deepcopy(design)


def _get_session_from_request():
    """Get or create a DesignSession from the current HTTP request.

    Tries session_id from: query param, header, or falls back to default.
    """
    session_id = (
        request.args.get("session_id")
        or request.headers.get("X-Session-ID")
        or "default"
    )
    return session_manager.get_or_create(session_id)

# ── Helper: netlist generation ──────────────────────────────────────────


def _generate_netlist_llm(pin_matrix, prompt):
    try:
        from config import get_llm_client
        client = get_llm_client(temperature=0.0, max_tokens=4096)
        pins_desc = "\n".join(
            f'  {key}: pin_name="{p["name"]}"'
            for key, p in sorted(pin_matrix.items())
        )
        system_prompt = (
            "You generate JSON netlists for EDA schematic routing. "
            "Return ONLY a JSON array of objects with 'source' and 'target' keys. "
            "Use the exact pin keys provided. Connect pins that should be wired together "
            "based on standard electronic design practice (e.g., same net names like GND/VCC, "
            "or as described by the user prompt). "
            "Temperature 0.0. No explanation, no markdown, just JSON."
        )
        user_prompt = f"Available pins:\n{pins_desc}\n\nUser intent: {prompt}" if prompt else f"Available pins:\n{pins_desc}\n\nConnect pins that share the same net name."
        full_response = ""
        for chunk in client.stream([{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]):
            full_response += chunk.content
        text = full_response.strip()
        text = text.removeprefix("```json").removesuffix("```").strip()
        import re
        text = re.sub(r'^[^{[]*|[^}\]]*$', '', text)
        return json.loads(text)
    except Exception as e:
        print(f"LLM netlist fallback: {e}")
        return None


def _generate_netlist_rules(pin_matrix):
    by_name = {}
    for key, pin in pin_matrix.items():
        name = pin.get('name', '')
        if not name:
            continue
        by_name.setdefault(name, []).append(key)

    netlist = []
    used = set()
    for name, keys in by_name.items():
        if len(keys) < 2:
            continue
        for i in range(1, len(keys)):
            pair = (keys[0], keys[i])
            if pair not in used:
                netlist.append({"source": keys[0], "target": keys[i]})
                used.add(pair)
    return netlist


def _ensure_selected_components_from_board_model(design: dict):
    if not design.get('selected_components') and design.get('board_model'):
        bm = design['board_model']
        comps = []
        placements = []
        for comp in bm.get('components', []):
            ref = comp.get('ref', '')
            name = comp.get('name', 'AI Component')
            fp = comp.get('footprint', '')
            comps.append({
                'ref_des': ref,
                'id_str': name if ':' in name else f"Device:{ref}",
                'category': 'Component',
                'description': name,
                'footprint': fp,
            })
            placements.append({
                'ref_des': ref,
                'x': comp.get('x', 0),
                'y': comp.get('y', 0),
                'rotation': comp.get('rotation', 0),
            })
        design['selected_components'] = comps
        design['component_placements'] = placements


# ── HTTP Middleware ──────────────────────────────────────────────────────


@app.after_request
def _no_cache(resp):
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


# ── Static Routes ───────────────────────────────────────────────────────


@app.route('/')
def index():
    from server.state import _PROJECT_ROOT
    return send_from_directory(os.path.join(_PROJECT_ROOT, 'static'), 'index.html')


@app.route('/static/<path:path>')
def serve_static(path):
    return send_from_directory('static', path)


@app.route('/kicanvas/<path:path>')
def serve_kicanvas(path):
    return send_from_directory('kicanvas', path)


# ── API Routes ──────────────────────────────────────────────────────────


@app.route('/api/search')
def api_search():
    query = request.args.get('q', '')
    if not query:
        return jsonify([])
    results = rag.search(query, k=5)
    return jsonify([{'id_str': r.id_str, 'text': r.text} for r in results])


@app.route('/api/pins')
def api_pins():
    id_str = request.args.get('id_str', '')
    if not id_str:
        return jsonify([])
    return jsonify(rag.pins(id_str))


@app.route('/api/sexpr')
def api_sexpr():
    id_str = request.args.get('id_str', '')
    if not id_str:
        return "Missing id_str", 400
    try:
        sexpr = rag.sexpr(id_str)
        return sexpr, 200, {'Content-Type': 'text/plain'}
    except Exception as e:
        return str(e), 404


@app.route('/api/generate_netlist', methods=['POST'])
def api_generate_netlist():
    data = request.get_json(silent=True) or {}
    pin_matrix = data.get('pinMatrix', {})
    prompt = data.get('prompt', '')

    netlist = _generate_netlist_llm(pin_matrix, prompt)
    if netlist is None:
        netlist = _generate_netlist_rules(pin_matrix)
    return jsonify(netlist)


@app.route('/api/save_layout', methods=['POST'])
def api_save_layout():
    data = request.get_json(silent=True) or {}
    with design_lock:
        ds = _get_session_from_request()
        has_design = ds.last_design.get('selected_components') or ds.last_design.get('component_ops') or data.get('wire_paths') or data.get('placements')
        if not has_design:
            return jsonify({'ok': False, 'error': 'No design to update'}), 404
        ds.wire_bender_layout['component_placements'] = data.get('placements', ds.wire_bender_layout.get('component_placements', []))
        ds.wire_bender_layout['wire_paths'] = data.get('wire_paths', ds.wire_bender_layout.get('wire_paths', []))
        ds.wire_bender_layout['power_labels'] = data.get('power_labels', ds.wire_bender_layout.get('power_labels', []))
        if 'board_model' in data and data['board_model'] is not None:
            ds.wire_bender_layout['board_model'] = data['board_model']
        if 'placements' in data:
            ds.last_design['component_placements'] = data['placements']
        if 'wire_paths' in data:
            ds.last_design['wire_paths'] = data['wire_paths']
        if 'power_labels' in data:
            ds.last_design['power_labels'] = data['power_labels']
        if 'board_model' in data and data['board_model'] is not None:
            ds.last_design['board_model'] = data['board_model']
    return jsonify({'ok': True})


@app.route('/api/export_sch')
def api_export_sch():
    with design_lock:
        from agent.exceptions import ExportValidationError
        ds = _get_session_from_request()
        if not ds.last_design.get('selected_components') and not ds.last_design.get('board_model'):
            return jsonify({"error": "No design generated yet. Run the AI agent first."}), 404
        design_copy = _deep_copy_design(ds.last_design)
    _ensure_selected_components_from_board_model(design_copy)
    try:
        from agent.kicad_export import generate_kicad_sch
        text = generate_kicad_sch(design_copy)
        return Response(
            text,
            mimetype='application/octet-stream',
            headers={'Content-Disposition': 'attachment; filename=circuitbot.kicad_sch'},
        )
    except ExportValidationError as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Export validation failed: {e}. Some wires could not be exported — try re-running the agent."}), 500
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Export failed: {e}"}), 500


@app.route('/api/export_pcb')
def api_export_pcb():
    with design_lock:
        ds = _get_session_from_request()
        if not ds.last_design.get('selected_components') and not ds.last_design.get('board_model'):
            return jsonify({"error": "No design generated yet. Run the AI agent first."}), 404
        design_copy = _deep_copy_design(ds.last_design)
    _ensure_selected_components_from_board_model(design_copy)
    try:
        from pcb_design.pcb_export import generate_kicad_pcb
        text = generate_kicad_pcb(design_copy)
        return Response(
            text,
            mimetype='application/octet-stream',
            headers={'Content-Disposition': 'attachment; filename=circuitbot.kicad_pcb'},
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"PCB export failed: {e}"}), 500


@app.route('/api/pcb_render_source')
def api_pcb_render_source():
    with design_lock:
        ds = _get_session_from_request()
        if not ds.last_design.get('board_model') and not ds.last_design.get('selected_components'):
            return "No PCB state available yet.", 404
        design_copy = _deep_copy_design(ds.last_design)
    try:
        from pcb_design.pcb_export import generate_kicad_pcb
        board_model = design_copy.get("board_model") or {}
        if board_model.get("_pcbnew_content"):
            text = generate_kicad_pcb(design_copy)
        elif board_model.get("_render_from_model"):
            text = generate_kicad_pcb(design_copy)
        elif design_copy.get("selected_components"):
            rich_design = dict(design_copy)
            rich_design.pop("board_model", None)
            text = generate_kicad_pcb(rich_design)
        else:
            text = generate_kicad_pcb(design_copy)
        return Response(text, mimetype='text/plain; charset=utf-8')
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"PCB render source failed: {e}", 500


@app.route('/api/pcb_enriched_board_model')
def api_pcb_enriched_board_model():
    with design_lock:
        ds = _get_session_from_request()
        if not ds.last_design.get('board_model') and not ds.last_design.get('selected_components'):
            # Return empty board model instead of 404 so PCB view can load
            empty_model = {
                "components": [], "traces": [], "vias": [], "nets": [],
                "outline_segments": [], "layer_count": 2,
            }
            return jsonify({"board_model": empty_model})
        design_copy = _deep_copy_design(ds.last_design)
    try:
        from pcb_design.pcb_export import generate_kicad_pcb
        from pcb_design.pcb_import import import_board
        from pcb_design.ratsnest import compute_ratsnest

        board_model = design_copy.get("board_model") or {}
        if board_model.get("_render_from_model"):
            from pcb_design.board_model import BoardModel as BM
            board_model["ratsnest"] = compute_ratsnest(BM.from_dict(board_model))
            return jsonify({"board_model": board_model})

        if board_model.get("_pcbnew_content"):
            pcb_text = board_model["_pcbnew_content"]
        elif design_copy.get("selected_components"):
            rich_design = dict(design_copy)
            rich_design.pop("board_model", None)
            pcb_text = generate_kicad_pcb(rich_design)
        else:
            pcb_text = generate_kicad_pcb(design_copy)

        with tempfile.NamedTemporaryFile("w", suffix=".kicad_pcb", delete=False, encoding="utf-8") as handle:
            handle.write(pcb_text)
            temp_path = handle.name
        try:
            model = import_board(temp_path)
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

        payload = model.to_dict()
        payload["ratsnest"] = compute_ratsnest(model)
        return jsonify({"board_model": payload})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/ratsnest', methods=['POST'])
def api_ratsnest():
    data = request.get_json(silent=True) or {}
    try:
        from pcb_design.board_model import BoardModel
        from pcb_design.ratsnest import compute_ratsnest
        model = BoardModel.from_dict(data)
        ratsnest = compute_ratsnest(model)
        return jsonify(ratsnest)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/circuit_json', methods=['GET', 'POST'])
def api_circuit_json():
    from pcb_design.board_model import BoardModel as BM
    from pcb_design.circuit_json_converter import board_model_to_circuit_json

    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        board_dict = data.get('board_model', data)
    else:
        with design_lock:
            ds = _get_session_from_request()
            board_dict = ds.last_design.get('board_model')

    if board_dict:
        model = BM.from_dict(board_dict)
    else:
        model = BM()

    circuit_json = board_model_to_circuit_json(model)
    return jsonify(circuit_json)


# ── Edit Events ─────────────────────────────────────────────────────────


@app.route('/api/apply_edits', methods=['POST'])
def api_apply_edits():
    from pcb_design.board_model import BoardModel, BoardTrace
    from agent.routing.api import apply_schematic_edit

    ds = _get_session_from_request()
    data = request.get_json(silent=True) or {}
    events = data.get("edit_events", [])
    if not events:
        return jsonify({"ok": False, "error": "No edit_events provided"}), 400
    if not isinstance(events, list):
        return jsonify({"ok": False, "error": "edit_events must be an array"}), 400

    BOARD_EVENT_TYPES = {"edit_trace_hint", "edit_pcb_trace_hint", "edit_component_location", "edit_pcb_component_location"}
    has_board_events = any(
        (event.get("edit_event_type") or event.get("pcb_edit_event_type") or "") in BOARD_EVENT_TYPES
        for event in events if isinstance(event, dict)
    )

    def _sanitize_id(s: str) -> str:
        return str(s).replace("/", "_").replace(".", "_").replace(" ", "_").replace("-", "_")

    def _resolve_component(model: BoardModel, pcb_component_id: str):
        raw = str(pcb_component_id or "").replace("pcb_component_", "", 1)
        if not raw:
            return None
        comp = model.component_at(raw)
        if comp is not None:
            return comp
        for c in model.components:
            if _sanitize_id(c.ref) == raw:
                return c
        return None

    def _resolve_port_pin(model: BoardModel, pcb_port_id: str) -> tuple[str, str] | tuple[None, None]:
        raw = str(pcb_port_id or "")
        if not raw.startswith("pcb_port_"):
            return (None, None)
        tail = raw[len("pcb_port_"):]
        for c in model.components:
            sref = _sanitize_id(c.ref)
            for p in c.pads:
                cand = f"{sref}_{p.number}"
                if cand == tail:
                    return (c.ref, str(p.number))
        return (None, None)

    def _net_for_pin(model: BoardModel, ref: str, pin_num: str) -> str:
        pin_key = f"{ref}:{pin_num}"
        for net in model.nets:
            if pin_key in net.get("pins", []):
                return net.get("name") or net.get("net", "") or "_manual"
        return "_manual"

    def _to_layer(route_point: dict) -> str:
        layer = route_point.get("layer", "")
        if layer == "bottom":
            return "B.Cu"
        if layer == "top":
            return "F.Cu"
        return "F.Cu"

    def _pin_exists(pin_matrix: dict, pin_key: str) -> bool:
        return bool(pin_key) and pin_key in pin_matrix

    def _normalize_schematic_path(path: list) -> list[dict]:
        normalized = []
        for point in path:
            if not isinstance(point, dict) or "x" not in point or "y" not in point:
                continue
            normalized.append({"x": point["x"], "y": point["y"]})
        return normalized

    def _schematic_net_for_pair(netlist: list, source: str, target: str) -> str:
        pair = {source, target}
        for conn in netlist:
            if {conn.get("source"), conn.get("target")} == pair:
                return conn.get("net") or ""
        return ""

    with design_lock:
        ds = _get_session_from_request()
        board_dict = ds.last_design.get("board_model")
        has_schematic_state = bool(ds.last_design.get("selected_components"))
        if board_dict is None and not has_schematic_state:
            return jsonify({"ok": False, "error": "No board model or schematic design loaded"}), 400
        if board_dict is not None and has_board_events:
            model = BoardModel.from_dict(board_dict)
        else:
            model = None

        applied = 0
        ignored = 0
        errors = []
        had_wire_events = False
        had_move_events = False
        for i, event in enumerate(events):
            if not isinstance(event, dict):
                errors.append({"index": i, "error": "Event must be a JSON object"})
                ignored += 1
                continue

            etype = event.get("edit_event_type") or event.get("pcb_edit_event_type")
            if not etype:
                errors.append({"index": i, "error": "Missing edit_event_type or pcb_edit_event_type"})
                ignored += 1
                continue

            in_progress = event.get("in_progress", True)
            if in_progress:
                ignored += 1
                continue

            if etype in ("edit_trace_hint", "edit_pcb_trace_hint"):
                if model is None:
                    errors.append({"index": i, "error": "No board model loaded"})
                    ignored += 1
                    continue
                route = event.get("route", [])
                if not isinstance(route, list) or len(route) < 2:
                    errors.append({"index": i, "error": "edit_trace_hint.route must be an array with >=2 points"})
                    ignored += 1
                    continue

                path = []
                for p in route:
                    if not isinstance(p, dict):
                        continue
                    if p.get("via"):
                        continue
                    if "x" not in p or "y" not in p:
                        continue
                    path.append((p["x"], p["y"]))

                if len(path) < 2:
                    errors.append({"index": i, "error": "edit_trace_hint route needs >=2 non-via points with x/y"})
                    ignored += 1
                    continue

                trace_width = route[0].get("trace_width", route[0].get("width", 0.254))
                layer = _to_layer(route[0])
                ref, pnum = _resolve_port_pin(model, event.get("pcb_port_id", ""))
                net_name = _net_for_pin(model, ref, pnum) if ref and pnum else "_manual"
                model.traces.append(BoardTrace(
                    net=net_name,
                    layer=layer,
                    width=trace_width,
                    path=path,
                ))
                applied += 1

            elif etype in ("edit_component_location", "edit_pcb_component_location"):
                if model is None:
                    errors.append({"index": i, "error": "No board model loaded"})
                    ignored += 1
                    continue
                comp_id = event.get("pcb_component_id", "")
                new_center = event.get("new_center", {})
                if not comp_id:
                    errors.append({"index": i, "error": "edit_component_location missing pcb_component_id"})
                    ignored += 1
                    continue
                if not isinstance(new_center, dict) or "x" not in new_center or "y" not in new_center:
                    errors.append({"index": i, "error": "edit_component_location.new_center must have x and y"})
                    ignored += 1
                    continue

                comp = _resolve_component(model, comp_id)
                if comp is not None:
                    comp.x = new_center["x"]
                    comp.y = new_center["y"]
                    applied += 1
                else:
                    errors.append({"index": i, "error": f"Component not found: {comp_id}"})
                    ignored += 1
            elif etype in ("schematic_add_wire", "add_wire"):
                source = event.get("source") or event.get("source_pin")
                target = event.get("target") or event.get("target_pin")
                path = event.get("path", [])
                pin_matrix = ds.last_design.get("pin_matrix", {})
                if not _pin_exists(pin_matrix, source) or not _pin_exists(pin_matrix, target):
                    errors.append({"index": i, "error": "schematic_add_wire source/target must be valid pin keys"})
                    ignored += 1
                    continue
                if source == target:
                    errors.append({"index": i, "error": "schematic_add_wire cannot connect a pin to itself"})
                    ignored += 1
                    continue
                if not isinstance(path, list) or len(path) < 2:
                    errors.append({"index": i, "error": "schematic_add_wire.path must be an array with >=2 points"})
                    ignored += 1
                    continue
                clean_path = _normalize_schematic_path(path)
                if len(clean_path) < 2:
                    errors.append({"index": i, "error": "schematic_add_wire path needs >=2 points with x/y"})
                    ignored += 1
                    continue
                wire_id = event.get("wire_id") or event.get("edit_event_id") or f"wire_{len(ds.last_design.get('wire_paths', [])) + 1}"
                net = event.get("net") or _schematic_net_for_pair(ds.last_design.get("netlist", []), source, target)
                edit_event = {
                    "edit_event_type": etype, "wire_id": wire_id,
                    "source": source, "target": target, "path": clean_path, "net": net,
                }
                ds.last_design["wire_paths"] = apply_schematic_edit(
                    ds.last_design.get("wire_paths", []),
                    edit_event,
                    ds.last_design.get("netlist", []),
                    ds.last_design.get("pin_matrix", {}),
                    ds.last_design.get("component_placements", []),
                )
                ds.wire_bender_layout["wire_paths"] = ds.last_design["wire_paths"]
                applied += 1
                had_wire_events = True
            elif etype in ("schematic_delete_wire", "delete_wire"):
                wire_id = event.get("wire_id")
                source = event.get("source") or event.get("source_pin")
                target = event.get("target") or event.get("target_pin")
                if not wire_id and not (source and target):
                    errors.append({"index": i, "error": "schematic_delete_wire needs wire_id or source/target"})
                    ignored += 1
                    continue
                edit_event = {
                    "edit_event_type": etype, "wire_id": wire_id,
                    "source": source, "target": target,
                }
                before = list(ds.last_design.get("wire_paths", []))
                ds.last_design["wire_paths"] = apply_schematic_edit(
                    before,
                    edit_event,
                    ds.last_design.get("netlist", []),
                    ds.last_design.get("pin_matrix", {}),
                    ds.last_design.get("component_placements", []),
                )
                ds.wire_bender_layout["wire_paths"] = ds.last_design["wire_paths"]
                applied += 1
                had_wire_events = True
            elif etype in ("schematic_move_component", "edit_schematic_component_location"):
                ref_des = event.get("ref_des") or event.get("component_ref")
                new_center = event.get("new_center", {})
                if not ref_des:
                    errors.append({"index": i, "error": "schematic_move_component missing ref_des"})
                    ignored += 1
                    continue
                if not isinstance(new_center, dict) or "x" not in new_center or "y" not in new_center:
                    errors.append({"index": i, "error": "schematic_move_component.new_center must have x and y"})
                    ignored += 1
                    continue
                placements = list(ds.last_design.get("component_placements", []))
                found = False
                for placement in placements:
                    if placement.get("ref_des") == ref_des:
                        placement["x"] = new_center["x"]
                        placement["y"] = new_center["y"]
                        if "rotation" in new_center:
                            placement["rotation"] = new_center["rotation"]
                        found = True
                        break
                if not found:
                    placements.append({"ref_des": ref_des, "x": new_center["x"], "y": new_center["y"]})
                ds.last_design["component_placements"] = placements
                ds.wire_bender_layout["component_placements"] = placements
                ds.last_design["wire_paths"] = apply_schematic_edit(
                    ds.last_design.get("wire_paths", []),
                    {"edit_event_type": etype, "ref_des": ref_des},
                    ds.last_design.get("netlist", []),
                    ds.last_design.get("pin_matrix", {}),
                    placements,
                )
                ds.wire_bender_layout["wire_paths"] = ds.last_design["wire_paths"]
                applied += 1
                had_move_events = True
                had_wire_events = True
            else:
                errors.append({"index": i, "error": f"Unknown event type: {etype}"})
                ignored += 1

        new_board = None
        if model is not None:
            from pcb_design.ratsnest import compute_ratsnest
            if applied > 0:
                model._pcbnew_content = None
            new_board = model.to_dict()
            new_board["_render_from_model"] = True
            new_board["ratsnest"] = compute_ratsnest(model)
            ds.last_design["board_model"] = new_board
            ds.wire_bender_layout["board_model"] = new_board

    resp: dict = {
        "ok": True, "applied": applied, "ignored": ignored,
    }
    if had_wire_events:
        resp["wire_paths"] = ds.last_design.get("wire_paths", [])
    if had_move_events:
        resp["component_placements"] = ds.last_design.get("component_placements", [])
    if new_board is not None:
        resp["board_model"] = new_board
    if errors:
        resp["errors"] = errors
    return jsonify(resp)


@app.route('/api/save_board_model', methods=['POST'])
def api_save_board_model():
    data = request.get_json(silent=True) or {}
    board_model = data.get("board_model")
    if not board_model:
        return jsonify({"ok": False, "error": "No board_model provided"}), 400

    if isinstance(board_model, dict):
        board_model.pop("_pcbnew_content", None)
        board_model["_render_from_model"] = True

    with design_lock:
        ds = _get_session_from_request()
        ds.set_design({"board_model": board_model})
        ds.set_layout({"board_model": board_model})

    return jsonify({"ok": True})


@app.route('/api/import_pcb', methods=['POST'])
def api_import_pcb():
    if 'pcb_file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['pcb_file']
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".kicad_pcb", delete=False) as handle:
            tmp_path = handle.name
        file.save(tmp_path)
        from pcb_design.pcb_import import import_board
        model = import_board(tmp_path)
        payload = model.to_dict()
        payload["_render_from_model"] = True

        with design_lock:
            ds = _get_session_from_request()
            ds.last_design["board_model"] = payload
            ds.wire_bender_layout["board_model"] = payload

        return jsonify({'board_model': payload})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass
