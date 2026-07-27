// ── Schematic Renderer (PixiJS) ──────────────────────────────────────────────
// Phase 1A: view-only renderer with grid, symbols, wires, zoom/pan, selection.
// Replaces static/renderer.js Canvas2D rendering path.

// NOTE: GRID_SIZE / sexprStr / isHiddenSexprText are defined in schematic.js
// (loaded before this file). Fallbacks keep unit tests working in isolation.
const _sexprStr = (typeof sexprStr === 'function')
    ? sexprStr
    : (v) => (v == null ? '' : String(v).replace(/^"+|"+$/g, '').trim());
const _isHiddenSexprText = (typeof isHiddenSexprText === 'function')
    ? isHiddenSexprText
    : (v) => { const s = _sexprStr(v); return !s || s === '~'; };

const SCH_COLORS = {
    bg: 0x0b0f0c,
    symbolLine: 0x3dff9a,
    symbolFill: 0x12261a,
    pinLine: 0x3dff9a,
    pinName: 0x8affc8,
    pinNum: 0x3dff9a,
    propertyRef: 0xa8ffd6,
    propertyVal: 0x7ad4a8,
    text: 0x6a7a70,
    wire: 0x2fd47a,
    wirePreview: 0x8affc8,
    junction: 0x2fd47a,
    terminal: 0x3dff9a,
    terminalHover: 0xffffff,
    terminalActive: 0xffd166,
    grid: 0x1a2e22,
    gridMajor: 0x24382c,
    selection: 0x3dff9a,
    powerGnd: 0x5b9cff,
    powerVcc: 0xff6b6b,
    noConnect: 0xff6b6b,
    netLabelBox: 0x2fd47a,
    netLabelText: 0x8affc8,
    netLabelStub: 0x3dff9a,
};

/** Resolve KiCad fill type from parsed S-expression fill node. */
function resolveFillType(fill) {
    if (!fill) return null;
    // Modern parse: ["fill", ["type", "background"]]
    if (Array.isArray(fill[1])) {
        if (fill[1][0] === 'type') return _sexprStr(fill[1][1]);
        const nested = getAttr(fill, 'type');
        if (nested) return _sexprStr(nested[1]);
    }
    // Legacy / string forms
    const raw = _sexprStr(fill[1]);
    if (raw === 'background' || raw === 'solid' || raw === 'none') return raw;
    if (raw.includes('background')) return 'background';
    if (raw.includes('solid')) return 'solid';
    if (raw.includes('none')) return 'none';
    return null;
}

/** True if a property/text op should be hidden on the canvas. */
function isSexprHidden(op) {
    const hide = getAttr(op, 'hide');
    if (hide) {
        const v = _sexprStr(hide[1] != null ? hide[1] : hide[0]);
        if (v === 'yes' || v === 'true' || v === '1' || v === 'hide') return true;
        // Bare (hide) with no value
        if (hide.length === 1 && hide[0] === 'hide') return true;
    }
    const effects = getAttr(op, 'effects');
    if (effects) {
        for (let i = 1; i < effects.length; i++) {
            const child = effects[i];
            if (child === 'hide' || _sexprStr(child) === 'hide') return true;
            if (Array.isArray(child) && child[0] === 'hide') {
                const v = _sexprStr(child[1] != null ? child[1] : 'yes');
                if (v === 'yes' || v === 'true' || v === '1' || v === 'hide') return true;
            }
        }
    }
    return false;
}

// ── Symbol style overrides ────────────────────────────────────────────────

const SymbolKind = {
    RESISTOR: "resistor",
    CAPACITOR: "capacitor",
    INDUCTOR: "inductor",
    DIODE: "diode",
    LED: "led",
    NPN: "npn",
    PNP: "pnp",
    BATTERY: "battery",
    SWITCH: "switch",
    OPAMP: "opamp",
};
const SymbolStandard = { IEC: "iec", ANSI: "ansi" };

const SymbolStyleOverrides = {
    // Resistors
    "Device:R":       { kind: SymbolKind.RESISTOR },
    "Device:R_Small": { kind: SymbolKind.RESISTOR },
    // Capacitors
    "Device:C":       { kind: SymbolKind.CAPACITOR },
    "Device:C_Small": { kind: SymbolKind.CAPACITOR },
    "Device:C_Polarized": { kind: SymbolKind.CAPACITOR },
    // Inductors
    "Device:L":       { kind: SymbolKind.INDUCTOR },
    "Device:L_Small": { kind: SymbolKind.INDUCTOR },
    // Diodes
    "Device:D":       { kind: SymbolKind.DIODE },
    "Device:D_Small": { kind: SymbolKind.DIODE },
    "Device:LED":     { kind: SymbolKind.LED },
    "Device:LED_Small": { kind: SymbolKind.LED },
    // Transistors
    "Device:Q_NPN_BCE":  { kind: SymbolKind.NPN },
    "Device:Q_PNP_BCE":  { kind: SymbolKind.PNP },
    "Device:Q_NPN_EBC":  { kind: SymbolKind.NPN },
    "Device:Q_PNP_EBC":  { kind: SymbolKind.PNP },
    // Battery
    "Device:Battery":     { kind: SymbolKind.BATTERY },
    "Device:Battery_Cell": { kind: SymbolKind.BATTERY },
    // Switch
    "Device:SW_Push":     { kind: SymbolKind.SWITCH },
    "Device:SW_SPST":     { kind: SymbolKind.SWITCH },
    // Op-amp
    "Device:Opamp":       { kind: SymbolKind.OPAMP },
    "Device:Opamp_Small": { kind: SymbolKind.OPAMP },
};

const SymbolStyleRenderers = {
    [SymbolKind.RESISTOR]: drawResistorAnsi,
    [SymbolKind.CAPACITOR]: drawCapacitorAnsi,
    [SymbolKind.INDUCTOR]: drawInductorAnsi,
    [SymbolKind.DIODE]: drawDiodeAnsi,
    [SymbolKind.LED]: drawLedAnsi,
    [SymbolKind.NPN]: drawNpnAnsi,
    [SymbolKind.PNP]: drawPnpAnsi,
    [SymbolKind.BATTERY]: drawBatteryAnsi,
    [SymbolKind.SWITCH]: drawSwitchAnsi,
    [SymbolKind.OPAMP]: drawOpampAnsi,
};

function detectBodyRect(comp) {
    const rects = comp.ops.filter(op => op[0] === 'rectangle');
    if (rects.length === 0) return null;
    const pins = comp.ops.filter(op => op[0] === 'pin');
    if (pins.length < 2) return null;

    let minTipY = Infinity, maxTipY = -Infinity;
    for (const pin of pins) {
        const at = getAttr(pin, 'at');
        const len = parseFloat((getAttr(pin, 'length') || [0, 0])[1]);
        const angle = parseFloat(at[3]) * Math.PI / 180;
        const tipY = parseFloat(at[2]) - Math.sin(angle) * len;
        minTipY = Math.min(minTipY, tipY);
        maxTipY = Math.max(maxTipY, tipY);
    }

    for (const r of rects) {
        const s = getAttr(r, 'start'), e = getAttr(r, 'end');
        if (!s || !e) continue;
        const ry1 = parseFloat(s[2]), ry2 = parseFloat(e[2]);
        const sy = Math.min(ry1, ry2), ey = Math.max(ry1, ry2);
        const halfH = (ey - sy) / 2;
        const tol = Math.max(0.05, halfH * 0.05);
        if (Math.abs(sy - minTipY) < tol && Math.abs(ey - maxTipY) < tol) return r;
    }
    return null;
}

function drawResistorAnsi(g, comp, rectOp) {
    const s = getAttr(rectOp, 'start'), e = getAttr(rectOp, 'end');
    const x1 = parseFloat(s[1]), y1 = parseFloat(s[2]);
    const x2 = parseFloat(e[1]), y2 = parseFloat(e[2]);
    const halfW = Math.abs(x2 - x1) / 2;
    const halfH = Math.abs(y2 - y1) / 2;
    const cx = (x1 + x2) / 2 + comp.x;
    const cy = (y1 + y2) / 2 + comp.y;
    const segments = Math.max(4, Math.round(halfH / 0.6));

    g.lineStyle(0.2032, SCH_COLORS.symbolLine, 1);
    for (let i = 0; i <= segments; i++) {
        const t = i / segments;
        const x = cx + (i % 2 === 0 ? -halfW : halfW);
        const y = cy + halfH - t * halfH * 2;
        i === 0 ? g.moveTo(x, y) : g.lineTo(x, y);
    }
}

function _getBodyBounds(comp, rectOp) {
    const s = getAttr(rectOp, 'start'), e = getAttr(rectOp, 'end');
    const x1 = parseFloat(s[1]), y1 = parseFloat(s[2]);
    const x2 = parseFloat(e[1]), y2 = parseFloat(e[2]);
    return {
        x: Math.min(x1, x2) + comp.x,
        y: Math.min(y1, y2) + comp.y,
        w: Math.abs(x2 - x1),
        h: Math.abs(y2 - y1),
        cx: (x1 + x2) / 2 + comp.x,
        cy: (y1 + y2) / 2 + comp.y,
    };
}

function drawCapacitorAnsi(g, comp, rectOp) {
    const b = _getBodyBounds(comp, rectOp);
    const gap = b.w * 0.25;
    g.lineStyle(0.2032, SCH_COLORS.symbolLine, 1);
    // Left plate
    g.moveTo(b.cx - gap, b.y);
    g.lineTo(b.cx - gap, b.y + b.h);
    // Right plate
    g.moveTo(b.cx + gap, b.y);
    g.lineTo(b.cx + gap, b.y + b.h);
}

function drawInductorAnsi(g, comp, rectOp) {
    const b = _getBodyBounds(comp, rectOp);
    const coils = 4;
    const coilW = b.w / coils;
    g.lineStyle(0.2032, SCH_COLORS.symbolLine, 1);
    g.moveTo(b.x, b.cy);
    for (let i = 0; i < coils; i++) {
        const cx = b.x + (i + 0.5) * coilW;
        g.arc(cx, b.cy, coilW / 2, Math.PI, 0, false);
    }
    g.lineTo(b.x + b.w, b.cy);
}

function drawDiodeAnsi(g, comp, rectOp) {
    const b = _getBodyBounds(comp, rectOp);
    const triW = b.w * 0.6;
    const triH = b.h * 0.8;
    g.lineStyle(0.2032, SCH_COLORS.symbolLine, 1);
    // Triangle
    g.moveTo(b.cx - triW / 2, b.cy - triH / 2);
    g.lineTo(b.cx + triW / 2, b.cy);
    g.lineTo(b.cx - triW / 2, b.cy + triH / 2);
    g.lineTo(b.cx - triW / 2, b.cy - triH / 2);
    // Cathode bar
    g.moveTo(b.cx + triW / 2, b.cy - triH / 2);
    g.lineTo(b.cx + triW / 2, b.cy + triH / 2);
}

function drawLedAnsi(g, comp, rectOp) {
    drawDiodeAnsi(g, comp, rectOp);
    const b = _getBodyBounds(comp, rectOp);
    const triW = b.w * 0.6;
    // Arrow rays
    g.lineStyle(0.15, SCH_COLORS.symbolLine, 0.8);
    const ax = b.cx + triW / 2 + b.w * 0.15;
    const ay1 = b.cy - b.h * 0.3;
    const ay2 = b.cy - b.h * 0.15;
    g.moveTo(ax, ay1);
    g.lineTo(ax + b.w * 0.12, ay1 - b.h * 0.12);
    g.moveTo(ax, ay2);
    g.lineTo(ax + b.w * 0.12, ay2 - b.h * 0.12);
}

function drawNpnAnsi(g, comp, rectOp) {
    const b = _getBodyBounds(comp, rectOp);
    const r = b.h * 0.35;
    g.lineStyle(0.2032, SCH_COLORS.symbolLine, 1);
    // Circle
    g.arc(b.cx, b.cy, r, 0, Math.PI * 2, false);
    // Base line
    g.moveTo(b.cx - r, b.cy);
    g.lineTo(b.x, b.cy);
    // Emitter line (with arrow)
    g.moveTo(b.cx - r * 0.3, b.cy + r * 0.6);
    g.lineTo(b.cx + r * 0.6, b.y + b.h);
    // Collector line
    g.moveTo(b.cx - r * 0.3, b.cy - r * 0.6);
    g.lineTo(b.cx + r * 0.6, b.y);
    // Arrow on emitter
    g.lineStyle(0.15, SCH_COLORS.symbolLine, 1);
    const arrowX = b.cx + r * 0.3;
    const arrowY = b.cy + r * 0.8;
    g.moveTo(arrowX - 0.15, arrowY - 0.1);
    g.lineTo(arrowX, arrowY + 0.05);
    g.lineTo(arrowX + 0.1, arrowY - 0.15);
}

function drawPnpAnsi(g, comp, rectOp) {
    const b = _getBodyBounds(comp, rectOp);
    const r = b.h * 0.35;
    g.lineStyle(0.2032, SCH_COLORS.symbolLine, 1);
    // Circle
    g.arc(b.cx, b.cy, r, 0, Math.PI * 2, false);
    // Base line
    g.moveTo(b.cx - r, b.cy);
    g.lineTo(b.x, b.cy);
    // Emitter line (with arrow pointing IN)
    g.moveTo(b.cx - r * 0.3, b.cy - r * 0.6);
    g.lineTo(b.cx + r * 0.6, b.y);
    // Collector line
    g.moveTo(b.cx - r * 0.3, b.cy + r * 0.6);
    g.lineTo(b.cx + r * 0.6, b.y + b.h);
    // Arrow on emitter (pointing toward base)
    g.lineStyle(0.15, SCH_COLORS.symbolLine, 1);
    const arrowX = b.cx - r * 0.1;
    const arrowY = b.cy - r * 0.4;
    g.moveTo(arrowX + 0.15, arrowY + 0.1);
    g.lineTo(arrowX, arrowY - 0.05);
    g.lineTo(arrowX - 0.1, arrowY + 0.15);
}

function drawBatteryAnsi(g, comp, rectOp) {
    const b = _getBodyBounds(comp, rectOp);
    g.lineStyle(0.2032, SCH_COLORS.symbolLine, 1);
    // Long line (positive)
    const longW = b.w * 0.5;
    g.moveTo(b.cx - longW / 2, b.cy - b.h * 0.15);
    g.lineTo(b.cx + longW / 2, b.cy - b.h * 0.15);
    // Short line (negative)
    const shortW = b.w * 0.25;
    g.moveTo(b.cx - shortW / 2, b.cy + b.h * 0.15);
    g.lineTo(b.cx + shortW / 2, b.cy + b.h * 0.15);
    // Plus sign
    g.lineStyle(0.12, SCH_COLORS.symbolLine, 0.8);
    g.moveTo(b.cx - 0.12, b.cy - b.h * 0.35);
    g.lineTo(b.cx + 0.12, b.cy - b.h * 0.35);
    g.moveTo(b.cx, b.cy - b.h * 0.35 - 0.12);
    g.lineTo(b.cx, b.cy - b.h * 0.35 + 0.12);
}

function drawSwitchAnsi(g, comp, rectOp) {
    const b = _getBodyBounds(comp, rectOp);
    g.lineStyle(0.2032, SCH_COLORS.symbolLine, 1);
    // Left terminal
    g.moveTo(b.x, b.cy);
    g.lineTo(b.cx - b.w * 0.2, b.cy);
    // Right terminal
    g.moveTo(b.cx + b.w * 0.2, b.cy);
    g.lineTo(b.x + b.w, b.cy);
    // Lever
    g.moveTo(b.cx - b.w * 0.2, b.cy);
    g.lineTo(b.cx + b.w * 0.15, b.cy - b.h * 0.35);
    // Circles at contact points
    g.lineStyle(0.12, SCH_COLORS.symbolLine, 1);
    g.arc(b.cx - b.w * 0.2, b.cy, 0.08, 0, Math.PI * 2, false);
    g.arc(b.cx + b.w * 0.2, b.cy, 0.08, 0, Math.PI * 2, false);
}

function drawOpampAnsi(g, comp, rectOp) {
    const b = _getBodyBounds(comp, rectOp);
    g.lineStyle(0.2032, SCH_COLORS.symbolLine, 1);
    // Triangle body
    g.moveTo(b.x, b.y);
    g.lineTo(b.x + b.w, b.cy);
    g.lineTo(b.x, b.y + b.h);
    g.lineTo(b.x, b.y);
    // Plus sign (non-inverting input)
    g.lineStyle(0.12, SCH_COLORS.symbolLine, 0.8);
    const plusY = b.cy + b.h * 0.25;
    g.moveTo(b.x + b.w * 0.15, plusY);
    g.lineTo(b.x + b.w * 0.3, plusY);
    g.moveTo(b.x + b.w * 0.225, plusY - 0.1);
    g.lineTo(b.x + b.w * 0.225, plusY + 0.1);
    // Minus sign (inverting input)
    const minusY = b.cy - b.h * 0.25;
    g.moveTo(b.x + b.w * 0.15, minusY);
    g.lineTo(b.x + b.w * 0.3, minusY);
}

class SchematicRenderer {
    constructor(containerId, callbacks) {
        this._callbacks = callbacks || {};
        this._schematic = null;
        this._zoom = 1;
        this._contentCenter = { x: 0, y: 0 };
        this._panOffset = { x: 0, y: 0 };
        this._isPanning = false;
        this._panStart = { x: 0, y: 0 };
        this._panOffsetStart = { x: 0, y: 0 };
        this._selectedComp = null;
        this._selectedWire = null;
        this._selectedImageMarker = null;
        this._hoveredImageMarker = null;
        this._pinHitTargets = [];
        this._netLabelHitTargets = [];
        this._imageMarkerHitTargets = [];
        this._wireDraft = null;
        this._hoverPin = null;
        this._activePin = null;
        this._hoverNetLabel = null;
        this._activeNetLabel = null;
        this._showWires = true;
        this._dpr = window.devicePixelRatio || 1;
        this._dragCompRef = null;
        this._dragDelta = { dx: 0, dy: 0 };
        this._interactionAbort = new AbortController();
        this._gridDirty = true;
        this._gridRaf = 0;
        this._overlayRaf = null;

        const container = document.getElementById(containerId);
        const parent = container.parentElement;

        this._app = new PIXI.Application({
            resizeTo: parent,
            backgroundColor: SCH_COLORS.bg,
            antialias: true,
            resolution: this._dpr,
            autoDensity: true,
        });

        container.appendChild(this._app.view);
        this._canvas = this._app.view;
        // Ensure the canvas fills the viewport even if Pixi init races layout
        this._canvas.style.width = '100%';
        this._canvas.style.height = '100%';
        this._canvas.style.display = 'block';
        this._canvas.style.touchAction = 'none';

        // Layer containers
        this._gridLayer = new PIXI.Container();
        this._wireLayer = new PIXI.Container();
        this._symbolLayer = new PIXI.Container();
        this._imageMarkerLayer = new PIXI.Container();
        this._pinLayer = new PIXI.Container();
        this._textLayer = new PIXI.Container();
        this._overlayLayer = new PIXI.Container();

        // World container flips Y axis (KiCad Y-up → PixiJS Y-down)
        this._world = new PIXI.Container();
        this._world.addChild(
            this._gridLayer,
            this._wireLayer,
            this._symbolLayer,
            this._imageMarkerLayer,
            this._pinLayer,
            this._textLayer,
            this._overlayLayer
        );
        this._app.stage.addChild(this._world);

        this._setupInteraction();
        this._symbolStyle = { standard: SymbolStandard.ANSI };

        // ResizeObserver keeps the Pixi view sized when side panels collapse
        if (typeof ResizeObserver !== 'undefined' && parent) {
            this._resizeObserver = new ResizeObserver(() => {
                try {
                    if (this._app && this._app.renderer && parent.clientWidth > 0) {
                        this._app.renderer.resize(parent.clientWidth, parent.clientHeight);
                        this._scheduleGridRedraw();
                    }
                } catch (_) {}
            });
            this._resizeObserver.observe(parent);
        }
    }

    get canvas() { return this._canvas; }
    get zoom() { return this._zoom; }

    // ── Load schematic ──────────────────────────────────────────────────────

    load(schematic) {
        this._schematic = schematic;
        this._selectedComp = null;
        this._fullRedraw();
        this.zoomToFit();
    }

    destroy() {
        this._schematic = null;
        this._interactionAbort.abort();
        if (this._gridRaf) {
            cancelAnimationFrame(this._gridRaf);
            this._gridRaf = 0;
        }
        if (this._overlayRaf) {
            cancelAnimationFrame(this._overlayRaf);
            this._overlayRaf = null;
        }
        if (this._resizeObserver) {
            try { this._resizeObserver.disconnect(); } catch (_) {}
            this._resizeObserver = null;
        }
        this._clearLayer(this._gridLayer);
        this._clearLayer(this._wireLayer);
        this._clearLayer(this._symbolLayer);
        this._clearLayer(this._pinLayer);
        this._clearLayer(this._textLayer);
        this._clearLayer(this._overlayLayer);
        if (this._canvas && this._canvas.parentNode) {
            this._canvas.parentNode.removeChild(this._canvas);
        }
        this._app.destroy(true, { children: true });
    }

    setSymbolStandard(standard) {
        if (!Object.values(SymbolStandard).includes(standard)) return;
        this._symbolStyle.standard = standard;
        this.refresh();
    }

    // ── Full redraw ──────────────────────────────────────────────────────────

    _fullRedraw() {
        this._clearLayer(this._gridLayer);
        this._clearLayer(this._wireLayer);
        this._clearLayer(this._symbolLayer);
        this._clearLayer(this._imageMarkerLayer);
        this._clearLayer(this._pinLayer);
        this._clearLayer(this._textLayer);
        this._clearLayer(this._overlayLayer);
        this._pinHitTargets = [];
        this._imageMarkerHitTargets = [];
        this._wireDraft = null;
        this._hoverPin = null;

        if (!this._schematic) return;

        this._renderGrid();
        this._renderWires();
        this._renderJunctions();
        this._renderPowerLabels();
        this._renderImageMarkers();

        const globalPinNames = [];
        for (const comp of this._schematic.components) {
            this._renderComponent(comp, globalPinNames);
        }

        // Net labels need _pinHitTargets (populated by _renderComponent)
        this._renderNetLabels();
        this._renderTerminals();
        this._renderSelection();
    }

    _renderComponent(comp, globalPinNames) {
        // Component x/y are already updated live during drag; only wire ends use _dragDelta.
        const ox = typeof comp.x === 'number' ? comp.x : 0;
        const oy = typeof comp.y === 'number' ? comp.y : 0;
        // Prefer explicit lib_id; fall back to id / name for older payloads
        const libKey = comp.lib_id || comp.id || comp.name || '';
        const override = SymbolStyleOverrides[libKey];

        if (override && comp._bodyRect === undefined) {
            comp._bodyRect = detectBodyRect(comp);
            // Fallback: if no body rect detected, use the first rectangle
            if (!comp._bodyRect) {
                comp._bodyRect = (comp.ops || []).find(op => op[0] === 'rectangle') || null;
            }
        }

        // If ANSI override is active, find the body rect and draw ANSI symbol first
        if (override && this._symbolStyle.standard === SymbolStandard.ANSI) {
            const renderer = SymbolStyleRenderers[override.kind];
            if (renderer) {
                // Find a body rectangle to use for bounds
                const bodyRect = comp._bodyRect || (comp.ops || []).find(op => op[0] === 'rectangle');
                if (bodyRect) {
                    const g = new PIXI.Graphics();
                    renderer(g, comp, bodyRect);
                    this._symbolLayer.addChild(g);
                }
            }
        }

        for (const op of (comp.ops || [])) {
            const type = op[0];

            if (type === 'rectangle') {
                // Skip body rectangles when ANSI override is active
                // (the ANSI renderer already drew the correct shape)
                if (override && this._symbolStyle.standard === SymbolStandard.ANSI) {
                    continue;
                }
                const g = new PIXI.Graphics();
                this._drawOpShape(g, op, ox, oy);
                this._symbolLayer.addChild(g);
            } else if (type === 'polyline' || type === 'circle' || type === 'arc') {
                const g = new PIXI.Graphics();
                this._drawOpShape(g, op, ox, oy);
                this._symbolLayer.addChild(g);
            } else if (type === 'pin') {
                this._drawPin(op, ox, oy, globalPinNames, comp);
            } else if (type === 'property' || type === 'text') {
                this._drawText(op, ox, oy, type);
            }
        }
    }

    // ── Shape drawing ────────────────────────────────────────────────────────

    _drawOpShape(g, op, ox, oy) {
        const type = op[0];

        const stroke = getAttr(op, 'stroke');
        const fill = getAttr(op, 'fill');
        let lineWidth = 0.254;
        if (stroke) {
            const w = getAttr(stroke, 'width');
            if (w) {
                const parsed = parseFloat(w[1]);
                if (!isNaN(parsed) && parsed > 0) lineWidth = parsed;
            }
        }
        // Floor very thin strokes so symbols stay visible when zoomed out
        lineWidth = Math.max(lineWidth, 0.12);

        let fillColor = null;
        let fillAlpha = 0;
        const fillType = resolveFillType(fill);
        if (fillType === 'background') {
            fillColor = SCH_COLORS.symbolFill;
            fillAlpha = 0.55;
        } else if (fillType === 'solid') {
            fillColor = SCH_COLORS.symbolLine;
            fillAlpha = 1;
        }

        g.lineStyle(lineWidth, SCH_COLORS.symbolLine, 1);

        if (type === 'rectangle') {
            const start = getAttr(op, 'start');
            const end = getAttr(op, 'end');
            if (start && end) {
                const x1 = parseFloat(start[1]) + ox, y1 = parseFloat(start[2]) + oy;
                const x2 = parseFloat(end[1]) + ox, y2 = parseFloat(end[2]) + oy;
                const rx = Math.min(x1, x2), ry = Math.min(y1, y2);
                const rw = Math.abs(x2 - x1), rh = Math.abs(y2 - y1);
                if (fillAlpha > 0) {
                    g.beginFill(fillColor, fillAlpha);
                    g.drawRect(rx, ry, rw, rh);
                    g.endFill();
                } else {
                    g.drawRect(rx, ry, rw, rh);
                }
            }
        } else if (type === 'polyline') {
            const pts = getAttr(op, 'pts');
            if (pts) {
                if (fillAlpha > 0) g.beginFill(fillColor, fillAlpha);
                g.moveTo(0, 0);
                let started = false;
                for (let i = 1; i < pts.length; i++) {
                    if (pts[i][0] === 'xy') {
                        const x = parseFloat(pts[i][1]) + ox, y = parseFloat(pts[i][2]) + oy;
                        if (!started) { g.moveTo(x, y); started = true; }
                        else g.lineTo(x, y);
                    }
                }
                if (fillAlpha > 0) g.endFill();
            }
        } else if (type === 'circle') {
            const center = getAttr(op, 'center');
            const rad = getAttr(op, 'radius');
            if (center && rad) {
                const cx = parseFloat(center[1]) + ox, cy = parseFloat(center[2]) + oy;
                const r = parseFloat(rad[1]);
                if (fillAlpha > 0) {
                    g.beginFill(fillColor, fillAlpha);
                    g.drawCircle(cx, cy, r);
                    g.endFill();
                } else {
                    g.drawCircle(cx, cy, r);
                }
            }
        } else if (type === 'arc') {
            const start = getAttr(op, 'start');
            const end = getAttr(op, 'end');
            const mid = getAttr(op, 'mid');
            if (start && end && mid) {
                const sx = parseFloat(start[1]) + ox, sy = parseFloat(start[2]) + oy;
                const mx = parseFloat(mid[1]) + ox, my = parseFloat(mid[2]) + oy;
                const ex = parseFloat(end[1]) + ox, ey = parseFloat(end[2]) + oy;
                // Quadratic bezier via moveTo + lineTo approximation
                const steps = 20;
                g.moveTo(sx, sy);
                for (let t = 1; t <= steps; t++) {
                    const u = t / steps;
                    const px = (1-u)*(1-u)*sx + 2*(1-u)*u*mx + u*u*ex;
                    const py = (1-u)*(1-u)*sy + 2*(1-u)*u*my + u*u*ey;
                    g.lineTo(px, py);
                }
            }
        }
    }

    // ── Pin drawing ──────────────────────────────────────────────────────────

    _drawPin(op, ox, oy, globalPinNames, comp) {
        const at = getAttr(op, 'at');
        const lenNode = getAttr(op, 'length');
        if (!at || !lenNode) return;

        const x = parseFloat(at[1]) + ox, y = parseFloat(at[2]) + oy;
        const len = parseFloat(lenNode[1]);
        const angDeg = parseFloat(at[3] || 0);
        const ang = angDeg * Math.PI / 180;
        const ex = x + Math.cos(ang) * len;
        const ey = y + Math.sin(ang) * len;
        const numNode = getAttr(op, 'number');
        const pinNum = numNode && numNode[1] ? String(numNode[1]).replace(/"/g, '') : '';
        if (comp && pinNum && pinNum !== '~') {
            this._pinHitTargets.push({
                key: `${comp.refDesignator}:${pinNum}`,
                refDes: comp.refDesignator,
                pinNum,
                x,
                y,
            });
        }

        // Pin stub line
        const g = new PIXI.Graphics();
        g.lineStyle(0.254, SCH_COLORS.pinLine, 1);
        g.moveTo(x, y);
        g.lineTo(ex, ey);
        this._pinLayer.addChild(g);

        // Pin number - KiCad-style: no pin numbers displayed on pads
        // const numNode = getAttr(op, 'number');
        // if (numNode && numNode[1] !== '"~"') {
        //     const pinNum = numNode[1];
        //     const size = this._getFontSize(op);
        //     let nx = x, ny = y;
        //     let anchorX = 0.5, anchorY = 1;
        //     if (angDeg === 0) { nx = x + len / 2; ny = y + 0.3; anchorX = 0.5; anchorY = 1; }
        //     else if (angDeg === 180) { nx = x - len / 2; ny = y + 0.3; anchorX = 0.5; anchorY = 1; }
        //     else if (angDeg === 90) { nx = x - 0.3; ny = y + len / 2; anchorX = 1; anchorY = 0.5; }
        //     else if (angDeg === 270) { nx = x - 0.3; ny = y - len / 2; anchorX = 1; anchorY = 0.5; }

        //     const FONT_RES = 24;
        //     const txt = new PIXI.Text(pinNum, {
        //         fontFamily: '"JetBrains Mono", "Fira Code", monospace',
        //         fontSize: FONT_RES,
        //         fill: SCH_COLORS.pinNum,
        //     });
        //     const scaleRatio = size / FONT_RES;
        //     txt.scale.set(scaleRatio, -scaleRatio);
        //     txt.anchor.set(anchorX, anchorY);
        //     txt.x = nx;
        //     txt.y = ny;
        //     this._textLayer.addChild(txt);
        // }

        // Pin name (with dedup). Skip KiCad "~" / empty placeholders.
        const nameNode = getAttr(op, 'name');
        const nameText = nameNode ? _sexprStr(nameNode[1]) : '';
        if (nameNode && !_isHiddenSexprText(nameText)) {
            let shouldRender = true;
            for (const prev of globalPinNames) {
                if (prev.name === nameText && Math.abs(ex - prev.x) < 3.0 && Math.abs(ey - prev.y) < 3.0) {
                    shouldRender = false;
                    break;
                }
            }

            if (shouldRender) {
                const size = this._getFontSize(nameNode);
                let nx = ex, ny = ey;
                let anchorX = 0, anchorY = 0.5;
                if (angDeg === 0) { nx += 0.5; anchorX = 0; anchorY = 0.5; }
                else if (angDeg === 180) { nx -= 0.5; anchorX = 1; anchorY = 0.5; }
                else if (angDeg === 90 || angDeg === 270) { nx -= 0.8; anchorX = 0.5; anchorY = 0.5; }

                const FONT_RES = 24;
                const txt = new PIXI.Text(nameText, {
                    fontFamily: '"IBM Plex Mono", "JetBrains Mono", "Fira Code", monospace',
                    fontSize: FONT_RES,
                    fill: SCH_COLORS.pinName,
                });
                const scaleRatio = size / FONT_RES;
                txt.scale.set(scaleRatio, -scaleRatio);
                txt.anchor.set(anchorX, anchorY);

                if (angDeg === 90 || angDeg === 270) {
                    txt.rotation = -Math.PI / 2;
                }

                txt.x = nx;
                txt.y = ny;
                this._textLayer.addChild(txt);
                globalPinNames.push({ name: nameText, x: ex, y: ey });
            }
        }
    }

    // ── Text drawing ─────────────────────────────────────────────────────────

    _drawText(op, ox, oy, type) {
        const at = getAttr(op, 'at');
        if (!at || isSexprHidden(op)) return;

        const propName = type === 'property' ? _sexprStr(op[1]) : '';
        const rawTxt = type === 'property' ? op[2] : op[1];
        const txt = _sexprStr(rawTxt);
        if (_isHiddenSexprText(txt)) return;

        // Only show Reference / Value on the schematic canvas (KiCad default)
        if (type === 'property' && propName !== 'Reference' && propName !== 'Value') {
            return;
        }

        const x = parseFloat(at[1]) + ox;
        const y = parseFloat(at[2]) + oy;
        const ang = parseFloat(at[3] || 0);
        const size = this._getFontSize(op);

        let fillColor = SCH_COLORS.propertyVal;
        if (propName === 'Reference') fillColor = SCH_COLORS.propertyRef;
        if (type === 'text') fillColor = SCH_COLORS.text;

        const FONT_RES = 24;
        const pixiTxt = new PIXI.Text(txt, {
            fontFamily: '"IBM Plex Mono", "JetBrains Mono", "Fira Code", monospace',
            fontSize: FONT_RES,
            fill: fillColor,
        });
        const scaleRatio = size / FONT_RES;
        pixiTxt.scale.set(scaleRatio, -scaleRatio);
        pixiTxt.anchor.set(0.5, 0.5);
        pixiTxt.x = x;
        pixiTxt.y = y;

        if (ang !== 0) pixiTxt.rotation = -ang * Math.PI / 180;

        this._textLayer.addChild(pixiTxt);
    }

    _getFontSize(op) {
        let size = 1.27;
        const effects = getAttr(op, 'effects');
        if (effects) {
            const font = getAttr(effects, 'font');
            if (font) {
                const s = getAttr(font, 'size');
                if (s) {
                    const val = parseFloat(s[1]);
                    if (!isNaN(val)) {
                        size = val;
                    }
                }
            }
        }
        return size;
    }

    // ── Grid rendering ───────────────────────────────────────────────────────

    _scheduleGridRedraw() {
        this._gridDirty = true;
        if (this._gridRaf) return;
        this._gridRaf = requestAnimationFrame(() => {
            this._gridRaf = 0;
            if (!this._gridDirty) return;
            this._gridDirty = false;
            this._clearLayer(this._gridLayer);
            this._renderGrid();
        });
    }

    _renderGrid() {
        const g = new PIXI.Graphics();
        const bounds = this._getVisibleWorldBounds();
        if (!bounds) return;

        const gridMm = GRID_SIZE;
        const majorGrid = gridMm * 10;

        const startX = Math.floor(bounds.minX / gridMm) * gridMm;
        const endX = Math.ceil(bounds.maxX / gridMm) * gridMm;
        const startY = Math.floor(bounds.minY / gridMm) * gridMm;
        const endY = Math.ceil(bounds.maxY / gridMm) * gridMm;

        // Cap density so zoomed-out pans stay cheap
        const spanX = Math.max(1, Math.ceil((endX - startX) / gridMm));
        const spanY = Math.max(1, Math.ceil((endY - startY) / gridMm));
        const maxDots = 12000;
        const step = Math.max(1, Math.ceil(Math.sqrt((spanX * spanY) / maxDots)));

        // Dot grid — only when reasonably zoomed in
        if (this._zoom > 0.12) {
            const dotRadius = Math.max(0.05, 0.9 / Math.max(this._zoom, 0.01));
            g.beginFill(SCH_COLORS.grid, 0.45);
            for (let x = startX; x <= endX; x += gridMm * step) {
                for (let y = startY; y <= endY; y += gridMm * step) {
                    g.drawCircle(x, y, Math.min(dotRadius, 0.2));
                }
            }
            g.endFill();
        }

        // Major grid lines
        const majorStartX = Math.floor(bounds.minX / majorGrid) * majorGrid;
        const majorEndX = Math.ceil(bounds.maxX / majorGrid) * majorGrid;
        const majorStartY = Math.floor(bounds.minY / majorGrid) * majorGrid;
        const majorEndY = Math.ceil(bounds.maxY / majorGrid) * majorGrid;

        const majorW = Math.max(0.04, 0.6 / Math.max(this._zoom, 0.01));
        g.lineStyle(Math.min(majorW, 0.25), SCH_COLORS.gridMajor, 0.28);
        for (let x = majorStartX; x <= majorEndX; x += majorGrid) {
            g.moveTo(x, bounds.minY);
            g.lineTo(x, bounds.maxY);
        }
        for (let y = majorStartY; y <= majorEndY; y += majorGrid) {
            g.moveTo(bounds.minX, y);
            g.lineTo(bounds.maxX, y);
        }

        this._gridLayer.addChild(g);
    }

    _getVisibleWorldBounds() {
        const w = this._app.screen.width;
        const h = this._app.screen.height;
        const tl = this.screenToWorld(0, 0);
        const br = this.screenToWorld(w, h);
        if (!tl || !br) return null;

        const margin = 20;
        return {
            minX: Math.min(tl.x, br.x) - margin,
            maxX: Math.max(tl.x, br.x) + margin,
            minY: Math.min(tl.y, br.y) - margin,
            maxY: Math.max(tl.y, br.y) + margin,
        };
    }

    // ── Wire rendering ───────────────────────────────────────────────────────

    _renderWires() {
        if (!this._schematic || !this._schematic.wirePaths) return;
        if (!this._showWires) return;

        const g = new PIXI.Graphics();
        g.lineStyle(0.254, SCH_COLORS.wire, 1);

        for (const wire of this._schematic.wirePaths) {
            if (!wire.path || wire.path.length < 2) continue;
            const srcRef = (wire.source || '').split(':')[0];
            const tgtRef = (wire.target || '').split(':')[0];
            const srcOff = srcRef === this._dragCompRef ? this._dragDelta : { dx: 0, dy: 0 };
            const tgtOff = tgtRef === this._dragCompRef ? this._dragDelta : { dx: 0, dy: 0 };
            const path = wire.path;
            const n = path.length;

            g.moveTo(path[0].x + srcOff.dx, path[0].y + srcOff.dy);
            for (let i = 1; i < n; i++) {
                const dx = Math.abs(path[i].x - path[i - 1].x);
                const dy = Math.abs(path[i].y - path[i - 1].y);
                if (dx > 0.001 && dy > 0.001) continue;
                let off = { dx: 0, dy: 0 };
                if (i < 2 && srcRef === this._dragCompRef) off = srcOff;
                else if (i >= n - 2 && tgtRef === this._dragCompRef) off = tgtOff;
                g.lineTo(path[i].x + off.dx, path[i].y + off.dy);
            }
        }

        this._wireLayer.addChild(g);
    }

    _renderJunctions() {
        if (!this._schematic || !this._schematic.junctionPoints) return;

        const g = new PIXI.Graphics();
        g.beginFill(SCH_COLORS.junction, 1);
        for (const j of this._schematic.junctionPoints) {
            g.drawCircle(j.x, j.y, 0.5);
        }
        g.endFill();
        this._wireLayer.addChild(g);
    }

    _renderPowerLabels() {
        if (!this._schematic || !this._schematic.powerLabels) return;

        const STUB = 2.54;
        for (const lbl of this._schematic.powerLabels) {
            const dir = lbl.dir || 'right';
            const dx = dir === 'right' ? 1 : dir === 'left' ? -1 : 0;
            const dy = dir === 'up' ? 1 : dir === 'down' ? -1 : 0;
            const ex = lbl.x + dx * STUB;
            const ey = lbl.y + dy * STUB;
            const isGnd = lbl.net === 'GND';

            const g = new PIXI.Graphics();
            const color = isGnd ? SCH_COLORS.powerGnd : SCH_COLORS.powerVcc;
            g.lineStyle(0.254, color, 1);

            // Stub line from pin to symbol
            g.moveTo(lbl.x, lbl.y);
            g.lineTo(ex, ey);

            if (isGnd) {
                // GND: three shrinking bars
                const px = -dy, py = dx;
                for (let i = 0; i < 3; i++) {
                    const w = 1.27 - i * 0.42;
                    const ox = ex + dx * i * 0.64;
                    const oy = ey + dy * i * 0.64;
                    g.moveTo(ox - px * w, oy - py * w);
                    g.lineTo(ox + px * w, oy + py * w);
                }
            } else {
                // Power: bar + net label
                const px = -dy, py = dx;
                g.moveTo(ex - px * 1.27, ey - py * 1.27);
                g.lineTo(ex + px * 1.27, ey + py * 1.27);

                // Net name label
                const FONT_RES = 24;
                const size = 1.6;
                const txt = new PIXI.Text(lbl.net, {
                    fontFamily: '"IBM Plex Mono", "JetBrains Mono", "Fira Code", monospace',
                    fontSize: FONT_RES,
                    fill: SCH_COLORS.powerVcc,
                });
                const scaleRatio = size / FONT_RES;
                txt.scale.set(scaleRatio, -scaleRatio);
                txt.anchor.set(dir === 'left' ? 1 : 0, 0.5);
                txt.x = ex + dx * 0.8;
                txt.y = ey + dy * 0.8;
                this._textLayer.addChild(txt);
            }

            this._wireLayer.addChild(g);
        }
    }

    // ── Net Label rendering ──────────────────────────────────────────────

    _renderNetLabels() {
        if (!this._schematic || !this._schematic.netLabels) return;

        this._netLabelHitTargets = [];
        const g = new PIXI.Graphics();
        const LABEL_SIZE = 1.2;
        const FLAG_WIDTH = 2.5; // Width of the flag background
        const FLAG_HEIGHT = 1.5; // Height of the flag background
        const STUB = 1.27;

        for (const lbl of this._schematic.netLabels) {
            const color = hexToPixi(netColor(lbl.net));

            let sx = lbl.x, sy = lbl.y;
            if (lbl.pin) {
                const pinTarget = this._pinHitTargets.find(p => p.key === lbl.pin);
                if (pinTarget) {
                    sx = pinTarget.x;
                    sy = pinTarget.y;
                    lbl.x = sx;
                    lbl.y = sy;
                }
            }

            // Stub line from connection point outward
            const angRad = lbl.orientation * Math.PI / 180;
            const dx = Math.cos(angRad);
            const dy = Math.sin(angRad);
            const ex = sx + dx * STUB;
            const ey = sy + dy * STUB;

            // Draw stub line
            g.lineStyle(0.2, color, 1);
            g.moveTo(sx, sy);
            g.lineTo(ex, ey);

            // Draw flag-style net label (like KiCad/Flux)
            // Flag is a small rectangle with the net name inside
            const flagX = ex;
            const flagY = ey;

            // Calculate flag corners based on orientation
            let corners;
            if (lbl.orientation === 0) {
                // Right-facing flag
                corners = [
                    { x: flagX, y: flagY - FLAG_HEIGHT / 2 },
                    { x: flagX + FLAG_WIDTH, y: flagY - FLAG_HEIGHT / 2 },
                    { x: flagX + FLAG_WIDTH, y: flagY + FLAG_HEIGHT / 2 },
                    { x: flagX, y: flagY + FLAG_HEIGHT / 2 },
                ];
            } else if (lbl.orientation === 180) {
                // Left-facing flag
                corners = [
                    { x: flagX, y: flagY - FLAG_HEIGHT / 2 },
                    { x: flagX - FLAG_WIDTH, y: flagY - FLAG_HEIGHT / 2 },
                    { x: flagX - FLAG_WIDTH, y: flagY + FLAG_HEIGHT / 2 },
                    { x: flagX, y: flagY + FLAG_HEIGHT / 2 },
                ];
            } else if (lbl.orientation === 90) {
                // Up-facing flag
                corners = [
                    { x: flagX - FLAG_HEIGHT / 2, y: flagY },
                    { x: flagX + FLAG_HEIGHT / 2, y: flagY },
                    { x: flagX + FLAG_HEIGHT / 2, y: flagY + FLAG_WIDTH },
                    { x: flagX - FLAG_HEIGHT / 2, y: flagY + FLAG_WIDTH },
                ];
            } else {
                // Down-facing flag (270 degrees)
                corners = [
                    { x: flagX - FLAG_HEIGHT / 2, y: flagY },
                    { x: flagX + FLAG_HEIGHT / 2, y: flagY },
                    { x: flagX + FLAG_HEIGHT / 2, y: flagY - FLAG_WIDTH },
                    { x: flagX - FLAG_HEIGHT / 2, y: flagY - FLAG_WIDTH },
                ];
            }

            // Draw flag background
            g.lineStyle(0.15, color, 1);
            g.beginFill(color, 0.15);
            g.moveTo(corners[0].x, corners[0].y);
            for (let i = 1; i < corners.length; i++) {
                g.lineTo(corners[i].x, corners[i].y);
            }
            g.lineTo(corners[0].x, corners[0].y);
            g.endFill();

            // Draw connection dot at stub end
            g.lineStyle(0, 0, 0);
            g.beginFill(color, 1);
            g.drawCircle(ex, ey, 0.25);
            g.endFill();

            this._wireLayer.addChild(g);

            // Net name text inside the flag
            const FONT_RES = 24;
            const txt = new PIXI.Text(lbl.net, {
                fontFamily: '"IBM Plex Mono", "JetBrains Mono", "Fira Code", monospace',
                fontSize: FONT_RES,
                fill: 0xffffff, // White text on colored background
                fontWeight: 'bold',
            });
            const scaleRatio = (LABEL_SIZE * 0.9) / FONT_RES;
            txt.scale.set(scaleRatio, -scaleRatio);

            // Position text in center of flag
            let anchorX = 0.5, anchorY = 0.5;
            let tx = flagX + FLAG_WIDTH / 2;
            let ty = flagY;

            if (lbl.orientation === 180) {
                tx = flagX - FLAG_WIDTH / 2;
            } else if (lbl.orientation === 90) {
                tx = flagX;
                ty = flagY + FLAG_WIDTH / 2;
                txt.rotation = -Math.PI / 2;
            } else if (lbl.orientation === 270) {
                tx = flagX;
                ty = flagY - FLAG_WIDTH / 2;
                txt.rotation = Math.PI / 2;
            }

            txt.anchor.set(anchorX, anchorY);
            txt.x = tx;
            txt.y = ty;
            this._textLayer.addChild(txt);

            // Hit target (at the flag center)
            this._netLabelHitTargets.push({
                id: lbl.id,
                net: lbl.net,
                x: flagX + FLAG_WIDTH / 2,
                y: flagY,
                label: lbl,
            });
        }
    }

    // ── Image Markers ──────────────────────────────────────────────────────

    _renderImageMarkers() {
        if (!this._schematic || !this._schematic.imageMarkers) return;
        for (const marker of this._schematic.imageMarkers) {
            if (!marker.imageDataUrl) continue;
            const isSelected = this._selectedImageMarker && this._selectedImageMarker.id === marker.id;
            const isHovered = this._hoveredImageMarker && this._hoveredImageMarker.id === marker.id;
            const isRevealed = !!marker._imageRevealed;
            const container = new PIXI.Container();
            container.position.set(marker.x, -marker.y);

            // --- Image (only when revealed, hangs below the pin) ---
            if (isRevealed) {
                let imgW = marker.width * marker.scale;
                let imgH = marker.height * marker.scale;
                try {
                    const texture = PIXI.Texture.from(marker.imageDataUrl);
                    const sprite = new PIXI.Sprite(texture);
                    sprite.anchor.set(0.5, 0);
                    sprite.width = imgW;
                    sprite.height = imgH;
                    sprite.rotation = marker.rotation;
                    sprite.position.set(0, 5);
                    container.addChild(sprite);

                    // Background card
                    const bg = new PIXI.Graphics();
                    bg.beginFill(0x0f172a, 0.92);
                    bg.lineStyle(0.2, 0x3dff9a, 0.25);
                    bg.drawRoundedRect(-imgW / 2 - 1, 4.5, imgW + 2, imgH + 2, 0.8);
                    bg.endFill();
                    container.addChildAt(bg, 0);

                    if (isSelected) {
                        const outline = new PIXI.Graphics();
                        outline.lineStyle(0.3, SCH_COLORS.selection, 0.9);
                        outline.drawRoundedRect(-imgW / 2 - 1.5, 4, imgW + 3, imgH + 3, 1);
                        container.addChild(outline);
                    }

                    // Leader line
                    const leader = new PIXI.Graphics();
                    leader.lineStyle(0.12, 0x3dff9a, 0.35);
                    leader.moveTo(0, 4);
                    leader.lineTo(0, 4.7);
                    container.addChild(leader);

                    // ── Image Card Toolbar ──────────────────────────────
                    if (this._zoom > 0.8) {
                        const tbScale = Math.min(1, Math.max(0.5, this._zoom / 2));
                        const tbY = 5 + imgH + 3.5;
                        const toolBar = new PIXI.Container();
                        toolBar.position.set(0, tbY);

                        const btnSize = 1.8;
                        const btnGap = 0.5;
                        const nBtns = 3;
                        const tbW = nBtns * btnSize + (nBtns - 1) * btnGap;
                        const tBg = new PIXI.Graphics();
                        tBg.beginFill(0x0f172a, 0.88);
                        tBg.lineStyle(0.15, 0x3dff9a, 0.2);
                        tBg.drawRoundedRect(-tbW / 2 - 0.5, -btnSize / 2 - 0.3, tbW + 1, btnSize + 0.6, 0.6);
                        tBg.endFill();
                        toolBar.addChild(tBg);

                        for (let i = 0; i < nBtns; i++) {
                            const bx = -tbW / 2 + i * (btnSize + btnGap) + btnSize / 2;
                            const btn = new PIXI.Graphics();
                            btn.beginFill(0x1e293b, 0.9);
                            btn.lineStyle(0.1, 0x3dff9a, 0.15);
                            btn.drawRoundedRect(-btnSize / 2, -btnSize / 2, btnSize, btnSize, 0.4);
                            btn.endFill();
                            btn.position.set(bx, 0);
                            btn.eventMode = 'static';
                            btn.cursor = 'pointer';
                            const action = i === 0 ? 'inspect' : i === 1 ? 'toggle' : 'delete';
                            btn._markerAction = action;
                            btn.on('pointerdown', (ev) => {
                                ev.stopPropagation();
                                if (this._callbacks.onImageMarkerToolbarAction) {
                                    this._callbacks.onImageMarkerToolbarAction(marker, action);
                                }
                            });
                            toolBar.addChild(btn);

                            // Icon label
                            try {
                                const iconLabels = { inspect: '\u{1F50D}', toggle: '\u{1F441}', delete: '\u{1F5D1}' };
                                const icon = new PIXI.Text(iconLabels[action] || '', {
                                    fontSize: 9,
                                    fill: 0x94a3b8,
                                    fontFamily: 'system-ui, sans-serif',
                                });
                                icon.anchor.set(0.5);
                                icon.position.set(bx, 0);
                                toolBar.addChild(icon);
                            } catch (err) { /* ignore */ }
                        }
                        container.addChild(toolBar);
                    }
                } catch (err) {
                    console.warn('ImageMarker sprite render failed:', err);
                }
            }

            // --- SVG-based map pin (crisp at any zoom) ---
            const pinTex = this._getMarkerPinTexture(isSelected, isRevealed, isHovered);
            const pinSprite = new PIXI.Sprite(pinTex);
            pinSprite.anchor.set(0.5, 1);
            const pinScale = 4 / 80;
            pinSprite.scale.set(pinScale, pinScale);
            pinSprite.position.set(0, 0);

            // Hover glow ring (behind pin)
            if (isHovered) {
                const glow = new PIXI.Graphics();
                glow.beginFill(0x3dff9a, 0.06);
                glow.drawCircle(0, -3.2, 4.5);
                glow.endFill();
                glow.beginFill(0x3dff9a, 0.03);
                glow.drawCircle(0, -3.2, 6);
                glow.endFill();
                container.addChildAt(glow, 0);
            }

            container.addChild(pinSprite);

            // --- Numbered badge ---
            const badgeR = 1.2;
            const badgeX = 2.0;
            const badgeY = -4.5;
            const badge = new PIXI.Graphics();
            badge.beginFill(isSelected ? 0x3dff9a : (isHovered ? 0xf59e0b : 0x0f172a), 0.95);
            badge.lineStyle(0.2, isSelected ? 0x3dff9a : (isHovered ? 0x3dff9a : 0xf59e0b), 0.8);
            badge.drawCircle(badgeX, badgeY, badgeR);
            badge.endFill();
            container.addChild(badge);

            try {
                const badgeText = new PIXI.Text(String(marker.markerNumber), {
                    fontSize: 14,
                    fill: isSelected ? 0x0f172a : (isHovered ? 0x3dff9a : 0xf59e0b),
                    fontFamily: 'system-ui, -apple-system, sans-serif',
                    fontWeight: '700',
                });
                badgeText.anchor.set(0.5);
                badgeText.position.set(badgeX, badgeY);
                container.addChild(badgeText);
            } catch (err) {
                console.warn('ImageMarker badge text render failed:', err);
            }

            // --- Hint text ---
            if (isHovered && !isRevealed) {
                try {
                    const hint = new PIXI.Text('Show image', {
                        fontSize: 10,
                        fill: 0x3dff9a,
                        fontFamily: 'system-ui, -apple-system, sans-serif',
                    });
                    hint.anchor.set(0.5, 0);
                    hint.position.set(0, -3.5);
                    container.addChild(hint);
                } catch (err) { /* ignore */ }
            }

            // --- Interaction (hover only; click handled in _handleClick) ---
            container.eventMode = 'static';
            container.cursor = 'pointer';

            container.on('pointerover', () => {
                this._hoveredImageMarker = marker;
                this._partialRedraw();
            });

            container.on('pointerout', () => {
                if (this._hoveredImageMarker && this._hoveredImageMarker.id === marker.id) {
                    this._hoveredImageMarker = null;
                    this._partialRedraw();
                }
            });

            this._imageMarkerLayer.addChild(container);
            this._imageMarkerHitTargets.push({ marker, container });
        }
    }

    /**
     * Generate a crisp SVG-based map pin texture, cached per color variant.
     * Renders to an offscreen canvas at 4x resolution for sharp edges.
     */
    _getMarkerPinTexture(isSelected, isRevealed, isHovered) {
        const cacheKey = `${isSelected ? 'sel' : isRevealed ? 'rev' : isHovered ? 'hov' : 'def'}`;
        if (!this._pinTextureCache) this._pinTextureCache = {};
        if (this._pinTextureCache[cacheKey]) return this._pinTextureCache[cacheKey];

        const w = 64, h = 80;
        const canvas = document.createElement('canvas');
        canvas.width = w * 4;
        canvas.height = h * 4;
        const ctx = canvas.getContext('2d');
        ctx.scale(4, 4);

        // Pin colors
        let fillColor, strokeColor, innerColor;
        if (isSelected) {
            fillColor = '#3dff9a';
            strokeColor = '#22c55e';
            innerColor = '#0f172a';
        } else if (isRevealed) {
            fillColor = '#22c55e';
            strokeColor = '#16a34a';
            innerColor = '#ffffff';
        } else if (isHovered) {
            fillColor = '#fbbf24';
            strokeColor = '#f59e0b';
            innerColor = '#1e293b';
        } else {
            fillColor = '#f59e0b';
            strokeColor = '#d97706';
            innerColor = '#ffffff';
        }

        // Drop shadow
        ctx.shadowColor = 'rgba(0,0,0,0.35)';
        ctx.shadowBlur = 6;
        ctx.shadowOffsetY = 2;

        // Teardrop pin path
        ctx.beginPath();
        ctx.moveTo(32, 76);  // tip
        ctx.bezierCurveTo(32, 76, 12, 50, 12, 32);
        ctx.bezierCurveTo(12, 14.3, 20.9, 6, 32, 6);
        ctx.bezierCurveTo(43.1, 6, 52, 14.3, 52, 32);
        ctx.bezierCurveTo(52, 50, 32, 76, 32, 76);
        ctx.closePath();
        ctx.fillStyle = fillColor;
        ctx.fill();

        // Remove shadow for inner details
        ctx.shadowColor = 'transparent';
        ctx.shadowBlur = 0;
        ctx.shadowOffsetY = 0;

        // Stroke outline
        ctx.strokeStyle = strokeColor;
        ctx.lineWidth = 1.5;
        ctx.stroke();

        // Inner white circle
        ctx.beginPath();
        ctx.arc(32, 30, 11, 0, Math.PI * 2);
        ctx.fillStyle = innerColor;
        ctx.fill();

        // Center dot
        ctx.beginPath();
        ctx.arc(32, 30, 5, 0, Math.PI * 2);
        ctx.fillStyle = fillColor;
        ctx.fill();

        // Glossy highlight on top
        const grad = ctx.createLinearGradient(20, 8, 44, 36);
        grad.addColorStop(0, 'rgba(255,255,255,0.3)');
        grad.addColorStop(1, 'rgba(255,255,255,0)');
        ctx.beginPath();
        ctx.moveTo(32, 8);
        ctx.bezierCurveTo(22, 8, 14, 16, 14, 28);
        ctx.bezierCurveTo(14, 20, 22, 12, 32, 12);
        ctx.bezierCurveTo(42, 12, 50, 20, 50, 28);
        ctx.bezierCurveTo(50, 16, 42, 8, 32, 8);
        ctx.closePath();
        ctx.fillStyle = grad;
        ctx.fill();

        const texture = PIXI.Texture.from(canvas);
        this._pinTextureCache[cacheKey] = texture;
        return texture;
    }

    hitTestImageMarker(wx, wy) {
        for (const t of this._imageMarkerHitTargets) {
            const m = t.marker;
            const isRevealed = !!m._imageRevealed;
            const pinR = 1.6;

            // Hit test pin body (circle centered above the tip)
            const bodyCy = m.y - pinR; // pin body center is pinR units ABOVE the tip in world coords
            const bodyDist = Math.sqrt((wx - m.x) ** 2 + (wy - bodyCy) ** 2);
            if (bodyDist <= pinR + 0.5) return m;

            // Hit test pin tip (small area at the location point)
            const tipDist = Math.sqrt((wx - m.x) ** 2 + (wy - m.y) ** 2);
            if (tipDist <= 1.5) return m;

            // If revealed, also hit test the image area
            if (isRevealed) {
                const hs = m.width * m.scale / 2;
                const hv = m.height * m.scale / 2;
                // Image hangs below the pin (starting at y + 4.5 in container space = y - 4.5 in world)
                if (wx >= m.x - hs && wx <= m.x + hs && wy >= m.y - 4.5 && wy <= m.y - 4.5 + hv) {
                    return m;
                }
            }
        }
        return null;
    }

    selectImageMarker(marker) {
        this._selectedImageMarker = marker;
        this._selectedComp = null;
        this._pinTextureCache = {}; // Invalidate pin textures (selection changed)
        this._partialRedraw();
        if (this._callbacks.onImageMarkerSelect) {
            this._callbacks.onImageMarkerSelect(marker);
        }
    }

    clearImageMarkerHover() {
        this._hoveredImageMarker = null;
    }

    _toggleMarkerReveal(marker) {
        marker._imageRevealed = !marker._imageRevealed;
        this._partialRedraw();
        if (this._callbacks.onImageMarkerToggle) {
            this._callbacks.onImageMarkerToggle(marker);
        }
    }

    clearImageMarkerSelection() {
        this._selectedImageMarker = null;
        this._hoveredImageMarker = null;
        this._pinTextureCache = {};
        this._partialRedraw();
    }

    // ── Selection ─────────────────────────────────────────────────────────────

    selectComponent(comp) {
        this._selectedComp = comp;
        this._selectedImageMarker = null;
        this._clearLayer(this._overlayLayer);
        this._renderSelection();
        if (this._callbacks.onSelect) {
            this._callbacks.onSelect(comp);
        }
    }

    clearSelection() {
        this._selectedComp = null;
        this._selectedImageMarker = null;
        this._clearLayer(this._overlayLayer);
        this._partialRedraw();
        if (this._callbacks.onSelect) {
            this._callbacks.onSelect(null);
        }
    }

    _renderSelection() {
        if (!this._selectedComp || !this._schematic) return;
        const comp = this._selectedComp;
        const bbox = comp.geomBBox;
        const pad = 1.0;

        const g = new PIXI.Graphics();
        g.lineStyle(0.3, SCH_COLORS.selection, 0.8);
        g.drawRect(
            comp.x + bbox.x - pad,
            comp.y + bbox.y - pad,
            bbox.w + pad * 2,
            bbox.h + pad * 2
        );
        this._overlayLayer.addChild(g);
    }

    hitTest(worldX, worldY) {
        if (!this._schematic) return null;
        for (let i = this._schematic.components.length - 1; i >= 0; i--) {
            const comp = this._schematic.components[i];
            const bbox = comp.geomBBox;
            if (worldX >= comp.x + bbox.x && worldX <= comp.x + bbox.x + bbox.w &&
                worldY >= comp.y + bbox.y && worldY <= comp.y + bbox.y + bbox.h) {
                return comp;
            }
        }
        return null;
    }

    hitTestPin(worldX, worldY) {
        if (!this._pinHitTargets || this._pinHitTargets.length === 0) return null;
        let best = null;
        let bestDist = Infinity;
        const tol = Math.max(0.8, 7 / Math.max(this._zoom, 0.01));
        for (const pin of this._pinHitTargets) {
            const dx = worldX - pin.x;
            const dy = worldY - pin.y;
            const dist = Math.sqrt(dx * dx + dy * dy);
            if (dist <= tol && dist < bestDist) {
                best = pin;
                bestDist = dist;
            }
        }
        return best;
    }

    hitTestNetLabel(worldX, worldY) {
        if (!this._netLabelHitTargets || this._netLabelHitTargets.length === 0) return null;
        let best = null;
        let bestDist = Infinity;
        const tol = Math.max(1.0, 10 / Math.max(this._zoom, 0.01));
        for (const nl of this._netLabelHitTargets) {
            const dx = worldX - nl.x;
            const dy = worldY - nl.y;
            const dist = Math.sqrt(dx * dx + dy * dy);
            if (dist <= tol && dist < bestDist) {
                best = nl;
                bestDist = dist;
            }
        }
        return best;
    }

    setActivePin(pin) {
        this._activePin = pin || null;
        this._renderInteractionOverlay();
    }

    setActiveNetLabel(nl) {
        this._activeNetLabel = nl || null;
        this._renderInteractionOverlay();
    }

    setSelectedWire(wire) {
        this._selectedWire = wire || null;
        this._renderInteractionOverlay();
    }

    setWireDraft(startPin, worldPoint) {
        this._wireDraft = startPin && worldPoint ? { startPin, worldPoint } : null;
        this._scheduleInteractionOverlay();
    }

    clearWireDraft() {
        this._wireDraft = null;
        this._activePin = null;
        this._activeNetLabel = null;
        this._renderInteractionOverlay();
    }

    /** Toggle wire visibility (useful when using net labels). */
    setShowWires(show) {
        this._showWires = show;
        this._fullRedraw();
    }

    getShowWires() {
        return this._showWires;
    }

    refresh() {
        this._dragCompRef = null;
        this._dragDelta = { dx: 0, dy: 0 };
        this._fullRedraw();
    }

    clearDragState() {
        this._dragCompRef = null;
        this._dragDelta = { dx: 0, dy: 0 };
        this._partialRedraw();
    }

    // ── Camera ───────────────────────────────────────────────────────────────

    zoomToFit() {
        if (!this._schematic || this._schematic.components.length === 0) return;

        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        for (const comp of this._schematic.components) {
            const g = comp.geomBBox;
            minX = Math.min(minX, comp.x + g.x);
            minY = Math.min(minY, comp.y + g.y);
            maxX = Math.max(maxX, comp.x + g.x + g.w);
            maxY = Math.max(maxY, comp.y + g.y + g.h);
        }

        if (minX === Infinity) return;

        const margin = 20;
        const vw = this._app.screen.width;
        const vh = this._app.screen.height;
        const bw = maxX - minX + margin * 2;
        const bh = maxY - minY + margin * 2;

        this._zoom = Math.min(vw / bw, vh / bh) * 0.9;
        this._zoom = Math.min(Math.max(this._zoom, 0.01), 50);

        this._contentCenter.x = (minX + maxX) / 2;
        this._contentCenter.y = (minY + maxY) / 2;
        this._panOffset = { x: 0, y: 0 };

        this._applyCamera();
        if (this._callbacks.onZoomChange) this._callbacks.onZoomChange(this._zoom);
    }

    setZoom(level) {
        this._zoom = Math.min(Math.max(level, 0.01), 50);
        this._applyCamera();
        if (this._callbacks.onZoomChange) this._callbacks.onZoomChange(this._zoom);
    }

    zoomTo(worldX, worldY, targetZoom) {
        this._contentCenter.x = worldX;
        this._contentCenter.y = worldY;
        this._panOffset = { x: 0, y: 0 };
        this._zoom = targetZoom || Math.max(this._zoom, 4);
        this._applyCamera();
        if (this._callbacks.onZoomChange) this._callbacks.onZoomChange(this._zoom);
    }

    _applyCamera() {
        const s = this._zoom;
        this._world.scale.set(s, -s);
        this._world.position.set(
            this._app.screen.width / 2 - this._contentCenter.x * s + this._panOffset.x,
            this._app.screen.height / 2 + this._contentCenter.y * s + this._panOffset.y
        );
        // Grid is screen-relative in world space — refresh after pan/zoom
        this._scheduleGridRedraw();
    }

    screenToWorld(sx, sy) {
        const s = this._zoom;
        const p = this._world.position;
        if (Math.abs(s) < 1e-10) return null;
        return {
            x: (sx - p.x) / s,
            y: -(sy - p.y) / s,
        };
    }

    worldToScreen(wx, wy) {
        const s = this._zoom;
        const p = this._world.position;
        return {
            x: wx * s + p.x,
            y: -wy * s + p.y,
        };
    }

    getCanvasCenterWorld() {
        const cx = this._app.screen.width / 2;
        const cy = this._app.screen.height / 2;
        return this.screenToWorld(cx, cy) || { x: 0, y: 0 };
    }

    // ── Interaction ──────────────────────────────────────────────────────────

    _setupInteraction() {
        const canvas = this._canvas;

        // Wheel zoom
        canvas.addEventListener('wheel', (e) => {
            e.preventDefault();
            const oldZoom = this._zoom;
            const delta = -e.deltaY;
            const factor = delta > 0 ? 1.1 : 1 / 1.1;
            const newZoom = Math.min(Math.max(oldZoom * factor, 0.01), 50);

            const rect = canvas.getBoundingClientRect();
            const sx = (e.clientX - rect.left) * (this._app.screen.width / rect.width);
            const sy = (e.clientY - rect.top) * (this._app.screen.height / rect.height);

            const wx = (sx - this._world.position.x) / this._world.scale.x;
            const wy = (sy - this._world.position.y) / this._world.scale.y;

            this._zoom = newZoom;

            this._panOffset.x = sx - this._app.screen.width / 2 + (this._contentCenter.x - wx) * newZoom;
            this._panOffset.y = sy - this._app.screen.height / 2 - (this._contentCenter.y - wy) * newZoom;

            this._applyCamera();
            if (this._callbacks.onZoomChange) this._callbacks.onZoomChange(this._zoom);
        }, { passive: false, signal: this._interactionAbort.signal });

        // Middle-button pan
        canvas.addEventListener('mousedown', (e) => {
            if (e.button === 1) {
                this._isPanning = true;
                this._panStart = { x: e.clientX, y: e.clientY };
                this._panOffsetStart = { x: this._panOffset.x, y: this._panOffset.y };
                canvas.style.cursor = 'grabbing';
            }
        }, { signal: this._interactionAbort.signal });

        // Left-click: select, drag-move component/marker, or pan on empty space
        let _leftStart = null;
        let _dragComp = null;
        let _dragMarker = null;
        let _dragStartPos = null;  // Original position before drag (avoids polluting marker/comp objects)
        let _leftPanning = false;

        canvas.addEventListener('mousedown', (e) => {
            if (e.button === 0) {
                _leftStart = { x: e.clientX, y: e.clientY };
                _dragComp = null;
                _dragMarker = null;
                _leftPanning = false;
                if (this._wireDraft) return;
                const rect = canvas.getBoundingClientRect();
                const sx = (e.clientX - rect.left) * (this._app.screen.width / rect.width);
                const sy = (e.clientY - rect.top) * (this._app.screen.height / rect.height);
                const world = this.screenToWorld(sx, sy);
                if (world) {
                    const pin = this.hitTestPin(world.x, world.y);
                    if (!pin) {
                        const marker = this.hitTestImageMarker(world.x, world.y);
                        if (marker) {
                            _dragMarker = marker;
                            this.selectImageMarker(marker);
                            _dragStartPos = { x: marker.x, y: marker.y };
                            return;
                        }
                        const comp = this.hitTest(world.x, world.y);
                        if (comp) {
                            _dragComp = comp;
                            this.selectComponent(comp);
                            _dragStartPos = { x: comp.x, y: comp.y };
                        }
                    }
                }
            }
        }, { signal: this._interactionAbort.signal });

        canvas.addEventListener('mousemove', (e) => {
            if (this._isPanning) {
                const dx = e.clientX - this._panStart.x;
                const dy = e.clientY - this._panStart.y;
                this._panOffset.x = this._panOffsetStart.x + dx;
                this._panOffset.y = this._panOffsetStart.y + dy;
                this._applyCamera();
                return;
            }

            if (e.buttons & 1 && _leftStart) {
                const dx = e.clientX - _leftStart.x;
                const dy = e.clientY - _leftStart.y;

                if (_dragMarker) {
                    const newX = _dragStartPos.x + dx / this._zoom;
                    const newY = _dragStartPos.y - dy / this._zoom;
                    this._dragDelta = { dx: newX - _dragStartPos.x, dy: newY - _dragStartPos.y };
                    _dragMarker.x = newX;
                    _dragMarker.y = newY;
                    this._partialRedraw();
                    return;
                }

                if (_dragComp) {
                    _dragComp.x = _dragStartPos.x + dx / this._zoom;
                    _dragComp.y = _dragStartPos.y - dy / this._zoom;
                    this._dragDelta.dx = _dragComp.x - _dragStartPos.x;
                    this._dragDelta.dy = _dragComp.y - _dragStartPos.y;
                    this._dragCompRef = _dragComp.refDesignator;
                    this._partialRedraw();
                    return;
                }

                if (!_leftPanning && (Math.abs(dx) > 4 || Math.abs(dy) > 4)) {
                    _leftPanning = true;
                    canvas.style.cursor = 'grabbing';
                    this._panOffsetStart = { x: this._panOffset.x, y: this._panOffset.y };
                }
                if (_leftPanning) {
                    this._panOffset.x = this._panOffsetStart.x + dx;
                    this._panOffset.y = this._panOffsetStart.y + dy;
                    this._applyCamera();
                    return;
                }
            }

            // Update coordinate display
            if (this._callbacks.onCoordChange) {
                const rect = canvas.getBoundingClientRect();
                const sx = (e.clientX - rect.left) * (this._app.screen.width / rect.width);
                const sy = (e.clientY - rect.top) * (this._app.screen.height / rect.height);
                const world = this.screenToWorld(sx, sy);
                if (world) {
                    this._callbacks.onCoordChange(world.x, world.y);
                    const hoverPin = this.hitTestPin(world.x, world.y);
                    const hoverKey = hoverPin ? hoverPin.key : '';
                    if ((this._hoverPin ? this._hoverPin.key : '') !== hoverKey) {
                        this._hoverPin = hoverPin;
                        this._scheduleInteractionOverlay();
                    }
                    // Net label hover
                    const hoverNl = this.hitTestNetLabel(world.x, world.y);
                    const hoverNlId = hoverNl ? hoverNl.id : '';
                    if ((this._hoverNetLabel ? this._hoverNetLabel.id : '') !== hoverNlId) {
                        this._hoverNetLabel = hoverNl;
                        this._scheduleInteractionOverlay();
                    }
                    canvas.style.cursor = this._wireDraft || hoverPin || hoverNl ? 'crosshair' : '';
                }
            }
            if (this._wireDraft) {
                const rect = canvas.getBoundingClientRect();
                const sx = (e.clientX - rect.left) * (this._app.screen.width / rect.width);
                const sy = (e.clientY - rect.top) * (this._app.screen.height / rect.height);
                const world = this.screenToWorld(sx, sy);
                if (world) this.setWireDraft(this._wireDraft.startPin, world);
            }
        }, { signal: this._interactionAbort.signal });

        canvas.addEventListener('mouseup', (e) => {
            if (e.button === 0) {
                if (_dragMarker) {
                    _dragStartPos = null;
                    const dx = e.clientX - _leftStart.x;
                    const dy = e.clientY - _leftStart.y;
                    const moved = Math.abs(dx) >= 4 || Math.abs(dy) >= 4;
                    if (moved && this._callbacks.onMarkerMoved) {
                        this._callbacks.onMarkerMoved(_dragMarker, this._dragDelta.dx, this._dragDelta.dy);
                    }
                    _dragMarker = null;
                    _leftStart = null;
                    return;
                }
                if (_dragComp) {
                    _dragStartPos = null;
                    const dx = e.clientX - _leftStart.x;
                    const dy = e.clientY - _leftStart.y;
                    const moved = Math.abs(dx) >= 4 || Math.abs(dy) >= 4;
                    if (moved && this._callbacks.onComponentMoved) {
                        this._callbacks.onComponentMoved(_dragComp, this._dragDelta.dx, this._dragDelta.dy);
                        _dragComp = null;
                        _leftStart = null;
                        return;
                    }
                    this._dragCompRef = null;
                    this._dragDelta = { dx: 0, dy: 0 };
                    _dragComp = null;
                    _leftStart = null;
                    return;
                }
                if (_leftPanning) {
                    _leftPanning = false;
                    _leftStart = null;
                    canvas.style.cursor = '';
                    return;
                }
                if (_leftStart) {
                    const dx = e.clientX - _leftStart.x;
                    const dy = e.clientY - _leftStart.y;
                    if (Math.abs(dx) < 4 && Math.abs(dy) < 4) {
                        this._handleClick(e);
                    }
                    _leftStart = null;
                }
            }

            if (e.button === 1) {
                this._isPanning = false;
                canvas.style.cursor = '';
            }
        }, { signal: this._interactionAbort.signal });

        canvas.addEventListener('mouseleave', () => {
            this._isPanning = false;
            this._dragCompRef = null;
            this._dragDelta = { dx: 0, dy: 0 };
            _leftPanning = false;
            _dragComp = null;
            _leftStart = null;
            canvas.style.cursor = '';
        }, { signal: this._interactionAbort.signal });

        // Double-click: open marker inspector
        canvas.addEventListener('dblclick', (e) => {
            const rect = canvas.getBoundingClientRect();
            const sx = (e.clientX - rect.left) * (this._app.screen.width / rect.width);
            const sy = (e.clientY - rect.top) * (this._app.screen.height / rect.height);
            const world = this.screenToWorld(sx, sy);
            if (!world) return;
            const marker = this.hitTestImageMarker(world.x, world.y);
            if (marker) {
                this.selectImageMarker(marker);
                if (this._callbacks.onImageMarkerDblClick) {
                    this._callbacks.onImageMarkerDblClick(marker);
                }
            }
        }, { signal: this._interactionAbort.signal });

        // Right-click context menu
        canvas.addEventListener('contextmenu', (e) => {
            e.preventDefault();
            const rect = canvas.getBoundingClientRect();
            const sx = (e.clientX - rect.left) * (this._app.screen.width / rect.width);
            const sy = (e.clientY - rect.top) * (this._app.screen.height / rect.height);
            const world = this.screenToWorld(sx, sy);
            // Check if right-clicking on a marker
            let marker = null;
            if (world) {
                marker = this.hitTestImageMarker(world.x, world.y);
            }
            if (this._callbacks.onContextMenu) {
                this._callbacks.onContextMenu(e.clientX, e.clientY, world, marker);
            }
        }, { signal: this._interactionAbort.signal });

        // Keyboard: Delete/Escape/Enter for marker interaction
        document.addEventListener('keydown', (e) => {
            // Guard: don't intercept keys when user is typing in an input field
            const tag = (document.activeElement || {}).tagName;
            if (tag === 'INPUT' || tag === 'TEXTAREA' || (document.activeElement || {}).isContentEditable) return;

            if (this._selectedImageMarker) {
                if (e.key === 'Delete' || e.key === 'Backspace') {
                    e.preventDefault();
                    if (this._callbacks.onImageMarkerDelete) {
                        this._callbacks.onImageMarkerDelete(this._selectedImageMarker);
                    }
                } else if (e.key === 'Escape') {
                    this._selectedImageMarker._imageRevealed = false;
                    this.clearImageMarkerSelection();
                } else if (e.key === 'Enter') {
                    // Toggle image reveal — no inspector popup
                    const m = this._selectedImageMarker;
                    m._imageRevealed = !m._imageRevealed;
                    this._partialRedraw();
                    if (this._callbacks.onImageMarkerToggle) {
                        this._callbacks.onImageMarkerToggle(m);
                    }
                }
            }
        }, { signal: this._interactionAbort.signal });
    }

    _partialRedraw() {
        if (!this._schematic) return;
        this._clearLayer(this._wireLayer);
        this._clearLayer(this._symbolLayer);
        this._clearLayer(this._imageMarkerLayer);
        this._clearLayer(this._pinLayer);
        this._clearLayer(this._textLayer);
        this._clearLayer(this._overlayLayer);
        this._pinHitTargets = [];
        this._imageMarkerHitTargets = [];

        this._renderWires();
        this._renderJunctions();
        this._renderPowerLabels();
        this._renderImageMarkers();
        const globalPinNames = [];
        for (const comp of this._schematic.components) {
            this._renderComponent(comp, globalPinNames);
        }
        this._renderNetLabels();
        this._renderTerminals();
        this._renderSelection();
        if (this._wireDraft || this._hoverPin || this._activePin || this._hoverNetLabel || this._activeNetLabel) {
            this._renderInteractionOverlay();
        }
    }

    _handleClick(e) {
        const canvas = this._canvas;
        const rect = canvas.getBoundingClientRect();
        const sx = (e.clientX - rect.left) * (this._app.screen.width / rect.width);
        const sy = (e.clientY - rect.top) * (this._app.screen.height / rect.height);

        const world = this.screenToWorld(sx, sy);
        if (!world) return;

        // Check net label clicks first (only in net label mode)
        const nl = this.hitTestNetLabel(world.x, world.y);
        if (nl && typeof this._callbacks.onNetLabelClick === 'function') {
            this._callbacks.onNetLabelClick(nl, world);
            return;
        }

        const pin = this.hitTestPin(world.x, world.y);
        if (pin && this._callbacks.onPinClick) {
            this._callbacks.onPinClick(pin, world);
            return;
        }

        // Check wire clicks
        if (typeof this._callbacks.onWireClick === 'function') {
            const wireResult = this._callbacks.onWireClick(world.x, world.y);
            if (wireResult) return;
        }

        const marker = this.hitTestImageMarker(world.x, world.y);
        if (marker) {
            this.selectImageMarker(marker);
            this._toggleMarkerReveal(marker);
            return;
        }

        const comp = this.hitTest(world.x, world.y);
        if (comp) {
            this.selectComponent(comp);
        } else {
            this.clearSelection();
            // Deselect wire if clicking on empty space
            if (typeof this._callbacks.onWireDeselect === 'function') {
                this._callbacks.onWireDeselect();
            }
        }
    }

    _renderTerminals() {
        if (!this._pinHitTargets || this._pinHitTargets.length === 0) return;
        const g = new PIXI.Graphics();

        // Build set of connected pins from wire paths AND net labels
        const connectedPins = new Set();
        const netPinColor = new Map(); // pinKey -> color
        if (this._schematic) {
            for (const w of this._schematic.wirePaths || []) {
                if (w.source) connectedPins.add(w.source);
                if (w.target) connectedPins.add(w.target);
            }
            for (const l of this._schematic.netLabels || []) {
                if (l.pin) {
                    connectedPins.add(l.pin);
                    netPinColor.set(l.pin, hexToPixi(netColor(l.net)));
                }
            }
        }

        for (const pin of this._pinHitTargets) {
            if (connectedPins.has(pin.key)) {
                const netC = netPinColor.has(pin.key) ? netPinColor.get(pin.key) : SCH_COLORS.terminal;
                // Connected pin: terminal circle
                g.lineStyle(0.12, netC, 0.85);
                g.beginFill(SCH_COLORS.bg, 0.95);
                g.drawCircle(pin.x, pin.y, 0.72);
                g.endFill();
                g.beginFill(netC, 0.9);
                g.drawCircle(pin.x, pin.y, 0.28);
                g.endFill();
            } else {
                // Unconnected pin: X marker (no-connect flag)
                const arm = 0.8;
                g.lineStyle(0.2, SCH_COLORS.noConnect, 0.9);
                g.moveTo(pin.x - arm, pin.y - arm);
                g.lineTo(pin.x + arm, pin.y + arm);
                g.moveTo(pin.x + arm, pin.y - arm);
                g.lineTo(pin.x - arm, pin.y + arm);
            }
        }
        this._overlayLayer.addChild(g);
    }

    _scheduleInteractionOverlay() {
        if (this._overlayRaf) return;
        this._overlayRaf = requestAnimationFrame(() => {
            this._overlayRaf = null;
            this._renderInteractionOverlay();
        });
    }

    _renderInteractionOverlay() {
        this._clearLayer(this._overlayLayer);
        this._renderTerminals();
        this._renderSelection();
        if (this._hoverPin) {
            const hg = new PIXI.Graphics();
            hg.lineStyle(0.16, SCH_COLORS.terminalHover, 0.95);
            hg.beginFill(SCH_COLORS.terminalHover, 0.18);
            hg.drawCircle(this._hoverPin.x, this._hoverPin.y, 1.15);
            hg.endFill();
            this._overlayLayer.addChild(hg);
        }
        if (this._activePin) {
            const ag = new PIXI.Graphics();
            ag.lineStyle(0.18, SCH_COLORS.terminalActive, 1);
            ag.beginFill(SCH_COLORS.terminalActive, 0.22);
            ag.drawCircle(this._activePin.x, this._activePin.y, 1.35);
            ag.endFill();
            this._overlayLayer.addChild(ag);
        }
        // Net label hover/active highlights
        if (this._hoverNetLabel) {
            const hg = new PIXI.Graphics();
            const c = hexToPixi(netColor(this._hoverNetLabel.net));
            hg.lineStyle(0.16, c, 0.95);
            hg.beginFill(c, 0.15);
            hg.drawCircle(this._hoverNetLabel.x, this._hoverNetLabel.y, 1.5);
            hg.endFill();
            this._overlayLayer.addChild(hg);
        }
        if (this._activeNetLabel) {
            const ag = new PIXI.Graphics();
            const c = hexToPixi(netColor(this._activeNetLabel.net));
            ag.lineStyle(0.18, c, 1);
            ag.beginFill(c, 0.25);
            ag.drawCircle(this._activeNetLabel.x, this._activeNetLabel.y, 1.8);
            ag.endFill();
            this._overlayLayer.addChild(ag);
        }
        // Wire selection highlight
        if (this._selectedWire && this._selectedWire.path && this._selectedWire.path.length > 1) {
            const wg = new PIXI.Graphics();
            // Draw selection highlight (thicker line with different color)
            wg.lineStyle(0.45, 0xffd166, 0.9); // Golden color for selection
            wg.moveTo(this._selectedWire.path[0].x, this._selectedWire.path[0].y);
            for (let i = 1; i < this._selectedWire.path.length; i++) {
                wg.lineTo(this._selectedWire.path[i].x, this._selectedWire.path[i].y);
            }
            // Draw selection handles at waypoints
            wg.lineStyle(0.15, 0xffffff, 1);
            wg.beginFill(0xffd166, 0.8);
            for (const pt of this._selectedWire.path) {
                wg.drawCircle(pt.x, pt.y, 0.35);
            }
            wg.endFill();
            this._overlayLayer.addChild(wg);
        }
        if (!this._wireDraft) return;
        const start = this._wireDraft.startPin;
        const end = this._wireDraft.worldPoint;
        const g = new PIXI.Graphics();

        // Draw 90-degree constrained wire preview (L-shape)
        // Snap to grid
        const sx = Math.round(start.x / 1.27) * 1.27;
        const sy = Math.round(start.y / 1.27) * 1.27;
        const ex = Math.round(end.x / 1.27) * 1.27;
        const ey = Math.round(end.y / 1.27) * 1.27;

        // Draw L-shaped preview
        g.lineStyle(0.254, SCH_COLORS.wirePreview, 0.9);
        g.moveTo(sx, sy);
        g.lineTo(ex, sy); // Horizontal segment first
        g.lineTo(ex, ey); // Then vertical segment

        // Draw waypoints
        g.beginFill(SCH_COLORS.wirePreview, 1);
        g.drawCircle(sx, sy, 0.5); // Start point
        g.drawCircle(ex, sy, 0.3); // Corner point
        g.drawCircle(ex, ey, 0.5); // End point
        g.endFill();

        // Draw direction indicator
        const dirX = ex - sx;
        const dirY = ey - sy;
        if (Math.abs(dirX) > 0.1 || Math.abs(dirY) > 0.1) {
            // Draw small arrow at the corner
            const arrowSize = 0.4;
            const cornerX = ex;
            const cornerY = sy;

            g.lineStyle(0.15, SCH_COLORS.wirePreview, 0.7);
            if (Math.abs(dirX) > 0.1) {
                // Arrow pointing in X direction
                const arrowDir = dirX > 0 ? 1 : -1;
                g.moveTo(cornerX - arrowDir * arrowSize, cornerY - arrowSize);
                g.lineTo(cornerX, cornerY);
                g.lineTo(cornerX - arrowDir * arrowSize, cornerY + arrowSize);
            } else {
                // Arrow pointing in Y direction
                const arrowDir = dirY > 0 ? 1 : -1;
                g.moveTo(cornerX - arrowSize, cornerY - arrowDir * arrowSize);
                g.lineTo(cornerX, cornerY);
                g.lineTo(cornerX + arrowSize, cornerY - arrowDir * arrowSize);
            }
        }

        this._overlayLayer.addChild(g);
    }

    _clearLayer(layer) {
        if (!layer) return;
        const children = layer.removeChildren() || [];
        for (const child of children) {
            if (!child || typeof child.destroy !== 'function') continue;
            try {
                child.destroy({ children: true, texture: true, baseTexture: true });
            } catch (_) {
                try {
                    child.destroy(true);
                } catch (_) {}
            }
        }
    }
}
