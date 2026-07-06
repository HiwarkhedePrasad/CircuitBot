/**
 * Phase 3: Geometry Builder — mock PIXI.Graphics and run drawPadShape.
 *
 * For each pad in the source of truth, determines:
 * - World position via getComponentPadPosition
 * - All PIXI draw calls that would be issued
 * - Separate drill geometry (which drawPadShape does NOT render)
 */

import { readFileSync, writeFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..', '..');

// ═══════════════════════════════════════════════════════════════════════
// Pure JS functions from utils.js
// ═══════════════════════════════════════════════════════════════════════

function rotatePoint(x, y, angleDeg) {
    const angle = (angleDeg || 0) * Math.PI / 180;
    const cos = Math.cos(angle);
    const sin = Math.sin(angle);
    return {
        x: x * cos - y * sin,
        y: x * sin + y * cos,
    };
}

function getComponentPadPosition(component, pad) {
    const rotated = rotatePoint(pad.x || 0, pad.y || 0, component.rotation || 0);
    return {
        x: component.x + rotated.x,
        y: component.y + rotated.y,
    };
}

// ═══════════════════════════════════════════════════════════════════════
// Mock PIXI.Graphics that records draw calls
// ═══════════════════════════════════════════════════════════════════════

class MockGraphics {
    constructor() {
        this.calls = [];
        this._currentFill = null;
    }

    beginFill(color, alpha = 1) {
        this._currentFill = { color, alpha };
        this.calls.push({ method: 'beginFill', args: [color, alpha] });
        return this;
    }

    endFill() {
        this._currentFill = null;
        this.calls.push({ method: 'endFill', args: [] });
        return this;
    }

    lineStyle(width, color, alpha = 1) {
        this.calls.push({ method: 'lineStyle', args: [width, color, alpha] });
        return this;
    }

    drawCircle(x, y, radius) {
        this.calls.push({
            method: 'drawCircle',
            args: [x, y, radius],
            fill: this._currentFill ? { ...this._currentFill } : null,
        });
        return this;
    }

    drawRect(x, y, w, h) {
        this.calls.push({
            method: 'drawRect',
            args: [x, y, w, h],
            fill: this._currentFill ? { ...this._currentFill } : null,
        });
        return this;
    }

    drawRoundedRect(x, y, w, h, radius) {
        this.calls.push({
            method: 'drawRoundedRect',
            args: [x, y, w, h, radius],
            fill: this._currentFill ? { ...this._currentFill } : null,
        });
        return this;
    }

    moveTo(x, y) {
        this.calls.push({ method: 'moveTo', args: [x, y] });
        return this;
    }

    lineTo(x, y) {
        this.calls.push({ method: 'lineTo', args: [x, y] });
        return this;
    }

    closePath() {
        this.calls.push({ method: 'closePath', args: [] });
        return this;
    }
}

// ═══════════════════════════════════════════════════════════════════════
// Replicate drawPadShape from utils.js
// ═══════════════════════════════════════════════════════════════════════

function drawPadShape(graphics, x, y, width, height, shape, rotation) {
    const w = Math.max(width, 0.2);
    const h = Math.max(height, 0.2);
    if (shape === 'circle') {
        graphics.drawCircle(x, y, Math.max(w, h) / 2);
        return;
    }
    if (shape === 'oval' || shape === 'roundrect') {
        const radius = Math.min(w, h) * 0.32;
        graphics.drawRoundedRect(x - w / 2, y - h / 2, w, h, radius);
        return;
    }
    if (!rotation) {
        graphics.drawRect(x - w / 2, y - h / 2, w, h);
        return;
    }
    const corners = [
        rotatePoint(-w / 2, -h / 2, rotation),
        rotatePoint(w / 2, -h / 2, rotation),
        rotatePoint(w / 2, h / 2, rotation),
        rotatePoint(-w / 2, h / 2, rotation),
    ];
    graphics.moveTo(x + corners[0].x, y + corners[0].y);
    for (let index = 1; index < corners.length; index += 1) {
        graphics.lineTo(x + corners[index].x, y + corners[index].y);
    }
    graphics.lineTo(x + corners[0].x, y + corners[0].y);
}

// ═══════════════════════════════════════════════════════════════════════
// Load Phase 0 truth
// ═══════════════════════════════════════════════════════════════════════

const truthPath = resolve(ROOT, '.opencode', 'investigation', 'phase0_source_truth.json');
const truth = JSON.parse(readFileSync(truthPath, 'utf-8'));

// Simulate the component
const component = {
    ref: 'U101',
    x: 0,
    y: 0,
    rotation: 0,
};

// For rotated variant, we also test at 90° rotation
const componentRotated = {
    ref: 'U101_rotated',
    x: 10,
    y: 5,
    rotation: 90,
};

// ═══════════════════════════════════════════════════════════════════════
// Build geometry for each pad
// ═══════════════════════════════════════════════════════════════════════

function buildPadGeometry(comp, pad) {
    const pos = getComponentPadPosition(comp, pad);
    const gfx = new MockGraphics();
    drawPadShape(gfx, pos.x, pos.y, pad.size_w, pad.size_h, pad.shape, pad.at_rotation);

    // Determine if drill geometry is needed
    const hasDrill = pad.drill_diameter != null;
    const drillGeometry = hasDrill ? [{
        type: 'circle',
        method: 'drawCircle',
        cx: pos.x,
        cy: pos.y,
        radius: pad.drill_diameter / 2,
        fill: null,
        isDrill: true,
    }] : [];

    return {
        index: pad._index,
        number: pad.number,
        shape: pad.shape,
        type: pad.type,
        localPosition: { x: pad.at_x, y: pad.at_y },
        worldPosition: pos,
        padDrawCalls: gfx.calls,
        drillGeometry,
        totalDrawCommands: gfx.calls.length + drillGeometry.length,
        hasDrill,
    };
}

const pads = [];
for (const p of truth.pads) {
    pads.push(buildPadGeometry(component, p));
}

// Also run with rotated component
const padsRotated = [];
for (const p of truth.pads) {
    padsRotated.push(buildPadGeometry(componentRotated, p));
}

// ═══════════════════════════════════════════════════════════════════════
// Summarize
// ═══════════════════════════════════════════════════════════════════════

// Count draw calls by type
const drawTypeCounts = {};
for (const pad of pads) {
    for (const call of pad.padDrawCalls) {
        drawTypeCounts[call.method] = (drawTypeCounts[call.method] || 0) + 1;
    }
}

// Count pads with drill geometry
const padsWithDrill = pads.filter(p => p.hasDrill);
const padsWithoutDrill = pads.filter(p => !p.hasDrill);

// Per number stats
const perNumber = {};
for (const pad of pads) {
    const n = pad.number;
    if (!perNumber[n]) perNumber[n] = { count: 0, drawCalls: 0, hasDrills: 0 };
    perNumber[n].count++;
    perNumber[n].drawCalls += pad.totalDrawCommands;
    if (pad.hasDrill) perNumber[n].hasDrills++;
}

// Roundrect analysis
const roundrectPads = pads.filter(p => p.shape === 'roundrect');
const roundrectRadiusValues = roundrectPads.map(p => {
    const rounded = p.padDrawCalls.find(c => c.method === 'drawRoundedRect');
    return rounded ? rounded.args[4] : null;
});

// Check the hardcoded radius vs expected (0.25 * min(w,h))
const roundrectExpectActual = roundrectPads.map((p, i) => {
    const truthPad = truth.pads.find(t => t._index === p.index);
    const expectedRratio = truthPad?.roundrect_rratio ?? 0.25;
    const actualRadius = roundrectRadiusValues[i];
    const expectedRadius = Math.min(p.worldPosition ? truth.pads.find(t => t._index === p.index)?.size_w || truth.pads[p.index]?.size_w : 0.5,
                                    truth.pads.find(t => t._index === p.index)?.size_h || truth.pads[p.index]?.size_h) * expectedRratio;
    const actualMin = Math.min(
        truth.pads.find(t => t._index === p.index)?.size_w || 0.5,
        truth.pads.find(t => t._index === p.index)?.size_h || 0.5,
    );
    const hardcodedRadius = actualMin * 0.32;
    return {
        index: p.index,
        number: p.number,
        expectedRratio,
        expectedRadius,
        hardcodedRadius,
        actualRadius,
        radiusMismatch: Math.abs(actualRadius - expectedRadius) > 1e-6,
    };
});

// ═══════════════════════════════════════════════════════════════════════
// Output
// ═══════════════════════════════════════════════════════════════════════

const output = {
    summary: {
        totalPads: pads.length,
        totalDrawCommands: pads.reduce((s, p) => s + p.totalDrawCommands, 0),
        padDrawCallTypes: drawTypeCounts,
        padsWithDrill: padsWithDrill.length,
        padsWithoutDrill: padsWithoutDrill.length,
        roundrectPadCount: roundrectPads.length,
        roundrectRadiusIssues: roundrectExpectActual.filter(r => r.radiusMismatch).length,
        perNumber,
    },
    roundrectAnalysis: roundrectExpectActual,
    pads,
    padsRotated,
};

const outPath = resolve(ROOT, '.opencode', 'investigation', 'phase3_geometry.json');
writeFileSync(outPath, JSON.stringify(output, null, 2), 'utf-8');

console.log(`Wrote ${outPath}`);
console.log('');
console.log('Summary:');
console.log(`  Total pads: ${output.summary.totalPads}`);
console.log(`  Total draw commands: ${output.summary.totalDrawCommands}`);
console.log(`  Draw call types:`, output.summary.padDrawCallTypes);
console.log(`  Pads with drill: ${output.summary.padsWithDrill}`);
console.log(`  Pads without drill: ${output.summary.padsWithoutDrill}`);
console.log(`  Roundrect pads: ${output.summary.roundrectPadCount}`);
console.log(`  Roundrect radius issues: ${output.summary.roundrectRadiusIssues}`);

if (roundrectExpectActual.some(r => r.radiusMismatch)) {
    console.log('\nRoundRect radius mismatches:');
    for (const r of roundrectExpectActual.filter(r => r.radiusMismatch).slice(0, 5)) {
        console.log(`  Pad ${r.number}[#${r.index}]: expected=${r.expectedRadius.toFixed(4)}, hardcoded=${r.hardcodedRadius.toFixed(4)}, actual=${r.actualRadius.toFixed(4)}`);
    }
}

// Also check if drills are rendered at all
const noDrillGeometry = padsWithDrill.filter(p => p.drillGeometry.length === 0);
console.log(`\nThru-hole pads with NO drill geometry: ${noDrillGeometry.length}`);
if (noDrillGeometry.length > 0) {
    console.log('  BUG: drawPadShape() does not render drill holes!');
}
