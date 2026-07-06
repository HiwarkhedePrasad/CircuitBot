/**
 * Phase 2: Normalizer comparison.
 *
 * Loads the source-of-truth pads, wraps them in a BoardComponent,
 * runs normalizeBoardModel() (extracted from utils.js), and compares
 * count + values before/after.
 */

import { readFileSync, writeFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..', '..');

// ═══════════════════════════════════════════════════════════════════════
// Pure JS functions extracted from static/pcb_view/utils.js
// (only those needed by normalizeBoardModel)
// ═══════════════════════════════════════════════════════════════════════

function deepClone(value) {
    return JSON.parse(JSON.stringify(value));
}

function toFiniteNumber(value, fallback = 0) {
    const num = Number(value);
    return Number.isFinite(num) ? num : fallback;
}

function normalizePoint(point) {
    if (Array.isArray(point) && point.length >= 2) {
        return { x: toFiniteNumber(point[0]), y: toFiniteNumber(point[1]) };
    }
    if (point && typeof point === 'object' && 'x' in point && 'y' in point) {
        return { x: toFiniteNumber(point.x), y: toFiniteNumber(point.y) };
    }
    return null;
}

function normalizeCopperLayerName(layer, fallback = 'F.Cu') {
    const raw = String(layer || fallback).trim();
    const aliases = {
        front_c: 'F.Cu',
        front_copper: 'F.Cu',
        f_cu: 'F.Cu',
        top: 'F.Cu',
        top_copper: 'F.Cu',
        back_c: 'B.Cu',
        back_copper: 'B.Cu',
        b_cu: 'B.Cu',
        bottom: 'B.Cu',
        bottom_copper: 'B.Cu',
    };
    return aliases[raw.toLowerCase()] || raw;
}

function normalizeBoardModel(boardModel) {
    const model = deepClone(boardModel || {});
    model.components = Array.isArray(model.components) ? model.components : [];
    model.traces = Array.isArray(model.traces) ? model.traces : [];
    model.vias = Array.isArray(model.vias) ? model.vias : [];
    model.nets = Array.isArray(model.nets) ? model.nets : [];
    model.outline_segments = Array.isArray(model.outline_segments) ? model.outline_segments : [];
    for (const component of model.components) {
        component.x = toFiniteNumber(component.x);
        component.y = toFiniteNumber(component.y);
        component.rotation = toFiniteNumber(component.rotation);
        component.pads = Array.isArray(component.pads) ? component.pads : [];
        component.graphics = Array.isArray(component.graphics) ? component.graphics : [];
        for (const pad of component.pads) {
            pad.x = toFiniteNumber(pad.x);
            pad.y = toFiniteNumber(pad.y);
            pad.width = toFiniteNumber(pad.width, 1);
            pad.height = toFiniteNumber(pad.height, 1);
            pad.rotation = toFiniteNumber(pad.rotation);
            if (pad.drill != null) pad.drill = toFiniteNumber(pad.drill, 0);
            pad.layers = (Array.isArray(pad.layers) ? pad.layers : ['F.Cu'])
                .map((layer) => normalizeCopperLayerName(layer));
        }
    }
    for (const trace of model.traces) {
        trace.layer = normalizeCopperLayerName(trace.layer, 'F.Cu');
        trace.width = toFiniteNumber(trace.width, 0.254);
        trace.path = (Array.isArray(trace.path) ? trace.path : [])
            .map(normalizePoint)
            .filter(Boolean);
    }
    for (const via of model.vias) {
        via.x = toFiniteNumber(via.x);
        via.y = toFiniteNumber(via.y);
        via.drill = toFiniteNumber(via.drill, 0.3);
        via.diameter = toFiniteNumber(via.diameter, 0.6);
        via.layers = (Array.isArray(via.layers) ? via.layers : ['F.Cu', 'B.Cu'])
            .map((layer) => normalizeCopperLayerName(layer));
    }
    for (const segment of model.outline_segments) {
        for (const key of ['start', 'end', 'center', 'mid']) {
            if (segment[key]) segment[key] = normalizePoint(segment[key]);
        }
        segment.points = (Array.isArray(segment.points) ? segment.points : [])
            .map(normalizePoint)
            .filter(Boolean);
    }
    return model;
}

// ═══════════════════════════════════════════════════════════════════════
// Load Phase 0 truth → build a BoardComponent
// ═══════════════════════════════════════════════════════════════════════

const truthPath = resolve(ROOT, '.opencode', 'investigation', 'phase0_source_truth.json');
const truth = JSON.parse(readFileSync(truthPath, 'utf-8'));

// Build a minimal board model from source-of-truth pads
const inputBoard = {
    components: [
        {
            ref: 'U101',
            footprint: 'QFN-48-1EP_5x5mm_P0.35mm_EP3.7x3.7mm_ThermalVias',
            x: 0,
            y: 0,
            rotation: 0,
            layer: 'F.Cu',
            value: 'ESP32',
            pads: truth.pads.map(p => ({
                number: p.number,
                x: p.at_x,
                y: p.at_y,
                width: p.size_w,
                height: p.size_h,
                shape: p.shape,
                type: p.type,
                rotation: p.at_rotation,
                drill: p.drill_diameter,
                layers: p.layers,
            })),
            graphics: [],
        },
    ],
};

// Record the input pad count per number
function countPads(comp) {
    const counts = {};
    for (const pad of comp.pads) {
        counts[pad.number] = (counts[pad.number] || 0) + 1;
    }
    return counts;
}

const inputCounts = countPads(inputBoard.components[0]);

// ═══════════════════════════════════════════════════════════════════════
// Run normalizer
// ═══════════════════════════════════════════════════════════════════════

const normalized = normalizeBoardModel(inputBoard);
const outputComp = normalized.components[0];
const outputCounts = countPads(outputComp);

// ═══════════════════════════════════════════════════════════════════════
// Compare
// ═══════════════════════════════════════════════════════════════════════

const deltas = [];

for (let i = 0; i < Math.max(inputBoard.components[0].pads.length, outputComp.pads.length); i++) {
    const input = inputBoard.components[0].pads[i] || {};
    const output = outputComp.pads[i] || {};

    if (!input.x && !output.x) continue;

    const diff = {
        index: i,
        number: input.number ?? output.number,
    };

    // Check for value drift
    const numFields = ['x', 'y', 'width', 'height', 'rotation'];
    const valDiffs = {};
    for (const f of numFields) {
        const iv = input[f] ?? 0;
        const ov = output[f] ?? 0;
        if (Math.abs(iv - ov) > 1e-9) {
            valDiffs[f] = { input: iv, output: ov };
        }
    }
    if (Object.keys(valDiffs).length) diff.value_diffs = valDiffs;

    // Check drill
    if (input.drill !== output.drill) {
        diff.drill_diff = { input: input.drill, output: output.drill };
    }

    // Check layers
    if (JSON.stringify(input.layers) !== JSON.stringify(output.layers)) {
        diff.layers_diff = { input: input.layers, output: output.layers };
    }

    // Check shape/type — these are not in normalization but preserved
    if (input.shape !== output.shape) {
        diff.shape_diff = { input: input.shape, output: output.shape };
    }
    if (input.type !== output.type) {
        diff.type_diff = { input: input.type, output: output.type };
    }

    if (Object.keys(diff).length > 2) { // more than just index + number
        deltas.push(diff);
    }
}

// ═══════════════════════════════════════════════════════════════════════
// Summary
// ═══════════════════════════════════════════════════════════════════════

const summary = {
    input_pad_count: inputBoard.components[0].pads.length,
    output_pad_count: outputComp.pads.length,
    count_match: inputBoard.components[0].pads.length === outputComp.pads.length,
    input_pad_counts_by_number: inputCounts,
    output_pad_counts_by_number: outputCounts,
    per_number_match: JSON.stringify(inputCounts) === JSON.stringify(outputCounts),
    value_deltas: deltas.length,
    layer_deltas: deltas.filter(d => d.layers_diff).length,
    shape_type_deltas: deltas.filter(d => d.shape_diff || d.type_diff).length,
};

const output = {
    summary,
    input_model: inputBoard,
    output_model: normalized,
    deltas,
};

const outPath = resolve(ROOT, '.opencode', 'investigation', 'phase2_normalized.json');
writeFileSync(outPath, JSON.stringify(output, null, 2), 'utf-8');

console.log(`Wrote ${outPath}`);
console.log('');
console.log('Summary:');
console.log(`  Input pad count:  ${summary.input_pad_count}`);
console.log(`  Output pad count: ${summary.output_pad_count}`);
console.log(`  Count match:      ${summary.count_match}`);
console.log(`  Per-number match: ${summary.per_number_match}`);
console.log(`  Value deltas:     ${summary.value_deltas}`);
console.log(`  Layer deltas:     ${summary.layer_deltas}`);
console.log(`  Shape/type deltas: ${summary.shape_type_deltas}`);

if (deltas.length) {
    console.log('\nSample deltas:');
    for (const d of deltas.slice(0, 5)) {
        console.log(`  Pad #${d.index} (number="${d.number}"):`, JSON.stringify(d));
    }
}
