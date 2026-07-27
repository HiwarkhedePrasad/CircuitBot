# Route Shortcut Fix - Complete

## Problem
R key was conflicting between Route tool and Rotate component.

## Solution
Changed Route tool shortcut from **R** to **X**.

## Changes Made

### 1. Keyboard Shortcut Handler
**File: `static/pcb_view/events.js`**

Changed Route tool shortcut from R to X:
```javascript
// Before:
if (event.key.toLowerCase() === 'r') {
    pcbSetTool(PCB_TOOL.ROUTE);
}

// After:
if (event.key.toLowerCase() === 'x') {
    pcbSetTool(PCB_TOOL.ROUTE);
}
```

### 2. Rotate Handler
**File: `static/pcb_view/events.js`**

Removed mode restriction (R now only does rotation):
```javascript
// Before:
if (event.key.toLowerCase() === 'r' && ...) {
    if (pcbState.activeTool !== PCB_TOOL.SELECT) return;  // Removed
    pcbRotateSelectedComponent();
}

// After:
if (event.key.toLowerCase() === 'r' && ...) {
    pcbRotateSelectedComponent();  // Works in any mode when component selected
}
```

### 3. Help Overlay
**File: `static/pcb_view/events.js`**

Updated shortcuts list:
```javascript
['X', 'Route tool'],  // Changed from R to X
['R', 'Rotate selected component 90°'],  // Now dedicated to rotate
```

### 4. Toolbar Button
**File: `static/index.html`**

Updated Route button:
```html
<!-- Before -->
<button id="pcbRouteToolBtn" title="Route traces (R)" data-shortcut="R">Route</button>

<!-- After -->
<button id="pcbRouteToolBtn" title="Route traces (X)" data-shortcut="X">Route</button>
```

## New Keyboard Shortcuts

| Key | Action |
|-----|--------|
| **H** | Pan tool |
| **S** | Select tool |
| **X** | Route tool |
| **V** | Via tool |
| **O** | Outline tool |
| **R** | Rotate selected component 90° |
| **Del** | Delete selected |
| **Ctrl+C** | Copy |
| **Ctrl+V** | Paste |
| **Ctrl+Z** | Undo |
| **Ctrl+Shift+Z** | Redo |
| **Shift+F** | Fit view |
| **N** | Highlight net |
| **D** | Board dimensions |
| **M** | Measure |
| **U** | Undo history |
| **Esc** | Cancel |
| **?** | Help |

## How It Works Now

### To Route Traces:
1. Press **X** to switch to Route tool
2. Click a pad to start routing
3. Click to add route points
4. Right-click to place via / switch layers
5. Click destination pad to finish

### To Rotate Components:
1. Press **S** to switch to Select tool
2. Click a component to select it (green border)
3. Press **R** to rotate 90°

## Result

No more conflicts! R is now dedicated to rotation, and X is for routing.
