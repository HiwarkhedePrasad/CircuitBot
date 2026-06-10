# ⚡ CircuitBot

<div align="center">

### AI-Powered KiCad Schematic Generation & Component Retrieval Agent

Build electronic schematics from natural language using **LangGraph**, **LLaMA-3**, **Hybrid RAG**, and **KiCad symbol intelligence**.

![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge\&logo=python\&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-000000?style=for-the-badge\&logo=flask\&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-Agentic_AI-blue?style=for-the-badge)
![Groq](https://img.shields.io/badge/Groq-LLaMA_3-orange?style=for-the-badge)
![SQLite](https://img.shields.io/badge/SQLite-003B57?style=for-the-badge\&logo=sqlite\&logoColor=white)
![Socket.IO](https://img.shields.io/badge/Socket.IO-010101?style=for-the-badge\&logo=socket.io\&logoColor=white)

</div>

---

## 📖 Overview

CircuitBot is an AI-powered Electronic Design Automation (EDA) assistant that transforms natural language circuit requirements into schematic-ready component layouts.

A user can describe a circuit such as:

> "Design an ESP32-based temperature monitoring system with battery backup and status LEDs."

CircuitBot automatically:

1. Analyzes the design requirements.
2. Breaks the design into functional subsystems.
3. Searches a 22,000+ component KiCad database using Hybrid RAG.
4. Selects suitable components using LLaMA-3.
5. Retrieves raw KiCad symbols.
6. Generates logical netlists.
7. Places components automatically.
8. Routes schematic connections using A* pathfinding.
9. Streams the entire workflow live to the browser.

---

# ✨ Features

### 🤖 Agentic Design Workflow

Powered by LangGraph, the AI agent performs:

* Requirement Analysis
* Component Research
* Component Selection
* Symbol Dispatch
* Netlist Generation
* Placement Planning
* Wire Routing

without manual intervention.

---

### 🔍 Hybrid RAG Component Retrieval

CircuitBot combines:

* Dense Vector Search

  * BAAI/bge-small-en-v1.5
  * TurboVec Quantized Index

* Lexical Search

  * SQLite FTS5 BM25

* Weighted Reciprocal Rank Fusion (RRF)

This enables both:

* Natural language queries
* Exact part-number searches

Examples:

```text
"3.3V regulator"
"AMS1117-3.3"
"ESP32 WiFi MCU"
"temperature sensor"
```

---

### 🧠 LLM-Guided Component Selection

Using Groq-hosted LLaMA-3:

* Evaluates retrieved candidates
* Selects optimal components
* Generates reference designators
* Produces netlists from circuit intent

---

### 📐 Automatic Layout Engine

Backend placement engine:

* Signal-flow based column placement
* Category-aware positioning
* Bounding-box collision avoidance
* Grid-snapped placement

---

### ⚡ Orthogonal A* Routing

Custom routing engine:

* Obstacle-aware routing
* Orthogonal traces
* Pin corridor carving
* Component collision avoidance

Built using:

```text
pathfinding
AStarFinder
Grid-based routing
```

---

### 🎨 Browser-Based KiCad Viewer

Supports:

* Raw KiCad S-Expression rendering
* Zooming
* Panning
* Component inspection
* Auto-layout visualization

All directly inside the browser using HTML5 Canvas.

---

### 📡 Real-Time Agent Streaming

Using Flask-SocketIO:

* Agent thoughts
* Search progress
* Component selections
* Layout updates
* Final routing results

are streamed live to the frontend.

---

# 🏗️ System Architecture

```text
User Prompt
     │
     ▼
┌────────────────────┐
│ LangGraph Agent    │
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│ Requirement Analysis│
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│ Hybrid RAG Search  │
│ BM25 + Dense Search│
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│ Component Selection│
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│ Symbol Retrieval   │
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│ Netlist Generation │
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│ Layout + Routing   │
└─────────┬──────────┘
          │
          ▼
     Schematic
```

---

# 📂 Project Structure

```text
CircuitBot/
│
├── server.py
├── test_parser.js
│
├── agent/
│   ├── graph.py
│   ├── layout_engine.py
│   ├── prompts.py
│   ├── state.py
│   └── tools.py
│
├── kicad_rag/
│   ├── builder.py
│   ├── client.py
│   ├── retrieval.py
│   ├── store.py
│   ├── constants.py
│   └── cli.py
│
└── static/
    ├── index.html
    ├── app.js
    ├── renderer.js
    ├── schematic.js
    └── style.css
```

---

# 🚀 Installation

## 1. Clone Repository

```bash
git clone https://github.com/HiwarkhedePrasad/CircuitBot.git

cd CircuitBot
```

---

## 2. Create Virtual Environment

```bash
python -m venv venv
```

### Linux / macOS

```bash
source venv/bin/activate
```

### Windows

```bash
venv\Scripts\activate
```

---

## 3. Install Dependencies

```bash
pip install \
flask \
flask-socketio \
langgraph \
groq \
fastembed \
turbovec \
python-dotenv \
pathfinding
```

---

## 4. Configure Environment Variables

Create a `.env` file:

```env
GROQ_API_KEY=YOUR_GROQ_API_KEY
```

---

## 5. Prepare KiCad Symbol Dataset

Clone:

```bash
kicad-symbols
```

inside:

```text
kicad_rag/
```

Expected structure:

```text
kicad_rag/
└── kicad-symbols/
```

---

## 6. Build Component Database

```bash
python -m kicad_rag build
```

This generates:

```text
circuitbot.sqlite
circuitbot.tvim
turbovec_dataset.json
```

---

## 7. Run CircuitBot

```bash
python server.py
```

Open:

```text
http://localhost:5000
```

---

# 🛠️ Example Prompts

### Embedded Systems

```text
ESP32 with battery charger and status LED
```

### Sensor Systems

```text
Temperature monitoring circuit with ADC and display
```

### Power Electronics

```text
3.3V regulated power supply using AMS1117
```

### IoT

```text
WiFi environmental monitoring station
```

---

# 📊 Tech Stack

| Layer                  | Technology                |
| ---------------------- | ------------------------- |
| Backend                | Flask                     |
| Realtime Communication | Socket.IO                 |
| Agent Framework        | LangGraph                 |
| LLM                    | LLaMA-3 via Groq          |
| Retrieval              | TurboVec + BM25           |
| Database               | SQLite FTS5               |
| Embeddings             | BGE Small EN v1.5         |
| Routing                | A* Pathfinding            |
| Frontend               | HTML5 Canvas + JavaScript |
| EDA Format             | KiCad Symbols             |

---

# 🔮 Future Improvements

* Full KiCad schematic export
* PCB generation
* SPICE simulation support
* Multi-agent architecture
* Constraint-aware routing
* BOM generation
* ERC/DRC validation
* Interactive schematic editing

---

# 🤝 Contributing

Contributions, feature requests, and pull requests are welcome.

If you'd like to improve CircuitBot:

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Open a Pull Request

---

# 📜 License

This project is released under the MIT License.

---

<div align="center">

Built with ❤️ for AI-Assisted Electronic Design Automation

</div>
