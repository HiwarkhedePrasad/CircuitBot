// --- PCB View Renderer ---
// Renders a realistic PCB layout with copper pads, traces, silkscreen,
// and FR4 substrate on the same canvas as the schematic view.

const PCB = {
    substrate: '#1a5c2a',
    substrateInner: '#1e6b30',
    boardEdge: '#777777',
    copper: '#c8903a',
    copperBright: '#dcb04a',
    copperDark: '#a07020',
    hole: '#111111',
    silk: '#ffffff',
    pin1Marker: '#ffffff',
    netColors: [
        '#c8903a', '#4090d0', '#d06040', '#50a050',
        '#a070c0', '#d0a040', '#5090a0', '#c06080',
    ],
    BOARD_MARGIN: 3.0,
    TRACE_WIDTH: 0.4,
    SILK_LINE: 0.12,
    HOLE_DIAMETER: 0.6,
};

function drawPCB() {
    if (!currentSchematic || !currentTransform || currentSchematic.components.length === 0) return;

    const { canvas, ctx } = getCanvasAndCtx();
    const t = currentTransform;

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Light grid
    Schematic.drawGrid(ctx, t, zoomLevel, canvas.width, canvas.height);

    ctx.save();
    ctx.translate(t.cx + panX, t.cy + panY);
    ctx.scale(t.baseScale * zoomLevel, -t.baseScale * zoomLevel);
    ctx.translate(-t.midX, -t.midY);
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    drawBoard(ctx, currentSchematic.components);
    drawCopperTraces(ctx, currentSchematic.wirePaths);
    drawAllPads(ctx, currentSchematic.components);
    drawSilkscreen(ctx, currentSchematic.components);

    ctx.restore();
}

// ── Board outline ──────────────────────────────────────────────────────

function getBoardBounds(components) {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    components.forEach(comp => {
        const g = comp.geomBBox || { x: -5, y: -5, w: 10, h: 10 };
        minX = Math.min(minX, comp.x + g.x);
        minY = Math.min(minY, comp.y + g.y);
        maxX = Math.max(maxX, comp.x + g.x + g.w);
        maxY = Math.max(maxY, comp.y + g.y + g.h);
    });
    const m = PCB.BOARD_MARGIN;
    return { x: minX - m, y: minY - m, w: maxX - minX + m * 2, h: maxY - minY + m * 2 };
}

function drawBoard(ctx, components) {
    const b = getBoardBounds(components);
    const r = 2.0;

    // Substrate fill
    const grad = ctx.createRadialGradient(b.x + b.w / 2, b.y + b.h / 2, 0, b.x + b.w / 2, b.y + b.h / 2, Math.max(b.w, b.h));
    grad.addColorStop(0, PCB.substrateInner);
    grad.addColorStop(1, PCB.substrate);
    ctx.fillStyle = grad;

    ctx.beginPath();
    ctx.moveTo(b.x + r, b.y);
    ctx.lineTo(b.x + b.w - r, b.y);
    ctx.quadraticCurveTo(b.x + b.w, b.y, b.x + b.w, b.y + r);
    ctx.lineTo(b.x + b.w, b.y + b.h - r);
    ctx.quadraticCurveTo(b.x + b.w, b.y + b.h, b.x + b.w - r, b.y + b.h);
    ctx.lineTo(b.x + r, b.y + b.h);
    ctx.quadraticCurveTo(b.x, b.y + b.h, b.x, b.y + b.h - r);
    ctx.lineTo(b.x, b.y + r);
    ctx.quadraticCurveTo(b.x, b.y, b.x + r, b.y);
    ctx.closePath();
    ctx.fill();

    // Board edge
    ctx.strokeStyle = PCB.boardEdge;
    ctx.lineWidth = 0.3;
    ctx.stroke();

    // Edge highlight (inner glow)
    ctx.strokeStyle = 'rgba(255,255,255,0.06)';
    ctx.lineWidth = 0.8;
    ctx.stroke();
}

// ── Copper traces ──────────────────────────────────────────────────────

function drawCopperTraces(ctx, wirePaths) {
    if (!wirePaths || wirePaths.length === 0) return;

    let colorIdx = 0;
    const netColorMap = {};

    wirePaths.forEach(wire => {
        if (!wire.path || wire.path.length < 2) return;
        const net = wire.net || '';
        if (!netColorMap[net]) {
            netColorMap[net] = PCB.netColors[colorIdx % PCB.netColors.length];
            colorIdx++;
        }
    });

    // Traces (drawn as copper with slight shadow)
    wirePaths.forEach(wire => {
        if (!wire.path || wire.path.length < 2) return;
        const pts = wire.path;
        const color = netColorMap[wire.net || ''] || PCB.copper;

        // Subtle dark backing for depth
        ctx.strokeStyle = 'rgba(0,0,0,0.3)';
        ctx.lineWidth = PCB.TRACE_WIDTH + 0.08;
        ctx.beginPath();
        ctx.moveTo(pts[0].x, pts[0].y);
        for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
        ctx.stroke();

        // Copper trace
        ctx.strokeStyle = color;
        ctx.lineWidth = PCB.TRACE_WIDTH;
        ctx.beginPath();
        ctx.moveTo(pts[0].x, pts[0].y);
        for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
        ctx.stroke();

        // Bright center highlight
        ctx.strokeStyle = 'rgba(255,220,140,0.12)';
        ctx.lineWidth = PCB.TRACE_WIDTH * 0.4;
        ctx.beginPath();
        ctx.moveTo(pts[0].x, pts[0].y);
        for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
        ctx.stroke();

        // Net label
        if (wire.net && pts.length >= 2) {
            const mid = Math.floor(pts.length / 2);
            ctx.save();
            ctx.translate(pts[mid].x, pts[mid].y + 1.2);
            ctx.scale(1, -1);
            ctx.fillStyle = 'rgba(255,255,255,0.35)';
            ctx.font = '0.7px monospace';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(wire.net, 0, 0);
            ctx.restore();
        }
    });
}

// ── Pad rendering ──────────────────────────────────────────────────────
// Supports: rect, roundrect (with radius), circle, oval, trapezoid
// Pad types: smd, thru_hole (with hole), connector, np_thru_hole

function drawPad(ctx, pad, ox, oy) {
    const cx = ox + pad.x;
    const cy = oy + pad.y;
    const w = pad.sx;
    const h = pad.sy;
    const rot = pad.ox || 0;
    const isThru = pad.type === 'thru_hole' || pad.type === 'np_thru_hole' || pad.type === 'connector';
    const shape = (pad.shape || 'rect').toLowerCase();

    const hw = w / 2;
    const hh = h / 2;

    ctx.save();
    if (rot) {
        ctx.translate(cx, cy);
        ctx.rotate(rot * Math.PI / 180);
        ctx.translate(-cx, -cy);
    }

    if (shape === 'circle') {
        // Circular pad
        const r = Math.max(w, h) / 2;

        // Copper fill
        ctx.fillStyle = PCB.copper;
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.fill();

        // Copper outline
        ctx.strokeStyle = PCB.copperDark;
        ctx.lineWidth = 0.05;
        ctx.stroke();

        // Bright center
        ctx.fillStyle = PCB.copperBright;
        ctx.beginPath();
        ctx.arc(cx - r * 0.15, cy - r * 0.15, r * 0.5, 0, Math.PI * 2);
        ctx.fill();

        // Hole for thru-hole
        if (isThru) {
            ctx.fillStyle = PCB.hole;
            ctx.beginPath();
            ctx.arc(cx, cy, PCB.HOLE_DIAMETER / 2, 0, Math.PI * 2);
            ctx.fill();
        }
    } else if (shape === 'oval') {
        // Oval pad
        const rx = w / 2;
        const ry = h / 2;
        const isHorizontal = w >= h;

        ctx.fillStyle = PCB.copper;
        ctx.beginPath();
        if (isHorizontal) {
            ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2);
        } else {
            ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI * 2);
        }
        ctx.fill();
        ctx.strokeStyle = PCB.copperDark;
        ctx.lineWidth = 0.05;
        ctx.stroke();

        if (isThru) {
            ctx.fillStyle = PCB.hole;
            ctx.beginPath();
            ctx.arc(cx, cy, PCB.HOLE_DIAMETER / 2, 0, Math.PI * 2);
            ctx.fill();
        }
    } else {
        // Rectangular / roundrect pad
        const radius = shape === 'roundrect' ? Math.min(w, h) * 0.25 : 0;

        // Copper
        ctx.fillStyle = PCB.copper;
        roundRect(ctx, cx - hw, cy - hh, w, h, radius);
        ctx.fill();

        // Outline
        ctx.strokeStyle = PCB.copperDark;
        ctx.lineWidth = 0.04;
        roundRect(ctx, cx - hw, cy - hh, w, h, radius);
        ctx.stroke();

        // Bright highlight (top-left)
        ctx.fillStyle = 'rgba(255,220,140,0.15)';
        roundRect(ctx, cx - hw + 0.1, cy - hh + 0.1, w * 0.45, h * 0.45, radius * 0.5);
        ctx.fill();

        // Hole for thru-hole
        if (isThru) {
            ctx.fillStyle = PCB.hole;
            ctx.beginPath();
            ctx.arc(cx, cy, PCB.HOLE_DIAMETER / 2, 0, Math.PI * 2);
            ctx.fill();
        }
    }

    ctx.restore();
}

function roundRect(ctx, x, y, w, h, r) {
    if (r <= 0) {
        ctx.rect(x, y, w, h);
        return;
    }
    r = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r);
    ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
}

function drawAllPads(ctx, components) {
    components.forEach(comp => {
        const pads = comp.pads;
        if (!pads || pads.length === 0) return;
        const rot = comp.rotation || 0;
        if (rot) {
            ctx.save();
            ctx.translate(comp.x, comp.y);
            ctx.rotate(rot * Math.PI / 180);
            ctx.translate(-comp.x, -comp.y);
        }
        pads.forEach(pad => drawPad(ctx, pad, comp.x, comp.y));
        if (rot) ctx.restore();
    });
}

// ── Silkscreen ─────────────────────────────────────────────────────────

function drawComponentSilkscreen(ctx, comp, bbox) {
    const rot = comp.rotation || 0;
    if (rot) {
        ctx.save();
        ctx.translate(comp.x, comp.y);
        ctx.rotate(rot * Math.PI / 180);
        ctx.translate(-comp.x, -comp.y);
    }

    const sx = comp.x + bbox.x;
    const sy = comp.y + bbox.y;
    const sw = bbox.w;
    const sh = bbox.h;

    // Slight shadow beneath silkscreen
    ctx.strokeStyle = 'rgba(0,0,0,0.3)';
    ctx.lineWidth = PCB.SILK_LINE + 0.08;
    ctx.strokeRect(sx + 0.08, sy + 0.08, sw, sh);

    // White silkscreen outline
    ctx.strokeStyle = PCB.silk;
    ctx.lineWidth = PCB.SILK_LINE;
    ctx.strokeRect(sx, sy, sw, sh);

    // Pin-1 marker (small circle or dot near first pad)
    const pads = comp.pads;
    if (pads && pads.length > 0) {
        const p1 = pads[0];
        const mx = comp.x + p1.x;
        const my = comp.y + p1.y;
        ctx.fillStyle = PCB.pin1Marker;
        ctx.beginPath();
        ctx.arc(mx, my, 0.2, 0, Math.PI * 2);
        ctx.fill();
    }

    if (rot) ctx.restore();
}

function drawSilkscreen(ctx, components) {
    components.forEach(comp => {
        const pads = comp.pads;

        if (pads && pads.length > 0) {
            // Compute bbox from all pad positions
            let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
            pads.forEach(p => {
                const hw = p.sx / 2, hh = p.sy / 2;
                minX = Math.min(minX, p.x - hw);
                maxX = Math.max(maxX, p.x + hw);
                minY = Math.min(minY, p.y - hh);
                maxY = Math.max(maxY, p.y + hh);
            });
            const margin = 0.4;
            drawComponentSilkscreen(ctx, comp, {
                x: minX - margin, y: minY - margin,
                w: maxX - minX + margin * 2, h: maxY - minY + margin * 2,
            });
        } else {
            // Fallback: use geomBBox
            const g = comp.geomBBox || { x: -2.54, y: -2.54, w: 5.08, h: 5.08 };
            drawComponentSilkscreen(ctx, comp, g);
        }

        // Reference designator in silkscreen
        ctx.save();
        ctx.translate(comp.x, comp.y + 0.6);
        ctx.scale(1, -1);
        ctx.fillStyle = PCB.silk;
        ctx.font = '0.7px monospace';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.globalAlpha = 0.9;
        ctx.fillText(comp.refDesignator, 0, 0);
        ctx.globalAlpha = 1.0;
        ctx.restore();
    });
}

// ── Enter PCB mode ─────────────────────────────────────────────────────

function enterPcbMode() {
    if (!currentSchematic || currentSchematic.components.length === 0) return;

    currentSchematic.mode = 'pcb';
    const { canvas } = getCanvasAndCtx();
    setupCanvasSize();
    const transform = currentSchematic.computeTransform(canvas.width, canvas.height);
    currentTransform = transform;
    zoomLevel = 1;
    panX = 0;
    panY = 0;

    drawPCB();
    attachZoomHandlers();
}
