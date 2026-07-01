// PCB Board Viewer
// Renders BoardModel JSON (from agent output or imported .kicad_pcb)
// using Canvas 2D with KiCad-style layer colors.

const PCB_COLORS = {
    // Layer colors (matching KiCad 8 defaults)
    "F.Cu": "#C46400",        // orange (KiCad pad color)
    "B.Cu": "#0000C4",        // blue
    "F.SilkS": "#C4C400",     // yellow
    "B.SilkS": "#C4C400",     // yellow
    "F.Mask": "#800080",      // purple
    "B.Mask": "#800080",      // purple
    "F.Paste": "#808080",     // grey
    "B.Paste": "#808080",     // grey
    "F.Fab": "#C4C400",       // yellow
    "B.Fab": "#C4C400",       // yellow
    "F.CrtYd": "#808080",     // grey
    "B.CrtYd": "#808080",     // grey
    "Edge.Cuts": "#00C400",   // green
    "Dwgs.User": "#808080",   // grey
    "Cmts.User": "#808080",   // grey
    "Eco1.User": "#C40000",   // red
    "Eco2.User": "#0000C4",   // blue
    // Default trace color by layer
    _traceTop: "#C40000",
    _traceBottom: "#0000C4",
    _via: "#00C400",
    _viaDrill: "#000000",
    _padTop: "#C46400",       // orange (main pad color)
    _padBottom: "#6400C4",
    _padThrough: "#00C464",
    _background: "#0A0A14",
    _grid: "#1A1A2E",
    _gridMajor: "#2A2A3E",
    _text: "#C0C0C0",
    _highlight: "#FFFF00",
    _ratsnest: "#808080",
};

/** Interaction modes for the PCB viewer state machine. */
const PCB_MODE = {
    IDLE: 'idle',
    PANNING: 'panning',
    DRAW_TRACE: 'draw_trace',
};

let pcbState = {
    boardModel: null,
    zoom: 1,
    panX: 0,
    panY: 0,
    baseScale: 1,
    midX: 0,
    midY: 0,
    cx: 0,
    cy: 0,
    mode: PCB_MODE.IDLE,
    // Pan tracking
    isDragging: false,
    dragStartX: 0,
    dragStartY: 0,
    // Draw-trace tracking (stub for now, used by Ticket 8)
    drawStartPad: null,
    highlightNet: null,
    listenersAttached: false,
    // Ratsnest data: {net_name: [{x1, y1, x2, y2}, ...]}
    ratsnest: null,
    _ratsnestPending: false,
    // Single-level undo (Ticket 5): most recently committed trace, or null
    lastCommittedTrace: null,
};

function pcbGetCanvas() {
    const el = document.getElementById('pcbCanvas');
    if (!el) return { canvas: null, ctx: null };
    return { canvas: el, ctx: el.getContext('2d') };
}

function pcbSetupCanvas() {
    const { canvas, ctx } = pcbGetCanvas();
    if (!canvas) return;
    const parent = canvas.parentElement;
    if (parent) {
        canvas.width = parent.clientWidth;
        canvas.height = parent.clientHeight;
    }
}

function pcbLoadBoard(boardModel) {
    pcbState.boardModel = boardModel;
    pcbState.ratsnest = null;
    pcbComputeTransform();
    pcbDraw();
    pcbFetchRatsnest();
}

function pcbComputeTransform() {
    const { canvas, ctx } = pcbGetCanvas();
    if (!canvas || !pcbState.boardModel) return;

    const model = pcbState.boardModel;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;

    function expand(x, y) {
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
    }

    for (const comp of (model.components || [])) {
        expand(comp.x, comp.y);
        for (const pad of (comp.pads || [])) {
            expand(comp.x + pad.x + pad.width / 2, comp.y + pad.y + pad.height / 2);
            expand(comp.x + pad.x - pad.width / 2, comp.y + pad.y - pad.height / 2);
        }
    }

    for (const trace of (model.traces || [])) {
        for (const pt of (trace.path || [])) {
            expand(pt.x, pt.y);
        }
    }

    for (const via of (model.vias || [])) {
        expand(via.x - via.diameter / 2, via.y - via.diameter / 2);
        expand(via.x + via.diameter / 2, via.y + via.diameter / 2);
    }

    if (minX === Infinity) { minX = -50; maxX = 50; minY = -50; maxY = 50; }

    const margin = 10;
    minX -= margin; maxX += margin; minY -= margin; maxY += margin;
    const bw = maxX - minX;
    const bh = maxY - minY;

    pcbState.midX = (minX + maxX) / 2;
    pcbState.midY = (minY + maxY) / 2;
    pcbState.cx = canvas.width / 2;
    pcbState.cy = canvas.height / 2;
    pcbState.baseScale = Math.min(canvas.width / bw, canvas.height / bh) * 0.9;
    pcbState.zoom = 1;
    pcbState.panX = 0;
    pcbState.panY = 0;
}

function pcbScreenToWorld(sx, sy) {
    const s = pcbState;
    const scale = s.baseScale * s.zoom;
    const wx = (sx - s.cx - s.panX) / scale + s.midX;
    const wy = -(sy - s.cy - s.panY) / scale + s.midY;
    return { x: wx, y: wy };
}

function pcbDraw() {
    const { canvas, ctx } = pcbGetCanvas();
    if (!canvas || !pcbState.boardModel) return;

    pcbSetupCanvas();
    const s = pcbState;
    const model = s.boardModel;
    const scale = s.baseScale * s.zoom;

    // Clear
    ctx.fillStyle = PCB_COLORS._background;
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    ctx.save();
    ctx.translate(s.cx + s.panX, s.cy + s.panY);
    ctx.scale(scale, -scale);
    ctx.translate(-s.midX, -s.midY);

    // Grid
    pcbDrawGrid(ctx);

    // Board outline
    pcbDrawOutline(ctx, model);

    // Ratsnest airwires (above board outline, below copper)
    pcbDrawRatsnest(ctx);

    // Bottom layers (back to front)
    pcbDrawZones(ctx, model, "B.Cu");
    pcbDrawTraces(ctx, model, "B.Cu");
    pcbDrawVias(ctx, model);
    pcbDrawPads(ctx, model, "B.Cu");

    // Top layers
    pcbDrawZones(ctx, model, "F.Cu");
    pcbDrawTraces(ctx, model, "F.Cu");
    pcbDrawPads(ctx, model, "F.Cu");
    pcbDrawSilkscreen(ctx, model, "F.SilkS");

    ctx.restore();

    // HUD overlay
    pcbDrawHUD(ctx, canvas);
}

function pcbDrawGrid(ctx) {
    const s = pcbState;
    const scale = s.baseScale * s.zoom;
    const viewW = 400 / scale;
    const viewH = 400 / scale;
    const x0 = s.midX - viewW / 2;
    const y0 = s.midY - viewH / 2;
    const x1 = s.midX + viewW / 2;
    const y1 = s.midY + viewH / 2;

    const gridSpacing = 1.27; // 50 mil
    const majorEvery = 10;

    const startX = Math.floor(x0 / gridSpacing) * gridSpacing;
    const startY = Math.floor(y0 / gridSpacing) * gridSpacing;

    // Dot grid — KiCad-style grid rendering
    const dotRadius = 0.06;
    ctx.beginPath();
    for (let x = startX; x <= x1; x += gridSpacing) {
        for (let y = startY; y <= y1; y += gridSpacing) {
            const isMajor = (Math.round(x / gridSpacing) % majorEvery) === 0 && (Math.round(y / gridSpacing) % majorEvery) === 0;
            ctx.fillStyle = isMajor ? PCB_COLORS._gridMajor : PCB_COLORS._grid;
            ctx.fillRect(x - dotRadius, y - dotRadius, dotRadius * 2, dotRadius * 2);
        }
    }
    ctx.fill();
}

function pcbDrawOutline(ctx, model) {
    // Draw Edge.Cuts outline from board outline or gr_lines
    ctx.strokeStyle = PCB_COLORS["Edge.Cuts"];
    ctx.lineWidth = 0.15;
    ctx.setLineDash([]);

    // Use trace path if available as approximate outline
    const edgeTraces = (model.traces || []).filter(t => t.layer === "Edge.Cuts");
    for (const t of edgeTraces) {
        const pts = t.path || [];
        if (pts.length < 2) continue;
        ctx.beginPath();
        ctx.moveTo(pts[0].x, pts[0].y);
        for (let i = 1; i < pts.length; i++) {
            ctx.lineTo(pts[i].x, pts[i].y);
        }
        ctx.closePath();
        ctx.stroke();
    }

    // If no Edge.Cuts traces, draw a bounding box
    if (edgeTraces.length === 0) {
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        for (const comp of (model.components || [])) {
            for (const pad of (comp.pads || [])) {
                const px = comp.x + pad.x, py = comp.y + pad.y;
                if (px < minX) minX = px;
                if (py < minY) minY = py;
                if (px > maxX) maxX = px;
                if (py > maxY) maxY = py;
            }
        }
        if (minX !== Infinity) {
            const margin = 3;
            minX -= margin; maxX += margin; minY -= margin; maxY += margin;
            ctx.strokeRect(minX, minY, maxX - minX, maxY - minY);
        }
    }
}

function pcbDrawTraces(ctx, model, layer) {
    const traces = (model.traces || []).filter(t => t.layer === layer);
    const scale = pcbState.baseScale * pcbState.zoom;

    for (const t of traces) {
        const pts = t.path || [];
        if (pts.length < 2) continue;
        const color = layer === "F.Cu" ? PCB_COLORS._traceTop : PCB_COLORS._traceBottom;
        ctx.strokeStyle = t.net === pcbState.highlightNet ? PCB_COLORS._highlight : color;
        ctx.lineWidth = (t.width || 0.254);
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        ctx.setLineDash([]);
        ctx.beginPath();
        ctx.moveTo(pts[0].x, pts[0].y);
        for (let i = 1; i < pts.length; i++) {
            ctx.lineTo(pts[i].x, pts[i].y);
        }
        ctx.stroke();

        // Draw via at endpoint if present
        if (t.via) {
            pcbDrawVia(ctx, t.via.x, t.via.y, 0.6, 0.3, scale);
        }
    }
}

function pcbDrawVia(ctx, x, y, diameter, drill, scale) {
    // Via pad
    ctx.fillStyle = PCB_COLORS._via;
    ctx.beginPath();
    ctx.arc(x, y, diameter / 2, 0, Math.PI * 2);
    ctx.fill();

    // Via drill hole
    ctx.fillStyle = PCB_COLORS._background;
    ctx.beginPath();
    ctx.arc(x, y, drill / 2, 0, Math.PI * 2);
    ctx.fill();
}

function pcbDrawVias(ctx, model) {
    const scale = pcbState.baseScale * pcbState.zoom;
    for (const via of (model.vias || [])) {
        pcbDrawVia(ctx, via.x, via.y, via.diameter || 0.6, via.drill || 0.3, scale);
    }
}

function pcbGetPadColor(layer, type) {
    if (type === "thru_hole") return PCB_COLORS._padThrough;
    if (layer === "F.Cu" || layer.startsWith("F.")) return PCB_COLORS._padTop;
    if (layer === "B.Cu" || layer.startsWith("B.")) return PCB_COLORS._padBottom;
    return PCB_COLORS._padThrough;
}

function pcbDrawPads(ctx, model, side) {
    const scale = pcbState.baseScale * pcbState.zoom;
    for (const comp of (model.components || [])) {
        // Only draw pads on requested side (top or bottom)
        for (const pad of (comp.pads || [])) {
            const onSide = pad.layers && pad.layers.some(l => l.startsWith(side[0]) || l === side);
            if (!onSide) continue;

            const px = comp.x + pad.x;
            const py = comp.y + pad.y;
            const w = pad.width || 1;
            const h = pad.height || 1;
            const rotation = (comp.rotation || 0) + (pad.rotation || 0);
            const color = pcbGetPadColor(side, pad.type);

            ctx.save();
            ctx.translate(px, py);
            if (rotation) ctx.rotate(rotation * Math.PI / 180);
            ctx.fillStyle = color;
            ctx.strokeStyle = color;
            ctx.lineWidth = 0.05;

            if (pad.shape === "circle") {
                ctx.beginPath();
                ctx.arc(0, 0, Math.max(w, h) / 2, 0, Math.PI * 2);
                ctx.fill();
            } else if (pad.shape === "oval") {
                const r = Math.min(w, h) / 2;
                const rectW = Math.max(w, h) - r * 2;
                const isHorizontal = w > h;
                ctx.beginPath();
                if (isHorizontal) {
                    ctx.arc(-rectW / 2, 0, r, 0, Math.PI * 2);
                    ctx.arc(rectW / 2, 0, r, 0, Math.PI * 2);
                } else {
                    ctx.arc(0, -rectW / 2, r, 0, Math.PI * 2);
                    ctx.arc(0, rectW / 2, r, 0, Math.PI * 2);
                }
                ctx.fill();
            } else {
                // rect / default
                ctx.fillRect(-w / 2, -h / 2, w, h);
            }

            // Drill hole for thru-hole
            if (pad.type === "thru_hole" && pad.drill) {
                ctx.fillStyle = PCB_COLORS._background;
                ctx.beginPath();
                ctx.arc(0, 0, pad.drill / 2, 0, Math.PI * 2);
                ctx.fill();
            }

            ctx.restore();
        }
    }
}

function pcbDrawSilkscreen(ctx, model, layer) {
    const scale = pcbState.baseScale * pcbState.zoom;
    ctx.strokeStyle = PCB_COLORS[layer] || "#C4C400";
    ctx.lineWidth = 0.15;
    ctx.font = `${0.8}px monospace`;
    ctx.fillStyle = PCB_COLORS[layer] || "#C4C400";

    for (const comp of (model.components || [])) {
        // Draw reference designator
        ctx.save();
        ctx.translate(comp.x, comp.y);
        if (comp.rotation) ctx.rotate(comp.rotation * Math.PI / 180);

        // Simple ref text above component
        const tx = 0, ty = -(Math.max(...(comp.pads || []).map(p => p.height + Math.abs(p.y)) || [5])) / 2 - 1.5;
        ctx.fillText(comp.ref || comp.footprint, tx, ty);

        ctx.restore();
    }
}

function pcbDrawZones(ctx, model, layer) {
    const zones = (model.zones || []).filter(z => z.layer === layer);
    for (const z of zones) {
        ctx.fillStyle = layer === "F.Cu" ? "rgba(196, 0, 0, 0.08)" : "rgba(0, 0, 196, 0.08)";
        ctx.strokeStyle = layer === "F.Cu" ? "rgba(196, 0, 0, 0.3)" : "rgba(0, 0, 196, 0.3)";
        ctx.lineWidth = 0.1;
        ctx.setLineDash([0.3, 0.3]);
        // Zone polygon rendering would need triangulation — skip for MVP
        ctx.setLineDash([]);
    }
}

function pcbDrawRatsnest(ctx) {
    const data = pcbState.ratsnest;
    if (!data) return;

    ctx.strokeStyle = PCB_COLORS._ratsnest;
    ctx.lineWidth = 0.08;
    ctx.setLineDash([0.3, 0.3]);

    for (const edges of Object.values(data)) {
        for (const e of edges) {
            ctx.beginPath();
            ctx.moveTo(e.x1, e.y1);
            ctx.lineTo(e.x2, e.y2);
            ctx.stroke();
        }
    }

    ctx.setLineDash([]);
}

/** Fetch ratsnest data from the server for the current board model. */
function pcbFetchRatsnest() {
    if (!pcbState.boardModel || pcbState._ratsnestPending) return;
    pcbState._ratsnestPending = true;

    fetch('/api/ratsnest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(pcbState.boardModel),
    })
        .then(r => r.json())
        .then(data => {
            pcbState.ratsnest = data;
            pcbState._ratsnestPending = false;
            pcbDraw();
        })
        .catch(err => {
            console.warn('Ratsnest fetch failed:', err);
            pcbState._ratsnestPending = false;
        });
}

/** Re-fetch ratsnest after a trace commit (called by Ticket 5/8 undo/commit). */
function pcbRefreshRatsnest() {
    pcbState.ratsnest = null;
    pcbFetchRatsnest();
}

function pcbDrawHUD(ctx, canvas) {
    ctx.save();
    ctx.fillStyle = "#C0C0C0";
    ctx.font = "12px monospace";
    const zoomPct = Math.round(pcbState.zoom * 100);
    ctx.fillText(`Zoom: ${zoomPct}%`, 10, 20);
    ctx.fillText(`Components: ${(pcbState.boardModel.components || []).length}`, 10, 36);
    ctx.fillText(`Traces: ${(pcbState.boardModel.traces || []).length}`, 10, 52);
    ctx.fillText(`Vias: ${(pcbState.boardModel.vias || []).length}`, 10, 68);
    if (pcbState.highlightNet) {
        ctx.fillStyle = PCB_COLORS._highlight;
        ctx.fillText(`Highlight: ${pcbState.highlightNet}`, 10, 84);
    }
    ctx.restore();
}

function pcbDrawCurrent() {
    pcbDraw();
}

// -- Event handlers (state machine) --

/** Return true if the given screen point hits a pad that has a ratsnest edge. */
function _pcbHitTestPad(sx, sy) {
    if (!pcbState.ratsnest || !pcbState.boardModel) return false;

    const world = pcbScreenToWorld(sx, sy);
    if (!world) return false;

    // Collect all ratsnest endpoint positions
    const endpoints = new Set();
    for (const edges of Object.values(pcbState.ratsnest)) {
        for (const e of edges) {
            endpoints.add(`${e.x1},${e.y1}`);
            endpoints.add(`${e.x2},${e.y2}`);
        }
    }
    if (endpoints.size === 0) return false;

    // Snap tolerance = half grid spacing
    const snapPx = 1.27 / 2;

    // Check each pad in the board model
    const model = pcbState.boardModel;
    for (const comp of (model.components || [])) {
        for (const pad of (comp.pads || [])) {
            const px = comp.x + pad.x;
            const py = comp.y + pad.y;
            if (endpoints.has(`${px},${py}`)) {
                const dx = world.x - px;
                const dy = world.y - py;
                if (Math.abs(dx) < snapPx && Math.abs(dy) < snapPx) {
                    return true;
                }
            }
        }
    }

    return false;
}

function pcbHandleWheel(e) {
    e.preventDefault();
    const delta = e.deltaY > 0 ? 0.85 : 1.18;
    pcbState.zoom = Math.min(Math.max(pcbState.zoom * delta, 0.05), 50);
    pcbDraw();
}

function pcbHandleMouseDown(e) {
    if (pcbState.mode === PCB_MODE.DRAW_TRACE) {
        // While drawing, left-click places a vertex; right-click cancels.
        // This is a stub — Ticket 8 will add real vertex placement here.
        return;
    }

    // Only left button starts panning or drawing
    if (e.button !== 0) return;

    const world = pcbScreenToWorld(e.clientX, e.clientY);
    if (!world) return;

    // Check if click hits a ratsnest pad → enter draw-trace mode
    const sx = e.clientX;
    const sy = e.clientY;
    if (_pcbHitTestPad(sx, sy)) {
        pcbState.mode = PCB_MODE.DRAW_TRACE;
        pcbState.drawStartPad = { x: world.x, y: world.y };
        e.target.style.cursor = 'crosshair';
        pcbDraw();
        return;
    }

    // Otherwise start panning
    pcbState.mode = PCB_MODE.PANNING;
    pcbState.dragStartX = e.clientX;
    pcbState.dragStartY = e.clientY;
    e.target.style.cursor = 'grabbing';
}

function pcbHandleMouseMove(e) {
    if (pcbState.mode === PCB_MODE.PANNING) {
        const dx = e.clientX - pcbState.dragStartX;
        const dy = e.clientY - pcbState.dragStartY;
        pcbState.dragStartX = e.clientX;
        pcbState.dragStartY = e.clientY;
        pcbState.panX += dx;
        pcbState.panY += dy;
        pcbDraw();
        return;
    }

    if (pcbState.mode === PCB_MODE.DRAW_TRACE) {
        // Stub — Ticket 8 will draw rubber-band line here
        pcbDraw();
        return;
    }
}

function pcbHandleMouseUp(e) {
    if (pcbState.mode === PCB_MODE.PANNING) {
        pcbState.mode = PCB_MODE.IDLE;
        if (e && e.target) e.target.style.cursor = 'grab';
        return;
    }

    if (pcbState.mode === PCB_MODE.DRAW_TRACE) {
        // Stub — Ticket 8 will commit trace segment here
        return;
    }
}

/** Cancel draw-trace mode and return to idle. */
function pcbCancelDraw() {
    if (pcbState.mode === PCB_MODE.DRAW_TRACE) {
        pcbState.mode = PCB_MODE.IDLE;
        pcbState.drawStartPad = null;
        pcbDraw();
    }
}

/** Record a committed trace for single-level undo.

Called by Ticket 8 (trace commit) to store the trace and its net so
Ctrl+Z can remove it.  Only the most recent trace is kept — subsequent
commits overwrite.
*/
function pcbSetLastCommittedTrace(traceData) {
    // traceData: {net, layer, width, path: [{x,y},...]}
    pcbState.lastCommittedTrace = traceData ? { ...traceData, path: traceData.path.map(p => ({ ...p })) } : null;
}

/** Undo the last committed trace: remove it from the board model, re-fetch ratsnest. */
function pcbUndoLastTrace() {
    const trace = pcbState.lastCommittedTrace;
    if (!trace || !pcbState.boardModel) return;

    // Remove matching trace from board model
    const traces = pcbState.boardModel.traces || [];
    const pathStr = JSON.stringify(trace.path);
    const idx = traces.findIndex(t => JSON.stringify(t.path) === pathStr);
    if (idx !== -1) {
        traces.splice(idx, 1);
    }

    pcbState.lastCommittedTrace = null;
    pcbState.mode = PCB_MODE.IDLE;
    pcbRefreshRatsnest();
    pcbDraw();
}

/** Handle keyboard shortcuts (called from app.js keydown). */
function pcbHandleKeyDown(e) {
    if (e.key === 'Escape') {
        pcbCancelDraw();
        return;
    }

    // Ctrl+Z or Cmd+Z: undo last committed trace
    if ((e.ctrlKey || e.metaKey) && e.key === 'z') {
        e.preventDefault();
        pcbUndoLastTrace();
    }
}

// Expose globally
window.pcbLoadBoard = pcbLoadBoard;
window.pcbDraw = pcbDraw;
window.pcbHandleWheel = pcbHandleWheel;
window.pcbHandleMouseDown = pcbHandleMouseDown;
window.pcbHandleMouseMove = pcbHandleMouseMove;
window.pcbHandleMouseUp = pcbHandleMouseUp;
window.pcbHandleKeyDown = pcbHandleKeyDown;
window.pcbSetupCanvas = pcbSetupCanvas;
window.pcbDrawCurrent = pcbDrawCurrent;
window.pcbScreenToWorld = pcbScreenToWorld;
window.pcbRefreshRatsnest = pcbRefreshRatsnest;
window.pcbSetLastCommittedTrace = pcbSetLastCommittedTrace;
window.pcbUndoLastTrace = pcbUndoLastTrace;
window.PCB_MODE = PCB_MODE;
window.pcbState = pcbState;
