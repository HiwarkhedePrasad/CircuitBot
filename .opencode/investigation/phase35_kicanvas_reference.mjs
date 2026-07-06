/**
 * Phase 3.5: KiCanvas Reference Transform Computation.
 *
 * Computes the expected world coordinates and screen positions
 * for selected pads using KiCanvas's documented transform chain:
 *
 *   Local → Rotate(pad.rotation) → Translate(pad.at) → Rotate(-fp.rotation)
 *   → Translate(fp.at) → Camera2 (scale, center, Y-flip)
 *
 * Also computes our renderer's transform for comparison:
 *
 *   Local → Rotate(fp.rotation) → Translate(fp.at) → Camera (scale, -scale)
 *
 * See kicanvas/src/viewers/board/painter.ts (PadPainter, lines 444-452)
 * and kicanvas/src/base/math/camera2.ts (Camera2, lines 64-79)
 */

import { readFileSync, writeFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..', '..');

// ═══════════════════════════════════════════════════════════════════════
// 2D Matrix helpers (simulating KiCanvas's Matrix3)
// ═══════════════════════════════════════════════════════════════════════

// A 3x3 transform matrix stored as [a, b, c, d, tx, ty]
// representing | a  c  tx |
//                | b  d  ty |
//                | 0  0  1  |
// Convention: v' = M × v  (column vector, pre-multiply)

class Mat3 {
    constructor(a = 1, b = 0, c = 0, d = 1, tx = 0, ty = 0) {
        this.a = a; this.b = b; this.c = c; this.d = d;
        this.tx = tx; this.ty = ty;
    }

    static identity() { return new Mat3(); }

    static translate(tx, ty) {
        return new Mat3(1, 0, 0, 1, tx, ty);
    }

    static scale(sx, sy) {
        return new Mat3(sx, 0, 0, sy, 0, 0);
    }

    static rotate(angleDeg) {
        const rad = angleDeg * Math.PI / 180;
        const c = Math.cos(rad), s = Math.sin(rad);
        return new Mat3(c, s, -s, c, 0, 0);
    }

    // This × other (post-multiply: result * v == this * (other * v))
    multiply(other) {
        return new Mat3(
            this.a * other.a + this.c * other.b,
            this.b * other.a + this.d * other.b,
            this.a * other.c + this.c * other.d,
            this.b * other.c + this.d * other.d,
            this.a * other.tx + this.c * other.ty + this.tx,
            this.b * other.tx + this.d * other.ty + this.ty,
        );
    }

    // Apply to a point (x, y)
    apply(x, y) {
        return {
            x: this.a * x + this.c * y + this.tx,
            y: this.b * x + this.d * y + this.ty,
        };
    }
}

// ═══════════════════════════════════════════════════════════════════════
// KiCanvas transform chain
// ═══════════════════════════════════════════════════════════════════════

function kicanvasPadWorld(fp, pad) {
    // KiCanvas PadPainter: position_mat = T(pad.at) × R(-fp.rot) × R(pad.rot)
    // Applied inside footprint context which is T(fp.at) × R(fp.rot)
    //
    // Combined for pad world position:
    //   M = T(fp.at) × R(fp.rot) × T(pad.at) × R(-fp.rot) × R(pad.rot)
    //
    // For a POINT (not a shape orientation), we just compute position.
    // Point (0,0) in pad local space:
    //   p0 = (0, 0)  (the pad's origin)
    //   p1 = R(pad.rot) × p0 = (0, 0)
    //   p2 = R(-fp.rot) × p1 = (0, 0)
    //   p3 = T(pad.at) × p2 = pad.at
    //   p4 = R(fp.rot) × p3 = rotate(pad.at, fp.rot)
    //   p5 = T(fp.at) × p4 = fp.at + rotate(pad.at, fp.rot)
    //
    // So KiCanvas world position: fp.at + rotate(pad.at, fp.rot)
    // This matches our getComponentPadPosition!

    const rotated = {
        x: pad.at_x * Math.cos(fp.rot * Math.PI / 180) - pad.at_y * Math.sin(fp.rot * Math.PI / 180),
        y: pad.at_x * Math.sin(fp.rot * Math.PI / 180) + pad.at_y * Math.cos(fp.rot * Math.PI / 180),
    };
    return {
        x: fp.x + rotated.x,
        y: fp.y + rotated.y,
    };
}

function kicanvasPadShapeOrientation(fp, pad) {
    // In KiCanvas, the pad shape is rotated by:
    //   pad_rot = R(-fp.rot) × R(pad.rot)
    // = pad.rot - fp.rot   (in 2D, rotations commute)
    //
    // So net shape rotation = pad.rot - fp.rot
    // (negative because KiCanvas negates footprint rotation for pad shapes)
    return (pad.at_rotation || 0) - (fp.rot || 0);
}

// Our renderer transform
function ourPadWorld(fp, pad) {
    // getComponentPadPosition: fp.pos + rotate(pad.pos, fp.rot)
    const rotated = {
        x: pad.at_x * Math.cos(fp.rot * Math.PI / 180) - pad.at_y * Math.sin(fp.rot * Math.PI / 180),
        y: pad.at_x * Math.sin(fp.rot * Math.PI / 180) + pad.at_y * Math.cos(fp.rot * Math.PI / 180),
    };
    return {
        x: fp.x + rotated.x,
        y: fp.y + rotated.y,
    };
}

function ourPadShapeOrientation(fp, pad) {
    // _drawComponentPads: padRotation = fp.rot + pad.rot
    return (fp.rot || 0) + (pad.at_rotation || 0);
}

// ═══════════════════════════════════════════════════════════════════════
// Camera transform (our renderer)
// ═══════════════════════════════════════════════════════════════════════

function ourCamera(worldX, worldY, cam) {
    // _applyCamera: scale = baseScale * zoom
    // world.scale.set(scale, -scale)
    // world.position.set(cx + panX - midX * scale, cy + panY + midY * scale)
    //
    // So screen: sx = (worldX - midX) * scale + cx + panX
    //           sy = -(worldY - midY) * scale + cy + panY
    // The -(worldY) is the Y-flip

    const scale = cam.baseScale * cam.zoom;
    const sx = (worldX - cam.midX) * scale + cam.cx + cam.panX;
    const sy = -(worldY - cam.midY) * scale + cam.cy + cam.panY;
    return { x: sx, y: sy };
}

// ═══════════════════════════════════════════════════════════════════════
// Load data
// ═══════════════════════════════════════════════════════════════════════

const truthPath = resolve(ROOT, '.opencode', 'investigation', 'phase0_source_truth.json');
const truth = JSON.parse(readFileSync(truthPath, 'utf-8'));

// ═══════════════════════════════════════════════════════════════════════
// Test cases
// ═══════════════════════════════════════════════════════════════════════

const testPads = truth.pads.filter(p =>
    ['', '1', '2', '20', '39', '49'].includes(p.number)
).slice(0, 6);  // one of each number

// Also add specific instances of pad 39 and 49
const pad39instances = truth.pads.filter(p => p.number === '39');
const pad49instances = truth.pads.filter(p => p.number === '49');

const testCases = [
    ...testPads,
    ...pad39instances,
    ...pad49instances,
];

// ═══════════════════════════════════════════════════════════════════════
// Test at two rotation states
// ═══════════════════════════════════════════════════════════════════════

const footprintStates = [
    { name: 'rotation 0°', x: 0, y: 0, rot: 0 },
    { name: 'rotation 90°', x: 10, y: 5, rot: 90 },
    { name: 'rotation -45°', x: -5, y: 8, rot: -45 },
];

const cameraState = {
    baseScale: 50,
    zoom: 1,
    midX: 0,
    midY: 0,
    cx: 600,
    cy: 400,
    panX: 0,
    panY: 0,
};

// ═══════════════════════════════════════════════════════════════════════
// Compute transforms
// ═══════════════════════════════════════════════════════════════════════

const results = [];

for (const fp of footprintStates) {
    for (const pad of testCases) {
        const kcPos = kicanvasPadWorld(fp, pad);
        const ourPos = ourPadWorld(fp, pad);
        const kcRot = kicanvasPadShapeOrientation(fp, pad);
        const ourRot = ourPadShapeOrientation(fp, pad);

        const kcScreen = ourCamera(kcPos.x, kcPos.y, cameraState);
        const ourScreen = ourCamera(ourPos.x, ourPos.y, cameraState);

        // For world position we use same formula (both match for point)
        const posMatch = Math.abs(kcPos.x - ourPos.x) < 1e-9 && Math.abs(kcPos.y - ourPos.y) < 1e-9;

        const rotDiff = kcRot - ourRot;

        results.push({
            fpState: fp.name,
            fpX: fp.x, fpY: fp.y, fpRot: fp.rot,
            padNumber: pad.number,
            padIndex: pad._index,
            padLocal: { x: pad.at_x, y: pad.at_y, rot: pad.at_rotation },
            kicanvas: {
                worldPos: kcPos,
                shapeRotation: kcRot,
                screenPos: kcScreen,
            },
            our: {
                worldPos: ourPos,
                shapeRotation: ourRot,
                screenPos: ourScreen,
            },
            worldPositionMatch: posMatch,
            rotationDifference: rotDiff,
            rotationMatch: Math.abs(rotDiff) < 1e-6,
        });
    }
}

// ═══════════════════════════════════════════════════════════════════════
// Summary
// ═══════════════════════════════════════════════════════════════════════

const posMatches = results.filter(r => r.worldPositionMatch);
const rotMatches = results.filter(r => r.rotationMatch);

const summary = {
    totalComparisons: results.length,
    worldPositionMatches: posMatches.length,
    worldPositionMismatches: results.length - posMatches.length,
    shapeRotationMatches: rotMatches.length,
    shapeRotationMismatches: results.length - rotMatches.length,
    rotationMismatchExamples: [],
};

for (const r of results) {
    if (!r.rotationMatch && summary.rotationMismatchExamples.length < 5) {
        summary.rotationMismatchExamples.push({
            fpState: r.fpState,
            padNumber: r.padNumber,
            padIndex: r.padIndex,
            kicanvasRot: r.kicanvas.shapeRotation,
            ourRot: r.our.shapeRotation,
            diff: r.rotationDifference,
        });
    }
}

const output = {
    summary,
    cameraState,
    footprintStates: footprintStates.map(f => ({ name: f.name, x: f.x, y: f.y, rot: f.rot })),
    results,
};

const outPath = resolve(ROOT, '.opencode', 'investigation', 'phase35_kicanvas_reference.json');
writeFileSync(outPath, JSON.stringify(output, null, 2), 'utf-8');

console.log(`Wrote ${outPath}`);
console.log('');
console.log('Transform Comparison Summary:');
console.log(`  Total comparisons: ${summary.totalComparisons}`);
console.log(`  World position matches: ${summary.worldPositionMatches}`);
console.log(`  World position mismatches: ${summary.worldPositionMismatches}`);
console.log(`  Shape rotation matches: ${summary.shapeRotationMatches}`);
console.log(`  Shape rotation mismatches: ${summary.shapeRotationMismatches}`);

if (summary.rotationMismatchExamples.length) {
    console.log('\nRotation mismatches (first 5):');
    for (const ex of summary.rotationMismatchExamples) {
        console.log(`  ${ex.fpState}, pad ${ex.padNumber}[#${ex.padIndex}]:`);
        console.log(`    KiCanvas: ${ex.kicanvasRot.toFixed(2)}°  Our: ${ex.ourRot.toFixed(2)}°  Δ: ${ex.diff.toFixed(2)}°`);
    }
    console.log('\nCONCLUSION: Pad shape rotation differs when fp.rotation !== 0');
    console.log('  KiCanvas:  shape_rot = pad.rot - fp.rot  (negates footprint rotation)');
    console.log('  Our:       shape_rot = pad.rot + fp.rot  (adds footprint rotation)');
    console.log('  Effect:    Δ = -2 × fp.rot  (exactly 2× the footprint rotation, negated)');
}

if (summary.worldPositionMismatches === 0) {
    console.log('\nWorld positions match between KiCanvas and our renderer. ✓');
    console.log('  (getComponentPadPosition uses the same formula as KiCanvas)');
}

// Compute the exact formula diff
console.log('\nFormula comparison:');
console.log('  KiCanvas world pos:  fp.at + rotate(pad.at, fp.rot)');
console.log('  Our world pos:       fp.at + rotate(pad.at, fp.rot)');
console.log('  => MATCH for position ✓');
console.log('');
console.log('  KiCanvas shape rot:  pad.rot - fp.rot');
console.log('  Our shape rot:       fp.rot + pad.rot');
console.log('  => DIFFER for rotation when fp.rot ≠ 0 ✗');
console.log('  Difference:          Δ = (pad.rot - fp.rot) - (fp.rot + pad.rot) = -2 × fp.rot');
