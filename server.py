import os
import json
import tempfile
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_from_directory, Response
from flask_socketio import SocketIO, emit
from kicad_rag.client import KicadRAG

dotenv_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path, override=True)

app = Flask(__name__, static_folder='static')
app.config['SECRET_KEY'] = os.urandom(16).hex()
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # never cache static files
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading', ping_timeout=120, ping_interval=25)

import threading

rag = KicadRAG()

# Last completed agent design — used by /api/export_sch
design_lock = threading.Lock()
LAST_DESIGN = {}
_WIREBENDER_LAYOUT = {}  # Preserved across agent-result overwrite

# Human-in-the-loop approval events: sid -> {event: threading.Event, result: dict}
_agent_events: dict[str, dict] = {}


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


# ── HTTP Routes ──────────────────────────────────────────────────────────────

@app.after_request
def _no_cache(resp):
    """Disable caching for static assets so JS/CSS edits always take effect."""
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


@app.route('/static/<path:path>')
def serve_static(path):
    return send_from_directory('static', path)


@app.route('/kicanvas/<path:path>')
def serve_kicanvas(path):
    return send_from_directory('kicanvas', path)


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
    """Receive the frontend (ELK) computed geometry for the last design."""
    data = request.get_json(silent=True) or {}
    with design_lock:
        has_design = LAST_DESIGN.get('selected_components') or LAST_DESIGN.get('component_ops') or data.get('wire_paths') or data.get('placements')
        if not has_design:
            return jsonify({'ok': False, 'error': 'No design to update'}), 404
        _WIREBENDER_LAYOUT['component_placements'] = data.get('placements', _WIREBENDER_LAYOUT.get('component_placements', []))
        _WIREBENDER_LAYOUT['wire_paths'] = data.get('wire_paths', _WIREBENDER_LAYOUT.get('wire_paths', []))
        _WIREBENDER_LAYOUT['power_labels'] = data.get('power_labels', _WIREBENDER_LAYOUT.get('power_labels', []))
        if 'board_model' in data and data['board_model'] is not None:
            _WIREBENDER_LAYOUT['board_model'] = data['board_model']
        # Also write through to LAST_DESIGN for immediate use
        if 'placements' in data:
            LAST_DESIGN['component_placements'] = data['placements']
        if 'wire_paths' in data:
            LAST_DESIGN['wire_paths'] = data['wire_paths']
        if 'power_labels' in data:
            LAST_DESIGN['power_labels'] = data['power_labels']
        if 'board_model' in data and data['board_model'] is not None:
            LAST_DESIGN['board_model'] = data['board_model']
    return jsonify({'ok': True})


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


@app.route('/api/export_sch')
def api_export_sch():
    """Export the last agent-generated design as a KiCad schematic file."""
    with design_lock:
        if not LAST_DESIGN.get('selected_components') and not LAST_DESIGN.get('board_model'):
            return "No design generated yet. Run the AI agent first.", 404
        design_copy = LAST_DESIGN.copy()
    _ensure_selected_components_from_board_model(design_copy)
    try:
        from agent.kicad_export import generate_kicad_sch
        text = generate_kicad_sch(design_copy)
        return Response(
            text,
            mimetype='application/octet-stream',
            headers={'Content-Disposition': 'attachment; filename=circuitbot.kicad_sch'},
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Export failed: {e}", 500


@app.route('/api/export_pcb')
def api_export_pcb():
    """Export the last agent-generated design as a KiCad PCB file."""
    with design_lock:
        if not LAST_DESIGN.get('selected_components') and not LAST_DESIGN.get('board_model'):
            return "No design generated yet. Run the AI agent first.", 404
        design_copy = LAST_DESIGN.copy()
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
        return f"PCB export failed: {e}", 500


@app.route('/api/pcb_render_source')
def api_pcb_render_source():
    """Return KiCad PCB text for the current board state.

    Unlike /api/export_pcb, this works for imported boards and manual PCB edits
    that only populate LAST_DESIGN['board_model'].
    """
    with design_lock:
        if not LAST_DESIGN.get('board_model') and not LAST_DESIGN.get('selected_components'):
            return "No PCB state available yet.", 404
        design_copy = LAST_DESIGN.copy()
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
    """Return a BoardModel re-imported from generated KiCad PCB text.

    This preserves the current app editing model while enriching footprint and
    pad geometry from the KiCad PCB representation.
    """
    with design_lock:
        if not LAST_DESIGN.get('board_model') and not LAST_DESIGN.get('selected_components'):
            return jsonify({"error": "No PCB state available yet."}), 404
        design_copy = LAST_DESIGN.copy()
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
    """Compute MST ratsnest edges for the given BoardModel.

    Accepts a BoardModel JSON body, returns a dict mapping net names
    to lists of ``{x1, y1, x2, y2}`` edge objects.
    """
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
    """Return the current BoardModel as a Circuit JSON array for tscircuit/pcb-viewer.

    GET: uses LAST_DESIGN's board_model (if available) or returns an empty board.
    POST: accepts an optional BoardModel JSON body.
    """
    from pcb_design.board_model import BoardModel as BM
    from pcb_design.circuit_json_converter import board_model_to_circuit_json

    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        board_dict = data.get('board_model', data)
    else:
        with design_lock:
            board_dict = LAST_DESIGN.get('board_model')

    if board_dict:
        model = BM.from_dict(board_dict)
    else:
        model = BM()

    circuit_json = board_model_to_circuit_json(model)
    return jsonify(circuit_json)


@app.route('/api/apply_edits', methods=['POST'])
def api_apply_edits():
    """Receive frontend edit events and apply them to the active design.

    Expects JSON::

        {
            "edit_events": [
                {
                    "pcb_edit_event_type": "edit_trace_hint",
                    "pcb_port_id": "...",
                    "route": [{"x": ..., "y": ..., "via": false, "trace_width": ...}],
                    "edit_event_id": "...",
                    "in_progress": false
                },
                {
                    "pcb_edit_event_type": "edit_component_location",
                    "pcb_component_id": "...",
                    "original_center": {"x": ..., "y": ...},
                    "new_center": {"x": ..., "y": ...},
                    "in_progress": false
                }
            ]
        }

    Only events with *in_progress: false* are committed.
    Returns the updated *board_model* with recalculated ratsnest.
    """
    from pcb_design.board_model import BoardModel, BoardTrace
    from agent.routing.api import apply_schematic_edit

    data = request.get_json(silent=True) or {}
    events = data.get("edit_events", [])
    if not events:
        return jsonify({"ok": False, "error": "No edit_events provided"}), 400
    if not isinstance(events, list):
        return jsonify({"ok": False, "error": "edit_events must be an array"}), 400

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
        board_dict = LAST_DESIGN.get("board_model")
        has_schematic_state = bool(LAST_DESIGN.get("selected_components"))
        if not board_dict and not has_schematic_state:
            return jsonify({"ok": False, "error": "No board model or schematic design loaded"}), 400
        model = BoardModel.from_dict(board_dict) if board_dict else None

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
                pin_matrix = LAST_DESIGN.get("pin_matrix", {})
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
                wire_id = event.get("wire_id") or event.get("edit_event_id") or f"wire_{len(LAST_DESIGN.get('wire_paths', [])) + 1}"
                net = event.get("net") or _schematic_net_for_pair(LAST_DESIGN.get("netlist", []), source, target)
                edit_event = {
                    "edit_event_type": etype, "wire_id": wire_id,
                    "source": source, "target": target, "path": clean_path, "net": net,
                }
                LAST_DESIGN["wire_paths"] = apply_schematic_edit(
                    LAST_DESIGN.get("wire_paths", []),
                    edit_event,
                    LAST_DESIGN.get("netlist", []),
                    LAST_DESIGN.get("pin_matrix", {}),
                    LAST_DESIGN.get("component_placements", []),
                )
                _WIREBENDER_LAYOUT["wire_paths"] = LAST_DESIGN["wire_paths"]
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
                before = list(LAST_DESIGN.get("wire_paths", []))
                LAST_DESIGN["wire_paths"] = apply_schematic_edit(
                    before,
                    edit_event,
                    LAST_DESIGN.get("netlist", []),
                    LAST_DESIGN.get("pin_matrix", {}),
                    LAST_DESIGN.get("component_placements", []),
                )
                _WIREBENDER_LAYOUT["wire_paths"] = LAST_DESIGN["wire_paths"]
                applied += 1
                ignored += 0
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
                placements = list(LAST_DESIGN.get("component_placements", []))
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
                LAST_DESIGN["component_placements"] = placements
                _WIREBENDER_LAYOUT["component_placements"] = placements
                LAST_DESIGN["wire_paths"] = apply_schematic_edit(
                    LAST_DESIGN.get("wire_paths", []),
                    {"edit_event_type": etype, "ref_des": ref_des},
                    LAST_DESIGN.get("netlist", []),
                    LAST_DESIGN.get("pin_matrix", {}),
                    placements,
                )
                _WIREBENDER_LAYOUT["wire_paths"] = LAST_DESIGN["wire_paths"]
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
            LAST_DESIGN["board_model"] = new_board
            _WIREBENDER_LAYOUT["board_model"] = new_board

    resp: dict = {
        "ok": True, "applied": applied, "ignored": ignored,
    }
    if had_wire_events:
        resp["wire_paths"] = LAST_DESIGN.get("wire_paths", [])
    if had_move_events:
        resp["component_placements"] = LAST_DESIGN.get("component_placements", [])
    if new_board is not None:
        resp["board_model"] = new_board
    if errors:
        resp["errors"] = errors
    return jsonify(resp)


@app.route('/api/save_board_model', methods=['POST'])
def api_save_board_model():
    """Persist a full BoardModel dict as the new source of truth.

    This is the fallback endpoint for when fine-grained event-level apply
    fails.  The caller sends the entire ``board_model`` dict.
    """
    data = request.get_json(silent=True) or {}
    board_model = data.get("board_model")
    if not board_model:
        return jsonify({"ok": False, "error": "No board_model provided"}), 400

    if isinstance(board_model, dict):
        board_model.pop("_pcbnew_content", None)
        board_model["_render_from_model"] = True

    with design_lock:
        LAST_DESIGN["board_model"] = board_model
        _WIREBENDER_LAYOUT["board_model"] = board_model

    return jsonify({"ok": True})


@app.route('/api/import_pcb', methods=['POST'])
def api_import_pcb():
    """Import a .kicad_pcb file and return BoardModel JSON."""
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
            LAST_DESIGN["board_model"] = payload
            _WIREBENDER_LAYOUT["board_model"] = payload

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


import uuid
import time

class ChatSession:
    def __init__(self):
        self.chat_history = []
        self.board_model = None
        self.proposals = {}
        self.last_active = time.time()

CHAT_SESSIONS: dict[str, ChatSession] = {}


def _create_empty_board_model():
    return {
        "components": [],
        "traces": [],
        "vias": [],
        "nets": [],
        "outline_segments": [],
        "_render_from_model": True,
    }


def _get_or_create_chat_session(session_id: str) -> ChatSession:
    session = CHAT_SESSIONS.get(session_id)
    if session is None:
        session = ChatSession()
        with design_lock:
            existing_board = LAST_DESIGN.get("board_model")
        session.board_model = json.loads(json.dumps(existing_board)) if existing_board else _create_empty_board_model()
        CHAT_SESSIONS[session_id] = session
    return session


def _prune_legacy_mock_history(session: ChatSession) -> None:
    session.chat_history = [
        msg for msg in session.chat_history
        if not (isinstance(msg, dict) and msg.get("role") == "assistant" and "Mock response" in str(msg.get("content")))
    ]


def _load_real_footprint_geometry(symbol_id: str | None, explicit_footprint: str | None = None) -> dict | None:
    try:
        footprint = explicit_footprint or ""
        if symbol_id:
            from agent.tools import fetch_footprint

            info = fetch_footprint(symbol_id) or {}
            footprint = footprint or info.get("footprint", "")
        if not footprint:
            return None

        from kicad_rag.store import footprint_path_for
        from pcb_design.pcb_import import _parse_footprint, parse_sexp

        fp_path = footprint_path_for(footprint)
        if not fp_path.is_file():
            return None
        ast = parse_sexp(fp_path.read_text(encoding="utf-8"))
        if isinstance(ast, list) and ast and ast[0] not in ("footprint", "module"):
            ast[0] = "footprint"
        parsed = _parse_footprint(ast)
        if not parsed:
            return None
        return {
            "footprint": footprint,
            "pads": [
                {
                    "num": str(p.number),
                    "number": str(p.number),
                    "name": str(p.number),
                    "x": p.x,
                    "y": p.y,
                    "width": p.width,
                    "height": p.height,
                    "shape": p.shape,
                    "type": p.type,
                    "rotation": p.rotation,
                    "drill": p.drill,
                    "drill_width": p.drill_width,
                    "drill_offset_x": p.drill_offset_x,
                    "drill_offset_y": p.drill_offset_y,
                    "roundrect_rratio": p.roundrect_rratio,
                    "rect_delta_x": p.rect_delta_x,
                    "rect_delta_y": p.rect_delta_y,
                    "layers": p.layers,
                    "targetNet": "",
                }
                for p in parsed.pads
            ],
            "graphics": parsed.graphics,
        }
    except Exception as exc:
        print(f"Footprint geometry load failed for {symbol_id or explicit_footprint}: {exc}")
        return None


def _looks_like_component_request(text: str) -> bool:
    normalized = " ".join((text or "").strip().lower().split())
    if not normalized:
        return False
    design_markers = [
        " with ",
        " using ",
        " connect ",
        " power supply",
        "charger",
        "circuit",
        "board",
        "design",
        "schematic",
        "system",
        "usb",
        "sensor with",
    ]
    padded = f" {normalized} "
    if any(marker in padded for marker in design_markers):
        return False
    return len(normalized.split()) <= 4 and len(normalized) <= 48


def _build_component_proposal_from_query(text: str):
    results = rag.search(text, k=1)
    if not results:
        return None
    best = results[0]
    pin_defs = rag.pins(best.id_str) or []
    if not pin_defs:
        pin_defs = [{"num": "1", "name": "P1"}, {"num": "2", "name": "P2"}]

    pin_count = len(pin_defs)
    half_span = max((pin_count - 1) * 0.6 / 2, 0.0)
    pins = []
    for idx, pin in enumerate(pin_defs):
        offset_y = half_span - idx * 0.6
        side = -1 if idx % 2 == 0 else 1
        pins.append({
            "num": str(pin.get("number") or pin.get("num") or idx + 1),
            "name": str(pin.get("name") or pin.get("label") or f"P{idx + 1}"),
            "x": side * 1.4,
            "y": offset_y,
            "width": 0.6,
            "height": 0.7,
            "targetNet": "",
        })

    proposal = {
        "type": "add_component",
        "component": {
            "name": getattr(best, "text", "") or best.id_str.split(":")[-1],
            "symbol_id": best.id_str,
            "footprint": getattr(best, "footprint", "") or "",
            "body": {
                "width": 3.2 if pin_count > 4 else 2.4,
                "height": max(1.6, pin_count * 0.5),
            },
            "pins": pins,
        },
        "id": str(uuid.uuid4()),
    }
    geometry = _load_real_footprint_geometry(best.id_str, proposal["component"]["footprint"])
    if geometry:
        proposal["component"]["footprint"] = geometry.get("footprint", proposal["component"]["footprint"])
        logical_pins = proposal["component"]["pins"]
        logical_by_num = {
            str(pin.get("num", pin.get("number", ""))): pin
            for pin in logical_pins
        }
        merged_pins = []
        for pad in geometry.get("pads", []):
            pad_num = str(pad.get("num", pad.get("number", "")))
            logical_pin = logical_by_num.get(pad_num)
            if logical_pin:
                merged_pad = pad.copy()
                if "name" in logical_pin:
                    merged_pad["name"] = logical_pin["name"]
                if "targetNet" in logical_pin:
                    merged_pad["targetNet"] = logical_pin["targetNet"]
                merged_pins.append(merged_pad)
            else:
                merged_pins.append(pad)
        proposal["component"]["pins"] = merged_pins or logical_pins
        proposal["component"]["graphics"] = geometry.get("graphics", [])
        pad_xs = [float(p.get("x", 0) or 0) for p in proposal["component"]["pins"]]
        pad_ys = [float(p.get("y", 0) or 0) for p in proposal["component"]["pins"]]
        if pad_xs and pad_ys:
            proposal["component"]["body"] = {
                "width": max(max(pad_xs) - min(pad_xs) + 1.8, 2.4),
                "height": max(max(pad_ys) - min(pad_ys) + 1.8, 1.8),
            }
    return proposal

@socketio.on('chat:message')
def handle_chat_message(data):
    session_id = data.get("session_id")
    text = data.get("text", "").strip()
    
    if not session_id:
        emit('agent:error', {'message': 'No session ID provided'})
        return

    if not text:
        emit('agent:error', {'message': 'No message provided'})
        return

    session = _get_or_create_chat_session(session_id)
    _prune_legacy_mock_history(session)
    session.chat_history.append({"role": "user", "content": text})

    proposal = None
    if "resistor" in text.lower():
        proposal = {
            "type": "add_component",
            "component": {
                "name": "10k Resistor 0402",
                "symbol_id": "Device:R",
                "footprint": "Resistor_SMD:R_0402_1005Metric",
                "body": {"width": 1.0, "height": 0.5},
                "pins": [
                    {"num": "1", "x": -0.5, "y": 0.0, "targetNet": "I2C_SDA"},
                    {"num": "2", "x": 0.5, "y": 0.0, "targetNet": "3V3"},
                ],
            },
            "id": str(uuid.uuid4())
        }
        geometry = _load_real_footprint_geometry(
            proposal["component"].get("symbol_id"),
            proposal["component"].get("footprint"),
        )
        if geometry:
            proposal["component"]["footprint"] = geometry.get("footprint", proposal["component"]["footprint"])
            logical_pins = proposal["component"]["pins"]
            logical_by_num = {
                str(pin.get("num", pin.get("number", ""))): pin
                for pin in logical_pins
            }
            merged_pins = []
            for pad in geometry.get("pads", []):
                pad_num = str(pad.get("num", pad.get("number", "")))
                logical_pin = logical_by_num.get(pad_num)
                if logical_pin:
                    merged_pad = pad.copy()
                    if "name" in logical_pin:
                        merged_pad["name"] = logical_pin["name"]
                    if "targetNet" in logical_pin:
                        merged_pad["targetNet"] = logical_pin["targetNet"]
                    merged_pins.append(merged_pad)
                else:
                    merged_pins.append(pad)
            proposal["component"]["pins"] = merged_pins or logical_pins
            proposal["component"]["graphics"] = geometry.get("graphics", [])
    elif _looks_like_component_request(text):
        try:
            proposal = _build_component_proposal_from_query(text)
        except Exception as exc:
            print(f"Component proposal lookup failed: {exc}")

    if proposal:
        session.proposals[proposal["id"]] = proposal
        emit('chat:proposal', proposal)
    else:
        socketio.emit('agent:log', {'message': 'Agent starting...'}, room=request.sid)
        socketio.start_background_task(_run_agent, text, request.sid)

@socketio.on('chat:reject_proposal')
def handle_reject_proposal(data):
    session_id = data.get("session_id")
    proposal_id = data.get("proposal_id")
    if session_id in CHAT_SESSIONS:
        session = CHAT_SESSIONS[session_id]
        session.proposals.pop(proposal_id, None)
        session.chat_history.append({
            "role": "system", 
            "content": f"The user REJECTED the previous proposal {proposal_id}. The board state has NOT changed."
        })


@socketio.on('chat:resume')
def handle_chat_resume(data):
    session_id = (data or {}).get("session_id")
    if not session_id:
        emit('agent:error', {'message': 'No session ID provided'})
        return

    session = _get_or_create_chat_session(session_id)
    _prune_legacy_mock_history(session)
    emit('chat:state', {
        'history': list(session.chat_history),
        'proposals': list(session.proposals.values()),
        'board_model': session.board_model,
    })

@socketio.on('chat:commit_proposal')
def handle_commit_proposal(data):
    session_id = data.get("session_id")
    proposal_id = data.get("id")
    x = float(data.get("x", 0) or 0)
    y = float(data.get("y", 0) or 0)

    if not session_id:
        emit('agent:error', {'message': 'No session ID provided'})
        return

    session = _get_or_create_chat_session(session_id)
    proposal = session.proposals.pop(proposal_id, None)
    if proposal is None:
        emit('agent:error', {'message': 'Proposal not found or already handled'})
        return

    board_model = session.board_model or _create_empty_board_model()
    board_model.setdefault("components", [])
    board_model.setdefault("traces", [])
    board_model.setdefault("vias", [])
    board_model.setdefault("nets", [])
    board_model.setdefault("outline_segments", [])
    board_model["_render_from_model"] = True

    comp_count = len([c for c in board_model["components"] if c.get("ref", "").startswith("R")])
    ref = f"R{comp_count + 1}"

    component = proposal.get("component", {})
    pads = []
    for pin in component.get("pins", []):
        pads.append({
            "num": pin.get("num", pin.get("number", "")),
            "number": pin.get("number", pin.get("num", "")),
            "net": pin.get("targetNet", ""),
            "x": pin.get("x", 0),
            "y": pin.get("y", 0),
            "width": pin.get("width", 0.6),
            "height": pin.get("height", 0.7),
            "shape": pin.get("shape", "rect"),
            "type": pin.get("type", "smd"),
            "rotation": pin.get("rotation", 0),
            "drill": pin.get("drill"),
            "drill_width": pin.get("drill_width"),
            "drill_offset_x": pin.get("drill_offset_x", 0),
            "drill_offset_y": pin.get("drill_offset_y", 0),
            "roundrect_rratio": pin.get("roundrect_rratio"),
            "rect_delta_x": pin.get("rect_delta_x", 0),
            "rect_delta_y": pin.get("rect_delta_y", 0),
            "layers": pin.get("layers", ["F.Cu"]),
        })

    new_comp = {
        "ref": ref,
        "name": component.get("name", "AI Component"),
        "footprint": component.get("footprint", ""),
        "x": x,
        "y": y,
        "rotation": 0,
        "layer": "F.Cu",
        "pads": pads,
        "graphics": component.get("graphics", []),
    }

    board_model["components"].append(new_comp)
    existing_nets = {net.get("name") for net in board_model["nets"] if isinstance(net, dict)}
    for pad in pads:
        net_name = pad.get("net")
        if net_name and net_name not in existing_nets:
            board_model["nets"].append({"name": net_name})
            existing_nets.add(net_name)

    session.board_model = board_model
    session.chat_history.append({
        "role": "assistant",
        "content": f"Placed {ref} at ({x:.2f}, {y:.2f}).",
    })

    with design_lock:
        LAST_DESIGN["board_model"] = json.loads(json.dumps(board_model))
        _WIREBENDER_LAYOUT["board_model"] = json.loads(json.dumps(board_model))

    emit('tscircuit:board-model-updated', {'board_model': board_model})
    emit('chat:reply', {'text': f"Successfully placed {ref} at ({x:.2f}, {y:.2f})."})


# ── WebSocket Events ─────────────────────────────────────────────────────────

@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")


@socketio.on('disconnect')
def handle_disconnect():
    print(f"Client disconnected: {request.sid}")
    entry = _agent_events.pop(request.sid, None)
    if entry:
        entry["result"]["approved"] = False
        entry["event"].set()


@socketio.on('agent:pcb_approve')
def handle_pcb_approve(data):
    """Receive user's PCB approval decision from frontend."""
    entry = _agent_events.pop(request.sid, None)
    if entry:
        entry["result"]["approved"] = data.get("approved", False)
        entry["event"].set()


@socketio.on('agent:generate')
def handle_agent_generate(data):
    """Launch agent in a background task. Frontend passes {prompt: '...'}."""
    prompt = (data or {}).get('prompt', '')
    if not prompt:
        emit('agent:error', {'message': 'No prompt provided.'})
        return

    emit('agent:log', {'message': 'Agent starting...'})
    socketio.start_background_task(_run_agent, prompt, request.sid)


def _run_agent(prompt: str, sid: str):
    """Background task that runs the LangGraph agent and pushes WS events."""
    approval_event = threading.Event()
    approval_result = {"approved": False}
    _agent_events[sid] = {"event": approval_event, "result": approval_result}

    try:
        from agent.graph import agent_graph

        def ws_emit(event, data):
            socketio.emit(event, data, room=sid)

        run_id = os.urandom(3).hex()
        config = {
            "configurable": {
                "emit": ws_emit,
                "run_id": run_id,
                "approval_event": approval_event,
                "approval_result": approval_result,
            }
        }
        socketio.emit("agent:log", {"message": f"Run {run_id} started"}, room=sid)
        result = agent_graph.invoke({"prompt": prompt}, config)

        # Persist the final design state for .kicad_sch / .kicad_pcb export
        # WireBender's frontend-computed layout takes priority over the
        # backend router's layout (saved via /api/save_layout → _WIREBENDER_LAYOUT).
        board_model = result.get('board_model', None) or result.get('_board_model', None)
        with design_lock:
            wb = _WIREBENDER_LAYOUT
            LAST_DESIGN.clear()
            LAST_DESIGN.update({
                'selected_components': result.get('selected_components', []),
                'component_ops': result.get('component_ops', {}),
                'component_placements': wb.get('component_placements') or result.get('component_placements', []),
                'wire_paths': wb.get('wire_paths') or result.get('wire_paths', []),
                'power_labels': wb.get('power_labels') or result.get('power_labels', []),
                'pin_matrix': result.get('pin_matrix', {}),
                'netlist': result.get('netlist', []),
                'nets': result.get('nets', []),
                'power_pins': result.get('power_pins', []),
                'board_model': board_model,
            })

        if board_model:
            socketio.emit('agent:pcb_ready', {'board_model': board_model}, room=sid)
    except Exception as e:
        socketio.emit('agent:error', {'message': str(e)}, room=sid)
        print(f"Agent error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        _agent_events.pop(sid, None)


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    os.makedirs('static', exist_ok=True)
    socketio.run(app, debug=True, port=5000, allow_unsafe_werkzeug=True)
