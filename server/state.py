import os
import threading

from flask import Flask
from flask_socketio import SocketIO
from kicad_rag.client import KicadRAG

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = Flask(
    __name__,
    static_folder=os.path.join(_PROJECT_ROOT, 'static'),
)
app.config['SECRET_KEY'] = os.urandom(16).hex()
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading', ping_timeout=120, ping_interval=25)

rag = KicadRAG()

design_lock = threading.Lock()
LAST_DESIGN = {}
_WIREBENDER_LAYOUT = {}

_agent_events: dict[str, dict] = {}
