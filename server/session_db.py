"""SQLite-backed session persistence.

Provides durable storage for design sessions and chat history,
surviving server restarts. Uses write-through pattern: in-memory
dict stays fast, SQLite is the backup.

Usage::

    from server.session_db import SessionDB
    db = SessionDB()
    db.save_design("session123", {"components": [...]})
    design = db.load_design("session123")
"""

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "sessions.db"
)

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS designs (
    session_id TEXT PRIMARY KEY,
    design_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    stage TEXT DEFAULT '',
    timestamp REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES designs(session_id)
);

CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_messages(session_id, timestamp);

CREATE TABLE IF NOT EXISTS schematic_assets (
    asset_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    image_data TEXT NOT NULL,
    mime_type TEXT DEFAULT 'image/png',
    created_at REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES designs(session_id)
);

CREATE INDEX IF NOT EXISTS idx_assets_session ON schematic_assets(session_id);
"""


class SessionDB:
    """Thread-safe SQLite session persistence."""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or _DB_PATH
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Get a thread-local connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
            self._local.conn = sqlite3.connect(self._db_path, timeout=10)
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_db(self) -> None:
        """Initialize database tables."""
        conn = self._get_conn()
        conn.executescript(_CREATE_TABLES)
        conn.commit()

    # ── Design Persistence ──────────────────────────────────────────────

    def save_design(self, session_id: str, design: dict) -> None:
        """Persist design data to disk."""
        try:
            conn = self._get_conn()
            now = time.time()
            conn.execute(
                """INSERT INTO designs (session_id, design_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET
                   design_json = excluded.design_json,
                   updated_at = excluded.updated_at""",
                (session_id, json.dumps(design, default=str), now, now),
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"Failed to save design for {session_id}: {e}")

    def load_design(self, session_id: str) -> dict | None:
        """Load persisted design data."""
        try:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT design_json FROM designs WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row:
                return json.loads(row["design_json"])
        except Exception as e:
            logger.warning(f"Failed to load design for {session_id}: {e}")
        return None

    def list_sessions(self, max_age_hours: float = 24) -> list[dict]:
        """List recent sessions with metadata."""
        try:
            conn = self._get_conn()
            cutoff = time.time() - (max_age_hours * 3600)
            rows = conn.execute(
                """SELECT session_id, created_at, updated_at,
                          json_extract(design_json, '$.selected_components') as comps
                   FROM designs
                   WHERE updated_at > ?
                   ORDER BY updated_at DESC""",
                (cutoff,),
            ).fetchall()
            result = []
            for row in rows:
                comps = json.loads(row["comps"]) if row["comps"] else []
                result.append({
                    "session_id": row["session_id"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "component_count": len(comps) if isinstance(comps, list) else 0,
                })
            return result
        except Exception as e:
            logger.warning(f"Failed to list sessions: {e}")
            return []

    # ── Chat History Persistence ────────────────────────────────────────

    def save_message(self, session_id: str, role: str, content: str,
                     stage: str = "") -> None:
        """Save a chat message to disk."""
        try:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO chat_messages (session_id, role, content, stage, timestamp)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, role, content, stage, time.time()),
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"Failed to save message for {session_id}: {e}")

    def load_messages(self, session_id: str, limit: int = 100) -> list[dict]:
        """Load chat history from disk."""
        try:
            conn = self._get_conn()
            rows = conn.execute(
                """SELECT role, content, stage, timestamp
                   FROM chat_messages
                   WHERE session_id = ?
                   ORDER BY timestamp DESC
                   LIMIT ?""",
                (session_id, limit),
            ).fetchall()
            # Reverse to chronological order
            return [
                {"role": r["role"], "content": r["content"],
                 "stage": r["stage"], "timestamp": r["timestamp"]}
                for r in reversed(rows)
            ]
        except Exception as e:
            logger.warning(f"Failed to load messages for {session_id}: {e}")
            return []

    def compact_messages(self, session_id: str, keep_recent: int = 20,
                         summary: str = "") -> int:
        """Compact old messages, keeping recent ones. Returns count removed."""
        try:
            conn = self._get_conn()
            # Get message count
            count = conn.execute(
                "SELECT COUNT(*) as cnt FROM chat_messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()["cnt"]

            if count <= keep_recent:
                return 0

            # Find cutoff timestamp for messages to remove
            cutoff_row = conn.execute(
                """SELECT timestamp FROM chat_messages
                   WHERE session_id = ?
                   ORDER BY timestamp DESC
                   LIMIT 1 OFFSET ?""",
                (session_id, keep_recent),
            ).fetchone()

            if not cutoff_row:
                return 0

            cutoff_ts = cutoff_row["timestamp"]

            # Insert summary if provided
            if summary:
                conn.execute(
                    """INSERT INTO chat_messages (session_id, role, content, stage, timestamp)
                       VALUES (?, 'system', ?, 'compaction', ?)""",
                    (session_id, summary, cutoff_ts - 0.001),
                )

            # Remove old messages
            cursor = conn.execute(
                "DELETE FROM chat_messages WHERE session_id = ? AND timestamp < ?",
                (session_id, cutoff_ts),
            )
            conn.commit()
            return cursor.rowcount
        except Exception as e:
            logger.warning(f"Failed to compact messages for {session_id}: {e}")
            return 0

    def get_message_count(self, session_id: str) -> int:
        """Get the number of messages for a session."""
        try:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM chat_messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return row["cnt"] if row else 0
        except Exception:
            return 0

    # ── Asset Persistence ───────────────────────────────────────────

    def save_asset(self, session_id: str, asset_id: str, image_data: str,
                   mime_type: str = "image/png") -> bool:
        """Store a schematic asset (screenshot image)."""
        try:
            conn = self._get_conn()
            conn.execute(
                """INSERT OR REPLACE INTO schematic_assets
                   (asset_id, session_id, image_data, mime_type, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (asset_id, session_id, image_data, mime_type, time.time()),
            )
            conn.commit()
            return True
        except Exception as e:
            logger.warning(f"Failed to save asset {asset_id}: {e}")
            return False

    def load_asset(self, asset_id: str) -> dict | None:
        """Load a schematic asset by ID."""
        try:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT * FROM schematic_assets WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()
            if row:
                return {
                    "asset_id": row["asset_id"],
                    "session_id": row["session_id"],
                    "image_data": row["image_data"],
                    "mime_type": row["mime_type"],
                    "created_at": row["created_at"],
                }
        except Exception as e:
            logger.warning(f"Failed to load asset {asset_id}: {e}")
        return None

    def delete_asset(self, asset_id: str) -> bool:
        """Delete a schematic asset by ID."""
        try:
            conn = self._get_conn()
            conn.execute(
                "DELETE FROM schematic_assets WHERE asset_id = ?",
                (asset_id,),
            )
            conn.commit()
            return True
        except Exception as e:
            logger.warning(f"Failed to delete asset {asset_id}: {e}")
            return False

    def list_assets(self, session_id: str) -> list[dict]:
        """List all assets for a session."""
        try:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT asset_id, session_id, mime_type, created_at FROM schematic_assets WHERE session_id = ?",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"Failed to list assets for {session_id}: {e}")
            return []

    # ── Cleanup ─────────────────────────────────────────────────────────

    def cleanup_old_sessions(self, max_age_hours: float = 168) -> int:
        """Remove sessions older than max_age_hours (default: 7 days)."""
        try:
            conn = self._get_conn()
            cutoff = time.time() - (max_age_hours * 3600)
            conn.execute("DELETE FROM chat_messages WHERE session_id IN "
                         "(SELECT session_id FROM designs WHERE updated_at < ?)",
                         (cutoff,))
            cursor = conn.execute("DELETE FROM designs WHERE updated_at < ?", (cutoff,))
            conn.commit()
            return cursor.rowcount
        except Exception as e:
            logger.warning(f"Failed to cleanup old sessions: {e}")
            return 0


# Global instance
_session_db: SessionDB | None = None
_session_db_lock = threading.Lock()


def get_session_db() -> SessionDB:
    """Get or create the global SessionDB instance."""
    global _session_db
    if _session_db is None:
        with _session_db_lock:
            if _session_db is None:
                _session_db = SessionDB()
    return _session_db
