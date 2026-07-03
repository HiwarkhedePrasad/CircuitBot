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
        this.children = [];
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
}

class Text {
    constructor() {
        this.scale = { set() {} };
        this.anchor = { set() {} };
    }
}

const context = {
    console,
    GRID_SIZE: 1.27,
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
    `${rendererSource}\nglobalThis.TestRenderer = SchematicRenderer;`,
    context
);

const renderer = Object.create(context.TestRenderer.prototype);
renderer._zoom = 1;
renderer._app = { screen: { width: 800, height: 600 } };
renderer._world = { position: { x: 0, y: 0 } };
renderer._gridLayer = new Container();
renderer._wireLayer = new Container();
renderer._symbolLayer = new Container();
renderer._pinLayer = new Container();
renderer._textLayer = new Container();
renderer._overlayLayer = new Container();
renderer._selectedComp = null;
renderer._schematic = {
    components: [{
        refDesignator: 'R1',
        x: 0,
        y: 0,
        ops: [[
            'rectangle',
            ['start', '-5', '-3'],
            ['end', '5', '3'],
        ], [
            'pin',
            ['at', '-5', '0', '180'],
            ['length', '2.54'],
            ['name', 'A'],
            ['number', '1'],
        ]],
        geomBBox: { x: -8, y: -4, w: 14, h: 8 },
    }],
    wirePaths: [{
        path: [
            { x: -10, y: 0 },
            { x: 0, y: 0 },
            { x: 0, y: 10 },
        ],
    }],
    junctionPoints: [],
    powerLabels: [],
};

assert.doesNotThrow(() => renderer._fullRedraw());
assert.strictEqual(renderer._symbolLayer.children.length, 1);
assert.strictEqual(renderer._wireLayer.children.length, 2);
assert.strictEqual(renderer._pinHitTargets.length, 1);
assert.strictEqual(renderer.hitTestPin(-5, 0).key, 'R1:1');
assert.doesNotThrow(() => renderer.setWireDraft(renderer._pinHitTargets[0], { x: 0, y: 0 }));
assert.ok(renderer._overlayLayer.children.length >= 1);
assert.doesNotThrow(() => renderer.setActivePin(renderer._pinHitTargets[0]));

console.log('schematic renderer regression test passed');
