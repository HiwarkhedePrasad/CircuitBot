import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Ensure we can import from the current directory
sys.path.append(str(Path(__file__).parent))

load_dotenv()

from agent.graph import agent_graph

def mock_emit(event, data):
    if event == "agent:log":
        print(f"[LOG] {data['message']}")
    elif event == "agent:thinking":
        print(f"[THINK] {data['message']}")
    elif event == "agent:done":
        print(f"[DONE] {data['message']}")
    elif event == "agent:error":
        print(f"[ERROR] {data['message']}")

prompt = "ESP32 with DS18B20 and USB-C power connector"
print(f"Running agent with prompt: {prompt}")

config = {"configurable": {"emit": mock_emit}}
try:
    result = agent_graph.invoke({"prompt": prompt}, config)
    print("\nFinal Result successfully generated.")
except Exception as e:
    print(f"\nAgent execution failed: {e}")
    import traceback
    traceback.print_exc()