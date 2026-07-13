# Agent Features: Conversational Refinement + Design Review

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add conversational design refinement (modify existing designs via natural language) and proactive design review (agent suggests improvements after generation) to the CircuitBot agent.

**Architecture:** Two new capabilities added to the existing LangGraph pipeline: (1) A `modify_design` intent routed through a separate `modify_graph` that handles surgical edits to `LAST_DESIGN`, and (2) a `design_review` node appended to the main pipeline that emits suggestions as chat messages.

**Tech Stack:** Python, LangGraph StateGraph, Flask-SocketIO, LLM (DeepSeek via OpenAI-compatible API)

---

## Global Constraints

- Python 3.10+, no new dependencies (use existing langgraph, langchain-openai, flask-socketio)
- All LLM calls go through `agent/llm_utils.py:_call_llm()` with retry logic
- All WebSocket emissions go through `server/ws_emit_utils.py:ws_emit()`
- Thread safety: `LAST_DESIGN` access requires `design_lock` from `server/state.py`
- Frontend: vanilla JS, no build step, files in `static/`
- Test with `pytest tests/` — add tests for each new module

---

## File Map

| Action | File | Purpose |
|--------|------|---------|
| Modify | `agent/prompt_router.py` | Add `modify_design` intent + keyword fallbacks |
| Modify | `agent/state.py` | Add modification-related fields to AgentState |
| Modify | `agent/builder.py` | Add `modify_graph` + `design_review` node to main graph |
| Modify | `server/ws_handlers.py` | Route `modify_design` intent + handle review events |
| Create | `agent/nodes/modify.py` | Modification pipeline nodes |
| Create | `agent/nodes/design_review.py` | Design review node |
| Create | `tests/test_modify.py` | Tests for modification logic |
| Create | `tests/test_design_review.py` | Tests for review logic |
| Modify | `static/app.js` | Render review suggestion cards |
| Modify | `static/style.css` | Styles for review suggestion cards |

---

## Task 1: Add `modify_design` Intent to Prompt Router

**Covers:** Conversational refinement — intent classification

**Files:**
- Modify: `agent/prompt_router.py`
- Test: `tests/test_modify.py`

**Interfaces:**
- Consumes: `route_prompt(text)` function signature (unchanged)
- Produces: Returns `{"intent": "modify_design", "confidence": 0.85, "modification_type": "value_change", "target": "R1", "value": "10k", ...}`

- [ ] **Step 1: Write failing test for modify_design intent**

Create `tests/test_modify.py`:

```python
import pytest
from agent.prompt_router import route_prompt


def test_modify_value_change():
    result = route_prompt("Change R1 to 10k ohm")
    assert result["intent"] == "modify_design"
    assert result["confidence"] >= 0.7
    assert result.get("modification_type") == "value_change"


def test_modify_add_component():
    result = route_prompt("Add a 100nF bypass cap on VCC")
    assert result["intent"] == "modify_design"
    assert result["confidence"] >= 0.7
    assert result.get("modification_type") == "add_component"


def test_modify_remove_component():
    result = route_prompt("Remove R3 from the design")
    assert result["intent"] == "modify_design"
    assert result["confidence"] >= 0.7
    assert result.get("modification_type") == "remove_component"


def test_modify_not_design_pipeline():
    result = route_prompt("Change R1 to 10k")
    assert result["intent"] != "design_pipeline"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_modify.py -v`
Expected: FAIL — `modify_design` not in valid intents

- [ ] **Step 3: Add `modify_design` to prompt router**

In `agent/prompt_router.py`, modify the `PROMPT_ROUTER_SYSTEM` prompt to include:

```
6. "modify_design" — User wants to MODIFY an existing design. Examples:
   - "Change R1 to 10k"
   - "Swap U1 for MCP1700"
   - "Add a bypass cap on VCC"
   - "Remove R3"
   - "Connect LED to pin 13"
   - "Make the power traces wider"
   The response must include: modification_type (value_change, part_swap, add_component, remove_component, net_modify, reroute), target (component ref or net name), and value (new value/part/details).
```

Add to the valid intents validation list:
```python
if result.get("intent") not in ("design_pipeline", "add_component", "component_query", "help", "modify_design"):
    result["intent"] = "other"
```

- [ ] **Step 4: Add keyword fallback for modify_design**

In `KEYWORD_FALLBACKS`, add before the existing entries:

```python
(r"(?:change|set|update|swap|replace|modify|adjust)\s+.+", "modify_design", 0.7),
(r"(?:add|insert)\s+.+", "modify_design", 0.65),
(r"(?:remove|delete|drop)\s+.+", "modify_design", 0.7),
(r"(?:connect|route|wire)\s+.+\s+(?:to|from|instead)", "modify_design", 0.65),
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_modify.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add agent/prompt_router.py tests/test_modify.py
git commit -m "feat(agent): add modify_design intent to prompt router"
```

---

## Task 2: Add Modification Fields to AgentState

**Covers:** Conversational refinement — state management

**Files:**
- Modify: `agent/state.py`

**Interfaces:**
- Consumes: Existing `AgentState` TypedDict
- Produces: Extended `AgentState` with modification fields

- [ ] **Step 1: Add fields to AgentState**

In `agent/state.py`, add to the `AgentState` TypedDict:

```python
# Modification fields
modification_type: Optional[str]  # value_change, part_swap, add_component, remove_component, net_modify, reroute
modification_target: Optional[dict]  # {ref: "R1", field: "value"} or {net: "VCC"}
modification_value: Optional[dict]  # {"value": "10k"} or {"part_id": "C1234"}
original_design: Optional[dict]  # Snapshot of LAST_DESIGN before modification
```

- [ ] **Step 2: Commit**

```bash
git add agent/state.py
git commit -m "feat(agent): add modification fields to AgentState"
```

---

## Task 3: Create Modification Pipeline Nodes

**Covers:** Conversational refinement — core modification logic

**Files:**
- Create: `agent/nodes/modify.py`
- Test: `tests/test_modify.py`

**Interfaces:**
- Consumes: `AgentState` with `modification_type`, `modification_target`, `modification_value`, `original_design`
- Produces: Updated `board_model`, `selected_components`, `nets` in state

- [ ] **Step 1: Write failing tests**

Add to `tests/test_modify.py`:

```python
from agent.nodes.modify import classify_modification_node, apply_modification_node


def test_classify_value_change():
    state = {
        "prompt": "Change R1 to 10k ohm",
        "modification_type": None,
        "modification_target": None,
        "modification_value": None,
    }
    result = classify_modification_node(state)
    assert result["modification_type"] == "value_change"
    assert result["modification_target"]["ref"] == "R1"
    assert "10k" in str(result["modification_value"])


def test_apply_value_change():
    state = {
        "modification_type": "value_change",
        "modification_target": {"ref": "R1"},
        "modification_value": {"value": "10k"},
        "original_design": {
            "selected_components": [
                {"ref": "R1", "value": "4.7k", "footprint": "0402", "name": "Resistor"}
            ]
        },
        "selected_components": [
            {"ref": "R1", "value": "4.7k", "footprint": "0402", "name": "Resistor"}
        ],
    }
    result = apply_modification_node(state)
    comps = result["selected_components"]
    r1 = next(c for c in comps if c["ref"] == "R1")
    assert r1["value"] == "10k"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_modify.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Create modify.py with classification node**

Create `agent/nodes/modify.py`:

```python
"""Modification pipeline nodes for conversational design refinement."""

from agent.llm_utils import _call_llm

MODIFY_CLASSIFY_SYSTEM = """You are a circuit design modification classifier.
Given a user's modification request, classify it and extract the target and value.

Return JSON:
{
  "modification_type": "value_change" | "part_swap" | "add_component" | "remove_component" | "net_modify" | "reroute",
  "target": {"ref": "R1"} or {"net": "VCC"} or {"description": "..."},
  "value": {"value": "10k"} or {"part_id": "..."} or {"description": "..."}
}

Examples:
- "Change R1 to 10k" → {"modification_type": "value_change", "target": {"ref": "R1"}, "value": {"value": "10k"}}
- "Swap U1 for MCP1700" → {"modification_type": "part_swap", "target": {"ref": "U1"}, "value": {"part_id": "MCP1700"}}
- "Add a 100nF cap on VCC" → {"modification_type": "add_component", "target": {"net": "VCC"}, "value": {"description": "100nF bypass capacitor"}}
- "Remove R3" → {"modification_type": "remove_component", "target": {"ref": "R3"}, "value": {}}
- "Connect LED to pin 13" → {"modification_type": "net_modify", "target": {"description": "LED"}, "value": {"pin": "13"}}
- "Make power traces wider" → {"modification_type": "reroute", "target": {"net": "VCC"}, "value": {"trace_width": "0.5mm"}}
"""


def classify_modification_node(state: dict) -> dict:
    """Classify the modification request using LLM."""
    prompt = state.get("prompt", "")
    result = _call_llm(MODIFY_CLASSIFY_SYSTEM, prompt)
    if not result:
        return {
            "modification_type": "unknown",
            "modification_target": {},
            "modification_value": {},
        }
    return {
        "modification_type": result.get("modification_type", "unknown"),
        "modification_target": result.get("target", {}),
        "modification_value": result.get("value", {}),
    }


def apply_modification_node(state: dict) -> dict:
    """Apply the classified modification to the design."""
    mod_type = state.get("modification_type")
    target = state.get("modification_target", {})
    value = state.get("modification_value", {})
    components = list(state.get("selected_components", []))
    board_model = dict(state.get("original_design", {}).get("board_model", {}) or {})
    nets = list(state.get("original_design", {}).get("nets", []) or [])

    if mod_type == "value_change":
        ref = target.get("ref", "")
        new_val = value.get("value", "")
        for comp in components:
            if comp.get("ref") == ref:
                comp["value"] = new_val
                break
        # Also update board_model components
        for comp in board_model.get("components", []):
            if comp.get("ref") == ref:
                comp["value"] = new_val
                break

    elif mod_type == "remove_component":
        ref = target.get("ref", "")
        components = [c for c in components if c.get("ref") != ref]
        board_model["components"] = [
            c for c in board_model.get("components", []) if c.get("ref") != ref
        ]

    elif mod_type == "add_component":
        # Add a placeholder component — the research/select nodes will refine it
        desc = value.get("description", "")
        new_comp = {
            "ref": f"NEW_{len(components) + 1}",
            "name": desc,
            "value": desc,
            "footprint": "",
            "pending": True,
        }
        components.append(new_comp)

    return {
        "selected_components": components,
        "board_model": board_model,
        "nets": nets,
    }


def emit_modification_result(state: dict) -> dict:
    """Format the modification result for the user."""
    mod_type = state.get("modification_type", "unknown")
    target = state.get("modification_target", {})
    value = state.get("modification_value", {})

    ref = target.get("ref", "")
    net = target.get("net", "")
    target_str = ref or net or target.get("description", "design")

    if mod_type == "value_change":
        text = f"Changed {target_str} to {value.get('value', 'new value')}"
    elif mod_type == "part_swap":
        text = f"Swapped {target_str} to {value.get('part_id', 'new part')}"
    elif mod_type == "add_component":
        text = f"Added {value.get('description', 'component')}"
    elif mod_type == "remove_component":
        text = f"Removed {target_str}"
    elif mod_type == "net_modify":
        text = f"Modified connections for {target_str}"
    elif mod_type == "reroute":
        text = f"Updated routing for {target_str}"
    else:
        text = f"Modified {target_str}"

    return {"_stage": "modify_complete", "error": None}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_modify.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/nodes/modify.py tests/test_modify.py
git commit -m "feat(agent): add modification pipeline nodes"
```

---

## Task 4: Create Modify Graph in Builder

**Covers:** Conversational refinement — graph wiring

**Files:**
- Modify: `agent/builder.py`

**Interfaces:**
- Consumes: `classify_modification_node`, `apply_modification_node`, `emit_modification_result` from `agent/nodes/modify.py`
- Produces: `modify_graph` callable that can be invoked from ws_handlers

- [ ] **Step 1: Add modify_graph to builder.py**

In `agent/builder.py`, add at the end of the file:

```python
from agent.nodes.modify import (
    classify_modification_node,
    apply_modification_node,
    emit_modification_result,
)


def build_modify_graph():
    """Build a lightweight graph for design modifications."""
    graph = StateGraph(AgentState)
    graph.add_node("classify", classify_modification_node)
    graph.add_node("apply", apply_modification_node)
    graph.add_node("emit", emit_modification_result)
    graph.set_entry_point("classify")
    graph.add_edge("classify", "apply")
    graph.add_edge("apply", "emit")
    graph.add_edge("emit", END)
    return graph.compile()


modify_graph = build_modify_graph()
```

- [ ] **Step 2: Verify import works**

Run: `python -c "from agent.builder import modify_graph; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add agent/builder.py
git commit -m "feat(agent): add modify_graph to builder"
```

---

## Task 5: Route modify_design in ws_handlers

**Covers:** Conversational refinement — WebSocket routing

**Files:**
- Modify: `server/ws_handlers.py`

**Interfaces:**
- Consumes: `modify_graph` from `agent/builder`, `LAST_DESIGN` from `server/state`
- Produces: Emits `chat:reply` and `tscircuit:board-model-updated` events

- [ ] **Step 1: Add modify_design routing**

In `server/ws_handlers.py`, in `handle_chat_message()`, add after the `add_component` block (around line 140):

```python
    elif intent == "modify_design" and confidence >= 0.7:
        if not LAST_DESIGN:
            ws_emit("chat:reply", {"text": "No design to modify. Create a design first."}, room=sid)
            return
        ws_emit("agent:log", {"message": f"Modifying design: {text}"}, room=sid)
        socketio.start_background_task(
            _run_modify, text, intent_data, sid, session_id
        )
        return
```

Add the `_run_modify` function:

```python
def _run_modify(text, intent_data, sid, session_id):
    """Run the modify pipeline in a background task."""
    from agent.builder import modify_graph
    from server.state import LAST_DESIGN, design_lock, socketio

    with design_lock:
        current_design = dict(LAST_DESIGN)

    initial_state = {
        "prompt": text,
        "original_design": current_design,
        "selected_components": current_design.get("selected_components", []),
        "board_model": current_design.get("board_model"),
        "nets": current_design.get("nets", []),
        "modification_type": intent_data.get("modification_type"),
        "modification_target": intent_data.get("target"),
        "modification_value": intent_data.get("value"),
    }

    try:
        result = modify_graph.invoke(initial_state)
        with design_lock:
            LAST_DESIGN["selected_components"] = result.get("selected_components", [])
            if result.get("board_model"):
                LAST_DESIGN["board_model"] = result["board_model"]
            if result.get("nets"):
                LAST_DESIGN["nets"] = result["nets"]

        mod_type = result.get("modification_type", "unknown")
        target = result.get("modification_target", {})
        ref = target.get("ref", "")
        value = result.get("modification_value", {})

        if mod_type == "value_change":
            reply = f"Changed {ref} to {value.get('value', 'new value')}"
        elif mod_type == "remove_component":
            reply = f"Removed {ref}"
        elif mod_type == "add_component":
            reply = f"Added {value.get('description', 'component')}"
        else:
            reply = f"Design modified successfully"

        ws_emit("chat:reply", {"text": reply}, room=sid)
        ws_emit("tscircuit:board-model-updated", {"board_model": LAST_DESIGN.get("board_model")}, room=sid)
    except Exception as e:
        ws_emit("chat:reply", {"text": f"Modification failed: {str(e)}"}, room=sid)
```

- [ ] **Step 2: Verify server starts**

Run: `python -c "from server.ws_handlers import handle_chat_message; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add server/ws_handlers.py
git commit -m "feat(server): route modify_design intent through modify_graph"
```

---

## Task 6: Create Design Review Node

**Covers:** Design review — core review logic

**Files:**
- Create: `agent/nodes/design_review.py`
- Test: `tests/test_design_review.py`

**Interfaces:**
- Consumes: `AgentState` with `selected_components`, `board_model`, `nets`
- Produces: List of review suggestions in state

- [ ] **Step 1: Write failing test**

Create `tests/test_design_review.py`:

```python
from agent.nodes.design_review import design_review_node


def test_design_review_returns_suggestions():
    state = {
        "selected_components": [
            {"ref": "U1", "name": "ESP32", "value": "ESP32-WROOM-32", "footprint": "QFN-48"},
            {"ref": "R1", "name": "Resistor", "value": "10k", "footprint": "0402"},
        ],
        "board_model": {"components": [], "traces": [], "nets": []},
        "nets": [{"name": "VCC", "pins": ["U1:3"]}, {"name": "GND", "pins": ["U1:1"]}],
        "prompt": "Design a simple LED circuit with ESP32",
    }
    result = design_review_node(state)
    assert "review_suggestions" in result
    assert isinstance(result["review_suggestions"], list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_design_review.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Create design_review.py**

Create `agent/nodes/design_review.py`:

```python
"""Design review node — proactive suggestions after design completion."""

from agent.llm_utils import _call_llm

REVIEW_SYSTEM = """You are a senior hardware engineer reviewing a circuit design.
Analyze the design and suggest improvements. Focus on:

1. POWER: Missing bypass/decoupling capacitors, power budget issues
2. SIGNAL: Missing pull-ups/pull-downs, signal integrity
3. PROTECTION: ESD protection, reverse polarity, current limiting
4. COST: Part consolidation, cheaper alternatives
5. LAYOUT: Component placement hints, trace routing suggestions

Return JSON:
{
  "suggestions": [
    {
      "category": "power" | "signal" | "protection" | "cost" | "layout",
      "severity": "high" | "medium" | "low",
      "description": "Clear description of the issue",
      "suggestion": "Actionable suggestion to fix it",
      "target": {"ref": "U1"} or {"net": "VCC"} or null
    }
  ]
}

Be concise. Only suggest issues that matter. Max 5 suggestions.
If the design looks good, return {"suggestions": []}.
"""


def design_review_node(state: dict) -> dict:
    """Run design review and generate suggestions."""
    components = state.get("selected_components", [])
    nets = state.get("nets", [])
    prompt = state.get("prompt", "")

    design_context = f"Design intent: {prompt}\n\nComponents:\n"
    for comp in components:
        design_context += f"- {comp.get('ref', '?')}: {comp.get('name', '?')} ({comp.get('value', '?')})\n"

    design_context += "\nNets:\n"
    for net in nets:
        pins = ", ".join(net.get("pins", []))
        design_context += f"- {net.get('name', '?')}: {pins}\n"

    result = _call_llm(REVIEW_SYSTEM, design_context)
    suggestions = []
    if result and isinstance(result, dict):
        suggestions = result.get("suggestions", [])

    return {"review_suggestions": suggestions}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_design_review.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent/nodes/design_review.py tests/test_design_review.py
git commit -m "feat(agent): add design review node"
```

---

## Task 7: Add Review Node to Main Pipeline

**Covers:** Design review — pipeline integration

**Files:**
- Modify: `agent/builder.py`

**Interfaces:**
- Consumes: `design_review_node` from `agent/nodes/design_review.py`
- Produces: Review suggestions emitted via WebSocket after design completion

- [ ] **Step 1: Add review node to graph**

In `agent/builder.py`, add the import:

```python
from agent.nodes.design_review import design_review_node
```

Add the node to the graph:

```python
graph.add_node("design_review", design_review_node)
```

Change the edge from `pcb_layout` to `END` to go through review first:

```python
# Replace: graph.add_edge("pcb_layout", END)
# With:
graph.add_edge("pcb_layout", "design_review")
graph.add_edge("design_review", END)
```

- [ ] **Step 2: Add review emission in agent_runner.py**

In `server/agent_runner.py`, after the agent completes and before emitting `agent:pcb_ready`, add:

```python
    # Emit design review suggestions
    review_suggestions = result.get("review_suggestions", [])
    if review_suggestions:
        for suggestion in review_suggestions:
            ws_emit("agent:review_suggestion", suggestion, room=sid)
        ws_emit("agent:review_complete", {"count": len(review_suggestions)}, room=sid)
```

- [ ] **Step 3: Verify import works**

Run: `python -c "from agent.builder import agent_graph; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add agent/builder.py server/agent_runner.py
git commit -m "feat(agent): integrate design review into main pipeline"
```

---

## Task 8: Frontend Review Suggestion Rendering

**Covers:** Design review — frontend display

**Files:**
- Modify: `static/app.js`
- Modify: `static/style.css`

**Interfaces:**
- Consumes: `agent:review_suggestion` and `agent:review_complete` WebSocket events
- Produces: Styled suggestion cards in the conversation area

- [ ] **Step 1: Add CSS for review cards**

In `static/style.css`, add after the proposal card styles:

```css
/* Review Suggestion Cards */
.review-card {
    background: linear-gradient(135deg, var(--surface-2), var(--surface-1));
    border: 1px solid var(--border-accent);
    border-radius: var(--radius-md);
    padding: 12px 14px;
    margin: 8px 0;
    max-width: 90%;
    font-family: var(--font-ui);
}
.review-card-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
}
.review-category {
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    padding: 2px 6px;
    border-radius: 4px;
    background: var(--green-dim);
    color: var(--green);
}
.review-severity-high { background: rgba(255, 68, 68, 0.1); color: #ff4444; }
.review-severity-medium { background: rgba(255, 170, 0, 0.1); color: #ffaa00; }
.review-severity-low { background: var(--green-dim); color: var(--green); }
.review-description {
    font-size: 12px;
    color: var(--text-main);
    line-height: 1.5;
    margin-bottom: 6px;
}
.review-suggestion {
    font-size: 11px;
    color: var(--text-dim);
    font-style: italic;
    margin-bottom: 8px;
}
.review-actions {
    display: flex;
    gap: 8px;
}
.review-actions button {
    padding: 4px 10px;
    border-radius: var(--radius-sm);
    font-size: 11px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s ease;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text-dim);
}
.review-actions .btn-apply {
    border-color: rgba(0, 255, 136, 0.2);
    color: var(--green);
}
.review-actions .btn-apply:hover {
    background: var(--green-dim);
}
.review-actions .btn-dismiss:hover {
    background: var(--surface-2);
    color: var(--text-main);
}
.review-summary {
    font-size: 11px;
    color: var(--text-dim);
    padding: 8px 0;
    border-top: 1px solid var(--border);
    margin-top: 8px;
}
```

- [ ] **Step 2: Add WebSocket handlers in app.js**

In `static/app.js`, in the `connectSocket()` function, add:

```javascript
        socket.on('agent:review_suggestion', (data) => {
            const card = document.createElement('div');
            card.className = 'review-card';

            const category = data.category || 'general';
            const severity = data.severity || 'low';

            card.innerHTML = `
                <div class="review-card-header">
                    <span class="review-category review-severity-${severity}">${category}</span>
                </div>
                <div class="review-description">${_escapeHtml(data.description || '')}</div>
                <div class="review-suggestion">${_escapeHtml(data.suggestion || '')}</div>
                <div class="review-actions">
                    <button class="btn-apply" data-target='${JSON.stringify(data.target || {})}' data-suggestion='${_escapeHtml(data.suggestion || '')}'>Apply</button>
                    <button class="btn-dismiss">Dismiss</button>
                </div>
            `;

            card.querySelector('.btn-dismiss').addEventListener('click', () => card.remove());
            card.querySelector('.btn-apply').addEventListener('click', () => {
                const suggestion = card.querySelector('.btn-apply').dataset.suggestion;
                agentPrompt.value = suggestion;
                agentBtn.click();
                card.remove();
            });

            agentConversation.appendChild(card);
            agentConversation.scrollTop = agentConversation.scrollHeight;
        });

        socket.on('agent:review_complete', (data) => {
            const summary = document.createElement('div');
            summary.className = 'review-summary';
            summary.textContent = `Review complete: ${data.count || 0} suggestion(s)`;
            agentConversation.appendChild(summary);
            agentConversation.scrollTop = agentConversation.scrollHeight;
        });
```

- [ ] **Step 3: Verify no syntax errors**

Open browser console, check for JS errors.

- [ ] **Step 4: Commit**

```bash
git add static/app.js static/style.css
git commit -m "feat(ui): add review suggestion card rendering"
```

---

## Task 9: End-to-End Test

**Covers:** All — integration verification

**Files:** None (manual testing)

- [ ] **Step 1: Start the server**

Run: `python server.py`

- [ ] **Step 2: Test conversational refinement**

1. Open browser to `http://localhost:5000`
2. Type: "design a 3.3V USB power supply with LED indicator"
3. Wait for design to complete
4. Type: "Change R1 to 10k"
5. Verify: Agent responds with confirmation, board model updates
6. Type: "Remove the LED"
7. Verify: Agent removes LED from design

- [ ] **Step 3: Test design review**

1. After a design completes, check the chat for review suggestion cards
2. Verify: Cards show category, description, suggestion
3. Click "Apply" on a suggestion
4. Verify: The suggestion text is sent as a new modification
5. Click "Dismiss" on a suggestion
6. Verify: The card is removed

- [ ] **Step 4: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests pass including new test_modify.py and test_design_review.py
