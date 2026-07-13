import os
from pathlib import Path
from dotenv import load_dotenv

dotenv_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path, override=True)

from config import ensure_proxy, LLM_BASE_URL, LLM_MODEL
if not ensure_proxy(timeout=15):
    print(
        f"WARNING: LLM proxy not available at {LLM_BASE_URL}.\n"
        f"  LLM features (agent analysis, validation, netlist generation) will fail.\n"
        f"  Run 'opencode serve' in the project directory, or set LLM_BASE_URL in .env\n"
        f"  to point to any OpenAI-compatible endpoint."
    )
else:
    print(f"LLM proxy ready — {LLM_MODEL} @ {LLM_BASE_URL}")

from server.state import app, socketio, rag, design_lock, session_manager
from server.chat import CHAT_SESSIONS, ChatSession, _build_component_proposal_from_query

import server.routes  # noqa: registers HTTP routes
import server.ws_handlers  # noqa: registers WebSocket handlers
