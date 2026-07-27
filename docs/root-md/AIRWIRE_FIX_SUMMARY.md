# Airwire/Ratsnest Fix Summary

## Problem
After drawing a trace between two pads, the white dashed lines (airwires/ratsnest) were still visible. In KiCad, these lines should disappear once a connection is routed.

## Root Cause
The ratsnest computation was using exact coordinate matching to determine if a trace connects two pads. Due to floating-point precision issues and snap-to-grid adjustments, the trace endpoints often didn't exactly match the pad positions, causing the airwires to remain visible.

## Solution

### 1. Tolerance-Based Matching (Critical Fix)
**File**: `static/pcb_view/editor_webgl.js`

Updated `_computeClientRatsnest()` to use tolerance-based matching (0.1mm) when matching trace endpoints to pad positions:

```javascript
const TOLERANCE = 0.1; // 0.1mm tolerance

// Helper to find which pad position a point is close to
const findClosestPadKey = (point) => {
    const px = Number(point.x);
    const py = Number(point.y);
    for (const pk of posKeys) {
        if (Math.abs(pk.x - px) < TOLERANCE && Math.abs(pk.y - py) < TOLERANCE) {
            return pk.key;
        }
    }
    return null;
};
```

This ensures that trace endpoints within 0.1mm of a pad position are treated as connected.

### 2. Explicit Ratsnest Refresh
**File**: `static/pcb_view/events.js`

Updated `commitRouteToBoard()` to explicitly recompute ratsnest after committing a trace:

```javascript
// Force recompute ratsnest with the new trace
pcbState.ratsnest = pcbEditor._computeClientRatsnest(pcbState.boardModel);
pcbEditor.requestOverlayRefresh();
```

### 3. KiCad-Style Airwire Rendering
**File**: `static/pcb_view/editor_webgl.js`

Updated airwire styling to match KiCad's appearance:

```javascript
// Before: thick, dim white
ctx.setLineDash([7, 6]);
this._strokeWorldPath(ctx, [start, end], 0.24, '#ffffff', 0.52);

// After: thin, bright, minimal
ctx.setLineDash([4, 4]); // KiCad-style thin dashed line
this._strokeWorldPath(ctx, [start, end], 0.15, '#aab8c8', 0.7);
```

Changes:
- Thinner line (0.15 instead of 0.24)
- KiCad-style color (#aab8c8 - muted blue-gray)
- Higher opacity (0.7 instead of 0.52)
- Shorter dash pattern (4,4 instead of 7,6)

### 4. Updated Color Constants
**File**: `static/pcb_view/constants.js`

Updated `airwireDim` color to match KiCad's style:

```javascript
airwireDim: 0x8899aa, // KiCad-style muted blue-gray
```

## Result

Now when you draw a trace between two pads:
1. The trace is committed to the board model
2. The ratsnest is recomputed with tolerance-based matching
3. The airwire between those two pads disappears
4. Only unrouted connections remain visible as thin dashed lines

The airwires now behave like KiCad:
- ✅ Disappear after routing
- ✅ Thin, minimal appearance
- ✅ Muted blue-gray color
- ✅ Only show unrouted connections

## Testing

To verify the fix:
1. Open CircuitBot and load a PCB file
2. Press **R** to activate Route tool
3. Click on a pad to start routing
4. Click on another pad to complete the trace
5. The white dashed line between those pads should disappear
6. Only other unrouted connections remain visible
