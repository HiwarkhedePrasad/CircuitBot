# CircuitBot Architecture Deep Dive

> Comprehensive reference for feature development. Generated from full codebase analysis.

---

## Table of Contents
1. [System Overview](#1-system-overview)
2. [Frontend-Backend Communication](#2-frontend-backend-communication)
3. [PCB State Management](#3-pcb-state-management)
4. [AI Agent Workflow](#4-ai-agent-workflow)
5. [KiCad Data Flow](#5-kicad-data-flow)
6. [Rendering Pipeline](#6-rendering-pipeline)
7. [Key Data Structures](#7-key-data-structures)

---

## 1. System Overview

CircuitBot is an AI-powered PCB/schematic design tool. A user describes a circuit in natural language, and a multi-stage LLM agent pipeline generates a complete schematic and PCB layout. The frontend provides interactive editing with real-time WebGL rendering.

```
┌─────────────────────────────────────────────────────────────────┐
│                        FRONTEND (Browser)                        │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │   Schematic   │  │    PCB View   │  │   Chat / Agent UI    │  │
│  │   (PixiJS)    │  │ (WebGL+Canvas)│  │   (Socket.IO)        │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘  │
│         │                  │                      │               │
│         └──────────────────┼──────────────────────┘               │
│                            │                                      │
└────────────────────────────┼──────────────────────────────────────┘
                             │ Socket.IO + REST
┌────────────────────────────┼──────────────────────────────────────┐
│                        BACKEND (Python)                            │
│                            │                                      │
│  ┌─────────────────────────┼──────────────────────────────────┐  │
│  │                    Flask + SocketIO                          │  │
│  │  ┌─────────┐  ┌────────┴──────┐  ┌─────────────────────┐  │  │
│  │  │  REST    │  │  WebSocket    │  │  DesignSession       │  │  │
│  │  │  Routes  │  │  Handlers     │  │  Manager             │  │  │
│  │  └────┬────┘  └───────┬──────┘  └──────────┬──────────┘  │  │
│  └───────┼────────────────┼─────────────────────┼─────────────┘  │
│          │                │                     │                  │
│  ┌───────┴────────────────┴─────────────────────┴─────────────┐  │
│  │                    LangGraph Agent                           │  │
│  │  analyze -> research -> select -> validate -> dispatch ->   │  │
│  │  netlist -> placement -> routing -> PCB layout              │  │
│  └────────────────────────────────────────────────────────────┘  │
│          │                                                       │
│  ┌───────┴────────────────────────────────────────────────────┐  │
│  │  KicadRAG (22k components)  |  pcb_design/  |  KiCad I/O  │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

### Tech Stack
| Layer | Technology | Purpose |
|-------|-----------|---------|
| Backend | Flask + Flask-SocketIO | Web server + real-time events |
| Agent | LangGraph (LangChain) | State machine with ~25 nodes |
| LLM | OpenAI-compatible proxy (localhost:4010) | Default: deepseek-v4-flash-free |
| Search | KicadRAG (bge-small-en-v1.5 + BM25) | 22k KiCad component search |
| Schematic | PixiJS 7 | 2D canvas rendering |
| PCB | WebGL2 + Canvas2D overlay | Dual-renderer board view |
| Geometry | Shapely, NetworkX | PCB placement, routing, DRC |
| Math | Custom KiCanvas math lib | Mat3, Vec2, Camera, Color |

---

## 2. Frontend-Backend Communication

### 2.1 Communication Channels

CircuitBot uses a **hybrid Socket.IO + REST** architecture:

| Channel | Used For | Pattern |
|---------|---------|---------|
| **Socket.IO** | Chat, agent progress, interactive approvals, real-time state | Bidirectional events |
| **REST API** | File I/O, search, data persistence, board model saves | Request-response |

### 2.2 Socket.IO Events

#### Client → Server (Emitted from `static/app.js`)

| Event | Payload | When |
|-------|---------|------|
| `chat:message` | `{session_id, text}` | User sends a message |
| `chat:resume` | `{session_id}` | Page load / reconnect |
| `chat:commit_proposal` | `{session_id, id, component, x, y}` | User accepts AI component |
| `chat:reject_proposal` | `{session_id, proposal_id}` | User rejects AI component |
| `agent:pcb_approve` | `{approved: bool}` | User approves/rejects PCB layout |
| `agent:board_config` | `{layer_count: 2\|4\|6\|8}` | User picks layer count |
| `agent:validation_help_response` | `{action: "retry"\|"skip"\|"force"\|"terminate"}` | User resolves validation error |

#### Server → Client (Received in `static/app.js`)

| Event | Purpose |
|-------|---------|
| `agent:thinking` | Agent status update |
| `agent:log` | Log message |
| `agent:thought_stream` | Structured progress events (thought, tool_call, step) |
| `agent:component` | Single component placed |
| `agent:layout_ready` | Full layout (placements, traces, power_labels, netlist) |
| `agent:pcb_ready` | Board model loaded, switch to PCB view |
| `agent:pcb_approval` | Prompt user to proceed to PCB |
| `agent:board_config` | Request board layer configuration |
| `agent:validation_help` | Validation error needing user decision |
| `agent:done` | Design complete |
| `agent:persisted` | Design saved to disk |
| `agent:error` | Error occurred |
| `agent:conversation` | Assistant message / tool card |
| `agent:review_suggestion` | Post-layout review suggestion |
| `chat:reply` | AI chat response |
| `chat:proposal` | AI component proposal |
| `chat:state` | Hydrated chat state on reconnect |
| `tscircuit:board-model-updated` | Board model changed (triggers re-render) |

### 2.3 REST API Endpoints

All endpoints include `?session_id=...` for session routing.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/search?q=<query>` | GET | RAG component search (top-5) |
| `/api/pins?id_str=<id>` | GET | Pin definitions for a component |
| `/api/sexpr?id_str=<id>` | GET | Raw KiCad S-expression |
| `/api/generate_netlist` | POST | LLM netlist generation from pin matrix |
| `/api/save_layout` | POST | Save schematic layout |
| `/api/save_board_model` | POST | Save PCB board model |
| `/api/pcb_enriched_board_model` | GET | Full board model with ratsnest |
| `/api/ratsnest` | POST | Compute ratsnest server-side |
| `/api/circuit_json` | GET/POST | Convert to tscircuit JSON |
| `/api/apply_edits` | POST | Apply 6 edit event types |
| `/api/export_sch` | GET | Export .kicad_sch file |
| `/api/export_pcb` | GET | Export .kicad_pcb file |
| `/api/import_pcb` | POST (multipart) | Import .kicad_pcb file |

### 2.4 Edit Event Types (`/api/apply_edits`)

| Event Type | Description |
|------------|-------------|
| `edit_trace_hint` / `edit_pcb_trace_hint` | Add traces to PCB |
| `edit_component_location` / `edit_pcb_component_location` | Move components |
| `schematic_add_wire` / `add_wire` | Add schematic wires |
| `schematic_delete_wire` / `delete_wire` | Remove schematic wires |
| `schematic_move_component` | Move schematic components |

### 2.5 Custom DOM Events (Internal Frontend)

| Event | Detail | Purpose |
|-------|--------|---------|
| `pcb:view-changed` | `{bounds}` | View bounds changed |
| `pcb:interaction-updated` | `{tool, mode, routeLayer, routeWidth, toolsEnabled}` | Tool/mode changed |
| `pcb:layers-updated` | `{visibleLayers}` | Layer visibility changed |
| `tscircuit:edit-sync` | `{ok, applied, ignored}` | Edit sync result |
| `tscircuit:board-model-updated` | `{board_model}` | Board model updated |

---

## 3. PCB State Management

### 3.1 Global State Object (`static/pcb_view/state.js`)

The central state is `pcbState`, a single mutable global object:

```javascript
pcbState = {
  // === Board Data ===
  boardModel: BoardModel,          // Single source of truth (normalized on every set)
  
  // === Render/Display ===
  renderMode: 'full' | 'overlay',
  visibleLayers: { F.Cu: true, B.Cu: true, F.SilkS: true, ... },  // 28 layers
  soloLayer: string | null,        // Shift+click to isolate one layer
  highlightedNet: string | null,   // Net name with glow effect
  
  // === Mode & Tool ===
  mode: PCB_MODE,                  // IDLE | PANNING | DRAG_COMPONENT | ROUTE | GHOST_PLACEMENT | DRAW_OUTLINE
  activeTool: PCB_TOOL,            // PAN | SELECT | ROUTE | VIA | OUTLINE
  
  // === Camera/Viewport ===
  zoom: number,                    // Current zoom multiplier
  panX: number, panY: number,      // Pan offset in screen pixels
  baseScale: number,               // Pixel-to-mm scale
  midX: number, midY: number,      // Board center in world coords
  cx: number, cy: number,          // Canvas center in screen pixels
  
  // === Hover State (read-only) ===
  hoveredPadKey: string | null,    // "REF:padNumber"
  hoveredComponentRef: string | null,
  hoveredViaIndex: number | null,
  hoveredTraceIndex: number | null,
  hoveredSegmentIndex: number | null,
  
  // === Selection ===
  selectedComponentRef: string | null,
  selectedTraceIndices: number[],  // Multi-select with Ctrl+click
  
  // === Drag State ===
  dragComponentRef: string | null,
  dragViaIndex: number | null,
  dragOrigin: {x,y} | null,
  dragPointerStart: {x,y} | null,
  
  // === Route State ===
  routeStartAnchor: object | null, // {kind, key, x, y, noSnap}
  routeNetName: string,
  routeLayer: string,              // Default 'F.Cu'
  routeWidth: number,              // Default 0.254mm
  routePoints: {x,y}[],
  routeVias: object[],
  routeCursor: {x,y} | null,
  
  // === Outline Drawing ===
  outlinePoints: {x,y}[],
  outlineDraft: {x,y} | null,
  
  // === Undo/Redo ===
  undoStack: array,
  redoStack: array,
  
  // === Ratsnest ===
  ratsnest: { [netName]: [{from, to, x1, y1, x2, y2}] },
  
  // === AI Integration ===
  ghostProposal: object | null,    // Ghost component from AI
  clipboard: object | null,        // Copy/paste
}
```

### 3.2 State Dispatchers

Five event dispatchers broadcast state changes:

| Dispatcher | Event | When |
|-----------|-------|------|
| `dispatchPcbViewChanged()` | `pcb:view-changed` | Camera/zoom changes |
| `dispatchPcbInteractionUpdated()` | `pcb:interaction-updated` | Tool/mode changes |
| `dispatchBoardSync(ok, detail)` | `tscircuit:edit-sync` | Save result |
| `dispatchBoardModelUpdated()` | `tscircuit:board-model-updated` | Board content changes |
| `dispatchPcbLayerVisibilityUpdated()` | `pcb:layers-updated` | Layer visibility changes |

### 3.3 Mutation Pattern

Every board mutation follows this exact pattern:

```
1. before = deepClone(pcbState.boardModel)    // Snapshot
2. Modify pcbState.boardModel in-place        // Apply change
3. pcbEditor.refresh()                        // Re-render
4. await pcbEditor.saveBoardModel()           // POST to /api/save_board_model
5. pcbEditor.pushHistory(name, before, after) // Push to undo stack
6. On failure: revert, re-render, dispatch error
```

### 3.4 BoardModel Structure

```
BoardModel
├── version: "20260206"
├── generator: "circuitbot"
├── _pcbnew_content: string | null     // Raw KiCad passthrough
├── components: BoardComponent[]
│   ├── ref, footprint, x, y, rotation, layer, value
│   ├── pads: PadDef[]
│   │   ├── number, x, y, width, height, shape, type, rotation
│   │   ├── drill, drill_width, drill_offset, roundrect_rratio
│   │   └── layers: string[]
│   └── graphics: dict[]
├── traces: BoardTrace[]
│   ├── net, layer, width, path: (x,y)[], via: (x,y) | null
├── vias: BoardVia[]
│   ├── x, y, drill, diameter, layers, net
├── zones: BoardZone[]
│   ├── net, layer, polygon (Shapely), priority
├── nets: dict[]                     // {name, pins: ["U1:5", "C1:2"]}
├── power_pins: dict[]
├── power_labels: dict[]
├── outline_segments: dict[]         // Edge.Cuts geometry
├── outline: Polygon (Shapely)
├── layers: (id, name, type)[]
└── layer_count: int = 2
```

### 3.5 Layer System

28-layer catalog with per-layer visibility:

| Group | Layers | Default Visible |
|-------|--------|----------------|
| Copper | F.Cu, B.Cu, In1.Cu, In2.Cu, ... | F.Cu, B.Cu |
| Graphics | F.SilkS, B.SilkS, F.Fab, B.Fab | F.SilkS |
| Mask | F.Mask, B.Mask | - |
| Outline | Edge.Cuts | Yes |
| Other | F.CrtYd, B.CrtYd, Dwgs.User, Cmts.User | - |

---

## 4. AI Agent Workflow

### 4.1 Intent Classification (`agent/prompt_router.py`)

User messages are classified into intents:

| Intent | Confidence Threshold | Action |
|--------|---------------------|--------|
| `design_pipeline` | >= 0.7 | Full agent pipeline |
| `add_component` | any | RAG search + proposal |
| `modify_design` | any | Lightweight modify graph |
| `component_query` | any | RAG search + info |
| `help` | any | Help text |
| `other` | any | Clarification |

### 4.2 Main Design Graph (`agent/builder.py`)

The LangGraph agent has ~25 nodes in this flow:

```
analyze
  │  LLM decomposes request into subsystems (MCU, sensor, power, etc.)
  ▼
research
  │  RAG search over 22k components + web research (DeepSearch) per subsystem
  ▼
select
  │  LLM reranker scores candidates 0-10, picks best, injects supporting passives
  ▼
datasheet_search ─► symbol_compatibility
  │
  ▼
validate
  │  LLM + deterministic checks (family integrity, module preference)
  ├──► validate_repair (loop) ──► validate
  ├──► ask_validation_help (user decides)
  └──► dispatch (success)
  │
  ▼
dispatch
  │  Loads KiCad S-expressions, extracts pin matrices
  ▼
symbol_validate ─► connection_search
  │
  ▼
netlist
  │  Power/GND pre-assignment + deterministic pin matching + LLM signal wiring
  ▼
power_net_repair ─► structural_net_validate ─► structural_net_repair
  │
  ▼
placement
  │  Schematic component placement (blocks_v2 engine, locked after first run)
  ▼
routing
  │  A* pathfinding for schematic wires
  ▼
connectivity_validate ─► connectivity_repair
  │
  ▼
schematic_audit
  │  ERC check + repair loop (max 3 iterations)
  ├──► schematic_repair ──► routing (loop)
  └──► ask_pcb_approval (success)
  │
  ▼
ask_pcb_approval
  │  User approves/rejects via WebSocket threading.Event
  ├──► ask_board_config (user picks 2/4/6/8 layers)
  ├──► pcb_layout
  │     Graph-driven placement + BoardModel + ratsnest
  ├──► design_review
  │     Post-layout suggestions
  └──► END
```

### 4.3 Modify Graph (Lightweight)

```
classify ─► apply ─► END
```

Classifies modification type (value_change, part_swap, add/remove component, net_modify, reroute) and applies to current design.

### 4.4 Agent State (`agent/state.py`)

`AgentState` TypedDict with 40+ fields:

| Category | Fields |
|----------|--------|
| Input | `prompt` |
| Analysis | `analysis`, `research_results`, `web_research_results` |
| Components | `selected_components`, `component_ops` |
| Netlist | `pin_matrix`, `netlist`, `nets`, `power_pins`, `power_labels` |
| Layout | `component_placements`, `wire_paths`, `component_bboxes` |
| Validation | `error`, `retry_count`, `validation_errors`, `validation_warnings`, `_erc_results` |
| PCB | `pcb_approved`, `layer_count`, `synthesis_graph` |
| Modification | `modification_type`, `modification_target`, `modification_value`, `original_design` |
| Exclusions | `rejected_ids`, `rejected_families`, `repair_failures` |

### 4.5 Key Agent Modules

| Module | Purpose |
|--------|---------|
| `agent/pin_matcher.py` | Zero-LLM deterministic pin matching (decoupling caps, pull-ups, UART, I2C, SPI) |
| `agent/reranker.py` | LLM-based component scoring (0-10) with type matching, library validation |
| `agent/support_rules.py` | Auto-injects passive components (decoupling caps, load caps, pull-ups) |
| `agent/synthesis/` | Canonical circuit graph for constraint-based validation |
| `agent/deep_search/` | Concurrent web research per subsystem |
| `agent/placement/` | Schematic placement (blocks_v2, SA optimizer, community detection) |
| `agent/routing/` | A* pathfinding for schematic wires |
| `agent/kicad_export.py` | Generates valid .kicad_sch with orthogonal routing |
| `agent/tools.py` | PCB calculation tools (trace width, impedance, via current, voltage drop) |

---

## 5. KiCad Data Flow

### 5.1 Three Independent Parsers

| Parser | Location | Language | Approach | Use Case |
|--------|----------|----------|----------|----------|
| Bidirectional | `Parser_Bidirectional/kicad-parser/` | JS | Generic AST + formatting metadata | Zero-loss round-trip |
| kicad-library-utils | `kicad-library-utils/common/sexpr.py` | Python | List-based AST | Import/export (.kicad_pcb, .kicad_mod, .kicad_sym) |
| KiCanvas | `kicanvas/src/kicad/parser.ts` | TypeScript | Declarative schema (P/T definitions) | Frontend rendering |

### 5.2 Import Pipeline (.kicad_pcb → BoardModel)

```
.kicad_pcb file
    │
    ▼
server/routes.py: api_import_pcb()
    │
    ├── pcb_import.import_board(path)
    │   ├── sexpr.parse_sexp(raw)           // Parse S-expressions
    │   ├── _collect_board_nodes(ast)       // Bucket by type
    │   ├── Parse nets: net_id → name
    │   ├── For each footprint → BoardComponent (with PadDef[])
    │   ├── For each segment → BoardTrace
    │   ├── For each via → BoardVia
    │   ├── For each zone → BoardZone (Shapely polygon)
    │   └── Parse Edge.Cuts → outline_segments + outline
    │
    ├── model.to_dict()                     // Serialize to JSON
    └── Store in DesignSession.last_design["board_model"]
    │
    ▼
Frontend: /api/pcb_enriched_board_model → render
```

### 5.3 Export Pipeline (BoardModel → .kicad_pcb)

```
DesignSession.last_design
    │
    ▼
pcb_export.generate_kicad_pcb(design)
    │
    ├── If _pcbnew_content exists → return as-is (passthrough)
    ├── If board_model exists:
    │   ├── Rebuild BoardModel from dict
    │   ├── For each component:
    │   │   ├── Resolve .kicad_mod from kicad-footprints/
    │   │   ├── Parse with sexpr.parse_sexp()
    │   │   ├── Modify AST: set position, ref, value, nets
    │   │   └── Serialize back
    │   ├── Emit segment/track/via/zone/outline S-expressions
    │   └── Output Edge.Cuts (gr_line, gr_arc, gr_rect, gr_poly)
    └── Fallback: generate from component/placement/wire dicts
    │
    ▼
.kicad_pcb text → download
```

### 5.4 RAG Search Pipeline

```
User query: "3.3V LDO regulator"
    │
    ▼
KicadRAG.search(query, k=5, mode="hybrid")
    │
    ├── Dense: embed with bge-small-en-v1.5 → TurboVec index
    ├── BM25: FTS5 on component text (2x weight)
    └── Weighted RRF fusion:
        score = 1/(K+rank_dense) + 2/(K+rank_bm25)
    │
    ▼
Result[]: id_str, text, score, pins, footprint, pads
    │
    ▼
Agent: fetch_sexpr() → raw KiCad symbol S-expression
Agent: fetch_footprint() → pad geometry for placement
```

### 5.5 Storage Schema

```sql
-- SQLite (circuitbot.sqlite)
CREATE TABLE symbols (
    id_int INTEGER PRIMARY KEY,
    id_str TEXT UNIQUE,        -- "Regulator_Linear:AMS1117-3.3"
    text TEXT,                  -- Embedding input text
    datasheet TEXT,
    extends TEXT,
    pins_json TEXT,             -- [{num, name, type}]
    footprint TEXT,
    fp_filters TEXT,
    pads_json TEXT              -- [{number, type, shape, ...}]
);
CREATE VIRTUAL TABLE symbols_fts USING fts5(...);  -- BM25 search

-- TurboVec (circuitbot.tvim)
-- Dense embeddings index for bge-small-en-v1.5
```

---

## 6. Rendering Pipeline

### 6.1 PCB View: Dual-Renderer System

```
┌─────────────────────────────────────────────────┐
│                  refresh()                        │
│                                                   │
│  1. Clear GL framebuffer (black)                  │
│                                                   │
│  2. Grid Shader Pass (WebGL fullscreen quad)      │
│     └── Minor grid (1.27mm), major (6.35mm)       │
│     └── Origin axes (red X, green Y)              │
│                                                   │
│  3. KiCanvas WebGL2 Content Pass (primary)        │
│     ├── Start render layer 'board'                │
│     ├── BoardPainter.paint():                     │
│     │   ├── _paintBoardOutline()                  │
│     │   ├── _paintTraces() (bottom→front)         │
│     │   ├── _paintVias()                          │
│     │   └── _paintComponents()                    │
│     │       ├── Body fill                         │
│     │       ├── Pads (mask, copper, drill)        │
│     │       └── Graphics (silk, fab, courtyard)   │
│     ├── PrimitiveSet.commit() → GPU upload        │
│     └── layer.render(camera) → drawArrays()       │
│                                                   │
│  4. Canvas2D Overlay (always on top)              │
│     ├── Airwires (dashed lines)                   │
│     ├── Board text (StrokeFontRenderer)           │
│     ├── Route preview (yellow dashed + crosshair) │
│     ├── Outline preview                           │
│     ├── Selection/hover highlights                │
│     ├── Net highlighting (glow)                   │
│     └── Ghost component (AI proposal)             │
│                                                   │
│  [Fallback: If KiCanvas fails, full Canvas2D]     │
└─────────────────────────────────────────────────┘
```

### 6.2 KiCanvas Rendering Stack

```
KiCMath (kicanvas_transform.js)
├── Color, Angle, Vec2, Mat3, BBox, Camera2, MathArc
│
KiCGL (kicanvas_webgl_helpers.js)
├── Uniform, ShaderProgram, VertexArray, Buffer
│
KiCVec (kicanvas_webgl_vector.js)
├── Circle, Polyline, Polygon
├── Tesselator → GPU-ready vertex arrays
├── PolygonSet, PolylineSet, CircleSet
├── PrimitiveSet (aggregates all three)
│
KiCRender (kicanvas_webgl_renderer.js)
├── WebGL2Renderer (layers, draw API)
├── WebGL2RenderLayer (wraps PrimitiveSet)
│
KiCBoard (kicanvas_board_painter.js)
└── BoardPainter.paint() → high-level board painting
```

### 6.3 Schematic View (PixiJS)

```
SchematicRenderer (schematic_renderer.js)
├── _gridLayer     → Dot grid + major lines
├── _wireLayer     → Wires, junctions, power labels, net labels
├── _symbolLayer   → Component symbols (ANSI overrides)
├── _pinLayer      → Pin stub lines
├── _textLayer     → Pin names, ref designators
└── _overlayLayer  → Selection, hover, wire draft
```

### 6.4 Client-Side Ratsnest Algorithm

```python
for each net with >= 2 pins:
    1. Collect pad positions from net pins
    2. Build adjacency graph from traces (0.2mm tolerance)
    3. Union-Find → find connected groups
    4. MST (minimum spanning tree) between disconnected groups
    5. Return edges as airwire guide lines
```

### 6.5 PCB Placement Algorithm (8 stages)

```
1. Build connectivity graph (weighted by net class: power=10, USB=8, etc.)
2. Community detection (greedy modularity) → component clusters
3. Component classification (CORE_IC, REGULATOR, PASSIVE, DECOUPLING, etc.)
4. Decoupling cap detection (caps bridging power+GND near ICs)
5. Cluster-level spring placement (force-directed)
6. Component-level spring placement (coarse refinement)
7. Rotation optimization (try 0/90/180/270, min weighted HPWL)
8. Overlap removal + pin-level HPWL refinement + grid snap
```

### 6.6 PCB Routing Algorithm

```
Shapely-aware A* maze router (router2.py)
├── Grid: 0.254mm resolution
├── Cost: base + via_penalty + congestion + bend_penalty
├── Priority: Power → High-speed → Critical → General
├── Rip-up & reroute: failed nets trigger shortest conflict removal
└── Full DRC: Shapely polygon clearance checks
```

---

## 7. Key Data Structures

### 7.1 Schematic Data Model (`static/schematic.js`)

```javascript
Schematic {
  components: SchematicComponent[]
  wirePaths: WirePath[]
  junctionPoints: {x,y}[]
  powerLabels: PowerLabel[]
  netLabels: NetLabel[]
  netlist: NetConnection[]
}

SchematicComponent {
  id, name, ops (S-expr drawing ops), category
  x, y, column, bbox, geomBBox, refDesignator, lib_id
}

NetLabel {
  id, net, x, y, orientation, pin
}
```

### 7.2 DesignSession (`server/state.py`)

```python
DesignSession:
  last_design: dict          # BOM, netlist, placements, wire_paths, board_model
  wire_bender_layout: dict   # Wire routing layout
  agent_events: dict         # threading.Events for PCB approval, validation help
  created_at: datetime
  last_active: datetime
```

### 7.3 ChatSession (`server/chat.py`)

```python
ChatSession:
  chat_history: list
  thought_stream: list
  board_model: dict
  proposals: list
  preferences: dict          # User learning (preferred parts, rejected parts, values)
```

---

## Appendix: File Reference

### Backend
| File | Lines | Purpose |
|------|-------|---------|
| `server.py` | 7 | Entry point |
| `server/__init__.py` | ~50 | Bootstrap, proxy check |
| `server/state.py` | ~150 | Global state, DesignSession, session manager |
| `server/routes.py` | 729 | All REST endpoints |
| `server/ws_handlers.py` | 457 | Socket.IO event handlers |
| `server/agent_runner.py` | 126 | Background agent execution |
| `server/chat.py` | 273 | Chat session management |
| `config.py` | 102 | LLM proxy config |

### Agent
| File | Lines | Purpose |
|------|-------|---------|
| `agent/builder.py` | 132 | LangGraph graph construction |
| `agent/state.py` | ~120 | AgentState TypedDict |
| `agent/prompt_router.py` | 172 | Intent classification |
| `agent/pin_matcher.py` | 442 | Deterministic pin matching |
| `agent/reranker.py` | 327 | Component scoring |
| `agent/kicad_export.py` | 587 | .kicad_sch generation |
| `agent/tools.py` | 264 | PCB calculation tools |
| `agent/utils.py` | 356 | Shared utilities |
| `agent/llm_utils.py` | 157 | LLM call infrastructure |
| `agent/emit_utils.py` | 153 | Event emission |

### PCB Design
| File | Lines | Purpose |
|------|-------|---------|
| `pcb_design/board_model.py` | 365 | Core data model |
| `pcb_design/pcb_import.py` | 541 | .kicad_pcb import |
| `pcb_design/pcb_export.py` | 521 | .kicad_pcb export |
| `pcb_design/placement.py` | 967 | 8-stage PCB placement |
| `pcb_design/router2.py` | 584 | A* maze router |
| `pcb_design/circuit_json_converter.py` | 311 | BoardModel → tscircuit JSON |
| `pcb_design/geometry.py` | 191 | Shapely geometry utilities |
| `pcb_design/pour.py` | 227 | Copper pour/fill |
| `pcb_design/ratsnest.py` | 195 | MST-based ratsnest |

### Frontend
| File | Lines | Purpose |
|------|-------|---------|
| `static/app.js` | ~2800 | Main application controller |
| `static/schematic.js` | 409 | Schematic data model |
| `static/schematic_renderer.js` | 1765 | PixiJS schematic renderer |
| `static/pcb_view/state.js` | 103 | PCB state management |
| `static/pcb_view/events.js` | 1370 | PCB event handlers |
| `static/pcb_view/editor_webgl.js` | 2369 | WebGL PCB editor |
| `static/pcb_view/utils.js` | 586 | Utility functions |
| `static/pcb_view/kicanvas_transform.js` | 701 | Math library |
| `static/pcb_view/kicanvas_webgl_vector.js` | 543 | Vector primitives |
| `static/pcb_view/kicanvas_board_painter.js` | 424 | Board content painter |
| `static/tscircuit-bridge.js` | 295 | tscircuit integration |
