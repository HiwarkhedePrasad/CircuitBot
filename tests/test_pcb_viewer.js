const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const constantsSource = fs.readFileSync('static/pcb_view/constants.js', 'utf8');
const stateSource = fs.readFileSync('static/pcb_view/state.js', 'utf8');
const utilsSource = fs.readFileSync('static/pcb_view/utils.js', 'utf8');

const context = {
    console,
    window: {
        dispatchEvent() {},
    },
    document: {},
    CustomEvent: function CustomEvent(name, payload) {
        this.type = name;
        this.detail = payload.detail;
    },
};

vm.createContext(context);
vm.runInContext(constantsSource, context);
vm.runInContext(`${stateSource}
globalThis.pcbState = pcbState;
`, context);
vm.runInContext(`${utilsSource}
globalThis.__PCB_TEST__ = {
    snapToGrid,
    rotatePoint,
    routePoint,
    appendRoutePoint,
    getComponentPadPosition,
    getPadPositionByPinKey,
    getComponentBounds,
    normalizeBoardModel,
    compactFootprintName,
    modelBounds,
    dedupePath,
    ensurePcbLayerVisibility,
    isPcbLayerVisible,
    setPcbLayerVisible,
    sortedBoardLayerNames,
    getPcbLayerLabel,
    getPcbLayerColor,
};
`, context);

const helpers = context.__PCB_TEST__;

assert.strictEqual(helpers.snapToGrid(1.31), 1.27);
const rotated = helpers.rotatePoint(1, 0, 90);
assert.ok(Math.abs(rotated.x) < 1e-9);
assert.ok(Math.abs(rotated.y - (-1)) < 1e-9);

assert.deepStrictEqual(
    JSON.parse(JSON.stringify(helpers.routePoint({ x: 5.1, y: 2.5 }))),
    { x: 5.08, y: 2.54 }
);

const appended = JSON.parse(JSON.stringify(
    helpers.appendRoutePoint([{ x: 0, y: 0 }], { x: 5.08, y: 2.54 })
));
assert.deepStrictEqual(appended, [
    { x: 0, y: 0 },
    { x: 5.08, y: 2.54 },
]);

const center = JSON.parse(JSON.stringify(
    helpers.getComponentPadPosition(
        { x: 10, y: 20, rotation: 90 },
        { x: 1, y: 0, rotation: 0 }
    )
));
assert.deepStrictEqual(center, { x: 10, y: 19 });

const centerWithPadRotation = JSON.parse(JSON.stringify(
    helpers.getComponentPadPosition(
        { x: 10, y: 20, rotation: 90 },
        { x: 1, y: 0, rotation: 90 }
    )
));
assert.deepStrictEqual(centerWithPadRotation, { x: 10, y: 19 });

const padFromPinKey = JSON.parse(JSON.stringify(
    helpers.getPadPositionByPinKey({
        components: [{
            ref: 'U1',
            x: 10,
            y: 20,
            rotation: 90,
            pads: [{ number: '1', x: 1, y: 0, width: 1, height: 1 }],
        }],
    }, 'U1:1')
));
assert.deepStrictEqual(padFromPinKey, { x: 10, y: 19 });

const bounds = JSON.parse(JSON.stringify(
    helpers.getComponentBounds({
        ref: 'R1',
        x: 0,
        y: 0,
        rotation: 0,
        pads: [
            { x: -1, y: 0, width: 1.2, height: 0.8 },
            { x: 1, y: 0, width: 1.2, height: 0.8 },
        ],
        graphics: [
            { kind: 'fp_line', start: { x: -2, y: -1 }, end: { x: 2, y: -1 } },
        ],
    })
));
assert.ok(bounds.minX < -2.5);
assert.ok(bounds.maxX > 2.5);

const normalized = JSON.parse(JSON.stringify(
    helpers.normalizeBoardModel({
        components: [{
            ref: 'U1',
            x: '10',
            y: '20',
            rotation: '90',
            pads: [{ number: '1', x: '1', y: '0', width: '1.2', height: '0.8', layers: null }],
            graphics: [],
        }],
        traces: [{ width: '0.254', path: [[0, 0], { x: 1.27, y: 0 }, null] }],
        vias: [{ x: '1', y: '2', drill: '0.3', diameter: '0.7' }],
        outline_segments: [{ kind: 'gr_line', start: [0, 0], end: { x: 5, y: 0 } }],
    })
));
assert.deepStrictEqual(normalized.traces[0].path, [{ x: 0, y: 0 }, { x: 1.27, y: 0 }]);
assert.deepStrictEqual(normalized.outline_segments[0].start, { x: 0, y: 0 });
assert.deepStrictEqual(normalized.components[0].pads[0].layers, ['F.Cu']);
assert.strictEqual(helpers.compactFootprintName('Resistor_SMD:R_0805_2012Metric'), '0805 2012Metric');

const visualBounds = JSON.parse(JSON.stringify(
    helpers.modelBounds({
        components: [{ x: 10, y: 20, rotation: 0, pads: [{ x: 0, y: 0, width: 1, height: 1 }], graphics: [] }],
        outline_segments: [{ kind: 'gr_line', start: { x: -5, y: -4 }, end: { x: 5, y: 4 } }],
    })
));
assert.deepStrictEqual(visualBounds, { minX: -5, minY: -4, maxX: 12.5, maxY: 22.5 });

const deduped = JSON.parse(JSON.stringify(
    helpers.dedupePath([{ x: 0, y: 0 }, { x: 0, y: 0 }, { x: 1.27, y: 0 }, { x: 1.27, y: 0 }])
));
assert.deepStrictEqual(deduped, [{ x: 0, y: 0 }, { x: 1.27, y: 0 }]);

context.pcbState.boardModel = normalized;
helpers.ensurePcbLayerVisibility(normalized);
assert.strictEqual(helpers.isPcbLayerVisible('F.Cu'), true);
assert.strictEqual(helpers.isPcbLayerVisible('Edge.Cuts'), true);
assert.strictEqual(helpers.isPcbLayerVisible('B.SilkS'), false);
helpers.setPcbLayerVisible('B.SilkS', true);
assert.strictEqual(helpers.isPcbLayerVisible('B.SilkS'), true);
const sortedLayers = JSON.parse(JSON.stringify(helpers.sortedBoardLayerNames({
    traces: [{ layer: 'B.Cu' }, { layer: 'F.Cu' }],
    components: [{
        layer: 'F.Cu',
        pads: [{ layers: ['F.Cu', 'F.Mask'] }],
        graphics: [{ layer: 'F.SilkS' }],
    }],
    vias: [{ layers: ['F.Cu', 'B.Cu'] }],
    outline_segments: [{ layer: 'Edge.Cuts' }],
})));
assert.ok(sortedLayers.includes('F.Cu'));
assert.ok(sortedLayers.includes('B.Cu'));
assert.strictEqual(helpers.getPcbLayerLabel('F.Cu'), 'TOP');
assert.strictEqual(helpers.getPcbLayerColor('Edge.Cuts'), '#19d7b0');

console.log('pcb viewer helper test passed');
