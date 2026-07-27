// --- Schematic Editor: Simplified Data Model (agent-only routing) ---
//
// All wiring and placement is performed by the backend agent.
// This module provides the data model and utility functions
// needed by the PixiJS renderer.

const GRID_SIZE = 1.27;
const BBOX_PAD = 2.0;

// ── Net label colour palette ──────────────────────────────────────────
// Each unique net name gets a deterministic colour so pins on the same
// logical net share a visible tint.
const NET_COLORS = [
    '#2fd47a', '#ff6b6b', '#5b9cff', '#ffd166', '#ff9ff3',
    '#54a0ff', '#5f27cd', '#01a3a4', '#f368e0', '#ff9f43',
    '#ee5a24', '#0abde3', '#10ac84', '#8395a7', '#c44569',
    '#574b90', '#f78fb3', '#3dc1d3', '#e15f41', '#63cdda',
];

function hashStr(s) {
    let h = 0;
    for (let i = 0; i < s.length; i++) {
        h = ((h << 5) - h) + s.charCodeAt(i);
        h |= 0;
    }
    return h;
}

function netColor(net) {
    const i = Math.abs(hashStr(net || '')) % NET_COLORS.length;
    return NET_COLORS[i];
}

function hexToPixi(hex) {
    return parseInt(hex.replace('#', ''), 16);
}

/** Strip residual quotes / whitespace from S-expression string tokens. */
function sexprStr(val) {
    if (val == null) return '';
    return String(val).replace(/^"+|"+$/g, '').trim();
}

/** True for KiCad "no name" pin/text placeholders. */
function isHiddenSexprText(val) {
    const s = sexprStr(val);
    return !s || s === '~';
}

/**
 * Detect T-junctions / multi-wire meets from orthogonal wire paths.
 * Returns [{x, y}, ...] grid-snapped unique points with degree >= 3.
 */
function computeJunctionPoints(wirePaths) {
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
        // Endpoints always count
        bump(path[0].x, path[0].y);
        bump(path[path.length - 1].x, path[path.length - 1].y);
        // Interior vertices count as degree contributions (corners / T-stubs)
        for (let i = 1; i < path.length - 1; i++) {
            bump(path[i].x, path[i].y);
        }
    }
    const junctions = [];
    for (const [k, n] of counts) {
        if (n < 3) continue;
        const [xs, ys] = k.split(',');
        junctions.push({ x: parseFloat(xs), y: parseFloat(ys) });
    }
    return junctions;
}

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

function snapToSchematicGrid(value) {
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
        // lib_id is the KiCad library id (e.g. "Device:R") used for style overrides
        this.lib_id = (typeof id === 'string' && id.includes(':')) ? id : id;
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
        // Enriched component data
        this.footprint = '';
        this.pads = [];
        this.pins = [];
        this.datasheet = '';
        this.datasheet_text = '';
        this.pin_summary = '';
        this.value = '';
        this.manufacturer = '';
        this.has_footprint = false;
    }

    extractRef() {
        for (const op of this.ops) {
            if (op[0] === 'property' && sexprStr(op[1]) === 'Reference') {
                return sexprStr(op[2]) || this.name.split(':').pop().substring(0, 8);
            }
        }
        return this.name.split(':').pop().substring(0, 8);
    }

    get width() { return this.bbox.w; }
    get height() { return this.bbox.h; }

    getPins() { return this.pins; }
    getFootprint() { return this.footprint; }
    getDatasheet() { return this.datasheet; }

    /** Enrich component with data from backend RAG lookup */
    enrich(data) {
        if (data.footprint) this.footprint = data.footprint;
        if (data.pads) this.pads = data.pads;
        if (data.pins) this.pins = data.pins;
        if (data.datasheet) this.datasheet = data.datasheet;
        if (data.datasheet_text) this.datasheet_text = data.datasheet_text;
        if (data.pin_summary) this.pin_summary = data.pin_summary;
        if (data.value) this.value = data.value;
        if (data.manufacturer) this.manufacturer = data.manufacturer;
        this.has_footprint = !!this.footprint;
    }
}

// ── Net Label ────────────────────────────────────────────────────────────
// A net label creates a logical connection between all pins that share
// the same `net` name — without requiring a visible wire.
class NetLabel {
    constructor(id, net, x, y, orientation = 0, pin = null) {
        this.id = id;
        this.net = net;              // net name (e.g. "VCC", "GND", "N$001")
        this.x = x;                  // position on canvas
        this.y = y;
        this.orientation = orientation;  // degrees: 0/90/180/270
        this.pin = pin;              // pin key this label is attached to, or null if free-standing
    }
}

// ── Image Marker ────────────────────────────────────────────────────────
// An image marker is a screenshot/photo placed on the schematic canvas
// as a visual reference. Asset_id prevents storing large base64 inside
// the design snapshot — the image itself lives in the asset store.
class ImageMarker {
    constructor(id, x, y, imageDataUrl, label, width, height, markerNumber, scale, rotation, assetId, revealed) {
        this.id = id;
        this.markerNumber = markerNumber || 0;
        this.x = x;
        this.y = y;
        this.imageDataUrl = imageDataUrl;
        this.label = label || '';
        this.width = width || 20;
        this.height = height || 15;
        this.scale = scale || 1;
        this.rotation = rotation || 0;
        this.assetId = assetId || null;
        this._imageRevealed = !!revealed;
    }

    toDesignSnapshot() {
        return {
            id: this.id,
            marker_number: this.markerNumber,
            label: this.label,
            x: this.x,
            y: this.y,
            width: this.width,
            height: this.height,
            scale: this.scale,
            rotation: this.rotation,
            asset_id: this.assetId,
            revealed: this._imageRevealed,
        };
    }

    static fromSnapshot(snap) {
        return new ImageMarker(
            snap.id,
            snap.x, snap.y,
            null,
            snap.label || '',
            snap.width || 20,
            snap.height || 15,
            snap.marker_number || 0,
            snap.scale || 1,
            snap.rotation || 0,
            snap.asset_id || null,
            snap.revealed || false,
        );
    }
}

// --- Schematic Manager (data container only) ---
class Schematic {
    constructor() {
        this.components = [];
        this.mode = 'schematic';
        this.wirePaths = [];
        this.junctionPoints = [];
        this.powerLabels = [];
        this.netLabels = [];
        this.imageMarkers = [];
        this.netlist = [];
        this._netLabelCounter = 0;
        this._imageMarkerCounter = 0;
    }

    addComponent(id, name, ops, category, description) {
        const existing = this.components.find(c => c.id === id);
        if (existing) return existing;
        const comp = new SchematicComponent(id, name, ops, category, description);
        // Simple staggered placement for manually added components
        const offset = this.components.length * 15;
        comp.x = snapToSchematicGrid(offset);
        comp.y = snapToSchematicGrid(offset);
        this.components.push(comp);
        return comp;
    }

    addRawComponent(id, refDes, ops, category, description, extra = {}) {
        const existing = this.components.find(c => c.refDesignator === refDes);
        if (existing) {
            if (extra.footprint) existing.footprint = extra.footprint;
            if (extra.pads) existing.pads = extra.pads;
            if (extra.datasheet) existing.datasheet = extra.datasheet;
            if (extra.datasheet_text) existing.datasheet_text = extra.datasheet_text;
            return existing;
        }
        const comp = new SchematicComponent(id, refDes, ops, category, description);
        comp.lib_id = id;
        comp.refDesignator = refDes;
        if (extra.footprint) comp.footprint = extra.footprint;
        if (extra.pads) comp.pads = extra.pads;
        if (extra.datasheet) comp.datasheet = extra.datasheet;
        if (extra.datasheet_text) comp.datasheet_text = extra.datasheet_text;
        if (extra.value) comp.value = extra.value;
        this.components.push(comp);
        return comp;
    }

    removeComponent(id) {
        this.components = this.components.filter(c => c.id !== id);
        // Remove net labels attached to this component's pins
        this.netLabels = this.netLabels.filter(l => {
            if (!l.pin) return true;
            const ref = l.pin.split(':')[0];
            const comp = this.components.find(c => c.refDesignator === ref);
            return !!comp; // keep if component still exists
        });
    }

    clear() {
        this.components = [];
        this.wirePaths = [];
        this.junctionPoints = [];
        this.powerLabels = [];
        this.netLabels = [];
        this.imageMarkers = [];
        this._netLabelCounter = 0;
        this._imageMarkerCounter = 0;
        this.netlist = [];
    }

    /** Recompute junction dots from current wire paths. */
    recomputeJunctions() {
        this.junctionPoints = computeJunctionPoints(this.wirePaths);
        return this.junctionPoints;
    }

    getById(id) {
        return this.components.find(c => c.id === id);
    }

    // ── Net Label methods ──────────────────────────────────────────────

    /** Add a new net label. Returns the created NetLabel. */
    addNetLabel(net, x, y, orientation = 0, pin = null) {
        const id = `nl_${++this._netLabelCounter}`;
        const label = new NetLabel(id, net, x, y, orientation, pin);
        this.netLabels.push(label);
        return label;
    }

    /** Remove a net label by id. Returns true if removed. */
    removeNetLabel(id) {
        const before = this.netLabels.length;
        this.netLabels = this.netLabels.filter(l => l.id !== id);
        return this.netLabels.length < before;
    }

    // ── Image Marker methods ──────────────────────────────────────

    getNextImageMarkerNumber() {
        let max = 0;
        for (const m of this.imageMarkers) {
            if (m.markerNumber > max) max = m.markerNumber;
        }
        return max + 1;
    }

    addImageMarkerAt(x, y, imageDataUrl, label, width, height, assetId, markerNumber) {
        const id = `img_${++this._imageMarkerCounter}`;
        const num = markerNumber !== undefined ? markerNumber : this.getNextImageMarkerNumber();
        const marker = new ImageMarker(id, x, y, imageDataUrl, label, width, height, num, 1, 0, assetId);
        this.imageMarkers.push(marker);
        return marker;
    }

    removeImageMarker(id) {
        const before = this.imageMarkers.length;
        this.imageMarkers = this.imageMarkers.filter(m => m.id !== id);
        return this.imageMarkers.length < before;
    }

    getImageMarkerById(id) {
        return this.imageMarkers.find(m => m.id === id) || null;
    }

    /** Load markers from a snapshot, syncing the counter to avoid ID collisions. */
    loadImageMarkers(markers) {
        if (!Array.isArray(markers)) return;
        for (const m of markers) {
            const marker = ImageMarker.fromSnapshot(m);
            this.imageMarkers.push(marker);
            const num = m.marker_number || 0;
            if (num >= this._imageMarkerCounter) {
                this._imageMarkerCounter = num + 1;
            }
            // Load image from asset store if assetId is present
            if (marker.assetId) {
                this._loadMarkerImage(marker);
            }
        }
    }

    async _loadMarkerImage(marker) {
        try {
            const resp = await fetch(`/api/schematic_assets/${marker.assetId}`);
            if (!resp.ok) return;
            const result = await resp.json();
            if (result.image_data) {
                marker.imageDataUrl = result.image_data;
            }
        } catch (err) {
            console.warn(`Failed to load image for marker ${marker.id}:`, err);
        }
    }

    /** Get net labels attached to a specific pin key. */
    getNetLabelsForPin(pinKey) {
        return this.netLabels.filter(l => l.pin === pinKey);
    }

    /** Get all pin keys that share a net name (from labels + wire paths). */
    getPinsForNet(net) {
        const pins = new Set();
        for (const l of this.netLabels) {
            if (l.net === net && l.pin) pins.add(l.pin);
        }
        for (const w of this.wirePaths) {
            if (w.net === net || (!w.net && (w.source || w.target))) {
                if (w.source) pins.add(w.source);
                if (w.target) pins.add(w.target);
            }
        }
        return pins;
    }

    /** Get all unique net names currently in use. */
    getAllNetNames() {
        const names = new Set();
        for (const l of this.netLabels) names.add(l.net);
        for (const w of this.wirePaths) {
            if (w.net) names.add(w.net);
        }
        return names;
    }

    /** Rename a net — updates ALL labels and wire entries with that name. */
    renameNet(oldName, newName) {
        if (!oldName || !newName || oldName === newName) return;
        for (const l of this.netLabels) {
            if (l.net === oldName) l.net = newName;
        }
        for (const w of this.wirePaths) {
            if (w.net === oldName) w.net = newName;
        }
    }

    /** Build a map: net name → Set of pin keys from ALL sources. */
    buildNetPinMap() {
        const map = new Map();
        for (const l of this.netLabels) {
            if (!l.pin) continue;
            if (!map.has(l.net)) map.set(l.net, new Set());
            map.get(l.net).add(l.pin);
        }
        for (const w of this.wirePaths) {
            const n = w.net || '';
            if (w.source) {
                if (!map.has(n)) map.set(n, new Set());
                map.get(n).add(w.source);
            }
            if (w.target) {
                if (!map.has(n)) map.set(n, new Set());
                map.get(n).add(w.target);
            }
        }
        return map;
    }

    // ── Design Snapshot Serialization ───────────────────────────────────────
    // Serializes the full canvas state into a backend-compatible format
    // for canonical state synchronization.

    /**
     * Serialize the current schematic to a design snapshot.
     * @param {number} revision - Current synced revision (optimistic locking).
     * @returns {Object} Backend-compatible design snapshot.
     */
    toDesignSnapshot(revision = 0) {
        const components = this.components.map(c => ({
            ref_des: c.refDesignator,
            id_str: c.lib_id || c.id,
            value: c.value || '',
            footprint: c.footprint || '',
            category: c.category || '',
            x: c.x,
            y: c.y,
            rotation: 0,
            ops: c.ops || [],
            pins: c.pins || [],
            pads: c.pads || [],
        }));

        const wire_paths = this.wirePaths.map(w => ({
            wire_id: w.wire_id,
            source: w.source,
            target: w.target,
            path: w.path || [],
            net: w.net || '',
            manual: w.manual || false,
        }));

        const net_labels = this.netLabels.map(nl => ({
            id: nl.id,
            net: nl.net,
            x: nl.x,
            y: nl.y,
            orientation: nl.orientation,
            pin: nl.pin,
        }));

        const image_markers = this.imageMarkers.map(m => m.toDesignSnapshot());

        return {
            revision,
            components,
            wire_paths,
            net_labels,
            image_markers,
            power_labels: this.powerLabels || [],
            netlist: this.netlist || [],
        };
    }

    /** Check if a pin is connected (via wire OR net label). */
    isPinConnected(pinKey) {
        for (const w of this.wirePaths) {
            if (w.source === pinKey || w.target === pinKey) return true;
        }
        for (const l of this.netLabels) {
            if (l.pin === pinKey) return true;
        }
        return false;
    }

    /** Generate next auto net name (N$001, N$002, …). */
    nextAutoNetName() {
        const existing = this.getAllNetNames();
        let i = 1;
        while (existing.has(`N$${String(i).padStart(3, '0')}`)) i++;
        return `N$${String(i).padStart(3, '0')}`;
    }
}
