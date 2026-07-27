import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.prompt_router import route_prompt



# Test helper: access the default session design
def _td():
    from server import session_manager
    return session_manager.get_or_create("test").last_design

def test_chat_proposal_commit_updates_session_and_board_state():
    from server import CHAT_SESSIONS, session_manager, app, design_lock, socketio

    session_id = "test-chat-session"
    ds = session_manager.get_or_create(session_id)
    with ds.lock:
        ds.set_design({
            "selected_components": [],
            "component_placements": [],
            "wire_paths": [],
            "board_model": {
                "components": [],
                "traces": [],
                "vias": [],
                "nets": [],
                "outline_segments": [],
                "_render_from_model": True,
            }
        })
    CHAT_SESSIONS.pop(session_id, None)

    client = socketio.test_client(app)
    try:
        client.emit("chat:message", {"session_id": session_id, "text": "/add resistor"})
        received = client.get_received()
        proposal_events = [event for event in received if event["name"] == "chat:proposal"]
        assert proposal_events, received
        proposal = proposal_events[0]["args"][0]
        assert proposal["component"]["pins"][0]["targetNet"] == ""
        proposal["component"]["pins"][0]["targetNet"] = "I2C_SDA"
        proposal["component"]["pins"][1]["targetNet"] = "3V3"

        client.emit(
            "chat:commit_proposal",
            {
                "session_id": session_id,
                "id": proposal["id"],
                "component": proposal["component"],
                "x": 12.5,
                "y": 7.25,
            },
        )
        received = client.get_received()
        names = [event["name"] for event in received]
        assert "tscircuit:board-model-updated" in names, received
        assert "chat:reply" in names, received

        session = CHAT_SESSIONS[session_id]
        assert len(session.board_model["components"]) == 1
        component = session.board_model["components"][0]
        assert component["ref"] == "R1"
        assert component["x"] == 12.5
        assert component["y"] == 7.25
        assert {pad["net"] for pad in component["pads"]} == {"I2C_SDA", "3V3"}

        with design_lock:
            persisted = session_manager.get_or_create(session_id).last_design
            assert len(persisted["board_model"]["components"]) == 1
            assert {net["name"] for net in persisted["board_model"]["nets"]} == {"I2C_SDA", "3V3"}
    finally:
        client.disconnect()
        CHAT_SESSIONS.pop(session_id, None)


def test_chat_resume_returns_history_pending_proposals_and_board_state():
    from server import CHAT_SESSIONS, session_manager, app, design_lock, socketio

    session_id = "test-chat-resume"
    with design_lock:
        saved_board = _td().get("board_model")
        _td()["board_model"] = {
            "components": [],
            "traces": [],
            "vias": [],
            "nets": [],
            "outline_segments": [],
            "_render_from_model": True,
        }
    CHAT_SESSIONS.pop(session_id, None)

    client = socketio.test_client(app)
    try:
        client.emit("chat:message", {"session_id": session_id, "text": "/add resistor"})
        received = client.get_received()
        proposal = next(event["args"][0] for event in received if event["name"] == "chat:proposal")

        client.emit("chat:resume", {"session_id": session_id})
        received = client.get_received()
        state = next(event["args"][0] for event in received if event["name"] == "chat:state")

        assert state["history"][0]["role"] == "user"
        assert state["history"][0]["content"] == "/add resistor"
        assert state["proposals"][0]["id"] == proposal["id"]
        assert state["board_model"]["components"] == []
    finally:
        client.disconnect()
        CHAT_SESSIONS.pop(session_id, None)
        with design_lock:
            _td()["board_model"] = saved_board


def test_short_component_prompt_returns_component_proposal_not_full_agent():
    import server
    from server import CHAT_SESSIONS, app, socketio

    session_id = "test-component-query"
    CHAT_SESSIONS.pop(session_id, None)
    saved_search = server.rag.search
    saved_pins = server.rag.pins
    server.rag.search = lambda text, k=1: [SimpleNamespace(id_str="MCU_Module:ESP32_DEVKIT", text="ESP32 DevKit")]
    server.rag.pins = lambda _id: [
        {"number": "1", "name": "3V3"},
        {"number": "2", "name": "GND"},
        {"number": "3", "name": "EN"},
    ]

    client = socketio.test_client(app)
    try:
        client.emit("chat:message", {"session_id": session_id, "text": "/add esp32 devkit"})
        received = client.get_received()
        names = [event["name"] for event in received]
        assert "chat:proposal" in names, received
        assert "agent:log" not in names, received
        proposal = next(event["args"][0] for event in received if event["name"] == "chat:proposal")
        assert proposal["component"]["name"] == "ESP32 DevKit"
        assert proposal["component"]["symbol_id"] == "MCU_Module:ESP32_DEVKIT"
        assert len(proposal["component"]["pins"]) == 3
    finally:
        client.disconnect()
        CHAT_SESSIONS.pop(session_id, None)
        server.rag.search = saved_search
        server.rag.pins = saved_pins


def test_complex_component_phrase_starts_design_pipeline(monkeypatch):
    monkeypatch.setattr(
        "agent.prompt_router._call_llm",
        lambda *args, **kwargs: '{"intent":"add_component","confidence":0.92,"reasoning":"llm guessed add","extracted_components":["ESP32","button","status LED"]}',
    )
    routing = route_prompt("ESP32 with button and status LED")
    assert routing["intent"] == "design_pipeline"


def test_component_proposal_prefers_generic_status_led():
    import server

    saved_search = server.rag.search
    saved_pins = server.rag.pins
    try:
        server.rag.search = lambda text, k=8: [
            SimpleNamespace(id_str="Device:LED_BRAG", text="RGB LED, blue/red/anode/green"),
            SimpleNamespace(id_str="Device:LED", text="Light emitting diode"),
        ]
        server.rag.pins = lambda _id: [
            {"number": "1", "name": "K"},
            {"number": "2", "name": "A"},
        ]
        proposal = server._build_component_proposal_from_query("status LED")
        assert proposal["component"]["symbol_id"] == "Device:LED"
    finally:
        server.rag.search = saved_search
        server.rag.pins = saved_pins


def test_chat_proposal_pads_preserve_physical_coordinates():
    import server
    from server import CHAT_SESSIONS, app, socketio

    session_id = "test-preserve-coordinates"
    CHAT_SESSIONS.pop(session_id, None)
    saved_search = server.rag.search
    saved_pins = server.rag.pins
    saved_load_fp = server.chat._load_real_footprint_geometry

    server.rag.search = lambda text, k=1: [SimpleNamespace(id_str="MCU_Module:ESP32_DEVKIT", text="ESP32 DevKit")]
    server.rag.pins = lambda _id: [
        {"number": "1", "name": "3V3"},
        {"number": "2", "name": "GND"},
    ]
    server.chat._load_real_footprint_geometry = lambda symbol_id, explicit_footprint=None: {
        "footprint": "ESP32_DEVKIT_FP",
        "pads": [
            {
                "num": "1",
                "number": "1",
                "name": "1",
                "x": 10.5,
                "y": 20.5,
                "width": 1.2,
                "height": 2.2,
                "shape": "rect",
                "type": "smd",
                "rotation": 90,
                "drill": None,
                "layers": ["F.Cu"],
                "targetNet": "",
            },
            {
                "num": "2",
                "number": "2",
                "name": "2",
                "x": -10.5,
                "y": -20.5,
                "width": 1.2,
                "height": 2.2,
                "shape": "rect",
                "type": "smd",
                "rotation": 90,
                "drill": None,
                "layers": ["F.Cu"],
                "targetNet": "",
            }
        ],
        "graphics": []
    }

    client = socketio.test_client(app)
    try:
        client.emit("chat:message", {"session_id": session_id, "text": "/add esp32 devkit"})
        received = client.get_received()
        proposal = next(event["args"][0] for event in received if event["name"] == "chat:proposal")
        
        pins = proposal["component"]["pins"]
        assert len(pins) == 2
        
        p1 = next(p for p in pins if p["num"] == "1")
        assert p1["x"] == 10.5
        assert p1["y"] == 20.5
        assert p1["width"] == 1.2
        assert p1["height"] == 2.2
        assert p1["name"] == "3V3"
        
        p2 = next(p for p in pins if p["num"] == "2")
        assert p2["x"] == -10.5
        assert p2["y"] == -20.5
        assert p2["width"] == 1.2
        assert p2["height"] == 2.2
        assert p2["name"] == "GND"
    finally:
        client.disconnect()
        CHAT_SESSIONS.pop(session_id, None)
        server.rag.search = saved_search
        server.rag.pins = saved_pins
        server.chat._load_real_footprint_geometry = saved_load_fp

