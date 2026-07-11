# Rotate Shortcut Fix - Complete

## Problem
Ctrl+R was triggering browser refresh instead of rotating the component.

## Root Cause
Ctrl+R is a browser shortcut for refresh/reload that fires before JavaScript handlers.

## Solution
Changed rotate shortcut from Ctrl+R to just **R** (when in Select mode with a component selected).

## Changes Made

**File: `static/pcb_view/events.js`**

### Before:
```javascript
// Ctrl+R: rotate selected component by 90°
if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'r') {
    if (!pcbState.boardModel || !pcbState.selectedComponentRef) return;
    event.preventDefault();
    pcbRotateSelectedComponent();
    return;
}
```

### After:
```javascript
// R: rotate selected component by 90° (when in Select mode and component selected)
if (event.key.toLowerCase() === 'r' && !event.ctrlKey && !event.metaKey && !event.altKey) {
    if (!pcbState.boardModel || !pcbState.selectedComponentRef) return;
    if (pcbState.activeTool !== PCB_TOOL.SELECT) return;
    event.preventDefault();
    pcbRotateSelectedComponent();
    return;
}
```

### Updated Help Overlay:
Changed from `Ctrl+R` to `R (in Select mode)` in the shortcuts list.

## How It Works Now

### To Rotate a Component:
1. Press **S** to switch to Select tool
2. Click on a component to select it (green border appears)
3. Press **R** to rotate 90°
4. Component rotates clockwise

### R Key Behavior:
| Mode | Component Selected | Action |
|------|-------------------|--------|
| Select | Yes | Rotate component |
| Select | No | Switch to Route tool |
| Route | Any | Start routing |
| Via | Any | Place via |
| Outline | Any | Draw outline |

## Quick Reference

| Shortcut | Action |
|----------|--------|
| S | Select tool |
| R (Select mode + component selected) | Rotate 90° |
| R (other modes) | Route tool |
| Del | Delete selected |
| Ctrl+C | Copy |
| Ctrl+V | Paste |

## Result

The rotate shortcut now works without triggering browser refresh. Users can press **R** in Select mode to rotate components.
