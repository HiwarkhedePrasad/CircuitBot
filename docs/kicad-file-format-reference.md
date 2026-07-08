# KiCad File Format Reference

Complete reference for KiCad 8.x S-expression file formats. KiCad uses Lisp-style S-expressions for all file formats.

---

## 1. S-Expression Basics

**Syntax:**
```
(keyword value)
(keyword (nested value))
(keyword
  (child1 value)
  (child2 value)
)
```

**Common Patterns:**
- Coordinates: `(at x y angle)` or `(xy x y)`
- UUIDs: `(uuid "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx")`
- Strings: `"quoted string"` or `unquoted_identifier`
- Numbers: `1.234` (always decimal, no units in file)
- Booleans: `yes` / `no`

---

## 2. Schematic Format (.kicad_sch)

### File Structure

```
(kicad_sch
  (version 20231120)
  (generator "eeschema")
  (generator_version "8.0")
  (uuid "project-uuid")
  (paper "A4")
  (title_block ...)
  (lib_symbols ...)
  (symbol ...)     (placed component instances)
  (wire ...)       (wires and connections)
  (bus ...)
  (junction ...)
  (label ...)
  (global_label ...)
  (hierarchical_label ...)
  (sheet ...)
  (text ...)
  (polyline ...)
  (rectangle ...)
)
```

### Symbol Instance

```
(symbol
  (lib_id "Device:R")
  (at 100 50 0)              ; x, y, rotation (0, 90, 180, 270)
  (unit 1)                   ; Multi-unit symbols
  (exclude_from_sim no)
  (in_bom yes)
  (on_board yes)
  (dnp no)                   ; Do Not Populate
  (uuid "symbol-uuid")
  (property "Reference" "R1"
    (at 100 45 0)
    (effects (font (size 1.27 1.27)))
  )
  (property "Value" "10k" ...)
  (property "Footprint" "Resistor_SMD:R_0402_1005Metric" ...)
  (property "LCSC" "C25744" ...)
  (pin "1" (uuid "pin1-uuid"))
  (pin "2" (uuid "pin2-uuid"))
  (instances
    (project "project_name"
      (path "/root-uuid" (reference "R1") (unit 1))
    )
  )
)
```

### Wire

```
(wire
  (pts (xy 100 50) (xy 120 50))
  (stroke (width 0) (type default))
  (uuid "wire-uuid")
)
```

### Label (Local Net Name)

```
(label "VCC"
  (at 100 50 0)
  (effects (font (size 1.27 1.27)))
  (uuid "label-uuid")
)
```

### Global Label (Cross-Sheet Net)

```
(global_label "USB_D+"
  (shape input)              ; input, output, bidirectional, tri_state, passive
  (at 150 60 0)
  (effects (font (size 1.27 1.27)))
  (uuid "global-uuid")
)
```

### Power Symbol

```
(symbol
  (lib_id "power:GND")
  (at 100 80 0)
  (unit 1)
  (exclude_from_sim yes)
  (in_bom no)
  (on_board yes)
  (uuid "power-uuid")
  (property "Reference" "#PWR01" ...)
  (property "Value" "GND" ...)
  (pin "1" (uuid "..."))
)
```

---

## 3. PCB Format (.kicad_pcb)

### File Structure

```
(kicad_pcb
  (version 20231014)
  (generator "pcbnew")
  (generator_version "8.0")
  (general
    (thickness 1.6)
    (legacy_teardrops no)
  )
  (paper "A4")
  (title_block ...)
  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    ...)
  (setup ...)
  (net 0 "")
  (net 1 "GND")
  (net 2 "VCC")
  (footprint ...)
  (segment ...)
  (via ...)
  (zone ...)
  (gr_line ...)
)
```

### Footprint

```
(footprint "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"
  (layer "F.Cu")
  (uuid "footprint-uuid")
  (at 100 50 0)
  (descr "SOIC-8 package")
  (property "Reference" "U1" ...)
  (property "Value" "ESP32-S3" ...)
  (pad "1" smd rect
    (at -1.905 -2.475)
    (size 0.6 1.5)
    (layers "F.Cu" "F.Paste" "F.Mask")
    (net 1 "GND")
    (uuid "pad-uuid")
  )
)
```

### Trace (Segment)

```
(segment
  (start 100 50)
  (end 120 50)
  (width 0.25)
  (layer "F.Cu")
  (net 1)
  (uuid "segment-uuid")
)
```

### Via

```
(via
  (at 110 60)
  (size 0.8)
  (drill 0.4)
  (layers "F.Cu" "B.Cu")
  (net 1)
  (uuid "via-uuid")
)
```

---

## 4. Units and Coordinates

| Context | Unit |
|---------|------|
| Schematic | mils (1/1000 inch), stored as mm in file |
| PCB | millimeters |
| Angles | degrees (0, 90, 180, 270 typical) |

---

## 5. UUID Generation

All elements require unique UUIDs. Format: `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`

---

## 6. Parsing S-Expressions (Python)

```python
def parse_sexpr(text):
    tokens = tokenize(text)
    return parse_list(tokens)
```

## 7. File Validation

After editing:
1. Open in KiCad to verify parsing
2. Run ERC/DRC via kicad-cli
3. Check for warnings about unknown tokens
