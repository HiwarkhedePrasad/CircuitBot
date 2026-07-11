# Prompt: KiCad .kicad_pcb to JSON Parser

You are a specialized parser agent. Your task is to read a KiCad `.kicad_pcb` file (S-expression format) and produce a single `pcb.json` file that a web-based renderer can consume directly — no additional transforms needed.

## Critical Rule

**Every coordinate in the output JSON must be in WORLD (board-level) coordinates.** Pad positions, graphics positions, text positions — all must be pre-transformed through their parent component's rotation and position. The renderer will NOT re-apply any rotation. If the renderer has to guess or re-transform, the parser has failed.

---

## Output Schema

```json
{
  "meta": {
    "source": "filename.kicad_pcb",
    "boardWidth_mm": 80.0,
    "boardHeight_mm": 49.0,
    "originX": 50.0,
    "originY": 71.0,
    "unit": "mm"
  },
  "outline": [
    { "type": "line", "x1": 50, "y1": 116, "x2": 50, "y2": 75 },
    { "type": "arc", "cx": 50, "cy": 75, "startX": 50, "startY": 69.34, "endX": 54, "endY": 71, "angle": 45 }
  ],
  "traces": [
    {
      "net": "GND",
      "layer": "F.Cu",
      "width": 0.15,
      "path": [{"x": 73.875, "y": 106.725}, {"x": 73.9, "y": 106.7}]
    }
  ],
  "vias": [
    { "x": 77.4, "y": 106.7, "diameter": 0.6, "drill": 0.3, "net": "GND", "layers": ["F.Cu", "B.Cu"] }
  ],
  "components": [
    {
      "ref": "U1",
      "value": "CY7C68013A",
      "footprint": "Package_DFN_QFN:Cypress_QFN-56...",
      "x": 69.85,
      "y": 95.5,
      "rotation": 0,
      "layer": "F.Cu",
      "pads": [
        {
          "number": "1",
          "x": 67.35,
          "y": 93.0,
          "width": 0.25,
          "height": 0.65,
          "shape": "rect",
          "type": "smd",
          "rotation": 0,
          "drill": null,
          "net": "VCC",
          "layers": ["F.Cu", "F.Mask", "F.Paste"]
        }
      ],
      "graphics": [
        {
          "kind": "reference",
          "text": "U1",
          "x": 69.85,
          "y": 93.5,
          "rotation": 0,
          "size": 1.0,
          "layer": "F.SilkS",
          "hidden": false
        },
        {
          "kind": "outline",
          "layer": "F.SilkS",
          "width": 0.12,
          "points": [
            {"x": 66.5, "y": 91.5},
            {"x": 73.2, "y": 91.5},
            {"x": 73.2, "y": 99.5},
            {"x": 66.5, "y": 99.5}
          ]
        }
      ]
    }
  ],
  "nets": [
    { "id": 0, "name": "" },
    { "id": 3, "name": "GND" }
  ],
  "zones": [
    {
      "net": "GND",
      "layer": "F.Cu",
      "priority": 0,
      "outline": [{"x": 50, "y": 75}, {"x": 130, "y": 75}, ...]
    }
  ]
}
```

---

## Parsing Rules

### 1. Board Outline (`Edge.Cuts` layer)

Extract ALL `gr_line` and `gr_arc` elements on layer `Edge.Cuts`.

**`gr_line`:**
```
(gr_line (start X1 Y1) (end X2 Y2) (stroke ...) (layer "Edge.Cuts") ...)
```
→ `{ "type": "line", "x1": X1, "y1": Y1, "x2": X2, "y2": Y2 }`

**`gr_arc`:**
```
(gr_arc (start X1 Y1) (mid Xm Ym) (end X2 Y2) (stroke ...) (layer "Edge.Cuts") ...)
```
→ `{ "type": "arc", "startX": X1, "startY": Y1, "midX": Xm, "midY": Ym, "endX": X2, "endY": Y2 }`

Also extract `center` if present. Compute `boardWidth` and `boardHeight` from the bounding box of all outline elements (including arc control points).

### 2. Traces (Segments)

Extract all `(segment ...)` elements. Group connected segments into polylines by:
1. Group by `(net ...)` and `(layer ...)`
2. For each group, chain segments: start from one segment, find the next segment whose start or end point matches (within 0.01mm tolerance), continue until no match.
3. Output each chain as one trace with a `path` array.

**Important:** Only extract traces on copper layers (`F.Cu`, `B.Cu`, `In1.Cu`, `In2.Cu`, etc.). Skip solder mask, silkscreen, etc.

```json
{
  "net": "3",           // net ID as string
  "layer": "F.Cu",
  "width": 0.15,        // from (width ...) attribute
  "path": [
    {"x": 73.875, "y": 106.725},
    {"x": 73.9, "y": 106.7}
  ]
}
```

### 3. Vias

```json
{
  "x": 77.4,
  "y": 106.7,
  "diameter": 0.6,      // from (size D)
  "drill": 0.3,         // from (drill D)
  "net": "3",
  "layers": ["F.Cu", "B.Cu"]
}
```

### 4. Components (Footprints) — THE CRITICAL PART

Each `(footprint ...)` becomes a component. The position and rotation come from:
```
(footprint "Library:Footprint" (layer "F.Cu")
  (at X Y ROTATION)       ← component position and rotation
  ...
)
```

#### 4a. Pad Positions — MUST BE IN WORLD COORDINATES

This is where most parsers fail. The pad's `(at lx ly)` is in the **footprint's local coordinate system**. You MUST apply the component's rotation to get world coordinates.

**Algorithm for each pad:**
```
worldX = component.x + pad.localX * cos(component.rotation) - pad.localY * sin(component.rotation)
worldY = component.y + pad.localX * sin(component.rotation) + pad.localY * cos(component.rotation)
```

Where rotation is in **degrees** and must be converted to radians.

**Example:**
- Component at (115.43, 113.28), rotation = -90°
- Pad local position: (2.54, 0)
- cos(-90°) = 0, sin(-90°) = -1
- worldX = 115.43 + 2.54 * 0 - 0 * (-1) = 115.43
- worldY = 113.28 + 2.54 * (-1) + 0 * 0 = 110.74

**Edge cases to handle:**
- Pad has its own `(at lx ly padRotation)` — the padRotation affects the pad SHAPE orientation, NOT the pad position. Position is still `lx, ly`.
- Pad with `(at lx ly)` and no rotation — position is just `lx, ly`.
- Negative coordinates — valid, just pass through.
- Zero-size pads — skip them.
- Pads on different layers (`*.Cu` means all copper layers).

#### 4b. Pad Shape

Map KiCad pad shapes to output:
- `rect` → `"rect"`
- `roundrect` → `"roundrect"` (include `roundrect_rratio`)
- `circle` → `"circle"`
- `oval` → `"oval"` (has `drill_width` for oval drill)
- `trapezoid` → `"trapezoid"` (has `rect_delta_x`, `rect_delta_y`)

#### 4c. Pad Type

- `smd` → `"smd"`
- `thru_hole` → `"thru_hole"`
- `np_thru_hole` → `"np_thru_hole"` (non-plated)
- `connect` → `"smd"` (treat as SMD for rendering)
- `pmask` / `cmask` → skip

#### 4d. Drill

For through-hole pads:
```json
{
  "drill": 1.0,           // round drill diameter
  "drill_width": null,    // for oval drills, this is the X dimension
  "drill_offset_x": 0,    // offset from pad center
  "drill_offset_y": 0
}
```

For `(drill oval DX DY)` — set `drill = DX`, `drill_width = DY`.

#### 4e. Graphics — ALSO MUST BE IN WORLD COORDINATES

Every `fp_text`, `fp_line`, `fp_arc`, `fp_rect`, `fp_circle` inside a footprint must have its coordinates transformed through the component rotation.

**`fp_text` (reference, value, user):**
```
(fp_text reference "U1" (at lx ly rotation) (layer "F.SilkS") ...)
```
→ Transform `(lx, ly)` through component rotation → world coords.

**`fp_line`:**
```
(fp_line (start lx1 ly1) (end lx2 ly2) (stroke ...) (layer "F.SilkS") ...)
```
→ Transform both start and end through component rotation.

**`fp_arc`:**
```
(fp_arc (start lx1 ly1) (mid lxm lym) (end lx2 ly2) (stroke ...) (layer "F.SilkS") ...)
```
→ Transform all three points through component rotation.

**Hidden text:** If `(hide)` is present, set `"hidden": true`. Skip hidden graphics in renderer.

### 5. Nets

```json
[
  { "id": 0, "name": "" },
  { "id": 3, "name": "GND" },
  { "id": 110, "name": "/IO_Banks/IO_Buffer_B/VSENSE" }
]
```

### 6. Zones

Zones have fills with polygon outlines. Extract:
```json
{
  "net": "3",
  "layer": "F.Cu",
  "priority": 0,
  "outline": [
    {"x": 50, "y": 75},
    {"x": 130, "y": 75},
    {"x": 130, "y": 120},
    {"x": 50, "y": 120}
  ]
}
```

If the zone has `(filled_polygon ...)` nodes, use those. If only `(polygon ...)` nodes, use those. If neither exists, output `"outline": null`.

**Zone fill types:**
- `(zone ... (fill yes) ...)` — filled zone
- `(zone ... (fill no))` — not filled, skip or output outline only

### 7. Coordinate System

KiCad uses:
- **X increases rightward** ✓ (same as screen)
- **Y increases downward** ✓ (same as screen)
- **Rotation: positive = counter-clockwise** (standard math convention)

This means a component with rotation=90 has its local X axis pointing upward (negative screen Y) and local Y axis pointing rightward (positive screen X).

---

## Validation Checklist

Before outputting the JSON, verify:

- [ ] Every pad's (x, y) is in world coordinates (test: compare a few pads against KiCad's visual output)
- [ ] Every graphic's coordinates are in world coordinates
- [ ] Trace polylines are properly chained (no orphaned single-segment traces)
- [ ] Board outline forms a closed shape (all endpoints connect)
- [ ] All copper-layer traces are included (F.Cu, B.Cu, inner layers)
- [ ] Zone outlines are polygons (arrays of {x,y} points)
- [ ] Component rotations are in degrees (not radians)
- [ ] Pad shapes match KiCad's definitions
- [ ] Drill dimensions are correct (especially oval drills)
- [ ] No NaN or undefined values in coordinates
- [ ] Net names are strings (even numeric-looking ones like "3")
- [ ] Layer names match KiCad conventions ("F.Cu", "B.Cu", "In1.Cu", etc.)

---

## Edge Cases to Handle

1. **Locked footprints:** `(footprint ... locked ...)` — same parsing, just note the locked flag.
2. **Flipped components:** `(footprint ... (layer "B.Cu") ...)` — pads on back layer. Coordinates still use same world system.
3. **Negative pad positions:** Valid. A pad at local (-2.54, 0) means it's to the left of the footprint origin.
4. **Zero-width traces:** `(width 0)` — use a default of 0.1mm.
5. **Custom pad shapes:** `(pad ... (chamfer_ratio 0.5) (chamfer_corners "top_left") ...)` — note chamfer info if present.
6. **Mounting holes:** These are footprints with `np_thru_hole` pads. Include them.
7. **Fiducials:** Small circular pads. Include them.
8. **Board outline with only arcs:** Some boards are circular or have complex outlines.
9. **Multiple board outlines:** Some panels have multiple boards — take the union or list all.
10. **Zone fill priority:** Higher priority zones fill first.
11. **Pads with no net:** `(net 0 "")` — valid, output net as "0" or "".
12. **Very large boards:** Coordinates can be 100+ mm. Ensure no integer overflow.
13. **Micro-vias:** Small vias with drill < 0.15mm. Include them.
14. **Buried vias:** Vias that don't span all layers. Include layer list.
15. **Castellated holes:** Pads on board edge. Include them.

---

## Implementation Notes

- Use a proper S-expression parser (not regex). The KiCad format has nested parentheses.
- The file can be 1-10 MB. Stream if needed, but DOM parsing is fine for < 20 MB.
- Coordinate precision: 6 decimal places is sufficient (KiCad uses ~0.001mm precision).
- Output JSON should be compact (no pretty-print) for files > 1MB, pretty-printed for smaller files.
- If a footprint references a library that isn't available, still parse what you can from the inline data.
