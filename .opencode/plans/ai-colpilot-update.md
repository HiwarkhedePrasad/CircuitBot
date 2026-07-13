# Plan: AI Design Co-Pilot → Agentic Thought Stream

## Goal
Replace the green/circuit-board chat with a **live Agentic Thought Stream** — every backend action (web search, component lookup, LLM calls, routing, placement) renders as structured cards with collapsible tool call accordions, vertical timeline, raw search results, and real-time status badges.

**Reference pattern:** Agentic Thought Stream / Chain-of-Thought Execution Log
- Thought narrative text ("I'll analyze your circuit...")
- Collapsible tool call accordions (chevron `▶` to expose details + raw data)
- Vertical stepper/timeline with connecting lines for sub-steps
- Status badges: pending (○) → running (⟳) → completed (✓) → failed (✕)
- **Raw web search results** visible inside accordion bodies (URLs, snippets, fetched content)

---

## Part 1 — Backend: Structured Event Emission

### 1a. New Event Format: `agent:thought_stream`

A unified event type carrying structured data for the frontend. Every backend node emits these instead of (or in addition to) raw `agent:thinking` / `agent:log`.

```python
{
    "type": "thought",          # "thought" | "tool_call" | "step"
    "id": "thought_001",        # unique event ID
    "content": "...",           # narrative text or tool title
    "status": "running",        # "pending" | "running" | "completed" | "failed"
    "details": "...",           # expandable body content (JSON, search results, code)
    "parent_id": None,          # for steps: links to parent tool_call id
}
```

### 1b. Add emit helpers in `agent/emit_utils.py`

```python
def emit_thought(config, content, id=None):
    """Emit a thought narrative card."""
    _emit(config, "agent:thought_stream", {
        "type": "thought",
        "id": id or f"thought_{uuid4().hex[:8]}",
        "content": content,
        "status": "completed",
    })

def emit_tool_begin(config, id, title):
    """Emit a tool call accordion in running state."""
    _emit(config, "agent:thought_stream", {
        "type": "tool_call",
        "id": id,
        "content": title,
        "status": "running",
    })

def emit_tool_end(config, id, summary, details=None, status="completed"):
    """Mark a tool call as completed/failed with optional expandable details."""
    _emit(config, "agent:thought_stream", {
        "type": "tool_call",
        "id": id,
        "content": summary,
        "status": status,
        "details": details,
    })

def emit_step(config, parent_id, label, status="pending"):
    """Emit a nested step inside a tool call's vertical timeline."""
    _emit(config, "agent:thought_stream", {
        "type": "step",
        "id": f"{parent_id}_step_{uuid4().hex[:4]}",
        "content": label,
        "status": status,
        "parent_id": parent_id,
    })
```

### 1c. Refactor `deep_search()` to emit intermediate events (`agent/deep_search/agent.py`)

Currently `deep_search(query)` returns only the final LLM synthesis. Change it to:

```python
def deep_search(query, config=None):
    """Search + synthesize, optionally emitting intermediate events."""

    if config:
        emit_tool_begin(config, "web_search", f"Searching the web for: {query}")
    
    # Step 1: raw search
    if config:
        emit_step(config, "web_search", "Querying TinyFish search API", "running")
    
    raw_results = tinyfish_search(query, max_results=5)
    
    if config:
        emit_step(config, "web_search", f"Found {count_results(raw_results)} results", "completed")
        emit_step(config, "web_search", "Fetching page content for top results", "running")
    
    # Step 2: optionally fetch top results
    urls = extract_urls(raw_results)
    fetched = []
    for i, url in enumerate(urls[:2]):
        content = tinyfish_fetch(url)
        fetched.append(content)
        if config:
            emit_step(config, "web_search", f"Fetched: {url[:60]}...", "completed")
    
    if config:
        emit_tool_end(config, "web_search",
            summary="Web search complete with raw results",
            details=f"RAW SEARCH RESULTS:\n{raw_results}\n\nFETCHED CONTENT:\n" + "\n---\n".join(fetched),
        )
        emit_thought(config, "Synthesizing search results into structured summary...")
    
    # Step 3: LLM synthesis (existing logic)
    prompt = f"Query: {query}\n\nSearch Results:\n{raw_results}\n\nFetched Content:\n" + "\n".join(fetched)
    response = _model.invoke([
        {"role": "system", "content": "You are a precise electronics research assistant."},
        {"role": "user", "content": prompt},
    ])
    
    if config:
        emit_thought(config, response.content)
    
    return response.content
```

Set `config=None` as default so callers that don't have a config (tests, imports) still work — only the pipeline nodes pass `config` to enable live streaming.

### 1d. Update DeepSearch call sites to pass `config`

Three nodes call `deep_search()`:

| File | Line | Change |
|---|---|---|
| `agent/nodes/research.py` | ~82 | Change `deep_search(...)` → `deep_search(..., config=config)` |
| `agent/nodes/datasheet_search.py` | ~33 | Same |
| `agent/nodes/connection_search.py` | ~93 | Same |

### 1e. Refactor remaining pipeline nodes to emit structured events

For each major node, replace scattered `_emit(config, "agent:thinking", ...)` + `_emit(config, "agent:log", ...)` with structured `emit_thought()`, `emit_tool_begin/end()`, `emit_step()` calls.

**Priority order (highest impact):**

| Node | File | Events to emit |
|---|---|---|
| `research_node` | `research.py` | `thought` per subsystem, `tool_call` for RAG search, `tool_call` for web search |
| `select_node` | `select.py` | `thought` for LLM scoring, `tool_call` per subsystem candidate evaluation |
| `datasheet_search_node` | `datasheet_search.py` | Already covered by deep_search refactor |
| `connection_search_node` | `connection_search.py` | Same |
| `validate_node` | `validate.py` | `tool_call` for validation checks, `step` per check |
| `dispatch_node` | `dispatch.py` | `tool_call` per component placement, `step` per symbol load |
| `netlist_node` | `netlist.py` | `thought` for netlist generation |
| `placement_node` | `placement.py` | `tool_call` for auto-placement, `step` per component |
| `routing_node` | `routing.py` | `tool_call` for wire routing, `step` per net |
| `schematic_audit_node` | `schematic_audit.py` | `tool_call` for ERC audit, `step` per check |
| `pcb_layout_node` | `pcb_layout.py` | `tool_call` for PCB placement, `step` per component |
| `design_review_node` | `design_review.py` | `thought` per review suggestion |

### 1f. Keep backward compatibility

Existing `agent:thinking`, `agent:log`, `agent:conversation` events remain emitted so the old frontend code paths still work. The new `agent:thought_stream` event is additive. After the frontend is fully migrated, old events can be removed.

---

## Part 2 — Frontend: Agentic Thought Stream Rendering

### 2a. Color Palette Overhaul (`static/style.css`)

Replace green/black Flux aesthetic with ChatGPT-dark indigo.

| Variable | Old | New |
|---|---|---|
| `--bg-main` | `#000000` | `#0f0f13` |
| `--surface-1` | `#0a0a0a` | `#1e1e2e` |
| `--surface-2` | `#111111` | `#252535` |
| `--surface-3` | `#181818` | `#2a2a3c` |
| `--accent` | `#00ff88` (green) | `#6366f1` (indigo) |
| `--accent-dim` | `rgba(0,255,136,0.08)` | `rgba(99,102,241,0.08)` |
| `--text-main` | `#a0a0a0` | `#d1d5db` |
| `--text-bright` | `#ffffff` | `#f3f4f6` |
| `--text-dim` | `#555555` | `#6b7280` |
| `--success` | `#00ff88` | `#22c55e` |
| `--danger` | `#ff4444` | `#ef4444` |

Add new thought-stream specific variables:
- `--timeline-line`: `rgba(255,255,255,0.08)`
- `--badge-pending`: `#6b7280`
- `--badge-running`: `#6366f1`
- `--badge-complete`: `#22c55e`
- `--badge-failed`: `#ef4444`
- `--accordion-bg`: `#1a1a2a`
- `--details-bg`: `rgba(0,0,0,0.3)`

### 2b. HTML Restructure (`static/index.html`)

Replace the right-panel agent workspace:

```html
<div class="right-panel">
  <div class="panel-header">
    <span class="panel-title">AI Design Co-Pilot</span>
    <span class="agent-status-text" id="agentStatus">Ready</span>
  </div>
  <div class="agent-workspace">
    <div class="agent-conversation" id="agentConversation">
      <!-- Empty state -->
      <div class="conv-empty">
        <div class="agent-avatar-lg">
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
            <path d="M10 2L12.5 7.5L18 10L12.5 12.5L10 18L7.5 12.5L2 10L7.5 7.5L10 2Z" fill="currentColor" opacity="0.6"/>
          </svg>
        </div>
        <h3>How can I help you?</h3>
        <p>Ask me to design a circuit, find components, or generate a PCB layout.</p>
        <div class="suggestion-chips" id="suggestionChips">
          <button class="chip" data-prompt="Design an amplifier">Design an amplifier</button>
          <button class="chip" data-prompt="Find an I2C sensor">Find an I2C sensor</button>
          <button class="chip" data-prompt="Create a power supply">Create a power supply</button>
          <button class="chip" data-prompt="Route a 2-layer PCB">Route a 2-layer PCB</button>
        </div>
      </div>
      <!-- Events rendered dynamically -->
    </div>
    <div class="agent-composer" id="agentComposer">
      <textarea id="agentPrompt" placeholder="Ask the AI Design Co-Pilot..." rows="1"></textarea>
      <button class="send-btn" id="agentBtn">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
          <path d="M1 8L15 1L10 15L7 9L1 8Z" fill="currentColor"/>
        </svg>
      </button>
    </div>
  </div>
</div>
```

### 2c. Socket.IO Listener — New `agent:thought_stream` handler (`static/app.js`)

Add a new socket listener:

```javascript
socket.on('agent:thought_stream', (data) => {
    handleThoughtStreamEvent(data);
});
```

The `handleThoughtStreamEvent()` function maps event types to DOM renderers:

| `data.type` | Action |
|---|---|
| `"thought"` | Append `.conv-thought` card to the conversation |
| `"tool_call"` + `status:"running"` | Append `.conv-tool-call` accordion with spinner badge |
| `"tool_call"` + `status:"completed"` | Update existing card: checkmark badge, set body content from `details`, make collapsible |
| `"tool_call"` + `status:"failed"` | Update existing card: X badge, show error in body |
| `"step"` + `status:"pending"` | Find parent `.conv-tool-call`, append `.step.pending` to its timeline |
| `"step"` + `status:"running"` | Update existing step in timeline to running state |
| `"step"` + `status:"completed"` | Update existing step to completed state |

### 2d. Thought Card Rendering

```javascript
function renderThought(data) {
    const div = document.createElement('div');
    div.className = 'conv-thought';
    div.innerHTML = `
        <div class="conv-thought-icon">
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
                <circle cx="7" cy="7" r="6" stroke="currentColor" stroke-width="1.5" opacity="0.4"/>
                <path d="M7 4V8M7 9.5V10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
            </svg>
        </div>
        <div class="conv-thought-content">${escapeHtml(data.content)}</div>
    `;
    agentConversation.appendChild(div);
    scrollToBottom();
}
```

### 2e. Tool Call Accordion Rendering

```javascript
let toolCallCards = {};

function renderToolCall(data) {
    const { id, content, status, details } = data;
    
    // If card already exists, update it
    if (toolCallCards[id]) {
        updateToolCall(id, status, details);
        return;
    }
    
    // Create new card
    const card = document.createElement('div');
    card.className = 'conv-tool-call';
    card.dataset.toolId = id;
    
    const header = document.createElement('div');
    header.className = 'tool-call-header';
    header.innerHTML = `
        <span class="tool-call-chevron">▶</span>
        <span class="tool-call-badge ${status}">${getBadgeIcon(status)}</span>
        <span class="tool-call-title">${escapeHtml(content)}</span>
    `;
    card.appendChild(header);
    
    // Body (hidden by default, revealed on chevron click)
    const body = document.createElement('div');
    body.className = 'tool-call-body';
    if (details) {
        const pre = document.createElement('pre');
        pre.className = 'tool-call-details';
        pre.textContent = details;
        body.appendChild(pre);
    }
    // Steps container for nested steps
    const steps = document.createElement('div');
    steps.className = 'tool-call-steps';
    body.appendChild(steps);
    card.appendChild(body);
    
    // Click to toggle
    header.addEventListener('click', () => {
        const isOpen = body.classList.toggle('open');
        header.querySelector('.tool-call-chevron').classList.toggle('open', isOpen);
    });
    
    agentConversation.appendChild(card);
    toolCallCards[id] = card;
    scrollToBottom();
}
```

### 2f. Step Rendering (inside tool call timeline)

```javascript
function renderStep(data) {
    const { parent_id, content, status } = data;
    const parent = document.querySelector(`[data-tool-id="${parent_id}"]`);
    if (!parent) return;
    
    const stepsContainer = parent.querySelector('.tool-call-steps');
    if (!stepsContainer) return;
    
    // Check if this step already exists
    const existing = stepsContainer.querySelector(`[data-step-label="${escapeAttr(content)}"]`);
    if (existing) {
        existing.className = `step ${status}`;
        existing.querySelector('.step-marker').textContent = getStepIcon(status);
        return;
    }
    
    const step = document.createElement('div');
    step.className = `step ${status}`;
    step.dataset.stepLabel = content;
    step.innerHTML = `
        <div class="step-marker">${getStepIcon(status)}</div>
        <div class="step-label">${escapeHtml(content)}</div>
    `;
    stepsContainer.appendChild(step);
    scrollToBottom();
}
```

### 2g. Accordion Chevron + Expandable Details

The `.tool-call-details` pre block renders raw data (JSON, search results, file contents) that users can inspect by clicking the chevron. This is where **raw web search results** appear:

```
RAW SEARCH RESULTS:
• BME280 Datasheet - Bosch Sensortec
  Precision humidity sensor with ±3% accuracy
  URL: https://www.bosch-sensortec.com/products/environmental-sensors/...

• Adafruit BME280 Breakout
  I2C address: 0x76 or 0x77 (configurable via SDO pin)
  URL: https://www.adafruit.com/product/2652

FETCHED CONTENT:
[Content from adafruit.com/product/2652]
...
```

### 2h. CSS — Thought Stream Components (`static/style.css`)

**(All the detailed CSS rules from the accordion, badges, timeline, thought cards, composer, chips as previously planned)**

### 2i. Remove Old Green Styles

Delete or update these CSS sections:
- `.agent-thinking-bar` → remove (replaced by inline badges)
- `.conv-progress` → remove (replaced by step timeline)
- `.typing-dots` → keep but restyle for indigo
- `.conv-agent-msg` → replaced by `.conv-thought`
- `.conv-milestone` → replaced by `.conv-thought`
- `.conv-user-msg` → keep (user messages stay)
- `.build-btn` → replaced by `.send-btn`

### 2j. Keep Existing Event Handlers for Compatibility

Old Socket.IO events (`agent:thinking`, `agent:log`, `agent:conversation`, `chat:reply`, etc.) continue to work. The new `agent:thought_stream` handler runs alongside them. In a future cleanup, the old handlers can be removed.

---

## Files Changed — Complete List

### Python Backend

| File | Change Type | Est. Lines |
|---|---|---|
| `agent/emit_utils.py` | Add `emit_thought()`, `emit_tool_begin()`, `emit_tool_end()`, `emit_step()` | +40 |
| `agent/deep_search/agent.py` | Refactor `deep_search()` to take optional `config` and emit intermediate events | +30 |
| `agent/nodes/research.py` | Pass `config=config` to `deep_search()`, add structured thought stream events | +15 |
| `agent/nodes/datasheet_search.py` | Same | +5 |
| `agent/nodes/connection_search.py` | Same | +5 |
| `agent/nodes/select.py` | Replace `agent:thinking` + `agent:log` with structured events | +40 |
| `agent/nodes/validate.py` | Same | +30 |
| `agent/nodes/dispatch.py` | Add tool_call per component placement | +15 |
| `agent/nodes/routing.py` | Add tool_call for wire routing with step per net | +20 |
| `agent/nodes/placement.py` | Add tool_call for auto-placement | +15 |
| `agent/nodes/schematic_audit.py` | Add tool_call for ERC with step per check | +15 |
| `agent/nodes/pcb_layout.py` | Add tool_call for PCB placement | +15 |
| `agent/nodes/design_review.py` | Add thought events for review suggestions | +10 |

### Frontend

| File | Change Type | Est. Lines |
|---|---|---|
| `static/style.css` | Color variables rewrite, new thought-stream component styles, remove old green styles | ~350 |
| `static/index.html` | Restructure right-panel HTML | ~40 |
| `static/app.js` | Add `handleThoughtStreamEvent()`, tool call accordion renderer, step renderer, thought renderer, update `addConversationMessage` | ~200 |

---

## Verification

1. Send a design prompt — watch events stream in real-time:
   - Thought cards appear for each stage
   - Tool call accordions appear with spinning badge
   - Steps appear inside accordions with connecting lines
   - Raw web search results are visible by clicking chevron
2. Wait for completion — status badges update to checkmarks
3. Click any chevron to expand/collapse details
4. `pytest tests/test_pcb_save_export.py` passes
5. JS test suites pass
