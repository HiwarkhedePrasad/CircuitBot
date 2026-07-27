# Trace Rendering Fix - Sharp 45° Angles Like KiCad

## Problem
Traces had smooth curves at corners instead of sharp 45° angles like KiCad.

## Root Cause
`_strokeWorldPath` used `_screenSmoothPath` which applied `ctx.quadraticCurveTo` at every corner, creating smooth Bézier curves.

## Fix Applied

**File:** `static/pcb_view/editor_webgl.js`

**Changed `_strokeWorldPath`:**
```javascript
// Before:
ctx.lineJoin = 'round';
this._screenSmoothPath(ctx, points);  // ← Smooth Bézier curves

// After:
ctx.lineJoin = 'miter';
this._screenPathFromWorldPoints(ctx, points);  // ← Sharp straight lines
```

**Changes:**
1. `lineJoin: 'round'` → `'miter'` — Sharp corners instead of rounded
2. `_screenSmoothPath` → `_screenPathFromWorldPoints` — Straight `lineTo` instead of `quadraticCurveTo`

## How It Works Now

**Before:**
```
Point A ──╲___╱── Point B
           Curve
```

**After (KiCad-style):**
```
Point A ──╲
            ╲── Point B
              Sharp 45° corner
```

## Verification
1. Load a PCB with traces
2. Verify traces have sharp 45° corners like KiCad
3. Route a new trace and verify corners are sharp
4. No visual artifacts at corner points

## Result
Traces now render with sharp 45° angles matching KiCad's professional look.
