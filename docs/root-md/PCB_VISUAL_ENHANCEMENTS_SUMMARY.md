# PCB Visual Enhancements & Via Modal - Complete

## Summary

Implemented three major visual enhancements to match KiCad's vibrant look:
1. **Vibrant component colors** - Components now look "alive" with bright, saturated colors
2. **Pure black background** - Changed from dark green (#0b1116) to #000000
3. **Via placement modal** - Right-click during routing shows centered modal to switch layers

---

## Feature 1: Vibrant Component Colors

### Changes Made

**File: `static/pcb_view/constants.js`**

Updated PCB_COLORS with brighter, more saturated values:
- `topCopper: 0xff4444` - Bright red for F.Cu traces
- `bottomCopper: 0x4488ff` - Bright blue for B.Cu traces
- `smdTop: 0xff6655` - Bright red SMD pads
- `smdBottom: 0x5599ff` - Bright blue SMD pads
- `throughPad: 0xffcc88` - Golden through-hole pads
- `silkscreen: 0xffffff` - Pure white silkscreen
- `selection: 0x00ffcc` - Bright cyan selection highlight

**File: `static/pcb_view/editor_webgl.js`**

Updated component rendering:
- Through-hole pads: `#ffcc88` (golden) with `#ffdd99` stroke
- Front copper pads: `#ff6655` (bright red) with `#ff8877` stroke
- Bottom copper pads: `#5599ff` (bright blue) with `#66aaff` stroke
- Silkscreen: Pure white with subtle glow
- Body fill: `rgba(20, 25, 35, 0.50)` (slightly more visible)

### Visual Result
- Components pop against the black background
- Copper traces are vivid and easy to see
- Silkscreen text is crisp and bright
- Overall look matches KiCad's professional appearance

---

## Feature 2: Pure Black Background

### Changes Made

**File: `static/pcb_view/constants.js`**

Updated background colors:
- `background: 0x000000` - Pure black
- `gridMinor: 0x0a0a0a` - Very subtle grid lines
- `gridMajor: 0x1a1a1a` - Visible grid lines
- `boardFill: 0x080808` - Dark board fill
- `hole: 0x000000` - Black drill holes

**File: `static/pcb_view/editor_webgl.js`**

Updated WebGL rendering:
- `gl.clearColor(0.0, 0.0, 0.0, 1.0)` - Pure black clear color
- Grid shader: `vec3 bgColor = vec3(0.0, 0.0, 0.0)` - Pure black background

**File: `static/style.css`**

Updated CSS variable:
- `--bg-canvas: #000000` - Pure black canvas background

### Visual Result
- Clean, professional black background
- Grid lines visible but not distracting
- Components and traces stand out clearly
- Matches KiCad's default dark theme

---

## Feature 3: Via Placement Modal

### Changes Made

**File: `static/pcb_view/events.js`**

Added two new functions:

1. **`pcbShowViaModal(screenX, screenY)`** - Shows centered modal with:
   - Title: "Place Via & Switch Layer"
   - F.Cu (Front) button - red border
   - B.Cu (Back) button - blue border
   - Cancel button
   - Hover effects on buttons
   - Closes on backdrop click

2. **`pcbPlaceViaAndSwitchLayer(targetLayer)`** - Handles via placement:
   - Creates via at cursor position
   - Appends cursor to route points
   - Switches route layer to selected layer
   - Updates UI and shows toast notification

Updated right-click handler during routing:
- **Before**: Right-click committed/finalized the route
- **After**: Right-click shows via modal for layer selection

### How It Works

1. Activate Route tool (press R)
2. Click on a pad to start routing
3. Click to add route points
4. **Right-click** during routing
5. **Centered modal appears** with layer options:
   - "F.Cu (Front)" - Red button
   - "B.Cu (Back)" - Blue button
   - "Cancel" - Gray button
6. **Select a layer**:
   - Via is placed at cursor position
   - Route switches to selected layer
   - Toast shows "Switched to F.Cu" or "Switched to B.Cu"
7. Continue routing on the new layer

### Alternative Methods
- **V key**: Still works to place via and switch layers (instant, no modal)
- **Right-click**: Now shows modal for explicit layer choice

---

## Files Modified

| File | Changes |
|------|---------|
| `static/pcb_view/constants.js` | Updated PCB_COLORS for vibrant colors and black background |
| `static/pcb_view/editor_webgl.js` | Updated WebGL clearColor, grid colors, component rendering |
| `static/pcb_view/events.js` | Added via modal, updated right-click handler during routing |
| `static/style.css` | Updated --bg-canvas to pure black |

---

## Verification Plan

### Visual Testing

1. **Color Test**:
   - Load a PCB with components
   - Verify: Copper pads are bright red (F.Cu) and blue (B.Cu)
   - Verify: Through-hole pads are golden
   - Verify: Silkscreen is bright white
   - Verify: Components look vibrant and "alive"

2. **Background Test**:
   - Verify: Background is pure black (#000000)
   - Verify: Grid lines are visible but subtle
   - Verify: Board outline stands out against black

3. **Via Modal Test**:
   - Activate Route tool (R)
   - Click on a pad to start routing
   - Click to add route points
   - Right-click during routing
   - Verify: Centered modal appears
   - Verify: Modal shows "F.Cu (Front)" and "B.Cu (Back)" buttons
   - Click "F.Cu (Front)"
   - Verify: Via is placed at cursor
   - Verify: Route continues on F.Cu layer
   - Verify: Toast shows "Switched to F.Cu"

4. **Modal Dismiss Test**:
   - Right-click during routing
   - Click "Cancel" button
   - Verify: Modal closes
   - Verify: Route is not committed
   - Right-click again
   - Click outside modal
   - Verify: Modal closes

---

## User Experience

### Before
- Dull, muted component colors
- Dark green background
- Right-click during routing committed the route
- No visual way to place via and switch layers

### After
- Vibrant, KiCad-like component colors
- Pure black professional background
- Right-click shows via modal for layer selection
- Smooth UX for placing vias during routing

---

## Quick Reference

| Action | How To |
|--------|--------|
| Start routing | Press R, click pad |
| Add route point | Left-click |
| Place via & switch layer | Right-click → Select layer |
| Place via (instant) | Press V during routing |
| Cancel modal | Click Cancel or outside modal |
| Commit route | Click on destination pad |

---

## Result

The PCB view now looks professional and vibrant like KiCad, with a clean black background and an intuitive via placement workflow. Users can right-click during routing to get a centered modal for layer selection, making the routing experience smooth and intuitive.
