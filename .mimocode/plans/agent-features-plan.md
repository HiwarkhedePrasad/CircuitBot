# CircuitBot Agent Features: Conversational Refinement + Design Review

## Goal
Add two capabilities to the CircuitBot agent:
1. **Conversational Design Refinement** — Users can modify existing designs via natural language ("change R1 to 10k", "swap the regulator", "add a bypass cap")
2. **Design Review** — After generation, the agent proactively suggests improvements and catches issues

## Scope
- Skip: simulation, manufacturing output, version control, multi-board, template library, learning from corrections
- Focus: conversational refinement + design review only

---

## Feature 1: Conversational Design Refinement

### What Changes
- Add `modify_design` intent to the prompt router
- Create a new `modify_node` in the LangGraph pipeline that handles surgical edits
- Wire it through the chat message handler
- Support: change component value, swap part, add component, remove component, modify net, reroute trace

### Files to Modify

**`agent/prompt_router.py`** — Add `modify_design` intent
- Add intent to `PROMPT_ROUTER_SYSTEM` prompt with examples
- Add to validation check on line 101
- Add keyword fallback regex patterns
- Add extraction of `modification_type` and `target` in the LLM response

**`server/ws_handlers.py`** — Route `modify_design` intent
- Add new branch after `add_component` check (around line 140)
- Call a new `handle_modify_design()` function
- This function loads the current `LAST_DESIGN`, builds an agent state from it, and invokes a `modify_graph` (separate from the main design graph)

**`agent/builder.py`** — Add `modify_graph` (separate graph for modifications)
- Simpler graph: `classify_modification -> apply_modification -> validate_change -> emit_result`
- Reuses existing nodes where possible (validate, netlist, placement, routing)

**`agent/nodes/modify.py`** — New file: modification handler
- `classify_modification_node`: LLM classifies the modification type (value_change, part_swap, add_component, remove_component, net_modify, reroute)
- `apply_modification_node`: Applies the change to `LAST_DESIGN.board_model` based on classification
- `validate_change_node`: Re-validates the affected area
- `emit_change_node`: Emits the updated board model to the client

**`agent/state.py`** — Add modification-related fields
- `modification_type: Optional[str]` — what kind of modification
- `modification_target: Optional[dict]` — what to modify (component ref, net name, etc.)
- `modification_value: Optional[dict]` — new value/part
- `original_design: Optional[dict]` — snapshot before modification

### Modification Types to Support

| Type | Example | Action |
|------|---------|--------|
| `value_change` | "Change R1 to 10k" | Update component value in board model |
| `part_swap` | "Swap U1 for MCP1700" | Search RAG for new part, replace in board model |
| `add_component` | "Add a 100nF bypass cap on VCC" | RAG search + add to board model + re-route |
| `remove_component` | "Remove R3" | Remove from board model + re-route |
| `net_modify` | "Connect LED to pin 13 instead" | Update netlist + re-route |
| `reroute` | "Route the power traces wider" | Update trace widths + re-route |

### Data Flow
```
User: "Change R1 to 10k ohm"
  → ws_handlers: route_prompt() → intent="modify_design", confidence=0.85
  → handle_modify_design():
      1. Load LAST_DESIGN into AgentState
      2. Set state.modification_type = "value_change"
      3. Set state.modification_target = {ref: "R1", field: "value"}
      4. Set state.modification_value = {"value": "10k"}
      5. Invoke modify_graph
  → modify_graph:
      classify_modification → apply_modification → validate_change → emit
  → emit: socket.emit('chat:reply', {text: "Changed R1 to 10kΩ"})
         + socket.emit('tscircuit:board-model-updated', {board_model})
```

---

## Feature 2: Design Review

### What Changes
- After the main pipeline completes (after `pcb_layout`), run a review pass
- The review analyzes the design and emits suggestions as chat messages
- Each suggestion is actionable — user can approve or dismiss

### Files to Modify

**`agent/nodes/design_review.py`** — New file: review node
- `design_review_node`: LLM analyzes the completed design
- Emits suggestions via WebSocket as `agent:conversation` events
- Each suggestion has: category, description, severity, suggested action

**`agent/builder.py`** — Add review node to pipeline
- Insert `design_review` between `pcb_layout` and `END`
- It runs AFTER the design is complete, as a final quality pass

**`server/ws_handlers.py`** — Handle review suggestions
- Add a new WS event `agent:review_suggestion` for each suggestion
- Add `agent:review_complete` when review finishes
- Frontend displays suggestions in the chat as actionable cards

**`static/app.js`** — Render review suggestions
- Handle `agent:review_suggestion` events
- Display as styled cards with approve/dismiss buttons
- Approved suggestions trigger a `modify_design` call

### Review Categories

| Category | What It Checks | Example |
|----------|---------------|---------|
| **Power** | Bypass caps, decoupling, power budget | "Add 100nF bypass cap on VCC pin of U1" |
| **Signal** | Pull-ups/pull-downs, termination | "Add 10k pull-up on RESET line" |
| **Protection** | ESD, reverse polarity, current limiting | "Add TVS diode on USB data lines" |
| **Thermal** | Power dissipation, copper pour | "R7 dissipates 0.5W — consider 1206 package" |
| **Cost** | Part alternatives, consolidation | "R2 and R4 are both 4.7k — use same part for both" |
| **Layout** | Component placement, trace routing | "Place C1 closer to U1 VCC pin" |

### Data Flow
```
Pipeline completes: pcb_layout → design_review
  → design_review_node:
      1. Reads board_model, netlist, selected_components
      2. LLM prompt: "Review this design for issues..."
      3. Parses LLM response into structured suggestions
      4. Emits each suggestion via socketio.emit('agent:review_suggestion', ...)
      5. Emits 'agent:review_complete' when done
  → Frontend:
      Renders each suggestion as a card in the conversation
      User clicks "Apply" → triggers modify_design flow
      User clicks "Dismiss" → removes the card
```

---

## Implementation Order

### Phase 1: Conversational Refinement (Core)
1. Add `modify_design` intent to `agent/prompt_router.py`
2. Create `agent/nodes/modify.py` with classify + apply + validate nodes
3. Create `modify_graph` in `agent/builder.py`
4. Add routing in `server/ws_handlers.py`
5. Add modification fields to `agent/state.py`
6. Test with basic modifications (value change, add component)

### Phase 2: Design Review
7. Create `agent/nodes/design_review.py`
8. Add review node to main pipeline in `agent/builder.py`
9. Add WS event handling in `server/ws_handlers.py`
10. Add frontend rendering in `static/app.js` + `static/style.css`

### Phase 3: Polish
11. Add more modification types (part swap, remove, net modify)
12. Improve review suggestion quality
13. Add undo support for modifications
14. Test end-to-end flows

---

## Verification

1. Start server: `python server.py`
2. Open browser, send "design a 3.3V USB power supply"
3. Wait for design to complete
4. Test modification: "Change R1 to 10k" → should update the design
5. Test modification: "Add a 100nF cap on VCC" → should add component
6. After design completes, review suggestions should appear in chat
7. Click "Apply" on a suggestion → should modify the design
8. Verify PCB view updates with changes
9. Verify undo works (Escape key or explicit undo command)
