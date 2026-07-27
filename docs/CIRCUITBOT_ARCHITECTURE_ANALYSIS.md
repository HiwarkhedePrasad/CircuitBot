# CircuitBot — Full Architectural Analysis

**Version analyzed:** 0.8.2 · **Branch:** feature/tscircuit-pcb-viewer
**Type:** AI-assisted KiCad schematic & PCB generation with a browser-based co-pilot, WebGL PCB editor, and 3D viewer.

---

## 1. What CircuitBot Is

CircuitBot turns a natural-language circuit description ("a USB-UART bridge with 3.3 V regulation") into a manufacturable KiCad schematic and PCB. A LangGraph agent pipeline (~39 nodes) does intake → research → part selection → netlist → schematic layout → PCB layout/routing → validation/repair, with human-in-the-loop gates. A Flask + SocketIO backend streams agent "thoughts" to a VS Code-style web UI that has three canvas surfaces (schematic, 2D PCB, 3D board) and a chat co-pilot. Everything runs locally; the LLM is reached through an auto-started `opencode serve` OpenAI-compatible proxy.

---

## 2. High-Level Architecture

```
Browser SPA (static/)
  chat co-pilot │ schematic (PixiJS) │ PCB 2D (WebGL) │ 3D (Three.js) │ tscircuit viewer
        │  SocketIO events (agent:*)        │  REST (/api/*)
        ▼                                     ▼
Flask + SocketIO backend (server/)
  routes.py (CRUD/export)  ws_handlers/chat (agent driver)  agent_runner  mcp_server
        │  invokes
        ▼
LangGraph agent pipeline (agent/)
  builder.py graph → ~39 nodes → repair loops + human gates
        │  uses
        ▼
Engines & knowledge
  routing/ (A* + direct)   placement/ (blocks_v2 + SA)   schematic/   pcb_design/ (PCB)
  kicad_export / erc_runner / skidl_runner   kicad_rag (retrieval+JLC)   deep_search
        │  all LLM calls via
        ▼
config.py → opencode serve --headless (OpenAI-compatible proxy, 127.0.0.1:4010/v1)
```

---

## 3. The Agent Pipeline (agent/)

The compiled graph is `agent_graph` built by `build_graph()` in **`agent/builder.py`**. `agent/graph.py` is only a backward-compat shim re-exporting nodes/utilities. State is `AgentState`, a `total=False` TypedDict — every field optional, no runtime validation.

**Intended phase order** (from state comments + router functions):
`analyze` → `clarify` → `architecture_planner` (freezes board_type/primary_mcu/provides) → capability/dependency expansion (`dependency_expander`, `capability_resolver`) → research (`research`, `deepresearch`, `datasheet_search`, `connection_search`) → `select` → `constraint_checker` (fatal vs repairable) → `freeze_components` → `dispatch` → `netlist` → schematic layout/audit → `placement`/`routing`/`pcb_layout` → `validate` (≤2 repair passes) → ERC loop via kicad-cli (with `_prev_erc_error_count` loop detection + `_erc_affected_nets` targeted re-route) → `design_review` → `llm_judge` quality gate.

**Human gates:** `clarify`, `ask_board_config` (layer count), `ask_pcb_approval`, `ask_validation_help`.
**Conditional routers** (utils.py): `_route_after_validate`, `_route_after_validation_help`, `_route_after_pcb_approval`.
**Retry bounds:** `MAX_LLM_RETRIES`, `MAX_VALIDATION_RETRIES`, `MAX_BATCH_PINS`.
A `modify` intent path handles post-hoc edits; a `ThompsonBandit` nudges some selection/repair choices.

**Node inventory by phase** (39 files in agent/nodes/):
- Intake/clarify: analyze, clarify, architecture_planner, ask_board_config
- Capability/planning: capability_resolver, dependency_expander, symbol_compatibility, deduplicator, freeze_components
- Research: research, deepresearch, datasheet_search, connection_search
- Selection: select, constraint_checker
- Netlist/schematic: dispatch, netlist, pin_marker, schematic_layout, schematic_audit, schematic_repair, structural_net_validate, structural_net_repair
- Layout/routing: placement, layout_route, routing, pcb_layout
- Validation/repair: validate, validate_repair, repair, connectivity_validate, connectivity_repair, power_net_repair, symbol_validate, llm_judge, design_review
- User-interaction: clarify, ask_board_config, ask_pcb_approval, ask_validation_help
- Modification: modify

**LLM vs deterministic:** LLM access is centralized in `utils.py` via `_call_llm` / `_call_llm_with_tools` with `_clean_json` parsing and stage contracts (`_check_stage_contract`). `dispatch_node` is purely deterministic (symbol fetch, s-expr parse) with feature-flagged enrichments (SynthesisGraph, live knowledge extraction). LLMs drive research/select/repair/judge; heavy instrumentation emits thought/tool_call/step events.

**Pipeline strengths:** clean shim, centralized LLM gateway with retry caps + contracts, freeze gates (architecture/component/netlist), loop detection, feature flags.
**Pipeline debt:** ~39 nodes with duplicated repair logic (six *repair* modules), flat optional TypedDict invites silent key drift (duplicate `review_suggestions`), actual edge wiring lives only in builder.py.

---

## 4. Geometry Engines

### Router (agent/routing/)
Layered: `api.py` (orchestrator `route_traces`) → `make_path.py` (candidate paths) → `astar.py` (fallback) → pure helpers (`geometry.py`, `collision.py`, `path_utils.py`, `constants.py`). Grid-snapped at **2.54 mm** via `_snap`. Obstacles are *schematic symbols* (rotated bboxes inflated by clearance + pin stub), not copper. Two-tier routing: **deterministic stub routing first** (pin-exit cardinal direction from KiCad angle, `PIN_STUB_LEN` step-out, then direct L/Z topologies, min-bend/min-collision pick), **A\* fallback** (4-neighbor grid, Manhattan heuristic, ±200 mm window, step cap). Connections sorted shortest-first; each path rejected on empty/non-orthogonal/over-length/collision>0/foreign-net-intersect → `dropped_pairs`. **No vias/layers** — single-layer schematic wire routing. `_prune_disconnected_net_islands` BFS-deletes non-main islands; `repair_placement_for_routing` can move satellite passives to recover drops. Editor shares one pin transform (`_absolute_pin_position`).

**Router weaknesses:** A* has no bend cost (bendy paths then straightened); O(n²) per connection (re-expands all prior traces each iteration); silent net dropping is the failure mode; in-place mutation of shared placement dicts; `legacy.py` + `PLACEMENT_ENGINE` flag = legacy paths still shipped.

### Placement (agent/placement/)
Placements are dicts `{ref_des, x, y, rotation, bbox, tier}`; tier −1 = satellite passives clustered around their parent IC. `community.py` + `graph.py` → connectivity-community detection groups components into blocks (`blocks_v2`), refined by simulated annealing (`sa_optimizer.py`/`annealing.py` + `perturbations.py` move set). Board-outline handling not verifiable from router side (A* window derives from endpoints only).

### Schematic layout (agent/schematic/)
Block-based symbol placer with scoring/optimization passes (`placement.py`, `optimizer.py`, `scoring.py`, `beautify.py`), wire generation (`wires.py`), label/power handling (`labels.py`), and a catalog/matcher/detector/expander set. Feeds the same wire dicts the router emits.

**Interchange format:** plain dicts — a netlist of `{source:"REF:PIN", target:"REF:PIN", net}`, a `pin_matrix` keyed `"REF:PIN"` → `{x,y,angle}`, and component placements. `route_traces` returns `(traces, dropped_pairs)`; the editor mutates the same wire dicts via `apply_schematic_edit`.

---

## 5. Backend (server/)

Flask + Flask-SocketIO monolith. `server.py` → `socketio.run(app, debug=True, port=5000, allow_unsafe_werkzeug=True)`. `server/__init__.py` loads .env, calls `config.ensure_proxy(15)`, builds singletons in `state.py` (`app`, `socketio`, `rag`, `design_lock`, `session_manager`), then imports routes + ws_handlers for registration side effects.

**HTTP routes (routes.py):**
- Static: `GET /`, `/static/<p>`, `/kicanvas/<p>`
- Component/RAG: `GET /api/search?q=` (k=5), `/api/pins?id_str=`, `/api/sexpr?id_str=`
- Netlist: `POST /api/generate_netlist` — LLM first (`_generate_netlist_llm`, regex-strips fences), falls back to `_generate_netlist_rules` (groups same-named pins)
- State: `POST /api/save_layout`, `POST /api/save_board_model`, `POST /api/import_pcb` (multipart upload)
- Export: `GET /api/export_sch`, `/api/export_pcb`, `/api/pcb_render_source`, `/api/pcb_enriched_board_model` (+ratsnest), `POST /api/ratsnest`, `GET|POST /api/circuit_json`
- Editing: `POST /api/apply_edits` (~300 lines) — PCB events (trace hints, component moves) + schematic events (add/delete wire, move component) via `apply_schematic_edit`, with per-event validation and applied/ignored/error tally.

**Concurrency:** thread-based. Global `design_lock` guards every mutation; deep copies taken before export so long generators don't hold the lock. Werkzeug threading mode (not eventlet/gevent).

**Sessions:** `_get_session_from_request` resolves `?session_id=` / `X-Session-ID` / `"default"` → `session_manager.get_or_create` → a DesignSession holding `last_design`/`wire_bender_layout`. Chat sessions (`CHAT_SESSIONS` in chat.py) are distinct from HTTP DesignSessions. `session_db.py` exists (persistence backend unverified). **MCP server** (`mcp_server.py`) exposes CircuitBot capabilities as MCP tools for the LLM agent.

**LLM infra (config.py):** `LLM_BASE_URL` default `http://127.0.0.1:4010/v1`, `LLM_MODEL` `opencode/deepseek-v4-flash-free`. `ensure_proxy` probes `/v1/models` (5 s cache), spawns `opencode serve --headless` from node_modules/.bin (hidden window on Windows) if down. `get_llm_client` returns a LangChain `ChatOpenAI` (dummy key). All model access funnels through this one endpoint.

**Backend weaknesses:** `allow_unsafe_werkzeug` + debug in the entry (not production-safe); **zero auth** — anyone reachable can read/write any session, session_id client-supplied; in-process memory only (no restart survival, single worker); single global lock serializes all edits; broad `except Exception` → 500 leaks tracebacks; unauthenticated file upload writes attacker content to disk before parsing.

---

## 6. Frontend (static/)

Server-rendered SPA shell — one `index.html` (782 lines), no router, tab-switched canvas surfaces. Plain browser globals (no ES modules); `scripts/build.js` concatenates ~21 files in fixed order via esbuild into `app.bundle.js` (+ lazy `app.bundle.3d.js`), injecting content-hash cache busters.

**Comms:** hybrid. SocketIO for agent events (`agent:thinking/log/component/layout_ready/done/pcb_ready/pcb_approval/board_config/clarify/validation_help/thought_stream/review_suggestion`) + user replies. REST for deterministic CRUD/export (`/api/sync_schematic_state` optimistic-lock revision sync, `/api/apply_edits`, `/api/save_layout`, `/api/export*`).

**UI surfaces:**
- Chat/co-pilot — suggestion chips, `/` palette, thought-stream accordion, pipeline-gate approval buttons, review-suggestion cards, `chat:resume` on reconnect.
- Pipeline panel (`pipeline-panel.js`) — phase rail + elapsed timer mirroring LangGraph execution.
- Schematic — PixiJS 7.4 canvas (`schematic_renderer.js`, 2,256 lines) over a `Schematic` model (`schematic.js`); full tool set (wire/bus/component/label/junction/no-connect/text/delete/image-marker), client-side smart-orthogonal wire routing, net-label mode, inline rename.
- PCB 2D — WebGL editor (`pcb_view/`), kicanvas-derived raw-WebGL2 painter triangulating KiCad primitives into batched buffers. Tools: Pan/Select/Route (45°/90°/free, H/V posture)/Via/Outline, per-layer visibility, ratsnest, width presets. `state.js` = `window.pcbState`; `events.js` (1,370 lines) input; `editor_webgl.js` (2,368 lines) controller.
- 3D — lazy Three.js r128 + OrbitControls + VRMLLoader (`pcb_viewer_3d/`): extruded board mesh, real `.wrl` models with cache + placeholder fallback, camera presets.
- tscircuit — `tscircuit-bridge.js` adapts the KiCad-centric model into tscircuit JSON and mounts `tscircuit-viewer.min.js`; an alternative modern viewport coexisting with the PixiJS path.

**Design system:** `astryx-tokens.css` (CSS custom props — copper/agent-amber, Syne/DM Sans/IBM Plex Mono) + `astryx-components.css` primitives; `style.css` (4,496 lines) monolith. Only partially adopted — inline styles remain.

**Frontend weaknesses:** triple/quad schematic renderer duplication (`schematic.js`, `schematic_renderer.js`, `renderer_legacy.js`, tscircuit bridge); concat-not-bundle (globals, load-order load-bearing, no tree-shaking); `app.js` 4,178-line god-file; CDN deps outside version control (old Three r128); no frontend tests.

---

## 7. Supporting Systems

### KiCad export (agent/kicad_export.py)
Hand-rolled S-expression emitter (no KiCad API). `generate_kicad_sch(design)` → `.kicad_sch v20231120`. Hardening: 1.27 mm grid snap, `_simplify_path` + `_orthogonalize` (no diagonals survive), segment caps (150 mm seg / 300 mm total), dedupe, wire endpoints snap to exact pin positions within half-grid, junction dots only at true T-junctions, power nets get 2.54 mm stubs + same-name `global_label`s with a direction-retry loop to avoid cross-net stub crossings, `no_connect` flags on unwired pins, and **EV001** raises `ExportValidationError` if any wire endpoint produced no segment.
- `sexpr_utils.py` — GRID/snap/pin_transform/pin_abs, paren-balance validator, `_parse_sexpr_to_ops` (resolves `extends` ≤5), `_extract_pins_from_ops` → pin matrix.
- `symbol_codec.py` — SchGen code-L1 lossless encoding (arXiv:2501.07774) + SHA-256 layout `fingerprint` + `compare_layout` diff for cross-run verification.
- `skidl_runner.py` — in-house SKiDL-*inspired* deterministic pre-ERC (GND/VCC shorts, multiple driven outputs, floating input/power pins). Not real SKiDL.
- `erc_runner.py` — shells `kicad-cli sch erc --format json --severity-all` (hardcoded KiCad 10.0 path under LOCALAPPDATA), classifies FIXABLE (pin_not_connected, unconnected_wire_endpoint, wire_dangling, power_pin_not_driven) vs non-fixable, keyed `ref:pin` for repair.

### tscircuit export
`tscircuit_export.py` + `tokn_converter.py` (+ top-level `tscircuit_data/` package) translate the design into tscircuit JSON so the browser viewer renders without KiCad; `data/tscircuit_cache` memoizes footprints.

### Component intelligence
`datasheet.py` fetch/parse → `knowledge_extractor.py` → `knowledge/` + `component_insight/` → `component_knowledge.py`. Selection: `library_registry.py` (symbol registry) → `sourcing.py` (JLC availability via `kicad_rag/jlcparts_db.py`) → `reranker.py` + `scoring/` → `component_substitution.py` for alternates. `pin_matcher.py` + `auto_pullup.py` encode design-rule knowledge.

### Deep search & RAG
`agent/deep_search/` + `nodes/deepresearch.py` — a research sub-agent, the pipeline's "go look it up on the web" escape hatch feeding research/datasheet nodes. `kicad_rag/` is a self-contained retrieval service (`retrieval.py`, `store.py`, `taxonomy.py`, `jlcparts_db.py`, `builder.py`, `client.py`) backed by `kicad_rag.db`/`circuitbot.sqlite` + vendored `kicad-symbols`, serving ranked symbol/part candidates to `select`/`connection_search` (fastembed local vectors).

### Benchmarks & tests
`agent/benchmarks/` (power_regulator, usb_uart) run by `benchmark_runner.py` → `benchmark_results/` measuring end-to-end generation quality. `tests/` + root `test_led_circuit.py`; CI in `.github/workflows/test.yml`. `test_e2e_result.json`/`test_export.kicad_sch` are committed run artifacts.

### Top-level dirs
- `Parser_Bidirectional/` — legacy/experimental KiCad parser worktrees.
- `pcb_design/` — **active** full PCB engine (board_model, pcb_export/import, pcbnew_runner/worker, placement, pour, ratsnest, router.py + router2.py — duplicate router = in-flight refactor).
- `website/` — legacy PCB-parse prototype.
- `tscircuit_data/` — active converter package. `data/` — runtime state (sessions.db, memory, evolution, tscircuit_cache).
- `kicad-footprints/`, `kicad-library-utils/`, `kicanvas/` — vendored libs.

---

## 8. Cross-Cutting Assessment

**Genuine strengths**
- Unusually hardened schematic emitter (grid discipline, orthogonalization, pin snapping, EV001 hard-fail, no_connect hygiene).
- Layered ERC: fast in-process deterministic checks + authoritative kicad-cli JSON ERC with fixable-error classification routed back into repair nodes — a real closed loop, not LLM self-check.
- Single pin-transform source of truth shared by router and editor; deterministic layout fingerprinting (code-L1) for reproducibility.
- Clean LLM seam (one proxy, graceful degradation) and a sensible SocketIO/REST split on the wire.
- Local, inspectable RAG + real JLC sourcing rather than a black-box vector service.

**Systemic weaknesses / risks**
- **Two parallel PCB worlds**: `pcb_design/` (Python, pcbnew automation) vs the tscircuit/WebGL path — and `router.py`/`router2.py`, `kicad-parser-temp`, `legacy.py` all signal unfinished migrations.
- **Duplication everywhere**: six repair node modules, triple/quad schematic renderers, duplicated GRID/snap helpers, duplicated pin-resolution logic in routing/api.py.
- **Scale ceilings**: single global `design_lock`; in-memory sessions; router O(n²) per connection; A* with no bend cost and a step cap that can silently drop nets.
- **Portability**: hardcoded Windows kicad-cli path (CI/Linux ERC silently returns None).
- **Security**: no auth anywhere, guessable session ids, unauthenticated file upload, dev server in debug.
- **Repo hygiene**: runtime artifacts (data/, scratch/, test_e2e_result.json, patch.diff, _f.py) committed alongside source; flat optional TypedDict as the only pipeline contract invites silent state drift.

**Biggest verification gaps** (things the analysis could not fully confirm): the exact graph edge wiring (lives only in `builder.py`), board-outline handling in placement, the internals of `schematic/` layout algorithms, and `session_db.py`/`mcp_server.py` internals.
