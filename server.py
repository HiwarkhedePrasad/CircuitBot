import os
from flask import Flask, request, jsonify, send_from_directory
from kicad_rag.client import KicadRAG

app = Flask(__name__, static_folder='static')
rag = KicadRAG()

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

if __name__ == '__main__':
    # Ensure static dir exists
    os.makedirs('static', exist_ok=True)
    app.run(debug=True, port=5000)
