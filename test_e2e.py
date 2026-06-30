"""Quick end-to-end test of the rebuilt graph."""
import os, sys, json, time
from pathlib import Path
from dotenv import load_dotenv
sys.path.insert(0, str(Path(__file__).resolve().parent))
load_dotenv()

from agent.builder import agent_graph

events_log = []

def _mock_safe_print(text):
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", errors="replace").decode("ascii"))


def mock_emit(event, data):
    msg = data.get("message", json.dumps(data)[:200])
    if event == "agent:log":
        _mock_safe_print(f"  {msg}")
    elif event == "agent:thinking":
        _mock_safe_print(f"[THINK] {msg}")
    elif event == "agent:error":
        _mock_safe_print(f"[ERROR] {msg}")
    elif event == "agent:done":
        _mock_safe_print(f"[DONE] {msg}")
    elif event == "agent:layout_ready":
        placements = len(data.get("placements", []))
        traces = len(data.get("traces", []))
        _mock_safe_print(f"[LAYOUT_READY] {placements} placements, {traces} traces")
    elif event == "agent:pcb_ready":
        _mock_safe_print(f"[PCB_READY] {msg}")
    elif event == "agent:activity":
        pass
    elif event == "agent:conversation":
        pass
    else:
        _mock_safe_print(f"[{event}] {msg}")
    events_log.append((event, data))


prompt = "3.3V power supply with LED indicator"
print(f"\n{'='*60}")
print(f"Running agent with prompt: {prompt}")
print(f"{'='*60}\n")

config = {"configurable": {"emit": mock_emit}}
t0 = time.time()
try:
    result = agent_graph.invoke({"prompt": prompt}, config)
    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"Agent completed in {elapsed:.1f}s")
    final_stage = result.get("_stage", "?")
    print(f"Final stage: {final_stage}")
    error = result.get("error")
    if error:
        print(f"ERROR: {error}")
    placements = result.get("component_placements", [])
    traces = result.get("wire_paths", [])
    nets = result.get("netlist", [])
    pcb_approved = result.get("pcb_approved", False)
    erc_retries = result.get("_erc_retries", 0)
    print(f"  Components placed: {len(placements)}")
    print(f"  Wires routed: {len(traces)}")
    print(f"  Netlist connections: {len(nets)}")
    print(f"  ERC retries: {erc_retries}")
    print(f"  PCB approved: {pcb_approved}")
    print(f"{'='*60}")

    with open("test_e2e_result.json", "w") as f:
        json.dump({
            "prompt": prompt,
            "elapsed": elapsed,
            "final_stage": final_stage,
            "error": error,
            "n_placements": len(placements),
            "n_traces": len(traces),
            "n_nets": len(nets),
            "pcb_approved": pcb_approved,
            "erc_retries": erc_retries,
            "events": events_log,
        }, f, indent=2, default=str)
    print("Result saved to test_e2e_result.json")
except Exception as e:
    import traceback
    elapsed = time.time() - t0
    print(f"\nAgent FAILED after {elapsed:.1f}s: {e}")
    traceback.print_exc()
    with open("test_e2e_result.json", "w") as f:
        json.dump({
            "prompt": prompt,
            "elapsed": elapsed,
            "error": str(e),
            "traceback": traceback.format_exc(),
            "events": events_log,
        }, f, indent=2, default=str)
