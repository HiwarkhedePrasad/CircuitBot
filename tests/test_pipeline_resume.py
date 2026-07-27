import uuid

from server import app, socketio
from server.state import session_manager


def test_chat_resume_returns_pipeline_snapshot():
    session_id = f"pipeline-{uuid.uuid4().hex}"
    snapshot = {
        "schema_version": 1,
        "run_id": "resume-run",
        "graph_version": "new",
        "status": "running",
        "sequence": 4,
        "started_at_ms": 1000,
        "completed_at_ms": None,
        "duration_ms": None,
        "current_stage": "select",
        "current_attempt": 1,
        "phase_catalog": [],
        "stage_catalog": [],
        "stages": {},
    }
    ds = session_manager.get_or_create(session_id)
    ds.set_pipeline_snapshot(snapshot)
    client = socketio.test_client(app)

    try:
        client.emit("chat:resume", {"session_id": session_id})
        state = next(
            event["args"][0]
            for event in client.get_received()
            if event["name"] == "chat:state"
        )
        assert state["pipeline"]["run_id"] == "resume-run"
        assert state["pipeline"]["current_stage"] == "select"
    finally:
        client.disconnect()
        session_manager.remove(session_id)


def test_design_session_snapshot_is_defensively_copied():
    session_id = f"pipeline-copy-{uuid.uuid4().hex}"
    ds = session_manager.get_or_create(session_id)
    source = {"schema_version": 1, "run_id": "copy-run", "stages": {"select": []}}

    try:
        ds.set_pipeline_snapshot(source)
        source["stages"]["select"].append({"attempt": 99})
        restored = ds.get_pipeline_snapshot()
        restored["stages"]["select"].append({"attempt": 2})

        assert ds.get_pipeline_snapshot()["stages"]["select"] == []
    finally:
        session_manager.remove(session_id)
