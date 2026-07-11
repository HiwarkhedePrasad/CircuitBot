# Routing UX Improvements - Complete

## Summary

Implemented two major routing UX improvements:
1. **Auto-snap to pins** - Clicking near a pin during routing auto-connects to it
2. **Dim non-connected components** - During routing, unrelated components dim and airwires highlight

---

## Feature 1: Auto-Snap to Pins

### Changes Made

**File: `static/pcb_view/utils.js`**

Added `findNearbyPad()` function:
- Searches all components and pads for the nearest pad within a given radius
- Returns the pad, component, key, and center coordinates
- Used for auto-connecting when clicking near a pin

**File: `static/pcb_view/events.js`**

Updated routing handler:
- After standard padHit check, added nearby pad detection with 1.5mm radius
- If click is within 1.5mm of a pad, auto-connects to that pad
- Prevents adding extra route points when user intended to connect

### How It Works

1. Activate Route tool (R)
2. Click a pad to start routing
3. Move cursor near another pad (within 1.5mm)
4. Click - route auto-connects to the nearby pad
5. No extra route point is added

### Before vs After

**Before:**
- Click slightly off pad → adds route point instead of connecting
- User had to click exactly on pad center

**After:**
- Click within 1.5mm of pad → auto-connects
- More forgiving and intuitive routing

---

## Feature 2: Dim Non-Connected Components

### Changes Made

**File: `static/pcb_view/editor_webgl.js`**

Added helper functions:
- `_isComponentConnectedToNet(component, netName)` - Checks if component has pads on the given net
- `_getRoutingDimAlpha(component)` - Returns opacity based on routing state and connection

Updated component rendering:
- **Body fill**: Uses dimming alpha (50% for connected, 10% for unconnected)
- **Copper pads**: Uses dimming alpha (100% for connected, 20% for unconnected)
- **Silkscreen/graphics**: Uses dimming alpha (100% for connected, 20% for unconnected)

Updated airwire rendering:
- **Current net airwires**: Bright cyan (`#00ffcc`, alpha 0.9)
- **Other net airwires**: Dimmed (`#333333`, alpha 0.3)

### How It Works

1. Load a PCB with multiple components
2. Activate Route tool (R)
3. Click a pad (e.g., "VCC" net)
4. **Components connected to VCC**: Remain bright (100% opacity)
5. **Other components**: Dim to 20% opacity
6. **VCC airwires**: Bright cyan, guiding you to connect
7. **Other airwires**: Dimmed, not distracting
8. Complete the route
9. **All components return to normal**

### Visual Effect

**Before routing:**
```
[R1] [R2] [C1] [U1] [J1]
 All components at full opacity
```

**During routing (VCC net):**
```
[R1] [R2] [C1] [U1] [J1]
  ↓    ↓    ↓    ↑    ↑
Bright Bright Dim  Bright Bright
(VCC) (VCC) (GND) (VCC) (VCC)
```

### Benefits

- **Focus**: User attention drawn to relevant components
- **Clarity**: Airwires clearly show where to connect
- **Professional**: Matches KiCad's routing experience
- **Intuitive**: Non-connected components fade into background

---

## Files Modified

| File | Changes |
|------|---------|
| `static/pcb_view/utils.js` | Added `findNearbyPad()` function |
| `static/pcb_view/events.js` | Added nearby pad detection for auto-snap |
| `static/pcb_view/editor_webgl.js` | Added component dimming, airwire highlighting, helper functions |

---

## Verification Plan

### Auto-Snap Test
1. Activate Route tool (R)
2. Click a pad to start routing
3. Move cursor near another pad (within 1.5mm but not exactly on it)
4. Click
5. Verify: Route auto-connects to the nearby pad
6. Verify: No extra route point is added

### Component Dimming Test
1. Load a PCB with multiple components
2. Activate Route tool (R)
3. Click a pad that belongs to "VCC" net
4. Verify: Components connected to VCC remain bright
5. Verify: Other components are dimmed (20% opacity)
6. Verify: Airwires for VCC net are bright cyan
7. Verify: Airwires for other nets are dimmed
8. Complete the route
9. Verify: All components return to full opacity

---

## Quick Reference

| Feature | How It Works |
|---------|--------------|
| Auto-snap | Click within 1.5mm of pad during routing |
| Component dimming | Automatic when routing a net |
| Airwire highlight | Current net shows bright cyan |
| Reset | Completing route returns all to normal |

---

## Result

The routing experience is now much more intuitive:
- **Auto-snap** makes it easier to connect to pads
- **Component dimming** focuses attention on relevant components
- **Airwire highlighting** clearly shows where to route

This matches KiCad's professional routing UX and significantly improves the user experience.
