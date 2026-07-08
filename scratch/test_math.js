const fs = require('fs');

// Mock window and document for global scope simulation
global.window = global;
global.document = {
    createElement: () => ({ getContext: () => ({}) })
};

// Mock pcbState
global.pcbState = {
    baseScale: 15,
    zoom: 1.0,
    midX: 100,
    midY: 100,
    boardModel: { components: [] }
};

// Mock gl-matrix / vec2 / mat3
const vec2 = {
    create: () => new Float32Array(2),
    transformMat3: (out, a, m) => {
        const x = a[0], y = a[1];
        out[0] = m[0] * x + m[3] * y + m[6];
        out[1] = m[1] * x + m[4] * y + m[7];
        return out;
    }
};
const mat3 = {
    create: () => new Float32Array(9),
    identity: (out) => {
        out.fill(0);
        out[0] = 1; out[4] = 1; out[8] = 1;
        return out;
    },
    translate: (out, a, v) => {
        const x = v[0], y = v[1];
        out[0] = a[0]; out[1] = a[1]; out[2] = a[2];
        out[3] = a[3]; out[4] = a[4]; out[5] = a[5];
        out[6] = a[0] * x + a[3] * y + a[6];
        out[7] = a[1] * x + a[4] * y + a[7];
        out[8] = a[2] * x + a[5] * y + a[8];
        return out;
    },
    scale: (out, a, v) => {
        const x = v[0], y = v[1];
        out[0] = a[0] * x; out[1] = a[1] * x; out[2] = a[2] * x;
        out[3] = a[3] * y; out[4] = a[4] * y; out[5] = a[5] * y;
        out[6] = a[6]; out[7] = a[7]; out[8] = a[8];
        return out;
    }
};
global.vec2 = vec2;
global.mat3 = mat3;

// Load utils.js
const utilsCode = fs.readFileSync('static/pcb_view/utils.js', 'utf8');
eval(utilsCode);

class MockPcbEditor {
    constructor() {
        this._overlayCanvas = { width: 800, height: 600 };
        this._viewMatrix = mat3.create();
        this._applyCamera();
    }

    _applyCamera() {
        const scale = pcbState.baseScale * pcbState.zoom;
        mat3.identity(this._viewMatrix);
        mat3.translate(this._viewMatrix, this._viewMatrix, [
            this._overlayCanvas.width / 2,
            this._overlayCanvas.height / 2
        ]);
        mat3.scale(this._viewMatrix, this._viewMatrix, [scale, -scale]);
        mat3.translate(this._viewMatrix, this._viewMatrix, [-pcbState.midX, -pcbState.midY]);
    }

    worldToScreen(wx, wy) {
        const out = vec2.create();
        vec2.transformMat3(out, [wx, wy], this._viewMatrix);
        return { x: out[0], y: out[1] };
    }

    _transformGraphicPoints(component, points) {
        return (points || []).map((point) => {
            const rotated = rotatePoint(point.x || 0, point.y || 0, component.rotation || 0);
            return {
                x: component.x + rotated.x,
                y: component.y + rotated.y,
            };
        });
    }
}

const editor = new MockPcbEditor();

const componentTemplate = {
    x: 100,
    y: 100,
    pads: [
        { number: "1", x: 0, y: 0 },
        { number: "15", x: 0, y: 35.56 }
    ],
    graphics: [
        { kind: "fp_line", start: { x: -1.38, y: 1.27 }, end: { x: -1.38, y: 36.94 } }
    ]
};

for (const rotation of [0, 90, 180, 270]) {
    console.log(`\n================= Rotation ${rotation} =================`);
    const comp = { ...componentTemplate, rotation };
    
    const p1 = getComponentPadPosition(comp, comp.pads[0]);
    const p15 = getComponentPadPosition(comp, comp.pads[1]);
    const s1 = editor.worldToScreen(p1.x, p1.y);
    const s15 = editor.worldToScreen(p15.x, p15.y);
    
    console.log("Pads Screen Y:");
    console.log(`  Pad 1:  ${s1.y.toFixed(2)}`);
    console.log(`  Pad 15: ${s15.y.toFixed(2)}`);
    console.log(`  Direction: ${s15.y > s1.y ? 'DOWNWARDS' : 'UPWARDS'}`);
    
    const gLine = comp.graphics[0];
    const trans = editor._transformGraphicPoints(comp, [gLine.start, gLine.end]);
    const sgStart = editor.worldToScreen(trans[0].x, trans[0].y);
    const sgEnd = editor.worldToScreen(trans[1].x, trans[1].y);
    
    console.log("Graphic Line Screen Y:");
    console.log(`  Start:  ${sgStart.y.toFixed(2)}`);
    console.log(`  End:    ${sgEnd.y.toFixed(2)}`);
    console.log(`  Direction: ${sgEnd.y > sgStart.y ? 'DOWNWARDS' : 'UPWARDS'}`);
}
