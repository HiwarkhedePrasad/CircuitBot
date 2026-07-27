import os
import threading
import time

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

# Maps Socket.IO request.sid → chat session_id (localStorage key).
# Used by disconnect handler to find the correct ChatSession for cleanup.
_sid_to_chat: dict[str, str] = {}
_sid_to_chat_lock = threading.Lock()


class DesignSession:
    """Session-scoped design state. Replaces global LAST_DESIGN and _WIREBENDER_LAYOUT."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.last_design: dict = {}
        self.wire_bender_layout: dict = {}
        self.agent_events: dict = {}
        self.created_at: float = time.time()
        self.last_active: float = time.time()
        self.lock = threading.Lock()

    def touch(self):
        self.last_active = time.time()

    def get_design(self) -> dict:
        with self.lock:
            return self.last_design

    def set_design(self, data: dict):
        with self.lock:
            self.last_design.update(data)
            self.touch()

    def clear_design(self):
        with self.lock:
            self.last_design.clear()
            self.touch()

    def get_layout(self) -> dict:
        with self.lock:
            return self.wire_bender_layout

    def set_layout(self, data: dict):
        with self.lock:
            self.wire_bender_layout.update(data)
            self.touch()


class DesignSessionManager:
    """Manages design sessions by ID. Thread-safe."""

    def __init__(self):
        self._sessions: dict[str, DesignSession] = {}
        self._lock = threading.Lock()

    def get_or_create(self, session_id: str) -> DesignSession:
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = DesignSession(session_id)
            session = self._sessions[session_id]
            session.touch()
            return session

    def get(self, session_id: str) -> DesignSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def remove(self, session_id: str):
        with self._lock:
            self._sessions.pop(session_id, None)

    def list_sessions(self) -> list[str]:
        with self._lock:
            return list(self._sessions.keys())


# Global session manager
session_manager = DesignSessionManager()
