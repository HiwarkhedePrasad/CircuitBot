# PCB Help Button Feature - Complete

## Summary

Added a visible "?" help button to the PCB toolbar that shows all keyboard shortcuts when clicked.

## Changes Made

### 1. Added Help Button to Toolbar HTML
**File:** `static/index.html`

Added a new button group with the "?" button after the Outline tool:
```html
<div class="pcb-tool-group" role="group" aria-label="PCB help">
    <button id="pcbHelpBtn" class="pcb-tool-chip" type="button" title="Keyboard shortcuts (?)" data-shortcut="?">?</button>
</div>
```

### 2. Added CSS Styling for Help Button
**File:** `static/style.css`

Added styling to make the help button visually distinct:
```css
#pcbHelpBtn {
    background: #252830;
    border-color: #4a4a6e;
    font-weight: bold;
    min-width: 32px;
}
#pcbHelpBtn:hover {
    background: #3a3a5e;
    border-color: #6a6a8e;
}
```

### 3. Added Click Handler for Help Button
**File:** `static/app.js`

Added reference to the help button element:
```javascript
const pcbHelpBtn = document.getElementById('pcbHelpBtn');
```

Added click event listener:
```javascript
if (pcbHelpBtn) {
    pcbHelpBtn.addEventListener('click', () => pcbToggleShortcutHelp());
}
```

## How It Works

### Button Location
- The "?" button is located in the PCB toolbar
- It appears after the Outline tool button
- It's visually distinct with a darker background

### Click Behavior
1. **Click the "?" button** → Help overlay appears
2. **Press the "?" key** → Help overlay appears
3. **Click outside overlay** → Overlay closes
4. **Press "?" again** → Overlay closes

### Help Overlay Features

The overlay displays all keyboard shortcuts organized by category:

**Tools:**
- H: Pan tool
- S: Select tool
- R: Route tool
- V: Via tool (or place via while routing)
- O: Outline tool

**Component Actions:**
- Del: Delete selected component/via/trace
- Ctrl+R: Rotate selected component 90°
- Ctrl+C: Copy selected component
- Ctrl+V: Paste component

**View Actions:**
- Ctrl+Z: Undo
- Ctrl+Shift+Z: Redo
- Shift+F: Fit view to board
- N: Highlight net (hover a pad first)
- D: Show board dimensions
- M: Measure distance to nearest pad
- U: Show undo history
- Esc: Cancel / clear highlights
- ?: Toggle this help overlay

## User Experience

### Before
- Users had to guess keyboard shortcuts
- Help was only available via "?" key (not discoverable)
- No visual indicator for help

### After
- Clear "?" button visible in toolbar
- One-click access to all shortcuts
- Keyboard shortcut also works (?)
- Help overlay shows all tools and actions
- Users can quickly learn all available features

## Toolbar Layout

```
[Pan] [Select] [Route] [Via] [Outline] [?]
  H      S       R      V      O        ?
```

## Verification

### Test Steps:
1. Open CircuitBot in browser
2. Click PCB tab and load a board
3. Verify "?" button is visible in toolbar
4. Click the "?" button
5. Verify help overlay appears with all shortcuts
6. Click outside overlay or press "?"
7. Verify overlay closes

### Keyboard Test:
1. Press "?" key
2. Verify same help overlay appears
3. Press "?" again
4. Verify overlay closes

## Files Modified

| File | Changes |
|------|---------|
| `static/index.html` | Added "?" help button to toolbar |
| `static/style.css` | Added CSS styling for help button |
| `static/app.js` | Added click handler for help button |

## Result

Users can now easily discover and access all PCB keyboard shortcuts by clicking the "?" button in the toolbar. The help overlay provides a comprehensive list of all available tools and actions.
