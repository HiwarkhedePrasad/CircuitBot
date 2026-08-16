<br>

<div align="center">

# ⚡ CircuitBot

**AI-assisted PCB design: from natural-language requirements to KiCad-ready boards**

[![CI](https://github.com/HiwarkhedePrasad/CircuitBot/actions/workflows/test.yml/badge.svg)](https://github.com/HiwarkhedePrasad/CircuitBot/actions/workflows/test.yml)
[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![LangGraph](https://img.shields.io/badge/LangGraph-multi--agent-orange)](https://github.com/langchain-ai/langgraph)
[![KiCad](https://img.shields.io/badge/KiCad-compatible-314CB0?logo=kicad&logoColor=white)](https://www.kicad.org/)
[![License](https://img.shields.io/github/license/HiwarkhedePrasad/CircuitBot)](LICENSE)

</div>

---

CircuitBot is a **multi-agent system that converts natural-language hardware requirements into KiCad-compatible schematics and PCB layouts**. A LangGraph-orchestrated pipeline of specialized agents handles requirement clarification, architecture planning, component selection, schematic generation, part placement, and routing — grounded by a retrieval-augmented component knowledge base so the LLM reasons about *real, verifiable parts* instead of hallucinated ones.

```
"design a USB-powered temperature sensor with ESP32-C3 and an OLED display"
        │
        ▼
┌──────────────────────────────────────────────────────────────────┐
│  CLARIFY → ANALYZE → ARCHITECT → SELECT → NETLIST → VALIDATE     │
│     │                                                  │         │
│     ▼                                                  ▼         │
│  RESEARCH/DATASHEET                            REPAIR LOOPS      │
│                                                         │        │
│        SCHEMATIC LAYOUT → PLACEMENT → ROUTING → EXPORT ▼        │
└──────────────────────────────────────────────────────────────────┘
        │
        ▼
   KiCad schematic + PCB  ·  live WebGL/3D preview  ·  design reports
```

## ✨ Features

- **Natural language to hardware** — describe a circuit in plain English; get a structured design spec, schematic, and routed PCB
- **40+ specialized agent nodes** — clarify, analyze, architecture planning, component selection, datasheet/deep research, netlist synthesis, schematic layout, placement, routing, validation, and repair loops
- **Grounded component selection** — RAG over a KiCad + JLC parts knowledge base (FAISS vector store + SQLite) with query expansion, fuzzy matching, and part locking
- **Deterministic validation** — ERC checks, structural net validation, connectivity repair, and an LLM judge on top of hard rule checks
- **Quality-scoring engine** — 10 schematic metrics (wire crossings, alignment, overlap, wire length/bends, pin direction, signal flow) with configurable weights
- **PCB pipeline** — placement, ratsnest analysis, dual autorouter implementations, copper pours, and coordinate validation
- **Interactive web UI** — real-time chat-driven design with a WebGL schematic/board viewer and a 3D board preview
- **Multiple export targets** — KiCad (`.kicad_sch`, `.kicad_pcb`), tscircuit, and circuit JSON
- **Benchmark suite** — reproducible evals: ESP32 sensor, op-amp amplifier, power regulator, RC filter, USB-UART, and PCB-GPT-style benchmarks
- **Session memory** — per-session design state, preferences, and resume-able pipelines
- **MCP server** — expose CircuitBot capabilities to MCP-compatible agents/clients

## 🤖 Agent Architecture

| Stage | Nodes | Purpose |
|---|---|---|
| Understanding | `clarify`, `analyze`, `ask_board_config` | Turn vague requirements into a precise design contract |
| Architecture | `architecture_planner`, `capability_resolver` | Block-level decomposition and feasibility |
| Research | `research`, `deepresearch`, `datasheet_search`, `connection_search` | Ground decisions in datasheets and reference designs |
| Selection | `select`, `freeze_components`, `symbol_compatibility` | Pick real parts; lock them against drift |
| Synthesis | `netlist`, `connection_emitter`, `dependency_expander`, `deduplicator` | Build validated connection graphs |
| Schematic | `schematic_layout`, `schematic_audit`, `schematic_repair` | Human-readable layout, not just connectivity |
| Validation | `validate`, `erc_runner`, `connectivity_validate`, `llm_judge`, `design_review` | Multi-layer correctness checks |
| Repair | `connectivity_repair`, `power_net_repair`, `structural_net_repair`, `validate_repair` | Self-healing pipeline stages |
| Physical | `placement`, `routing`, `layout_route` | Autorouted board geometry |
| Runtime | critic agent, constraint solver, design twin, design evolution, memory service | Long-horizon design state and refinement |

## 🧠 Knowledge & Retrieval Layer

- **`kicad_rag/`** — custom RAG stack: taxonomy-based indexing, vector store (FAISS), SQLite metadata, JLC parts database ingestion, query expansion
- **`agent/knowledge/`** — component catalog, board-type knowledge, dependency graphs, fuzzy matching, part locking
- **`agent/component_insight/`** — datasheet summarization injected into agent context
- **`agent/component_substitution.py`** — equivalent-part fallbacks when a selected component is unavailable

Every LLM decision about *what part to use* is anchored in retrieved, structured component data — this is the core anti-hallucination strategy.

## 🖥️ Web UI & Viewers

The Flask + Socket.IO server drives an interactive design workspace:

- **Pipeline panel** — watch each agent node execute with live state
- **WebGL schematic/board renderer** — KiCanvas-style rendering adapted for live agent output
- **3D board viewer** — component models, placement preview, camera controls
- **Canvas-aware chat** — the agent sees what you see (selection context feeds prompts)

## 🧪 Benchmarks & Evaluation

`agent/benchmarks/` contains reproducible design challenges with a runner (`benchmark_runner.py`) and tracked results — used to measure end-to-end success rate and iterate on agent reliability:

| Benchmark | Domain |
|---|---|
| `rc_filter` | Passive analog |
| `usb_uart` | Interface bridging |
| `esp32_sensor` | Microcontroller + peripherals |
| `opamp_amplifier` | Analog signal chain |
| `power_regulator` | Power delivery |
| `pcbgpt_bench` | Open benchmark-style tasks |

## 🛠️ Tech Stack

| Layer | Technologies |
|---|---|
| Agent orchestration | Python · LangGraph-style state machine · 40+ nodes · repair loops |
| LLM integration | OpenAI-compatible API · local LLMs · model-agnostic config |
| Retrieval | FAISS · SQLite · RAG · query expansion · fuzzy matching |
| EDA formats | KiCad S-expression codec (parse + serialize) · tscircuit · circuit JSON |
| PCB engine | Placement · ratsnest · autorouting ×2 · copper pours · geometry |
| Server | Flask · Socket.IO · MCP server · session persistence |
| Frontend | Vanilla JS · WebGL · 3D rendering · real-time pipeline UI |
| Quality | pytest · node test suites · ruff (lint + format) · GitHub Actions CI |

## 🚀 Getting Started

### Requirements

- Python 3.10+
- Node.js 20+
- An OpenAI-compatible LLM endpoint (local models work too — see `.env.example`)

### Setup

```bash
git clone https://github.com/HiwarkhedePrasad/CircuitBot.git
cd CircuitBot

python -m venv .venv && .venv\Scripts\activate   # or: source .venv/bin/activate
pip install -r requirements.txt
npm install

cp .env.example .env    # configure your LLM endpoint / API keys
```

### Run

```bash
npm start        # builds frontend assets, then starts the server on port 5000
```

Or manually: `node scripts/build.js && python server.py`

### Run tests

```bash
pytest tests/ -q                    # Python test suite (40+ files)
ruff check agent/ server/ tests/    # lint
node tests/test_pcb_viewer.js       # JS viewer regression tests
```

## 📁 Project Structure

```
agent/           # Multi-agent pipeline: graph, nodes, runtime, scoring, synthesis
agent/nodes/     # 40+ pipeline nodes (clarify → route → validate → repair)
agent/runtime/   # Critic, constraint solver, design twin, memory, evolution
agent/schematic/ # Schematic engine: parser, beautify, placement, wires, scoring
agent/scoring/   # 10 quality metrics + weighting
agent/knowledge/ # Component catalog, dependency graphs, part locking
kicad_rag/       # RAG stack: FAISS vectors, taxonomy, JLC parts ingestion
pcb_design/      # Board model, placement, ratsnest, routers, pours, export
server/          # Flask routes, Socket.IO handlers, MCP server, session DB
static/          # Web UI: WebGL/3D PCB viewers, schematic renderer, pipeline panel
tests/           # Python + JS test suites (CI-enforced)
Parser_Bidirectional/ # Standalone KiCad S-expression parser (JS)
tscircuit_data/  # tscircuit format conversion
```

## 🗺️ Roadmap

- [ ] Autorouter v3: interactive ripup-and-rerun with designer-in-the-loop constraints
- [ ] Multi-board hierarchical designs (boards as components)
- [ ] DRC-in-the-loop during routing via `pcbnew` scripting
- [ ] SPICE simulation feedback loop feeding repair nodes
- [ ] Cost/availability-aware BOM optimization against live JLC stock

## 🤝 Contributing

Contributions are welcome — open an issue to discuss first, then a PR with tests. Keep agent behavior deterministic where possible and ground new component decisions in the knowledge base.

## 📄 License

[LICENSE](LICENSE)

---

<div align="center">

**Built by [Prasad Hiwarkhede](https://github.com/HiwarkhedePrasad)** · AI Engineer focused on agentic systems that ship

If this project interests you, a ⭐ goes a long way.

</div>
