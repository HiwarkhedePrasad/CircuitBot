"""Audit pad 49 instances: render order, positions, and collision analysis."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRUTH = json.loads((ROOT / ".opencode" / "investigation" / "phase0_source_truth.json").read_text("utf-8"))

# Show all pad 49 instances in render order
print("Pad 49 instances (in render order):")
print(f"{'Idx':>4} {'Num':>4} {'Type':>10} {'Shape':>10} {'X':>8} {'Y':>8} "
      f"{'SizeW':>6} {'SizeH':>6} {'Drill':>6} {'Layers':>25}")
print("-" * 100)

thru = []
rects = []
for p in TRUTH["pads"]:
    if p["number"] == "49":
        layers = ",".join(p.get("layers", []))[:25]
        drill = str(p.get("drill_diameter", ""))
        print(f'{p["_index"]:>4} {p["number"]:>4} {p["type"]:>10} {p["shape"]:>10} '
              f'{p["at_x"]:>8.3f} {p["at_y"]:>8.3f} {p["size_w"]:>6.2f} '
              f'{p["size_h"]:>6.2f} {drill:>6} {layers:>25}')
        if p["type"] == "thru_hole":
            thru.append(p)
        elif p["type"] == "smd":
            rects.append(p)

print()

# Render order analysis
print("=" * 60)
print("RENDER ORDER ANALYSIS")
print("=" * 60)
print()
all_p49 = [p for p in TRUTH["pads"] if p["number"] == "49"]
print(f"Pad 49 instances: {len(all_p49)} total")
print(f"  First 16: {len(thru)} thru_hole circle (thermal vias) — drawn FIRST")
print(f"  Last 2:   {len(rects)} smd rect (large thermal pads) — drawn LAST")
print()
print("CRITICAL: The 2 large smd rect pads (3.7x3.7mm) are drawn AFTER the 16")
print("thru_hole circle pads (0.5mm diameter). The large rect pads will")
print("COMPLETELY COVER the thermal vias underneath because both are at (0,0).")
print()
print("In _drawComponentPads (editor.js:643-671), each pad iteration:")
print("  1. Creates mask (semi-transparent, slightly larger)")
print("  2. Creates padGraphic (opaque copper fill)")
print("  3. If thru_hole: creates hole circle (dark center)")
print("")
print("For the large F.Cu rect pad (index 29 in pad 49 list):")
print("  - mask: drawRoundedRect at (0,0) size 4.04x4.04")
print("  - padGraphic: drawRect at (0,0) size 3.7x3.7, opaque fill")
print("  - This opaque rect HIDES all 16 thermal vias drawn before it")
print()

# Unique positions
thru_positions = [(p["at_x"], p["at_y"]) for p in thru]
unique_pos = set(thru_positions)
print(f"Thermal via positions: {len(thru_positions)} entries, {len(unique_pos)} unique")
print()
for x, y in sorted(unique_pos):
    count = thru_positions.count((x, y))
    print(f"  ({x:>7.4f}, {y:>7.4f}) × {count}")
print()

# Check grid
xs = sorted(set(x for x, y in unique_pos))
ys = sorted(set(y for x, y in unique_pos))
print(f"Grid X range: {min(xs):.4f} to {max(xs):.4f} (step ~{xs[1]-xs[0]:.4f})")
print(f"Grid Y range: {min(ys):.4f} to {max(ys):.4f} (step ~{ys[1]-ys[0]:.4f})")
print(f"Expected grid: {len(xs)} × {len(ys)} = {len(xs)*len(ys)}")
print(f"Actual points: {len(unique_pos)}")
print()

all_combos = [(x, y) for x in xs for y in ys]
missing = sorted(set(all_combos) - unique_pos)
if missing:
    print(f"MISSING grid positions: {len(missing)}")
    for x, y in missing:
        print(f"  ({x:.4f}, {y:.4f})")
else:
    print("All grid positions present — full 4×4 array")
print()

# KiCanvas comparison: how does KiCanvas handle this?
print("=" * 60)
print("KICANVAS COMPARISON")
print("=" * 60)
print()
print("KiCanvas renders pads per-layer. Thermal vias (layers=*.Cu, *.Mask, *.Paste)")
print("and large thermal pad (layers=F.Cu, F.Mask) are on DIFFERENT layers.")
print("PadPainter.layers_for() maps *.Cu to pads_front + pads_back layers.")
print("So thermal vias render on pads_front layer, and the large pad also renders")
print("on pads_front layer.")
print()
print("However, KiCanvas's canvas renderer handles overlapping shapes by")
print("alpha-blending. The thermal vias are drawn as circles (copper + hole)")
print("and the large pad is drawn as a rect. Both are visible.")
print()
print("In our renderer, PIXI draws shapes in sequence: later shapes OVERWRITE")
print("earlier ones at the same position if they are opaque fill.")
print("The large rect pad (opaque) completely covers the vias.")
print()

# The fix
print("=" * 60)
print("ROOT CAUSE")
print("=" * 60)
print()
print("Bug B7: Thermal vias hidden under large thermal pad")
print("  File: static/pcb_view/editor.js, _drawComponentPads (line 643)")
print("  Cause: Large smd rect pads (3.7mm) are drawn AFTER the 16")
print("         thermal via circles (0.5mm), covering them entirely.")
print("  Fix:   Either:")
print("    1. Draw large pads FIRST, then vias on top (reorder)")
print("    2. Or cut holes in the large pad for each via (complex)")
print("    3. Or render via drills as separate layer on top of all pads")
print()

# Show all z-order
print("Z-ORDER (bottom to top):")
for i, p in enumerate(all_p49):
    label = f"via #{i}" if p["type"] == "thru_hole" else f"rect #{i} ({p['layers'][0]})"
    print(f"  Layer {i}: {p['type']:>10} {p['shape']:>8} at ({p['at_x']:.3f},{p['at_y']:.3f}) size {p['size_w']}x{p['size_h']} — {label}")
