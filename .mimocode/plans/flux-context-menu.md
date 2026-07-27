# CircuitBot: Flux-Style Right-Click Context Menu Integration

## Goal

Integrate Flux-style context menu patterns into CircuitBot's PCB view, adding missing non-AI editing actions (Flip Layer, Align, Comments, Trace Width) and enhancing empty-space context menu with quick AI checks. CircuitBot already has most of Flux's AI features — this closes the interaction gaps.

## What CircuitBot Already Has vs What Flux Has

| Action | Flux | CircuitBot | Gap? |
|--------|------|------------|------|
| **Component: Explain** | Yes | Yes (`pcbExplainEntity`) | No |
| **Component: Find Alternatives** | Yes | Yes (`pcbFindAlternatives`) | No |
| **Component: Simulate Power** | No | Yes (`pcbSimulateComponent`) | We exceed Flux |
| **Component: Simulate Thermal** | No | Yes (`pcbSimulateComponent`) | We exceed Flux |
| **Component: Check Availability** | No | Yes (`pcbCheckAvailability`) | We exceed Flux |
| **Component: Show on Schematic** | No | Yes (`pcbShowOnSchematic`) | We exceed Flux |
| **Component: Rotate** | Yes | Yes (`pcbRotateSelectedComponent`) | No |
| **Component: Delete** | Yes | Yes (`pcbDeleteSelectedComponent`) | No |
| **Component: Flip Layer** | Yes | **MISSING** | **YES** |
| **Component: Align** | Yes | **MISSING** | **YES** |
| **Trace: Explain** | No | Yes (`pcbExplainEntity`) | We exceed Flux |
| **Trace: Verify Impedance** | No | Yes (`pcbSimulateTrace`) | We exceed Flux |
| **Trace: Current Capacity** | No | Yes (`pcbSimulateTrace`) | We exceed Flux |
| **Trace: Voltage Drop** | No | Yes (`pcbSimulateTrace`) | We exceed Flux |
| **Trace: Delete** | Yes | **MISSING** (via Del key only) | **YES** |
| **Trace: Adjust Width** | Yes | **MISSING** | **YES** |
| **Empty: Check Decoupling** | Yes | **MISSING** | **YES** |
| **Empty: Check Reset Pins** | Yes | **MISSING** | **YES** |
| **Empty: Explain Circuit** | Yes | **MISSING** | **YES** |
| **Empty: Constraint Check** | No | Yes (`pcbRunFullConstraintCheck`) | We exceed Flux |
| **Empty: Engineering Plan** | No | Yes (`pcbShowPlanDialog`) | We exceed Flux |
| **Empty: Insert Comment** | Yes | **MISSING** | **YES** |
| **Net: Explain** | No | Yes (`pcbExplainEntity`) | We exceed Flux |
| **Net: Highlight** | No | Yes | We exceed Flux |

## Implementation Plan

### Phase 1: Non-AI Editing Actions (events.js)

All changes in `static/pcb_view/events.js` inside `pcbShowContextMenu()`.

#### 1A. Flip Layer (Component Context Menu)

Add after "Rotate 90°":
```
items.push({ label: 'Flip to Other Side', action: () => pcbFlipComponentLayer(compHit.ref) });
```

New function `pcbFlipComponentLayer(refDes)`:
- Find component in `pcbState.boardModel.components`
- Change its layer from `F.Cu` → `B.Cu` or `B.Cu` → `F.Cu`
- Also flip pad layers (each pad inherits from component layer)
- Push to undo history
- Request overlay refresh

Implementation:
```javascript
function pcbFlipComponentLayer(refDes) {
    const comp = (pcbState.boardModel.components || []).find(c => c.ref === refDes);
    if (!comp) return;
    const before = deepClone(pcbState.boardModel);
    const currentLayer = (comp.layer || 'F.Cu').toLowerCase();
    const isFront = currentLayer.includes('front') || currentLayer === 'f.cu' || currentLayer === 'fc' || currentLayer === 'top';
    comp.layer = isFront ? 'B.Cu' : 'F.Cu';
    // Flip pads
    if (comp.pads) {
        comp.pads.forEach(pad => {
            pad.layer = comp.layer;
        });
    }
    const after = deepClone(pcbState.boardModel);
    pcbEditor.pushHistory('flip component layer', before, after);
    pcbEditor.requestOverlayRefresh();
}
```

#### 1B. Alignment Submenu (Component Context Menu)

Add after "Flip to Other Side":
```
items.push({ type: 'separator' });
items.push({ label: 'Align ▸', submenu: [
    { label: 'Align Top', action: () => pcbAlignComponent(compHit.ref, 'top') },
    { label: 'Align Bottom', action: () => pcbAlignComponent(compHit.ref, 'bottom') },
    { label: 'Align Left', action: () => pcbAlignComponent(compHit.ref, 'left') },
    { label: 'Align Right', action: () => pcbAlignComponent(compHit.ref, 'right') },
    { label: 'Align Center H', action: () => pcbAlignComponent(compHit.ref, 'center_h') },
    { label: 'Align Center V', action: () => pcbAlignComponent(compHit.ref, 'center_v') },
]});
```

New function `pcbAlignComponent(refDes, direction)`:
- Calculate bounding boxes of all selected components (or all components if none selected)
- Move the target component to align with the group's bounding box edge
- Push to undo history

Implementation:
```javascript
function pcbAlignComponent(refDes, direction) {
    const components = pcbState.boardModel.components || [];
    const target = components.find(c => c.ref === refDes);
    if (!target) return;
    
    // Get all selected components (or all if none selected)
    const selectedRefs = pcbState.selectedComponentRefs || [refDes];
    const others = components.filter(c => selectedRefs.includes(c.ref) && c.ref !== refDes);
    if (others.length === 0) return;
    
    const before = deepClone(pcbState.boardModel);
    
    // Calculate bounding box of others
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const c of others) {
        const w = (c.width || 2) / 2;
        const h = (c.height || 2) / 2;
        minX = Math.min(minX, c.x - w);
        maxX = Math.max(maxX, c.x + w);
        minY = Math.min(minY, c.y - h);
        maxY = Math.max(maxY, c.y + h);
    }
    
    const tw = (target.width || 2) / 2;
    const th = (target.height || 2) / 2;
    
    switch (direction) {
        case 'top': target.y = minY + th; break;
        case 'bottom': target.y = maxY - th; break;
        case 'left': target.x = minX + tw; break;
        case 'right': target.x = maxX - tw; break;
        case 'center_h': target.x = (minX + maxX) / 2; break;
        case 'center_v': target.y = (minY + maxY) / 2; break;
    }
    
    const after = deepClone(pcbState.boardModel);
    pcbEditor.pushHistory('align component', before, after);
    pcbEditor.requestOverlayRefresh();
}
```

Note: Submenu rendering requires extending `pcbShowContextMenu` to handle nested items. Add a `submenu` type that creates a flyout on hover.

#### 1C. Trace Actions (Trace Context Menu)

Add after existing trace items:
```
items.push({ type: 'separator' });
items.push({ label: 'Adjust trace width...', action: () => pcbAdjustTraceWidth(traceHit) });
items.push({ label: 'Delete trace', action: () => pcbDeleteTrace(traceHit) });
```

New function `pcbAdjustTraceWidth(traceHit)`:
- Show a small popup/modal with width input
- On confirm, update trace width and push to history

New function `pcbDeleteTrace(traceHit)`:
- Remove the trace from boardModel
- Push to undo history

#### 1D. Insert Comment (Empty Space Menu)

Add after "Create engineering plan...":
```
items.push({ label: 'Insert Comment...', action: () => pcbInsertComment() });
```

New function `pcbInsertComment()`:
- Show a modal with text input
- On confirm, add a comment annotation to `pcbState.boardModel.comments` array
- Comments are rendered as text labels in the overlay (editor_webgl.js)

New state field: `pcbState.boardModel.comments = []`

New rendering in `editor_webgl.js`: Draw comment markers (small speech-bubble icons with text on hover).

### Phase 2: Quick AI Checks on Empty Space (events.js + routes.py)

#### 2A. "Explain Circuit" on Empty Space

Add to empty-space menu:
```
items.push({ label: 'Explain this circuit', action: () => pcbExplainCircuit() });
```

New function `pcbExplainCircuit()`:
- Call existing `/api/object_knowledge` with `entity_type: 'circuit'`
- Display in the existing panel

New route in `server/routes.py`:
```python
@app.route('/api/explain_circuit', methods=['POST'])
def api_explain_circuit():
    session_id = ...
    design = session_manager.get_or_create(session_id).get_design()
    # Build a circuit-level explanation from knowledge_db + synthesis_graph
    explanations = []
    for comp in design.get("components", []):
        kdb = design.get("knowledge_db", {})
        if comp.get("id_str") in kdb:
            explanations.append(f"{comp['ref_des']}: {kdb[comp['id_str']].get('description', 'No description')}")
    return jsonify({"explanation": "\n".join(explanations)})
```

#### 2B. "Check Decoupling Capacitors" on Empty Space

Add to empty-space menu:
```
items.push({ label: 'Check decoupling capacitors', action: () => pcbCheckDecouplingCaps() });
```

New function `pcbCheckDecouplingCaps()`:
- Call new route `/api/check_decoupling`
- Display results in panel

New route:
```python
@app.route('/api/check_decoupling', methods=['POST'])
def api_check_decoupling():
    # Analyze design for ICs missing decoupling caps
    # Check: each IC should have a 100nF cap within 2mm
    # Returns list of warnings
```

#### 2C. "Check Reset Pins" on Empty Space

Add to empty-space menu:
```
items.push({ label: 'Check reset pins', action: () => pcbCheckResetPins() });
```

New route:
```python
@app.route('/api/check_reset_pins', methods=['POST'])
def api_check_reset_pins():
    # Analyze design for reset pins without pull-up/pull-down
    # Check: NRST, RESET, /RST pins should have proper termination
    # Returns list of warnings
```

### Phase 3: Submenu Rendering (events.js)

Extend `pcbShowContextMenu` to handle `submenu` type items:

```javascript
// When item has submenu property
if (item.submenu) {
    btn.style.display = 'flex';
    btn.style.justifyContent = 'space-between';
    btn.innerHTML = `${item.label} <span style="color:#666;">▸</span>`;
    const flyout = document.createElement('div');
    flyout.style.cssText = `position:absolute;left:100%;top:0;background:#1a1d23;border:1px solid #3c3c3c;border-radius:6px;padding:4px 0;min-width:160px;display:none;`;
    for (const sub of item.submenu) {
        const subBtn = document.createElement('div');
        subBtn.style.cssText = 'padding:6px 14px;cursor:pointer;color:#ccc;transition:background 0.1s;';
        subBtn.textContent = sub.label;
        subBtn.addEventListener('mouseenter', () => subBtn.style.background = 'rgba(77,241,194,0.1)');
        subBtn.addEventListener('mouseleave', () => subBtn.style.background = 'transparent');
        subBtn.addEventListener('click', (e) => { e.stopPropagation(); pcbHideContextMenu(); sub.action(); });
        flyout.appendChild(subBtn);
    }
    btn.style.position = 'relative';
    btn.appendChild(flyout);
    btn.addEventListener('mouseenter', () => flyout.style.display = 'block');
    btn.addEventListener('mouseleave', () => flyout.style.display = 'none');
}
```

## Files to Modify

| File | Change |
|------|--------|
| `static/pcb_view/events.js` | Add Flip Layer, Align submenu, Trace Width, Delete Trace, Insert Comment, Explain Circuit, Check Decoupling, Check Reset Pins to context menu |
| `static/pcb_view/editor_webgl.js` | Render comment annotations in overlay |
| `server/routes.py` | Add `/api/explain_circuit`, `/api/check_decoupling`, `/api/check_reset_pins` routes |

## Keyboard Shortcuts (existing, verify coverage)

| Key | Action | Status |
|-----|--------|--------|
| R | Rotate selected component 90° | Already works |
| Del | Delete selected component/via/trace | Already works |
| F | Flip layer (new) | **ADD** |
| Ctrl+A | Select all | Verify |
| Ctrl+Z | Undo | Already works |
| Ctrl+Y | Redo | Already works |

Add `F` key handler in keyboard event listener for flip.

## Success Criteria

1. Right-click component shows: AI actions + Flip Layer + Align submenu + Rotate + Delete
2. Right-click trace shows: AI actions + Adjust Width + Delete + Highlight Net
3. Right-click empty space shows: Quick AI checks + Constraint Check + Engineering Plan + Insert Comment
4. Align submenu works with flyout (hover to expand)
5. Flip Layer moves component between F.Cu and B.Cu
6. Trace width adjustment shows input popup and updates trace
7. Insert Comment creates a visible annotation on the board
8. All actions support undo via `pcbEditor.pushHistory()`
9. No regressions in existing context menu functionality
