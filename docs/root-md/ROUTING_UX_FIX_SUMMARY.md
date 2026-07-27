# Routing UX Fix - Complete

## Problem
Airwires were dimmed from the start, even before clicking a pad to route.

## Root Cause
In `_drawAirwiresCanvas`, the airwire rendering logic was:
```javascript
if (isCurrentNet) {
    // Bright for current net
} else {
    // Dim for ALL other airwires (even when not routing!)
}
```

When not routing, `isCurrentNet` was false for all nets, so ALL airwires went to the dim branch.

## Fix Applied

**File: `static/pcb_view/editor_webgl.js`**

Changed airwire rendering logic:
```javascript
// Before:
if (isCurrentNet) {
    this._strokeWorldPath(ctx, [start, end], 0.25, '#00ffcc', 0.9);
} else {
    this._strokeWorldPath(ctx, [start, end], 0.15, '#333333', 0.3);
}

// After:
if (isRouting && isCurrentNet) {
    // Bright cyan for current net (when routing)
    this._strokeWorldPath(ctx, [start, end], 0.25, '#00ffcc', 0.9);
} else if (isRouting && !isCurrentNet) {
    // Dim for other nets (when routing)
    this._strokeWorldPath(ctx, [start, end], 0.15, '#333333', 0.3);
} else {
    // Not routing - show all airwires bright white
    this._strokeWorldPath(ctx, [start, end], 0.15, '#aab8c8', 0.7);
}
```

## Behavior Now

### When Route tool is selected (but no pad clicked):
- **All airwires**: Bright white (`#aab8c8`, alpha 0.7)
- **All components**: Full opacity (100%)
- **No dimming**: Everything looks normal

### After clicking a pad to start routing:
- **Current net airwires**: Bright cyan (`#00ffcc`, alpha 0.9)
- **Other net airwires**: Dimmed (`#333333`, alpha 0.3)
- **Connected components**: Full opacity (100%)
- **Unconnected components**: Dimmed (20% opacity)

### After completing the route:
- **All airwires**: Return to bright white
- **All components**: Return to full opacity

## Summary

| State | Airwires | Components |
|-------|----------|------------|
| Route tool selected, no pad clicked | Bright white | Full opacity |
| Routing (pad clicked) - connected net | Bright cyan | Full opacity |
| Routing (pad clicked) - other nets | Dimmed | Dimmed (20%) |
| Route completed | Bright white | Full opacity |

## Result

The UX is now intuitive:
- **Before routing**: Everything looks normal, airwires guide you
- **During routing**: Focus on connected components and current net
- **After routing**: Everything returns to normal

This matches the user's request: "do not dim them from the start, when the traces or pins is not selected to draw the trace the other suggest lines should stay white"
