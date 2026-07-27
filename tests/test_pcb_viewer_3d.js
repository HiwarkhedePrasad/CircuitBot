const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

// Mock browser / WebGL environment for 3D tests
class MockVector3 {
    constructor(x=0, y=0, z=0) { this.x = x; this.y = y; this.z = z; }
    set(x, y, z) { this.x = x; this.y = y; this.z = z; return this; }
    copy(v) { this.x = v.x; this.y = v.y; this.z = v.z; return this; }
    length() { return Math.sqrt(this.x*this.x + this.y*this.y + this.z*this.z); }
}

class MockColor {
    constructor(c) { this.color = c; }
}

class MockMaterial {
    constructor(opts={}) {
        Object.assign(this, opts);
        this.wireframe = false;
        this.emissive = new MockColor(0);
        this.emissiveIntensity = 0;
    }
    dispose() {}
    clone() { return new MockMaterial(this); }
}

class MockGeometry {
    constructor() { this.attributes = {}; }
    center() {}
    setAttribute() {}
    setIndex() {}
    computeVertexNormals() {}
    dispose() {}
    clone() { return new MockGeometry(); }
}

class MockGroup {
    constructor() {
        this.children = [];
        this.position = new MockVector3();
        this.rotation = new MockVector3();
        this.scale = new MockVector3(1, 1, 1);
        this.userData = {};
        this.visible = true;
    }
    add(child) { this.children.push(child); }
    remove(child) {
        const idx = this.children.indexOf(child);
        if (idx !== -1) this.children.splice(idx, 1);
    }
    clear() { this.children = []; }
    getObjectByName(name) {
        if (this.name === name) return this;
        for (const child of this.children) {
            if (child.name === name) return child;
            if (child.getObjectByName) {
                const found = child.getObjectByName(name);
                if (found) return found;
            }
        }
        return null;
    }
    traverse(cb) {
        cb(this);
        for (const child of this.children) {
            if (child.traverse) child.traverse(cb);
            else cb(child);
        }
    }
}

class MockMesh extends MockGroup {
    constructor(geo, mat) {
        super();
        this.isMesh = true;
        this.geometry = geo;
        this.material = mat;
    }
}

const THREE = {
    Group: MockGroup,
    Mesh: MockMesh,
    Vector3: MockVector3,
    Color: MockColor,
    MeshStandardMaterial: MockMaterial,
    BoxGeometry: MockGeometry,
    CylinderGeometry: MockGeometry,
    TubeGeometry: MockGeometry,
    ExtrudeGeometry: MockGeometry,
    BufferGeometry: MockGeometry,
    Shape: class {
        moveTo() {}
        lineTo() {}
    },
    CatmullRomCurve3: class {
        constructor() {}
    },
    PerspectiveCamera: class {
        constructor(fov, aspect, near, far) {
            this.fov = fov;
            this.aspect = aspect;
            this.position = new MockVector3();
            this.lookAt = () => {};
        }
        updateProjectionMatrix() {}
    },
    Scene: class extends MockGroup {
        constructor() { super(); this.background = null; this.fog = null; }
    },
    WebGLRenderer: class {
        constructor() {
            this.domElement = { parentNode: null, style: {}, toDataURL: () => 'data:image/png;base64,', addEventListener() {} };
            this.shadowMap = {};
        }
        setPixelRatio() {}
        setSize() {}
        render() {}
        dispose() {}
    },
    OrbitControls: class {
        constructor(camera, domElement) {
            this.target = new MockVector3();
        }
        update() {}
    },
    AmbientLight: class extends MockGroup {},
    HemisphereLight: class extends MockGroup {},
    DirectionalLight: class extends MockGroup {
        constructor() { super(); this.position = new MockVector3(); }
    },
    DoubleSide: 2,
    Float32BufferAttribute: class {},
    BufferAttribute: class {},
    ACESFilmicToneMapping: 4,
    FogExp2: class {},
};

const domContainer = {
    clientWidth: 800,
    clientHeight: 600,
    appendChild(el) { el.parentNode = domContainer; },
    removeChild(el) { el.parentNode = null; },
};

const context = {
    console,
    THREE,
    document: {
        getElementById(id) {
            if (id === 'view3DContainer') return domContainer;
            return null;
        },
        createElement(tag) { return { src: '', style: {}, appendChild() {} }; },
        head: { appendChild() {} },
    },
    window: {
        devicePixelRatio: 1,
        ResizeObserver: class {
            constructor(cb) { this.cb = cb; }
            observe() {}
            disconnect() {}
        },
        requestAnimationFrame: (fn) => setTimeout(fn, 16),
        cancelAnimationFrame: (id) => clearTimeout(id),
    },
    ResizeObserver: class {
        constructor(cb) { this.cb = cb; }
        observe() {}
        disconnect() {}
    },
    requestAnimationFrame: (fn) => setTimeout(fn, 16),
    cancelAnimationFrame: (id) => clearTimeout(id),
};

vm.createContext(context);

// Load 3D files in bundle order
const sources = [
    'pcb_view/pcb_viewer_3d/constants_3d.js',
    'pcb_view/pcb_viewer_3d/model_cache.js',
    'pcb_view/pcb_viewer_3d/scene_setup.js',
    'pcb_view/pcb_viewer_3d/camera_controller.js',
    'pcb_view/pcb_viewer_3d/board_mesh_builder.js',
    'pcb_view/pcb_viewer_3d/placeholder_builder.js',
    'pcb_view/pcb_viewer_3d/component_model_loader.js',
    'pcb_view/pcb_viewer_3d/component_placer.js',
    'pcb_view/pcb_viewer_3d/layer_panel_3d.js',
    'pcb_view/pcb_viewer_3d/pcb_viewer_3d.js',
];

for (const src of sources) {
    const code = fs.readFileSync('static/' + src, 'utf8');
    vm.runInContext(code, context);
}

// ── Tests ──────────────────────────────────────────────────────────────────────

console.log('Testing PcbViewer3D initialization & window.init3DViewer ...');
assert.strictEqual(typeof context.window.init3DViewer, 'function', 'init3DViewer should be defined on window');

const boardModel = {
    components: [
        { ref: 'R1', footprint: 'Resistor_SMD:R_0805_2012Metric', x: 10, y: 20, rotation: 0, pads: [{ number: '1', x: -1, y: 0 }] },
        { ref: 'C1', footprint: 'Capacitor_SMD:C_0805_2012Metric', x: 30, y: 40, rotation: 90, pads: [{ number: '1', x: -1, y: 0 }] },
    ],
    traces: [
        { layer: 'F.Cu', net: 'GND', width: 0.25, path: [{ x: 10, y: 20 }, { x: 30, y: 40 }] },
    ],
    vias: [
        { x: 20, y: 30, drill: 0.3, diameter: 0.6, net: 'GND' },
    ],
    outline_segments: [
        { type: 'line', points: [{ x: 0, y: 0 }, { x: 50, y: 0 }, { x: 50, y: 50 }, { x: 0, y: 50 }] }
    ]
};

const instance = context.window.init3DViewer(boardModel);
assert.ok(instance, 'init3DViewer should return a PcbViewer3D instance');
assert.strictEqual(context.window.pcbViewer3DInstance, instance, 'pcbViewer3DInstance should be set on window');

const bbox = instance.boardMeshBuilder._computeBBox(boardModel.outline_segments, boardModel);
assert.strictEqual(bbox.minX, 0);
assert.strictEqual(bbox.minY, 0);
assert.strictEqual(bbox.maxX, 50);
assert.strictEqual(bbox.maxY, 50);

// Test centerOffset in ComponentPlacer
const compR1 = instance.componentPlacer.getComponent('R1');
assert.ok(compR1, 'Component R1 should be placed');
// cx = (0 + 50)/2 = 25, cz = -(0 + 50)/2 = -25.
// R1 pos: x = 10 - 25 = -15, z = -20 - (-25) = 5
assert.strictEqual(compR1.position.x, -15, 'R1 X position centered correctly');
assert.strictEqual(compR1.position.z, 5, 'R1 Z position centered correctly');

// Controls test
instance.viewTop();
instance.viewFront();
instance.fitToBoard();
instance.toggleWireframe();
assert.strictEqual(instance._wireframe, true, 'Wireframe should be toggled on');
instance.toggleWireframe();
assert.strictEqual(instance._wireframe, false, 'Wireframe should be toggled off');

instance.toggleExplode();
assert.strictEqual(instance._exploded, true, 'Explode view should be toggled on');
instance.toggleExplode();
assert.strictEqual(instance._exploded, false, 'Explode view should be toggled off');

// Layer opacity test
instance.setBoardOpacity(0.5);

// Test fallback bbox when outline_segments is empty
const boardModelNoOutline = {
    components: [{ ref: 'U1', x: 5, y: 15 }],
    traces: [{ path: [{ x: 0, y: 0 }, { x: 10, y: 20 }] }],
    outline_segments: [],
};
instance.loadBoard(boardModelNoOutline);
const compU1 = instance.componentPlacer.getComponent('U1');
assert.ok(compU1, 'Component U1 should be placed even without outline_segments');

instance.dispose();

console.log('✓ 3D PCB Viewer unit and integration tests passed successfully!');
