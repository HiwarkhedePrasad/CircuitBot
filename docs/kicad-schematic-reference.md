# KiCad Schematic Agent Reference

Reference for programmatic KiCad schematic generation, coordinate transforms, and ERC-clean patterns.

---

## 1. Critical Coordinate System

### The Y-axis Trap

- **Symbol libraries (.kicad_sym)** use Y-up (math convention)
- **Schematics (.kicad_sch)** use Y-down (screen convention)
- You MUST negate Y when transforming from library to schematic space

### Transform Formula

Pin at library (px, py), symbol placed at schematic (sx, sy) with rotation R:

| Rotation | Schematic Position |
|----------|-------------------|
| 0        | (sx + px, sy **-** py) |
| 90       | (sx + py, sy + px) |
| 180      | (sx - px, sy **+** py) |
| 270      | (sx - py, sy - px) |

### Python Implementation

```python
GRID = 1.27

def snap(v: float) -> float:
    return round(v / GRID) * GRID

def pin_transform(pin_x: float, pin_y: float, rotation: int = 0):
    transforms = {
        0:   ( pin_x, -pin_y),
        90:  ( pin_y,  pin_x),
        180: (-pin_x,  pin_y),
        270: (-pin_y, -pin_x),
    }
    return transforms[rotation]

def pin_abs(sx, sy, px, py, rotation=0):
    dx, dy = pin_transform(px, py, rotation)
    return (snap(sx + dx), snap(sy + dy))
```

---

## 2. 2-Pin Passive Conventions

Standard KiCad 2-pin passive symbols (Device:R, Device:C, Device:L):
- Pin 1 at library (0, 2.54)
- Pin 2 at library (0, -2.54)

With rotation 0 at schematic (sx, sy):
- Pin 1 schematic: (sx, sy - 2.54)
- Pin 2 schematic: (sx, sy + 2.54)

---

## 3. PWR_FLAG Rules

For every power net that originates from a voltage regulator (not a power symbol), add a PWR_FLAG:

- **Rule of thumb**: If a net is driven by a component whose output pin type is `passive` (not `power_out`), that net needs a PWR_FLAG
- Add on GND nets too
- Missing PWR_FLAGs cause `power_pin_not_driven` ERC errors

---

## 4. Sub-symbol Naming

In `.kicad_sch` embedded `lib_symbols`:
- Top-level: `(symbol "Library:Name" ...)`
- Sub-symbols use ONLY the name without library prefix: `(symbol "Name_0_1" ...)`

**Wrong:** `(symbol "Device:R_0_1" ...)`
**Correct:** `(symbol "R_0_1" ...)`

---

## 5. Grid Snapping

Every coordinate must be a multiple of 1.27mm. Apply `snap()` to:
- Component positions
- Wire endpoints
- Label positions
- No-connect positions
- PWR_FLAG positions

---

## 6. No-Connect Flags

Every unused pin on every IC MUST have a no-connect flag. Missing flags cause `pin_not_connected` errors.

---

## 7. Checklist

1. All coordinates snapped to 1.27mm grid
2. Every IC pin either connected or flagged with no-connect
3. Sub-symbol names have no library prefix
4. PWR_FLAG on every voltage regulator output net AND on GND
5. Parenthesis balance verified (depth 0 at end of file)
6. Pin positions verified against .kicad_sym library (not guessed)
