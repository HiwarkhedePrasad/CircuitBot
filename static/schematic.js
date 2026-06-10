// --- Schematic Editor: Multi-Component Placement Engine ---

const GRID_SIZE = 1.27;          // 50 mil standard KiCad grid in mm
const COLUMN_SPACING = 15.0;     // mm between column centers
const ROW_CLEARANCE = 3.0;       // mm min clearance between components in a column
const BBOX_PAD = 2.0;            // mm padding around component bounding box

// Column definitions for functional zoning (left-to-right signal flow)
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
    return COLUMN_DEFS.length - 1; // default to last column (Peripherals)
}

function snapToGrid(value) {
    return Math.round(value / GRID_SIZE) * GRID_SIZE;
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

// --- Schematic Manager ---
class Schematic {
    constructor() {
        this.components = [];
        this.mode = 'single'; // 'single' or 'schematic'
    }

    addComponent(id, name, ops, category, description) {
        const existing = this.components.find(c => c.id === id);
        if (existing) return existing;

        const comp = new SchematicComponent(id, name, ops, category, description);
        this.components.push(comp);
        this.autoLayout();
        return comp;
    }

    addRawComponent(id, refDes, ops, category, description) {
        const existing = this.components.find(c => c.id === id);
        if (existing) return existing;

        const comp = new SchematicComponent(id, refDes, ops, category, description);
        comp.refDesignator = refDes;
        this.components.push(comp);
        return comp;
    }

    removeComponent(id) {
        this.components = this.components.filter(c => c.id !== id);
        this.autoLayout();
    }

    clear() {
        this.components = [];
        this.wirePaths = [];
        this.pinMatrix = {};
        this.netlist = [];
    }

    getById(id) {
        return this.components.find(c => c.id === id);
    }

    // --- Column-Based Auto Layout (Left-to-Right signal flow) ---
    autoLayout() {
        if (this.components.length === 0) return;

        // Group components by column
        const columns = [[], [], [], []];
        this.components.forEach(comp => {
            columns[comp.column].push(comp);
        });

        // Calculate column widths (widest component + padding)
        const colWidths = columns.map(col => {
            if (col.length === 0) return 0;
            return Math.max(...col.map(c => c.width + BBOX_PAD * 2));
        });

        // Calculate total width
        let totalWidth = 0;
        let activeColCount = 0;
        for (let i = 0; i < 4; i++) {
            if (columns[i].length > 0) {
                totalWidth += colWidths[i];
                activeColCount++;
            }
        }
        totalWidth += (activeColCount - 1) * COLUMN_SPACING;

        // Place components column by column
        let xOffset = -totalWidth / 2;

        for (let i = 0; i < 4; i++) {
            if (columns[i].length === 0) continue;

            const col = columns[i];
            const colTotalHeight = col.reduce((sum, c) => sum + c.height + BBOX_PAD * 2, -BBOX_PAD * 2 + ROW_CLEARANCE * (col.length - 1));
            let yOffset = -colTotalHeight / 2;

            col.forEach(comp => {
                comp.x = snapToGrid(xOffset + (colWidths[i] - comp.width) / 2);
                comp.y = snapToGrid(yOffset);
                yOffset += comp.height + BBOX_PAD * 2 + ROW_CLEARANCE;
            });

            xOffset += colWidths[i] + COLUMN_SPACING;
        }
    }

    // --- A* pathfinding for orthogonal wiring ---
    loadGridWalls() {
        const walls = [];
        this.components.forEach(comp => {
            const ax = comp.x + comp.bbox.x - BBOX_PAD;
            const ay = comp.y + comp.bbox.y - BBOX_PAD;
            const aw = comp.bbox.w + BBOX_PAD * 2;
            const ah = comp.bbox.h + BBOX_PAD * 2;
            walls.push({ x: ax, y: ay, w: aw, h: ah });
        });
        return walls;
    }

    autoRoute(netlist) {
        this.netlist = netlist;
        this.wirePaths = [];
        const walls = this.loadGridWalls();
        for (const conn of netlist) {
            const src = this.pinMatrix[conn.source];
            const tgt = this.pinMatrix[conn.target];
            if (!src || !tgt) continue;
            const path = this.findOrthogonalPath(walls, src, tgt);
            if (path) {
                this.wirePaths.push({ source: conn.source, target: conn.target, path });
            }
        }
        return this.wirePaths;
    }

    findOrthogonalPath(walls, src, tgt) {
        const toG = v => Math.round(v / GRID_SIZE);
        const gsx = toG(src.x), gsy = toG(src.y);
        const gex = toG(tgt.x), gey = toG(tgt.y);

        const key = (x, y) => `${x},${y}`;
        const wallSet = new Set();
        for (const w of walls) {
            const x1 = toG(w.x), y1 = toG(w.y);
            const x2 = toG(w.x + w.w), y2 = toG(w.y + w.h);
            for (let x = x1; x <= x2; x++) {
                for (let y = y1; y <= y2; y++) {
                    wallSet.add(key(x, y));
                }
            }
        }
        wallSet.delete(key(gsx, gsy));
        wallSet.delete(key(gex, gey));

        const h = (x, y) => Math.abs(x - gex) + Math.abs(y - gey);
        const open = new Map();
        const closed = new Set();
        const gScore = new Map();
        const cameFrom = new Map();
        const startK = key(gsx, gsy);
        gScore.set(startK, 0);
        open.set(startK, { x: gsx, y: gsy, dir: null, f: h(gsx, gsy) });

        while (open.size > 0) {
            let best = null, bestF = Infinity;
            for (const [, v] of open) {
                if (v.f < bestF) { best = v; bestF = v.f; }
            }
            const ck = key(best.x, best.y);
            if (best.x === gex && best.y === gey) {
                const path = [];
                let cur = ck;
                while (cur) {
                    const [cx, cy] = cur.split(',').map(Number);
                    path.unshift({ x: cx * GRID_SIZE, y: cy * GRID_SIZE });
                    cur = cameFrom.get(cur) || null;
                }
                return path;
            }
            open.delete(ck);
            closed.add(ck);

            const dirs = [[1, 0, 0], [-1, 0, 1], [0, 1, 2], [0, -1, 3]];
            for (const [dx, dy, nd] of dirs) {
                const nx = best.x + dx, ny = best.y + dy;
                const nk = key(nx, ny);
                if (closed.has(nk) || wallSet.has(nk)) continue;
                const turnCost = (best.dir !== null && best.dir !== nd) ? 3 : 0;
                const tentG = (gScore.get(ck) ?? 0) + 1 + turnCost;
                if (tentG < (gScore.get(nk) ?? Infinity)) {
                    gScore.set(nk, tentG);
                    cameFrom.set(nk, ck);
                    open.set(nk, { x: nx, y: ny, dir: nd, f: tentG + h(nx, ny) });
                }
            }
        }
        return null;
    }

    // --- Resolve absolute pin coordinates for routing ---
    resolveAbsolutePins() {
        this.pinMatrix = {};
        this.components.forEach(comp => {
            comp.ops.forEach(op => {
                if (op[0] !== 'pin') return;
                const at = getAttr(op, 'at');
                const lenNode = getAttr(op, 'length');
                const numNode = getAttr(op, 'number');
                if (!at || !lenNode || !numNode) return;

                const px = parseFloat(at[1]);
                const py = parseFloat(at[2]);
                const angDeg = parseFloat(at[3] || 0);
                const ang = angDeg * Math.PI / 180;
                const len = parseFloat(lenNode[1]);

                const ex = px + Math.cos(ang) * len;
                const ey = py + Math.sin(ang) * len;

                const nameNode = getAttr(op, 'name');
                const pinName = nameNode ? nameNode[1].replace(/"/g, '') : '';
                const pinNum = numNode[1].replace(/"/g, '');
                const absX = snapToGrid(comp.x + ex);
                const absY = snapToGrid(comp.y + ey);
                const key = `${comp.refDesignator}:${pinNum}`;
                this.pinMatrix[key] = { x: absX, y: absY, name: pinName, refDes: comp.refDesignator, pinNum };
            });
        });
        return this.pinMatrix;
    }

    // --- Compute transform for rendering all components ---
    computeTransform(canvasW, canvasH) {
        if (this.components.length === 0) return null;

        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        this.components.forEach(comp => {
            minX = Math.min(minX, comp.x);
            minY = Math.min(minY, comp.y);
            maxX = Math.max(maxX, comp.x + comp.width + BBOX_PAD * 2);
            maxY = Math.max(maxY, comp.y + comp.height + BBOX_PAD * 2);
        });

        const margin = 10;
        minX -= margin; minY -= margin; maxX += margin; maxY += margin;
        const w = maxX - minX;
        const h = maxY - minY;

        const scale = Math.min(canvasW / w, canvasH / h) * 0.85;
        const cx = canvasW / 2;
        const cy = canvasH / 2;
        const midX = (minX + maxX) / 2;
        const midY = (minY + maxY) / 2;

        return { baseScale: scale, cx, cy, midX, midY };
    }

    // Draw the 1.27mm grid in schematic mode
    static drawGrid(ctx, transform, zoomLevel, canvasW, canvasH) {
        const t = transform;
        if (!t) return;

        const s = t.baseScale * zoomLevel;

        // Compute visible area in mm coords
        const visLeft = t.midX - (canvasW / 2 - t.cx - panX) / s;
        const visRight = t.midX + (canvasW / 2 - t.cx + panX) / s;
        const visTop = t.midY - (canvasH / 2 - t.cy - panY) / s;
        const visBottom = t.midY + (canvasH / 2 - t.cy + panY) / s;

        const gridMm = GRID_SIZE;
        const startGridX = Math.floor(visLeft / gridMm) * gridMm;
        const endGridX = Math.ceil(visRight / gridMm) * gridMm;
        const startGridY = Math.floor(visTop / gridMm) * gridMm;
        const endGridY = Math.ceil(visBottom / gridMm) * gridMm;

        ctx.save();
        ctx.translate(t.cx + panX, t.cy + panY);
        ctx.scale(s, -s);
        ctx.translate(-t.midX, -t.midY);

        // Minor grid dots
        ctx.fillStyle = 'rgba(100, 160, 200, 0.2)';
        const dotRadius = 0.08;
        for (let x = startGridX; x <= endGridX; x += gridMm) {
            for (let y = startGridY; y <= endGridY; y += gridMm) {
                ctx.beginPath();
                ctx.arc(x, y, dotRadius, 0, Math.PI * 2);
                ctx.fill();
            }
        }

        // Major grid lines (every 10 * 1.27 = 12.7mm)
        const majorGrid = gridMm * 10;
        const majorStartX = Math.floor(visLeft / majorGrid) * majorGrid;
        const majorEndX = Math.ceil(visRight / majorGrid) * majorGrid;
        const majorStartY = Math.floor(visTop / majorGrid) * majorGrid;
        const majorEndY = Math.ceil(visBottom / majorGrid) * majorGrid;

        ctx.strokeStyle = 'rgba(100, 160, 200, 0.1)';
        ctx.lineWidth = 0.05;
        for (let x = majorStartX; x <= majorEndX; x += majorGrid) {
            ctx.beginPath();
            ctx.moveTo(x, visTop - 10);
            ctx.lineTo(x, visBottom + 10);
            ctx.stroke();
        }
        for (let y = majorStartY; y <= majorEndY; y += majorGrid) {
            ctx.beginPath();
            ctx.moveTo(visLeft - 10, y);
            ctx.lineTo(visRight + 10, y);
            ctx.stroke();
        }

        // Draw column zone backgrounds
        const columns = [[], [], [], []];
        // We need to know column positions, which are set by autoLayout
        // Use the component positions to determine column zones
        if (typeof currentSchematic !== 'undefined' && currentSchematic.components.length > 0) {
            const colXPositions = new Map();
            currentSchematic.components.forEach(comp => {
                if (!colXPositions.has(comp.column)) colXPositions.set(comp.column, []);
                colXPositions.get(comp.column).push(comp.x);
            });

            const colColors = ['rgba(80, 200, 80, 0.04)', 'rgba(200, 200, 80, 0.04)', 'rgba(80, 140, 240, 0.04)', 'rgba(220, 100, 80, 0.04)'];
            const colLabels = ['Power & Inputs', 'Power Management', 'Core Processing', 'Peripherals'];

            colXPositions.forEach((xPositions, colIdx) => {
                if (xPositions.length === 0) return;
                const colMinX = Math.min(...xPositions) - BBOX_PAD * 2;
                const colMaxX = Math.max(...xPositions) + currentSchematic.components.find(c => c.column === colIdx).width + BBOX_PAD * 4;
                const zoneW = colMaxX - colMinX;

                ctx.fillStyle = colColors[colIdx] || colColors[3];
                ctx.fillRect(colMinX - 2, visTop - 10, zoneW + 4, visBottom - visTop + 20);

                // Column label at top
                ctx.save();
                ctx.translate(colMinX + zoneW / 2, visTop + 3);
                ctx.scale(1, -1);
                ctx.fillStyle = 'rgba(180, 200, 220, 0.25)';
                ctx.font = `${1.8}px "Segoe UI", Arial, sans-serif`;
                ctx.textAlign = 'center';
                ctx.textBaseline = 'top';
                ctx.fillText(colLabels[colIdx] || '', 0, 0);
                ctx.restore();
            });
        }

        ctx.restore();
    }
}
