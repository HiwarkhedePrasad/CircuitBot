import os
import json
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit
from kicad_rag.client import KicadRAG

dotenv_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path, override=True)

app = Flask(__name__, static_folder='static')
app.config['SECRET_KEY'] = os.urandom(16).hex()
socketio = SocketIO(app, cors_allowed_origins="*")

rag = KicadRAG()


def _generate_netlist_llm(pin_matrix, prompt):
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
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
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
        )
        text = resp.choices[0].message.content.strip()
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

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


@app.route('/static/<path:path>')
def serve_static(path):
    return send_from_directory('static', path)


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


# ── WebSocket Events ─────────────────────────────────────────────────────────

@socketio.on('connect')
def handle_connect():
    print(f"Client connected: {request.sid}")


@socketio.on('disconnect')
def handle_disconnect():
    print(f"Client disconnected: {request.sid}")


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
    try:
        from agent.graph import agent_graph

        def ws_emit(event, data):
            socketio.emit(event, data, room=sid)

        config = {"configurable": {"emit": ws_emit}}
        agent_graph.invoke({"prompt": prompt}, config)
    except Exception as e:
        socketio.emit('agent:error', {'message': str(e)}, room=sid)
        print(f"Agent error: {e}")
        import traceback
        traceback.print_exc()


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    os.makedirs('static', exist_ok=True)
    socketio.run(app, debug=True, port=5000, allow_unsafe_werkzeug=True)
