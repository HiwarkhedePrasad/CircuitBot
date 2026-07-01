# ⚡ CircuitBot

<div align="center">

### AI-Powered KiCad Schematic & PCB Generation Agent

Describe a circuit in natural language — CircuitBot autonomously produces a complete schematic (`.kicad_sch`) and PCB layout (`.kicad_pcb`) using a **LangGraph agent** with **Hybrid RAG**, **ERC auto-fix**, and a **human approval gate**.

![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-000000?style=for-the-badge&logo=flask&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-Agentic_AI-blue?style=for-the-badge)
![Socket.IO](https://img.shields.io/badge/Socket.IO-010101?style=for-the-badge&logo=socket.io&logoColor=white)
![KiCad](https://img.shields.io/badge/KiCad-314CB2?style=for-the-badge&logo=kicad&logoColor=white)

</div>

---

## Overview

CircuitBot is an AI-powered Electronic Design Automation (EDA) assistant. A user describes a circuit in natural language:

> "Design a 3.3V power supply with USB-C input, AMS1117 regulator, and LED indicator."

CircuitBot automatically:

1. **Analyzes** the prompt into functional subsystems
2. **Searches** a 22,000+ KiCad symbol database via Hybrid RAG (BM25 + dense embeddings)
3. **Selects** optimal components with an LLM (DeepSeek V4 Flash)
4. **Validates** selections, retrying if bare ICs or library mismatches are found
5. **Generates** netlists from circuit intent
6. **Places** components on the schematic sheet with collision avoidance
7. **Routes** orthogonal A* wires between pins
8. **Audits** the schematic with KiCad ERC (kicad-cli), auto-repairing missing wires (up to 3 retries)
9. **Asks** for human approval before proceeding to PCB
10. **Generates** a complete PCB layout with GND copper pour, thermal reliefs, and DRC

Everything streams live to the browser via Socket.IO.

---

## Pipeline

```
User Prompt
    ↓
analyze          → LLM decomposes prompt into subsystems
    ↓
research         → Hybrid RAG search over 22K+ KiCad symbols
    ↓
select           → LLM picks best components, assigns ref designators
    ↓
validate         → Checks for bare-IC issues, module preference, ref-des collisions
    ↓              ↺ retries back to select if issues found
dispatch         → Fetches raw KiCad S-expression symbols
    ↓
netlist          → LLM + rule-based netlist generation
    ↓
placement        → Grid-snapped, collision-avoiding schematic placement
    ↓
routing          → Orthogonal A* wire routing
    ↓
schematic_audit  → Generates .kicad_sch, runs KiCad ERC
    ↓              ↺ if fixable errors → repair → re-route (up to 3x)
ask_pcb_approval → Human-in-the-loop gate via WebSocket
    ↓              (if approved ↓, if rejected → END)
pcb_layout       → Force-directed PCB placement, pcbnew subprocess routing,
    ↓              GND copper pour (4-spoke thermal relief), DRC
DONE             → agent:pcb_ready with board model
```

---

## Features

### Agentic Design Workflow (LangGraph)
13-node state machine with conditional retry loops, typed state, and automatic rollback on failure.

### Hybrid RAG Component Retrieval
- Dense vector search (BAAI/bge-small-en-v1.5 via TurboVec)
- Lexical search (SQLite FTS5 BM25)
- Weighted Reciprocal Rank Fusion (RRF)

### LLM-Guided Selection
Uses DeepSeek V4 Flash (via local proxy at `:4010`) — evaluates candidates, scores, justifies, and selects components. Support rules auto-inject decoupling caps, pull-ups, PWR_FLAG symbols.

### Schematic Export
Custom KiCad S-expression generator producing valid `.kicad_sch` files with:
- Pin-accurate symbol instances
- Orthogonal wire segments snapped to 1.27 mm grid
- Global power labels with centroid-hub fan-out
- Junction dots at T-junctions
- Short-circuit detection

### Automatic ERC Auto-Fix
- Runs `kicad-cli sch erc --format json`
- Filters `pin_not_connected` for intentionally unassigned signal pins
- Detects missing physical wires via `wire_paths` (not abstract netlist)
- Removes stale netlist connections before re-wiring
- Relaxes MAX_WIRE_LEN to 300mm on ERC passes
- Stops after 3 retries or zero fixable errors

### Human Approval Gate
Blocks PCB generation until the user clicks Approve/Reject in the browser. Implemented via `threading.Event` + Socket.IO.

### PCB Generation
- Force-directed component placement with decay cooling
- Trade-off curves for collision vs. wirelength optimization
- KiCad `pcbnew` as subprocess (not sys.path injection)
- GND copper pour with 4-spoke thermal relief
- Design Rule Check (DRC)
- SHIELD pins auto-assigned to GND net

### Browser-Based Viewer
HTML5 Canvas rendering of schematics and PCBs with zoom, pan, and component inspection. Chat-style UI with live streaming of agent thoughts and tool events.

---

## Project Structure

```
CircuitBot/
├── server.py                 — Flask + Socket.IO web server
├── test_e2e.py               — Headless end-to-end test
│
├── agent/                    — Core agent logic
│   ├── builder.py            — LangGraph graph construction
│   ├── state.py              — AgentState TypedDict
│   ├── utils.py              — Shared helpers, conditional routers
│   ├── prompts.py            — LLM system/user prompts
│   ├── erc_runner.py         — KiCad ERC subprocess wrapper
│   ├── kicad_export.py       — .kicad_sch S-expression generator
│   ├── layout_engine.py      — Placement + A* routing backend
│   ├── reranker.py           — Candidate re-ranking with domain rules
│   ├── support_rules.py      — Auto-injection (caps, pull-ups, PWR_FLAG)
│   ├── datasheet.py          — PDF datasheet extraction
│   ├── bus_checker.py        — Bus interface validation
│   └── nodes/                — 13 pipeline stage functions
│       ├── analyze.py
│       ├── research.py
│       ├── select.py
│       ├── validate.py
│       ├── dispatch.py
│       ├── netlist.py
│       ├── placement.py
│       ├── routing.py
│       ├── schematic_audit.py
│       ├── schematic_repair.py
│       ├── ask_pcb_approval.py
│       ├── pcb_layout.py
│       └── layout_route.py (legacy)
│
├── pcb_design/               — PCB generation layer
│   ├── board_model.py        — PCB data model
│   ├── placement.py          — Force-directed placement
│   ├── router.py / router2.py — Trace routing engines
│   ├── pour.py               — GND copper pour with thermal relief
│   ├── coord_validator.py    — Coordinate validation
│   ├── pcbnew_runner.py      — Calls KiCad pcbnew as subprocess
│   ├── pcbnew_worker.py      — pcbnew Python scripting
│   ├── pcb_export.py         — .kicad_pcb file generator
│   └── pcb_import.py         — .kicad_pcb file parser
│
├── kicad_rag/                — Component retrieval system
│   ├── builder.py            — Builds SQLite + TurboVec index
│   ├── client.py             — Unified search client
│   ├── retrieval.py          — Ranking + fusion logic
│   ├── store.py              — Vector store management
│   ├── constants.py          — Library paths & categories
│   └── cli.py                — CLI entry point
│
└── static/                   — Frontend
    ├── index.html
    ├── app.js
    ├── schematic.js
    ├── schematic_renderer.js
    ├── style.css
    └── renderer_legacy.js
```

---

## Installation

### Prerequisites
- Python 3.10+
- KiCad 10.0 (for ERC, pcbnew)
- OpenAI-compatible LLM endpoint (default: `http://localhost:4010/v1`)

### 1. Clone

```bash
git clone https://github.com/HiwarkhedePrasad/CircuitBot.git
cd CircuitBot
```

### 2. Virtual Environment

```bash
python -m venv venv
source venv/bin/activate     # Linux/macOS
venv\Scripts\activate        # Windows
```

### 3. Dependencies

```bash
pip install flask flask-socketio langgraph python-dotenv pathfinding \
            requests fastembed turbovec
```

### 4. Environment

Create `.env`:

```env
OPENAI_API_KEY=your_api_key_here
OPENAI_BASE_URL=http://localhost:4010/v1
```

### 5. Prepare KiCad Symbols

Clone the [kicad-symbols](https://github.com/KiCad/kicad-symbols) repo into `kicad_rag/`:

```bash
cd kicad_rag
git clone https://github.com/KiCad/kicad-symbols.git
cd ..
```

### 6. Build Index

```bash
python -m kicad_rag build
```

Generates: `circuitbot.sqlite`, `circuitbot.tvim`, `turbovec_dataset.json`.

### 7. Run

```bash
python server.py
```

Open `http://localhost:5000`.

---

## Example Prompts

```
ESP32 with battery charger and status LED
Temperature monitoring circuit with ADC and display
3.3V regulated power supply using AMS1117
USB-C powered environmental monitoring station
ESP32 with DS18B20 temperature sensor and OLED display
```

---

## Tech Stack

| Layer                  | Technology                        |
|------------------------|-----------------------------------|
| Backend                | Flask + Flask-SocketIO            |
| Agent Framework        | LangGraph (StateGraph)            |
| LLM                    | DeepSeek V4 Flash (local proxy)   |
| Retrieval              | TurboVec (dense) + BM25 (lexical) |
| Database               | SQLite FTS5 + TurboVec index      |
| Embeddings             | BGE Small EN v1.5                 |
| Schematic Routing      | A* Pathfinding (orthogonal)       |
| PCB Routing            | KiCad pcbnew (subprocess)         |
| ERC                    | kicad-cli sch erc (JSON output)   |
| Frontend               | HTML5 Canvas + Vanilla JS         |
| EDA Format             | KiCad .kicad_sch / .kicad_pcb     |

---

## Known Issues

- **DS18B20 ignored** — reranker prefers I2C parts (TMP117) over 1-Wire; subsystem type string biases retrieval
- **USB-UART dedup** — CP2102N vs CP2102C treated as distinct by `_is_duplicate_of` (same family, different base name)
- **Module preference loop** — bare ICs can be re-selected on retry despite `rejected_ids` tracking
- **Ref-des collisions** — support rules may assign duplicate R/C designators

---

## License

MIT
