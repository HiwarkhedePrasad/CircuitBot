"""
End-to-end test: Generate a simple LED circuit using CircuitBot pipeline.
Tests the full flow: user request → agent → component selection → netlist → schematic.
"""

import socketio
import time
import json
import sys

# Create a Socket.IO client
sio = socketio.Client(logger=False, engineio_logger=False)

SESSION_ID = "test_led_circuit_" + str(int(time.time()))
DESIGN_PROMPT = "/design a simple LED circuit with a 330 ohm resistor and a red LED powered by 3.3V"

# Track results
results = {
    "connected": False,
    "messages": [],
    "thoughts": [],
    "tool_calls": [],
    "components": [],
    "netlist": None,
    "board_model": None,
    "done": False,
    "errors": [],
}


@sio.on("connect")
def on_connect():
    results["connected"] = True
    print("[OK] Connected to CircuitBot backend")


@sio.on("disconnect")
def on_disconnect():
    print("[INFO] Disconnected from backend")


@sio.on("chat:reply")
def on_chat_reply(data):
    text = data.get("text", "")
    results["messages"].append(text)
    print(f"[REPLY] {text[:200]}")


@sio.on("agent:log")
def on_agent_log(data):
    msg = data.get("message", "")
    print(f"[LOG] {msg[:150]}")


@sio.on("agent:thought_stream")
def on_thought_stream(data):
    ttype = data.get("type", "")
    content = data.get("content", "")
    status = data.get("status", "")

    if ttype == "thought":
        results["thoughts"].append(content)
        print(f"[THINK] {content[:100]}")
    elif ttype == "tool_call":
        results["tool_calls"].append({"title": content, "status": status})
        print(f"[TOOL] {content} ({status})")
    elif ttype == "step":
        print(f"[STEP] {content} ({status})")


@sio.on("agent:conversation")
def on_conversation(data):
    ctype = data.get("type", "")
    content = data.get("content", "")
    if ctype == "assistant":
        print(f"[ASSISTANT] {content[:200]}")


@sio.on("agent:component")
def on_component(data):
    comp = {
        "id_str": data.get("id_str", ""),
        "ref_des": data.get("ref_des", ""),
        "category": data.get("category", ""),
        "description": data.get("description", ""),
    }
    results["components"].append(comp)
    print(f"[COMPONENT] {comp['ref_des']}={comp['id_str']}")


@sio.on("agent:layout_ready")
def on_layout_ready(data):
    placements = data.get("placements", [])
    traces = data.get("traces", [])
    netlist = data.get("netlist", [])
    results["netlist"] = netlist
    print(f"[LAYOUT] {len(placements)} placements, {len(traces)} traces, {len(netlist)} nets")


@sio.on("agent:done")
def on_done(data):
    results["done"] = True
    count = data.get("component_count", 0)
    print(f"[DONE] Design complete! {count} components")


@sio.on("agent:error")
def on_error(data):
    msg = data.get("message", "")
    results["errors"].append(msg)
    print(f"[ERROR] {msg}")


@sio.on("agent:pcb_ready")
def on_pcb_ready(data):
    bm = data.get("board_model", {})
    results["board_model"] = bm
    comps = bm.get("components", [])
    traces = bm.get("traces", [])
    print(f"[PCB] Board model: {len(comps)} components, {len(traces)} traces")


@sio.on("agent:pcb_approval")
def on_pcb_approval(data):
    print("[APPROVAL] PCB layout ready, approving...")
    sio.emit("agent:pcb_approve", {"approved": True})


@sio.on("agent:board_config")
def on_board_config(data):
    print("[CONFIG] Board config requested, selecting 2-layer...")
    sio.emit("agent:board_config", {"layer_count": 2})


@sio.on("agent:validation_help")
def on_validation_help(data):
    errors = data.get("errors", [])
    print(f"[VALIDATION] {len(errors)} issues, retrying...")
    sio.emit("agent:validation_help_response", {"action": "retry"})


@sio.on("agent:clarify")
def on_clarify(data):
    questions = data.get("questions", [])
    print(f"[CLARIFY] {len(questions)} questions, answering...")
    answers = {}
    for q in questions:
        qid = q.get("id", "")
        options = q.get("options", [])
        if options:
            answers[qid] = options[0]
    sio.emit("agent:clarify_response", {"answers": answers})


def main():
    print("=" * 60)
    print("CircuitBot End-to-End Test: Simple LED Circuit")
    print("=" * 60)
    print(f"Prompt: {DESIGN_PROMPT}")
    print(f"Session: {SESSION_ID}")
    print()

    # Connect to backend
    print("Connecting to backend on port 5000...")
    try:
        sio.connect("http://localhost:5000")
    except Exception as e:
        print(f"[FAIL] Could not connect: {e}")
        print("Make sure the server is running: python server.py")
        sys.exit(1)

    # Resume session to get existing state
    sio.emit("chat:resume", {"session_id": SESSION_ID})
    time.sleep(1)

    # Send design request
    print()
    print("Sending design request...")
    print("-" * 40)
    sio.emit("chat:message", {
        "session_id": SESSION_ID,
        "text": DESIGN_PROMPT,
    })

    # Wait for completion (max 300 seconds)
    print()
    print("Waiting for agent to complete (max 300s)...")
    start = time.time()
    while not results["done"] and (time.time() - start) < 300:
        time.sleep(1)
        elapsed = int(time.time() - start)
        if elapsed % 15 == 0 and elapsed > 0:
            print(f"  ... {elapsed}s elapsed")

    # Print summary
    print()
    print("=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)

    if results["done"]:
        print("Status: SUCCESS")
    elif results["errors"]:
        print("Status: FAILED")
    else:
        print("Status: TIMEOUT (120s)")

    print(f"Components selected: {len(results['components'])}")
    for c in results["components"]:
        print(f"  {c['ref_des']}: {c['id_str']} ({c['description'][:40]})")

    print(f"Thoughts: {len(results['thoughts'])}")
    print(f"Tool calls: {len(results['tool_calls'])}")

    if results["netlist"]:
        print(f"Netlist: {len(results['netlist'])} nets")
        for net in results["netlist"][:5]:
            print(f"  {net.get('source', '?')} -> {net.get('target', '?')}")

    if results["board_model"]:
        bm = results["board_model"]
        print(f"Board model: {len(bm.get('components', []))} components, {len(bm.get('traces', []))} traces")

    if results["errors"]:
        print(f"Errors: {len(results['errors'])}")
        for e in results["errors"][:3]:
            print(f"  {e[:100]}")

    print()
    print("Test complete!")
    sio.disconnect()


if __name__ == "__main__":
    main()
