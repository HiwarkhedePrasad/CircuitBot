// --- Schematic Editor: Simplified Data Model (agent-only routing) ---
//
// All wiring and placement is performed by the backend agent.
// This module provides the data model and utility functions
// needed by the PixiJS renderer.

const GRID_SIZE = 1.27;
const BBOX_PAD = 2.0;

// Column definitions for display grouping only
const COLUMN_DEFS = [
    { label: 'Power & Inputs',    keywords: ['Regulator', 'Connector', 'Power', 'Battery', 'Source', 'Switch', 'Fuse', 'Diode'] },
    { label: 'Power Management',  keywords: ['LDO', 'Buck', 'Boost', 'Capacitor', 'Inductor', 'Filter', 'Converter'] },
    { label: 'Core Processing',   keywords: ['MCU', 'ESP32', 'STM32', 'Processor', 'FPGA', 'DSP', 'Memory', 'CPU', 'RF_Module'] },
    { label: 'Peripherals',       keywords: ['Sensor', 'Display', 'LED', 'Motor', 'Driver', 'ADC', 'DAC', 'OpAmp', 'Logic', 'Timer'] },
];

function getColumnForCategory(category) {
    const cat = category.toUpperCase();
    for (let i = 0; i < COLUMN_DEFS.length; i++) {
        for (const kw of COLUMN_DEFS[i].keywords) {
            if (cat.includes(kw.toUpperCase())) return i;
        }
    }
    return COLUMN_DEFS.length - 1;
}

function snapToGrid(value) {
    return Math.round(value / GRID_SIZE) * GRID_SIZE;
}

function getAttr(node, name) {
    if (!Array.isArray(node)) return null;
    for (let i = 1; i < node.length; i++) {
        if (Array.isArray(node[i]) && node[i][0] === name) return node[i];
    }
    return null;
}

// --- Bounding Box Calculation ---
function calculateOpsBBox(ops) {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;

    function update(x, y) {
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
    }

    ops.forEach(op => {
        const type = op[0];
        if (type === 'rectangle') {
            const s = getAttr(op, 'start'), e = getAttr(op, 'end');
            if (s) update(parseFloat(s[1]), parseFloat(s[2]));
            if (e) update(parseFloat(e[1]), parseFloat(e[2]));
        } else if (type === 'polyline') {
            const pts = getAttr(op, 'pts');
            if (pts) for (let i = 1; i < pts.length; i++) if (pts[i][0] === 'xy') update(parseFloat(pts[i][1]), parseFloat(pts[i][2]));
        } else if (type === 'circle') {
            const c = getAttr(op, 'center'), r = getAttr(op, 'radius');
            if (c && r) {
                const cx = parseFloat(c[1]), cy = parseFloat(c[2]), rv = parseFloat(r[1]);
                update(cx - rv, cy - rv); update(cx + rv, cy + rv);
            }
        } else if (type === 'pin') {
            const at = getAttr(op, 'at'), len = getAttr(op, 'length');
            if (at && len) {
                const x = parseFloat(at[1]), y = parseFloat(at[2]);
                const l = parseFloat(len[1]), a = parseFloat(at[3] || 0) * Math.PI / 180;
                update(x, y); update(x + Math.cos(a) * l, y + Math.sin(a) * l);
            }
        } else if (type === 'property' || type === 'text') {
            const at = getAttr(op, 'at'), hide = getAttr(op, 'hide');
            if (at && (!hide || hide[1] !== 'yes')) update(parseFloat(at[1]), parseFloat(at[2]));
        }
    });

    if (minX === Infinity) return { x: -5, y: -5, w: 10, h: 10 };
    return { x: minX - BBOX_PAD, y: minY - BBOX_PAD, w: maxX - minX + BBOX_PAD * 2, h: maxY - minY + BBOX_PAD * 2 };
}

// Tight bounding box of ONLY the symbol geometry + pins
function calculateGeometryBBox(ops) {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    const update = (x, y) => {
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
    };
    ops.forEach(op => {
        const type = op[0];
        if (type === 'rectangle') {
            const s = getAttr(op, 'start'), e = getAttr(op, 'end');
            if (s) update(parseFloat(s[1]), parseFloat(s[2]));
            if (e) update(parseFloat(e[1]), parseFloat(e[2]));
        } else if (type === 'polyline') {
            const pts = getAttr(op, 'pts');
            if (pts) for (let i = 1; i < pts.length; i++) if (pts[i][0] === 'xy') update(parseFloat(pts[i][1]), parseFloat(pts[i][2]));
        } else if (type === 'circle') {
            const c = getAttr(op, 'center'), r = getAttr(op, 'radius');
            if (c && r) {
                const cx = parseFloat(c[1]), cy = parseFloat(c[2]), rv = parseFloat(r[1]);
                update(cx - rv, cy - rv); update(cx + rv, cy + rv);
            }
        } else if (type === 'arc') {
            ['start', 'mid', 'end'].forEach(k => {
                const a = getAttr(op, k);
                if (a) update(parseFloat(a[1]), parseFloat(a[2]));
            });
        } else if (type === 'pin') {
            const at = getAttr(op, 'at'), len = getAttr(op, 'length');
            if (at && len) {
                const x = parseFloat(at[1]), y = parseFloat(at[2]);
                const l = parseFloat(len[1]), a = parseFloat(at[3] || 0) * Math.PI / 180;
                update(x, y); update(x + Math.cos(a) * l, y + Math.sin(a) * l);
            }
        }
    });
    if (minX === Infinity) return { x: -2.54, y: -2.54, w: 5.08, h: 5.08 };
    const PAD = 1.27;
    return { x: minX - PAD, y: minY - PAD, w: maxX - minX + PAD * 2, h: maxY - minY + PAD * 2 };
}

// --- SchematicComponent ---
class SchematicComponent {
    constructor(id, name, ops, category, description) {
        this.id = id;
        this.name = name;
        this.ops = ops;
        this.category = category;
        this.description = description || '';
        this.x = 0;
        this.y = 0;
        this.column = getColumnForCategory(category);
        this.bbox = calculateOpsBBox(ops);
        this.geomBBox = calculateGeometryBBox(ops);
        this.refDesignator = this.extractRef();
    }

    extractRef() {
        for (const op of this.ops) {
            if (op[0] === 'property' && (op[1] === 'Reference' || op[1] === '"Reference"')) return op[2].replace(/"/g, '');
        }
        return this.name.split(':').pop().substring(0, 8);
    }

    get width() { return this.bbox.w; }
    get height() { return this.bbox.h; }
}

// --- Schematic Manager (data container only) ---
class Schematic {
    constructor() {
        this.components = [];
        this.mode = 'schematic';
        this.wirePaths = [];
        this.junctionPoints = [];
        this.powerLabels = [];
    }

    addComponent(id, name, ops, category, description) {
        const existing = this.components.find(c => c.id === id);
        if (existing) return existing;
        const comp = new SchematicComponent(id, name, ops, category, description);
        // Simple staggered placement for manually added components
        const offset = this.components.length * 15;
        comp.x = snapToGrid(offset);
        comp.y = snapToGrid(offset);
        this.components.push(comp);
        return comp;
    }

    addRawComponent(id, refDes, ops, category, description) {
        const existing = this.components.find(c => c.refDesignator === refDes);
        if (existing) return existing;
        const comp = new SchematicComponent(id, refDes, ops, category, description);
        comp.refDesignator = refDes;
        this.components.push(comp);
        return comp;
    }

    removeComponent(id) {
        this.components = this.components.filter(c => c.id !== id);
    }

    clear() {
        this.components = [];
        this.wirePaths = [];
        this.junctionPoints = [];
        this.powerLabels = [];
    }

    getById(id) {
        return this.components.find(c => c.id === id);
    }
}
