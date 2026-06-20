// --- Schematic Editor: Multi-Component Placement Engine (hardened) ---
//
// Improvements over the original:
//   * A* router with proper cost function (distance + bend penalty +
//     congestion penalty for cells used by other wires).
//   * Strict orthogonal output — never emits a diagonal segment.
//   * Pin-direction-aware stubs (wires exit symbol body in the right
//     direction, not through it).
//   * Grid-snapped endpoints (1.27 mm).
//   * Wire length cap (MAX_WIRE_MANHATTAN) — absurdly long wires are
//     dropped instead of drawn.
//   * Junction detection: a dot is drawn only where 3+ wire ends meet
//     at the same grid point.

const GRID_SIZE = 1.27;          // 50 mil standard KiCad grid in mm
const COLUMN_SPACING = 15.0;     // mm between column centers
const ROW_CLEARANCE = 3.0;       // mm min clearance between components in a column
const BBOX_PAD = 2.0;            // mm padding around component bounding box
const MAX_WIRE_MANHATTAN = 150.0; // mm — wires longer than this are DROPPED (matches backend)
const PIN_STUB_LEN = 2.54;       // mm — one grid step out from symbol body
const BEND_PENALTY = 3;          // A* extra cost per direction change
const CONGESTION_PENALTY = 8;    // A* extra cost per cell already used by a wire

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
    return COLUMN_DEFS.length - 1;
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

// --- Schematic Manager ---
class Schematic {
    constructor() {
        this.components = [];
        this.mode = 'single';
        this.wirePaths = [];
        this.junctionPoints = [];
        this.pinMatrix = {};
        this.netlist = [];
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
        const existing = this.components.find(c => c.refDesignator === refDes);
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
        this.junctionPoints = [];
        this.pinMatrix = {};
        this.netlist = [];
    }

    getById(id) {
        return this.components.find(c => c.id === id);
    }

    // --- Column-Based Auto Layout (Left-to-Right signal flow) ---
    autoLayout() {
        if (this.components.length === 0) return;

        const columns = [[], [], [], []];
        this.components.forEach(comp => columns[comp.column].push(comp));

        const colWidths = columns.map(col => {
            if (col.length === 0) return 0;
            return Math.max(...col.map(c => c.width + BBOX_PAD * 2));
        });

        let totalWidth = 0;
        let activeColCount = 0;
        for (let i = 0; i < 4; i++) {
            if (columns[i].length > 0) {
                totalWidth += colWidths[i];
                activeColCount++;
            }
        }
        totalWidth += (activeColCount - 1) * COLUMN_SPACING;

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

    // --- Build wall set for A* (component bodies block routing) ---
    loadGridWalls() {
        const walls = new Set();
        const toG = v => Math.round(v / GRID_SIZE);
        this.components.forEach(comp => {
            const ax = comp.x + comp.bbox.x - BBOX_PAD;
            const ay = comp.y + comp.bbox.y - BBOX_PAD;
            const aw = comp.bbox.w + BBOX_PAD * 2;
            const ah = comp.bbox.h + BBOX_PAD * 2;
            const x1 = toG(ax), y1 = toG(ay);
            const x2 = toG(ax + aw), y2 = toG(ay + ah);
            for (let x = x1; x <= x2; x++) {
                for (let y = y1; y <= y2; y++) {
                    walls.add(`${x},${y}`);
                }
            }
        });
        return walls;
    }

    // --- Determine pin electrical direction from symbol op ---
    pinDirection(pinKey) {
        const pin = this.pinMatrix[pinKey];
        if (!pin) return 'right';
        // angle stored in pinMatrix (0=right, 90=up, 180=left, 270=down)
        const ang = pin.angle || 0;
        if (ang >= 45 && ang < 135) return 'up';
        if (ang >= 135 && ang < 225) return 'left';
        if (ang >= 225 && ang < 315) return 'down';
        return 'right';
    }

    // --- Compute stub point (one grid step out from symbol body) ---
    stubPoint(pinKey) {
        const pin = this.pinMatrix[pinKey];
        if (!pin) return null;
        const dir = this.pinDirection(pinKey);
        if (dir === 'left')  return { x: snapToGrid(pin.x - PIN_STUB_LEN), y: snapToGrid(pin.y) };
        if (dir === 'up')    return { x: snapToGrid(pin.x), y: snapToGrid(pin.y + PIN_STUB_LEN) };
        if (dir === 'down')  return { x: snapToGrid(pin.x), y: snapToGrid(pin.y - PIN_STUB_LEN) };
        return { x: snapToGrid(pin.x + PIN_STUB_LEN), y: snapToGrid(pin.y) };
    }

    // --- Auto-route all nets ---
    // HARD CAP: any wire longer than MAX_WIRE_MANHATTAN is DROPPED, not
    // drawn.  A dropped wire is better than a 800mm monster crossing the
    // whole canvas.  This is the fix for the "green wires from corner
    // to corner" bug.
    autoRoute(netlist) {
        this.netlist = netlist;
        this.wirePaths = [];
        this.junctionPoints = [];
        const walls = this.loadGridWalls();

        // Build a congestion map: cell → count of wires using it
        const congestion = new Map();
        const addCongestion = (path) => {
            for (let i = 0; i < path.length; i++) {
                const k = `${path[i].x},${path[i].y}`;
                congestion.set(k, (congestion.get(k) || 0) + 1);
            }
        };

        // Pre-filter: drop any connection whose pins are too far apart
        // even before routing — it can never produce a valid wire.
        const routable = netlist.filter(conn => {
            const src = this.pinMatrix[conn.source];
            const tgt = this.pinMatrix[conn.target];
            if (!src || !tgt) return false;
            const mhd = Math.abs(src.x - tgt.x) + Math.abs(src.y - tgt.y);
            return mhd <= MAX_WIRE_MANHATTAN;
        });

        // Sort by Manhattan distance (shortest first)
        const sorted = [...routable].sort((a, b) => {
            const sa = this.pinMatrix[a.source], ta = this.pinMatrix[a.target];
            const sb = this.pinMatrix[b.source], tb = this.pinMatrix[b.target];
            if (!sa || !ta || !sb || !tb) return 0;
            const da = Math.abs(sa.x - ta.x) + Math.abs(sa.y - ta.y);
            const db = Math.abs(sb.x - tb.x) + Math.abs(sb.y - tb.y);
            return da - db;
        });

        for (const conn of sorted) {
            const src = this.pinMatrix[conn.source];
            const tgt = this.pinMatrix[conn.target];
            if (!src || !tgt) continue;
            if (src.x === tgt.x && src.y === tgt.y) continue;

            // Get stub points (so wire exits the symbol body correctly)
            const srcStub = this.stubPoint(conn.source);
            const tgtStub = this.stubPoint(conn.target);
            if (!srcStub || !tgtStub) continue;

            // A* from stub to stub
            const path = this.findOrthogonalPath(walls, congestion, srcStub, tgtStub);
            if (!path || path.length < 2) continue;  // DROP, don't fallback

            // Full path: src pin → src stub → ... → tgt stub → tgt pin
            const fullPath = [src, ...path, tgt];
            // Clean: remove consecutive duplicates
            const cleaned = [];
            for (const p of fullPath) {
                if (cleaned.length === 0 ||
                    Math.abs(cleaned[cleaned.length - 1].x - p.x) > 0.001 ||
                    Math.abs(cleaned[cleaned.length - 1].y - p.y) > 0.001) {
                    cleaned.push(p);
                }
            }

            // HARD FINAL GUARD: verify orthogonal + under length cap
            let isOrtho = true;
            let totalLen = 0;
            for (let i = 0; i < cleaned.length - 1; i++) {
                const dx = Math.abs(cleaned[i].x - cleaned[i + 1].x);
                const dy = Math.abs(cleaned[i].y - cleaned[i + 1].y);
                if (dx > 0.001 && dy > 0.001) { isOrtho = false; break; }
                totalLen += dx + dy;
            }
            if (!isOrtho) continue;                  // DROP diagonal wires
            if (totalLen > MAX_WIRE_MANHATTAN) continue;  // DROP too-long wires
            if (cleaned.length < 2) continue;

            this.wirePaths.push({ source: conn.source, target: conn.target, path: cleaned });
            addCongestion(cleaned);
        }

        // Detect junctions: grid points where 3+ wire endpoints meet
        const endpoints = new Map();
        for (const w of this.wirePaths) {
            for (const p of w.path) {
                const k = `${snapToGrid(p.x)},${snapToGrid(p.y)}`;
                endpoints.set(k, (endpoints.get(k) || 0) + 1);
            }
        }
        for (const [k, count] of endpoints) {
            if (count >= 3) {
                const [x, y] = k.split(',').map(Number);
                this.junctionPoints.push({ x, y });
            }
        }

        return this.wirePaths;
    }

    // --- A* pathfinding with bend + congestion cost ---
    findOrthogonalPath(walls, congestion, src, tgt) {
        const toG = v => Math.round(v / GRID_SIZE);
        const gsx = toG(src.x), gsy = toG(src.y);
        const gex = toG(tgt.x), gey = toG(tgt.y);

        const key = (x, y) => `${x},${y}`;
        // Make sure start/end are walkable
        walls.delete(key(gsx, gsy));
        walls.delete(key(gex, gey));

        const h = (x, y) => Math.abs(x - gex) + Math.abs(y - gey);
        const open = new Map();
        const closed = new Set();
        const gScore = new Map();
        const cameFrom = new Map();
        const startK = key(gsx, gsy);
        gScore.set(startK, 0);
        open.set(startK, { x: gsx, y: gsy, dir: null, f: h(gsx, gsy) });

        const MAX_PATH_LEN = Math.max(30, (Math.abs(gsx - gex) + Math.abs(gsy - gey)) * 6);

        while (open.size > 0) {
            // Pick lowest-f node
            let best = null, bestF = Infinity;
            for (const [, v] of open) {
                if (v.f < bestF) { best = v; bestF = v.f; }
            }
            const ck = key(best.x, best.y);

            if (best.x === gex && best.y === gey) {
                // Reconstruct
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
                if (closed.has(nk) || walls.has(nk)) continue;
                const turnCost = (best.dir !== null && best.dir !== nd) ? BEND_PENALTY : 0;
                const congCost = (congestion.get(nk) || 0) * CONGESTION_PENALTY;
                const tentG = (gScore.get(ck) ?? 0) + 1 + turnCost + congCost;
                if (tentG < (gScore.get(nk) ?? Infinity)) {
                    if (tentG > MAX_PATH_LEN) continue;
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

                // Pin endpoint = where the wire actually attaches
                const ex = px + Math.cos(ang) * len;
                const ey = py + Math.sin(ang) * len;

                const nameNode = getAttr(op, 'name');
                const pinName = nameNode ? nameNode[1].replace(/"/g, '') : '';
                const pinNum = numNode[1].replace(/"/g, '');
                const absX = snapToGrid(comp.x + ex);
                const absY = snapToGrid(comp.y + ey);
                const key = `${comp.refDesignator}:${pinNum}`;
                this.pinMatrix[key] = {
                    x: absX, y: absY,
                    name: pinName,
                    refDes: comp.refDesignator,
                    pinNum,
                    angle: angDeg,
                };
            });
        });
        return this.pinMatrix;
    }

    // --- Compute transform for rendering all components ---
    computeTransform(canvasW, canvasH) {
        if (this.components.length === 0) return null;

        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        this.components.forEach(comp => {
            const g = comp.geomBBox;
            minX = Math.min(minX, comp.x + g.x);
            minY = Math.min(minY, comp.y + g.y);
            maxX = Math.max(maxX, comp.x + g.x + g.w);
            maxY = Math.max(maxY, comp.y + g.y + g.h);
        });

        const margin = 20;
        minX -= margin; minY -= margin; maxX += margin; maxY += margin;
        const w = maxX - minX;
        const h = maxY - minY;

        const scale = Math.min(canvasW / w, canvasH / h) * 0.9;
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

        ctx.fillStyle = 'rgba(100, 160, 200, 0.15)';
        const dotRadius = 0.06;
        for (let x = startGridX; x <= endGridX; x += gridMm) {
            for (let y = startGridY; y <= endGridY; y += gridMm) {
                ctx.beginPath();
                ctx.arc(x, y, dotRadius, 0, Math.PI * 2);
                ctx.fill();
            }
        }

        const majorGrid = gridMm * 10;
        const majorStartX = Math.floor(visLeft / majorGrid) * majorGrid;
        const majorEndX = Math.ceil(visRight / majorGrid) * majorGrid;
        const majorStartY = Math.floor(visTop / majorGrid) * majorGrid;
        const majorEndY = Math.ceil(visBottom / majorGrid) * majorGrid;

        ctx.strokeStyle = 'rgba(100, 160, 200, 0.08)';
        ctx.lineWidth = 0.04;
        for (let x = majorStartX; x <= majorEndX; x += majorGrid) {
            ctx.beginPath();
            ctx.moveTo(x, visTop - 100);
            ctx.lineTo(x, visBottom + 100);
            ctx.stroke();
        }
        for (let y = majorStartY; y <= majorEndY; y += majorGrid) {
            ctx.beginPath();
            ctx.moveTo(visLeft - 100, y);
            ctx.lineTo(visRight + 100, y);
            ctx.stroke();
        }

        ctx.restore();
    }
}
