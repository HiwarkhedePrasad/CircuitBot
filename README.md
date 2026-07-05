# CircuitBot

AI-assisted electronics design from natural-language intent to KiCad-compatible schematic and PCB files.

CircuitBot combines an agentic design pipeline, KiCad library retrieval, schematic generation, PCB import/export, and an interactive browser-based PCB editor. It is built for rapidly turning a circuit idea into inspectable, editable `.kicad_sch` and `.kicad_pcb` artifacts.

## What It Does

CircuitBot lets you describe a circuit in plain English, then helps produce and inspect the resulting EDA artifacts:

- Analyze a user prompt into functional circuit requirements.
- Retrieve KiCad-compatible symbols and parts using hybrid search.
- Select and validate components with agent rules and LLM assistance.
- Generate schematic netlists and KiCad schematic files.
- Place and route schematic wiring with orthogonal grid-aware paths.
- Run KiCad ERC checks when KiCad CLI is available.
- Gate PCB generation behind explicit user approval.
- Generate, import, edit, view, and export KiCad PCB board models.
- Interactively inspect PCB layers, traces, pads, vias, silkscreen, and footprints in the browser.

## Current Highlights

- **Agentic EDA workflow:** LangGraph-based pipeline for prompt analysis, part research, selection, validation, schematic generation, audit, approval, and PCB layout.
- **Hybrid KiCad retrieval:** BM25/SQLite lexical search plus dense embedding retrieval over KiCad library data.
- **KiCad file support:** Exports `.kicad_sch` and `.kicad_pcb`; imports `.kicad_pcb` into the internal board model.
- **Web PCB editor:** WebGL-backed PCB viewport with Canvas2D detailed board painting for smooth panning, zooming, traces, vias, pads, silkscreen, and labels.
- **Layer controls:** PCB layer panel with color swatches, visibility toggles, and scrollable KiCad-style layer list.
- **Manual PCB editing:** Select and move components, place vias, route traces, save the board model, and export the current layout.
- **Live browser UI:** Flask + Socket.IO frontend streams agent progress and keeps the user in the loop during long-running generation steps.

## Architecture

```text
User prompt
  -> Agent analysis
  -> KiCad library retrieval
  -> Component selection
  -> Validation and support-rule injection
  -> Netlist generation
  -> Schematic placement and routing
  -> KiCad schematic export / ERC audit
  -> Human PCB approval
  -> PCB placement, routing, import/export, and browser editing
```

## Repository Layout

```text
CircuitBot/
├── server.py                    Flask + Socket.IO API server
├── config.py                    Runtime configuration helpers
├── requirements.txt             Python dependencies
├── package.json                 Frontend/package metadata
├── README.md                    Project overview and setup
├── LICENSE                      Project license
├── CIRCUITBOT_SUMMARY.md        Current engineering status summary
│
├── agent/                       LangGraph agent and design pipeline
│   ├── builder.py               Graph construction
│   ├── state.py                 Shared agent state
│   ├── prompts.py               LLM prompts
│   ├── erc_runner.py            KiCad ERC runner
│   ├── kicad_export.py          KiCad schematic exporter
│   └── nodes/                   Pipeline node implementations
│
├── pcb_design/                  PCB data model, import/export, routing helpers
│   ├── board_model.py           Shared BoardModel representation
│   ├── pcb_import.py            KiCad PCB importer
│   ├── pcb_export.py            KiCad PCB exporter
│   ├── ratsnest.py              Connectivity/ratsnest helpers
│   └── pcbnew_runner.py         KiCad pcbnew subprocess bridge
│
├── kicad_rag/                   KiCad component retrieval/indexing
│   ├── builder.py               Search/index builder
│   ├── client.py                Retrieval client
│   ├── retrieval.py             Ranking/fusion logic
│   └── store.py                 Vector/index storage
│
├── static/                      Browser UI
│   ├── index.html               Main app shell
│   ├── app.js                   UI orchestration and API wiring
│   ├── style.css                Application styling
│   ├── schematic_renderer.js    Schematic canvas renderer
│   └── pcb_view/                Modular PCB viewer/editor
│       ├── constants.js         PCB constants and layer catalog
│       ├── state.js             PCB editor state
│       ├── utils.js             Geometry/layer/helpers
│       ├── gl_math.js           Local matrix fallback
│       ├── editor_webgl.js      WebGL grid + detailed PCB renderer
│       └── events.js            PCB interaction handlers
│
├── tests/                       Python and Node test coverage
├── kicanvas/                    KiCanvas reference/vendor code
├── kicad-library-utils/         KiCad utility/vendor code
└── Parser_Bidirectional/        Parser support code
```

## Requirements

### Core

- Python 3.10+
- Node.js 18+
- Git

### Recommended for full EDA flow

- KiCad with `kicad-cli` available on `PATH`
- KiCad `pcbnew` Python environment for PCB subprocess workflows
- An OpenAI-compatible or project-supported LLM endpoint/API key

Python dependencies are listed in `requirements.txt`. The frontend package dependency is listed in `package.json`.

## Setup

### 1. Clone

```bash
git clone https://github.com/HiwarkhedePrasad/CircuitBot.git
cd CircuitBot
```

### 2. Create a Python environment

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Linux/macOS:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Install Node dependencies

```bash
npm install
```

### 4. Configure environment

Copy `.env.example` to `.env` and fill in the required API key or endpoint values for your local setup.

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

### 5. Run the app

```bash
python server.py
```

Open:

```text
http://localhost:5000
```

## Usage

### Generate a design

1. Open the web app.
2. Enter a circuit request, for example:

```text
ESP32 with USB-C power, DS18B20 temperature sensor, status LED, and 3.3V regulator
```

3. Watch the agent progress in the right-side activity panel.
4. Review the generated schematic.
5. Approve PCB generation when prompted.
6. Inspect or edit the board in PCB View.

### Import a PCB

1. Click **PCB View**.
2. Click **Import PCB** or drop a `.kicad_pcb` file into the upload area.
3. Use pan/zoom, layer toggles, select, route, and via tools to inspect or edit the board.

### Edit PCB layout

- **Pan:** move around the board.
- **Select:** click a component and drag it to a new location.
- **Route:** draw traces on the active copper layer.
- **Via:** place or move vias.
- **Layer panel:** toggle visibility for copper, silkscreen, fab, courtyard, mask, paste, and edge layers.
- **Ctrl+S / Cmd+S:** save the current board model and export the updated `.kicad_pcb`.

Browser security prevents silently overwriting the original imported file. CircuitBot saves the live board state in the backend and exports a new KiCad-compatible PCB file.

## API Overview

Important server endpoints:

| Endpoint | Purpose |
|---|---|
| `/api/search` | Search indexed KiCad library data |
| `/api/sexpr` | Fetch symbol S-expression data |
| `/api/generate_netlist` | Generate netlist data from schematic state |
| `/api/export_sch` | Export current KiCad schematic |
| `/api/export_pcb` | Export current KiCad PCB |
| `/api/import_pcb` | Import `.kicad_pcb` into BoardModel JSON |
| `/api/save_board_model` | Persist current browser-edited board model |
| `/api/pcb_enriched_board_model` | Return enriched board model for PCB rendering |
| `/api/ratsnest` | Compute board ratsnest/connectivity |
| `/api/circuit_json` | Convert board model to circuit JSON |
| `/api/apply_edits` | Apply structured edit events |

Socket.IO is used for agent progress, approval, completion, and PCB-ready events.

## Testing

Run the JavaScript PCB viewer checks:

```bash
node tests/test_pcb_viewer.js
```

Run Python tests:

```bash
pytest -q
```

Useful targeted checks:

```bash
node --check static/app.js
node --check static/pcb_view/editor_webgl.js
python -m py_compile server.py pcb_design/board_model.py pcb_design/pcb_import.py pcb_design/pcb_export.py
```

## Development Notes

- `BoardModel` is the shared source of truth between import, export, rendering, and manual PCB edits.
- Imported PCB files are normalized into `BoardModel` through `pcb_design/pcb_import.py`.
- Browser PCB rendering is split into modular files under `static/pcb_view/`.
- The PCB editor uses WebGL for the infinite grid/background and Canvas2D for high-detail board geometry.
- Layer visibility is stored in `pcbState.visibleLayers` and honored by the PCB renderer.
- Manual PCB edits should call `/api/save_board_model` before export.

## Known Limitations

- Some KiCad footprint and board primitives may still need deeper rendering parity with KiCanvas/KiCad.
- Imported board stackup metadata is not yet fully inferred; the layer catalog includes common KiCad layer names and placeholders.
- Browser save cannot overwrite an arbitrary local file path; edited boards are exported as downloaded `.kicad_pcb` files.
- Agent component selection quality depends on retrieval data, LLM behavior, and support-rule coverage.
- Full KiCad ERC/DRC behavior requires a correctly installed KiCad environment.

## Roadmap

- Add `Objects` and `Nets` tabs beside the PCB layer panel.
- Add per-layer opacity and active-layer isolation controls.
- Improve KiCad stackup parsing and imported inner-layer naming.
- Add connected-trace update behavior when moving components.
- Expand renderer support for more KiCad custom pad and zone primitives.
- Strengthen component-selection rules for exact user-specified parts.

## License

This project is licensed under the MIT License. See `LICENSE` for details.
