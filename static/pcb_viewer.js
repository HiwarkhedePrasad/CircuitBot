// PCB Board Viewer
// Renders BoardModel JSON (from agent output or imported .kicad_pcb)
// using Canvas 2D with KiCad-style layer colors.

const PCB_COLORS = {
    // Layer colors (matching KiCad 8 defaults)
    "F.Cu": "#C40000",        // red
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
    _padTop: "#C46400",
    _padBottom: "#6400C4",
    _padThrough: "#00C464",
    _background: "#0A0A14",
    _grid: "#1A1A2E",
    _gridMajor: "#2A2A3E",
    _text: "#C0C0C0",
    _highlight: "#FFFF00",
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
    isDragging: false,
    dragStartX: 0,
    dragStartY: 0,
    highlightNet: null,
    listenersAttached: false,
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
    pcbComputeTransform();
    pcbDraw();
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

    for (let x = startX; x <= x1; x += gridSpacing) {
        const isMajor = (Math.round(x / gridSpacing) % majorEvery) === 0;
        ctx.strokeStyle = isMajor ? PCB_COLORS._gridMajor : PCB_COLORS._grid;
        ctx.lineWidth = isMajor ? (0.05 / scale) : (0.025 / scale);
        ctx.beginPath();
        ctx.moveTo(x, y0);
        ctx.lineTo(x, y1);
        ctx.stroke();
    }
    for (let y = startY; y <= y1; y += gridSpacing) {
        const isMajor = (Math.round(y / gridSpacing) % majorEvery) === 0;
        ctx.strokeStyle = isMajor ? PCB_COLORS._gridMajor : PCB_COLORS._grid;
        ctx.lineWidth = isMajor ? (0.05 / scale) : (0.025 / scale);
        ctx.beginPath();
        ctx.moveTo(x0, y);
        ctx.lineTo(x1, y);
        ctx.stroke();
    }
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

// -- Event handlers (shared with main canvas) --
function pcbHandleWheel(e) {
    e.preventDefault();
    const delta = e.deltaY > 0 ? 0.85 : 1.18;
    pcbState.zoom = Math.min(Math.max(pcbState.zoom * delta, 0.05), 50);
    pcbDraw();
}

function pcbHandleMouseDown(e) {
    pcbState.isDragging = true;
    pcbState.dragStartX = e.clientX;
    pcbState.dragStartY = e.clientY;
    e.target.style.cursor = 'grabbing';
}

function pcbHandleMouseMove(e) {
    if (!pcbState.isDragging) return;
    const dx = e.clientX - pcbState.dragStartX;
    const dy = e.clientY - pcbState.dragStartY;
    pcbState.dragStartX = e.clientX;
    pcbState.dragStartY = e.clientY;
    pcbState.panX += dx;
    pcbState.panY += dy;
    pcbDraw();
}

function pcbHandleMouseUp(e) {
    pcbState.isDragging = false;
    if (e && e.target) e.target.style.cursor = 'grab';
}

// Expose globally
window.pcbLoadBoard = pcbLoadBoard;
window.pcbDraw = pcbDraw;
window.pcbHandleWheel = pcbHandleWheel;
window.pcbHandleMouseDown = pcbHandleMouseDown;
window.pcbHandleMouseMove = pcbHandleMouseMove;
window.pcbHandleMouseUp = pcbHandleMouseUp;
window.pcbSetupCanvas = pcbSetupCanvas;
window.pcbDrawCurrent = pcbDrawCurrent;
window.pcbScreenToWorld = pcbScreenToWorld;
