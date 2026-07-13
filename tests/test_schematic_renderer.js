const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

class Container {
    constructor() {
        this.children = [];
    }

    addChild(...children) {
        this.children.push(...children);
    }

    removeChildren() {
        const prev = this.children;
        this.children = [];
        return prev;
    }
}

class Graphics {
    lineStyle() { return this; }
    moveTo() { return this; }
    lineTo() { return this; }
    beginFill() { return this; }
    endFill() { return this; }
    drawCircle() { return this; }
    drawRect() { return this; }
    clear() { return this; }
    destroy() {}
}

class Text {
    constructor() {
        this.scale = { set() {} };
        this.anchor = { set() {} };
    }
    destroy() {}
}

// Minimal schematic.js helpers (same semantics as static/schematic.js)
function sexprStr(val) {
    if (val == null) return '';
    return String(val).replace(/^"+|"+$/g, '').trim();
}
function isHiddenSexprText(val) {
    const s = sexprStr(val);
    return !s || s === '~';
}
function computeJunctionPoints(wirePaths) {
    const GRID_SIZE = 1.27;
    const counts = new Map();
    const keyOf = (x, y) => {
        const sx = Math.round(x / GRID_SIZE) * GRID_SIZE;
        const sy = Math.round(y / GRID_SIZE) * GRID_SIZE;
        return `${sx.toFixed(4)},${sy.toFixed(4)}`;
    };
    const bump = (x, y) => {
        const k = keyOf(x, y);
        counts.set(k, (counts.get(k) || 0) + 1);
    };
    for (const wire of wirePaths || []) {
        const path = wire.path || [];
        if (path.length < 2) continue;
        bump(path[0].x, path[0].y);
        bump(path[path.length - 1].x, path[path.length - 1].y);
        for (let i = 1; i < path.length - 1; i++) bump(path[i].x, path[i].y);
    }
    const junctions = [];
    for (const [k, n] of counts) {
        if (n < 3) continue;
        const [xs, ys] = k.split(',');
        junctions.push({ x: parseFloat(xs), y: parseFloat(ys) });
    }
    return junctions;
}

const context = {
    console,
    GRID_SIZE: 1.27,
    sexprStr,
    isHiddenSexprText,
    computeJunctionPoints,
    requestAnimationFrame: (fn) => { fn(); return 0; },
    cancelAnimationFrame: () => {},
    PIXI: {
        Container,
        Graphics,
        Text,
    },
    getAttr(node, name) {
        return node.slice(1).find(
            child => Array.isArray(child) && child[0] === name
        ) || null;
    },
};

vm.createContext(context);
const rendererSource = fs.readFileSync('static/schematic_renderer.js', 'utf8');
vm.runInContext(
    `${rendererSource}\nglobalThis.TestRenderer = SchematicRenderer;\nglobalThis.resolveFillType = resolveFillType;\nglobalThis.isSexprHidden = isSexprHidden;`,
    context
);

// ── Fill parsing ────────────────────────────────────────────────────────────
assert.strictEqual(
    context.resolveFillType(['fill', ['type', 'background']]),
    'background',
    'nested fill type background'
);
assert.strictEqual(
    context.resolveFillType(['fill', ['type', 'solid']]),
    'solid',
    'nested fill type solid'
);
assert.strictEqual(
    context.resolveFillType(['fill', '(type background)']),
    'background',
    'legacy string fill type'
);

// ── Hide detection ──────────────────────────────────────────────────────────
assert.ok(context.isSexprHidden(['property', 'Footprint', '', ['hide', 'yes']]));
assert.ok(!context.isSexprHidden(['property', 'Reference', 'R1', ['at', '0', '0', '0']]));
assert.ok(context.isSexprHidden([
    'property', 'Value', 'x',
    ['effects', ['font', ['size', '1.27', '1.27']], 'hide'],
]));

// ── Junction detection ──────────────────────────────────────────────────────
const junc = computeJunctionPoints([
    { path: [{ x: 0, y: 0 }, { x: 10, y: 0 }] },
    { path: [{ x: 0, y: 0 }, { x: 0, y: 10 }] },
    { path: [{ x: 0, y: 0 }, { x: -10, y: 0 }] },
]);
assert.strictEqual(junc.length, 1);
assert.ok(Math.abs(junc[0].x) < 0.01 && Math.abs(junc[0].y) < 0.01);

// ── Renderer smoke ──────────────────────────────────────────────────────────
const renderer = Object.create(context.TestRenderer.prototype);
renderer._zoom = 1;
renderer._app = { screen: { width: 800, height: 600 }, renderer: { resize() {} } };
renderer._world = { position: { x: 0, y: 0 }, scale: { set() {} } };
renderer._gridLayer = new Container();
renderer._wireLayer = new Container();
renderer._symbolLayer = new Container();
renderer._pinLayer = new Container();
renderer._textLayer = new Container();
renderer._overlayLayer = new Container();
renderer._selectedComp = null;
renderer._pinHitTargets = [];
renderer._dragCompRef = null;
renderer._dragDelta = { dx: 0, dy: 0 };
renderer._wireDraft = null;
renderer._hoverPin = null;
renderer._activePin = null;
renderer._gridDirty = false;
renderer._gridRaf = 0;
renderer._contentCenter = { x: 0, y: 0 };
renderer._panOffset = { x: 0, y: 0 };
renderer._symbolStyle = { standard: 'ansi' };
renderer._callbacks = {};
renderer._schematic = {
    components: [{
        refDesignator: 'R1',
        lib_id: 'Device:R',
        id: 'Device:R',
        x: 0,
        y: 0,
        ops: [[
            'rectangle',
            ['start', '-1.016', '-2.54'],
            ['end', '1.016', '2.54'],
            ['fill', ['type', 'background']],
        ], [
            'pin',
            ['at', '0', '3.81', '270'],
            ['length', '1.27'],
            ['name', '~'],
            ['number', '1'],
        ], [
            'pin',
            ['at', '0', '-3.81', '90'],
            ['length', '1.27'],
            ['name', '~'],
            ['number', '2'],
        ], [
            'property',
            'Reference',
            'R1',
            ['at', '2', '0', '90'],
        ], [
            'property',
            'Footprint',
            'R_0805',
            ['hide', 'yes'],
            ['at', '0', '0', '0'],
        ]],
        geomBBox: { x: -3, y: -5, w: 6, h: 10 },
    }],
    wirePaths: [{
        source: 'R1:1',
        target: 'R1:2',
        path: [
            { x: 0, y: 3.81 },
            { x: 5, y: 3.81 },
            { x: 5, y: -3.81 },
            { x: 0, y: -3.81 },
        ],
    }],
    junctionPoints: [{ x: 0, y: 0 }],
    powerLabels: [{ x: 0, y: 5, net: 'VCC', dir: 'up' }],
};

assert.doesNotThrow(() => renderer._fullRedraw());
assert.ok(renderer._symbolLayer.children.length >= 1, 'symbols drawn');
assert.ok(renderer._wireLayer.children.length >= 1, 'wires drawn');
assert.strictEqual(renderer._pinHitTargets.length, 2, 'two pin hit targets');
assert.strictEqual(renderer.hitTestPin(0, 3.81).key, 'R1:1');
assert.doesNotThrow(() => renderer.setWireDraft(renderer._pinHitTargets[0], { x: 0, y: 0 }));
assert.ok(renderer._overlayLayer.children.length >= 1);
assert.doesNotThrow(() => renderer.setActivePin(renderer._pinHitTargets[0]));
assert.doesNotThrow(() => renderer._partialRedraw());

console.log('schematic renderer regression test passed');
