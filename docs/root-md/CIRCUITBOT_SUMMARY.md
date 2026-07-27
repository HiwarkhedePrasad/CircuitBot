# CircuitBot Project Summary

## Current Status

CircuitBot is an AI-assisted EDA application that connects a LangGraph design agent with a browser-based schematic and PCB editor. The project currently supports natural-language circuit generation, KiCad schematic export, KiCad PCB import/export, manual PCB editing, and a modular WebGL/Canvas PCB viewer.

## Core Capabilities

- Natural-language circuit design through a Flask + Socket.IO web app.
- LangGraph-based agent pipeline for analysis, research, selection, validation, schematic generation, audit, approval, and PCB layout.
- Hybrid KiCad library retrieval using lexical and dense search components.
- KiCad schematic generation and export through `.kicad_sch` S-expression output.
- KiCad PCB import/export through the shared `BoardModel` abstraction.
- Browser PCB editor with pan, zoom, select, component move, routing, via placement, layer visibility, and save/export behavior.
- `Ctrl+S` / `Cmd+S` saves the active board model and exports the current `.kicad_pcb`.

## Important Frontend State

The PCB viewer has moved from the legacy monolithic `static/pcb_viewer.js` path into modular files under `static/pcb_view/`:

```text
static/pcb_view/
├── constants.js
├── state.js
├── utils.js
├── gl_math.js
├── editor_webgl.js
└── events.js
```

The active renderer uses WebGL for the infinite grid/background and Canvas2D for detailed PCB geometry. This split keeps camera movement smooth while preserving detailed rendering for traces, vias, pads, silkscreen, fab, courtyard, labels, and board outline.

## PCB Viewer Features Implemented

- Local matrix fallback through `gl_math.js`.
- KiCad-style layer catalog and visibility state.
- Scrollable PCB layer panel in the left sidebar.
- Layer filtering for traces, pads, vias, footprint graphics, text, and edge cuts.
- Deep-zoom label gating to prevent dense imported boards from becoming unreadable.
- Component hit-testing fix for select/move behavior.
- Freeform component dragging without grid snapping.
- Save/export shortcut for edited board models.

## Key Backend Endpoints

| Endpoint | Purpose |
|---|---|
| `/api/import_pcb` | Import `.kicad_pcb` into `BoardModel` JSON |
| `/api/save_board_model` | Persist browser-edited board state |
| `/api/export_pcb` | Export current board as KiCad PCB |
| `/api/pcb_enriched_board_model` | Return render-ready board model |
| `/api/ratsnest` | Compute connectivity/ratsnest |
| `/api/export_sch` | Export current schematic |
| `/api/circuit_json` | Convert board model to circuit JSON |

## Known Engineering Limitations

- Full KiCad visual parity is still incomplete for advanced footprint primitives, custom pads, zones, and stackup metadata.
- Imported inner-layer names are represented through a common fixed catalog until stackup parsing is expanded.
- Browser save exports a new file instead of overwriting an arbitrary imported local file path.
- Moving a component updates its position, but connected trace rerouting/update behavior still needs additional work.
- Agent component correctness still depends on retrieval quality and support-rule coverage.

## Recommended Next Work

1. Add `Objects` and `Nets` tabs beside the layer panel.
2. Add active-layer isolation and opacity controls.
3. Parse real KiCad stackup names from imported boards.
4. Recompute or preserve trace connectivity when components are moved.
5. Improve renderer support for custom pads, zones, and filled copper pours.
6. Strengthen agent selection for exact user-requested parts and duplicate-family avoidance.

## Quick Validation Commands

```bash
node --check static/app.js
node --check static/pcb_view/editor_webgl.js
node tests/test_pcb_viewer.js
pytest -q
```
