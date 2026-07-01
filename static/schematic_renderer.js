// ── Schematic Renderer (PixiJS) ──────────────────────────────────────────────
// Phase 1A: view-only renderer with grid, symbols, wires, zoom/pan, selection.
// Replaces static/renderer.js Canvas2D rendering path.

// NOTE: GRID_SIZE is already defined in schematic.js (loaded before this file).
const SCH_COLORS = {
    bg: 0x1a1a2e,
    symbolLine: 0xE34E32,
    symbolFill: 0x4a3a2e,
    pinLine: 0xE34E32,
    pinName: 0x00A8A8,
    pinNum: 0xE34E32,
    propertyRef: 0x00A8A8,
    propertyVal: 0x00A8A8,
    text: 0x888888,
    wire: 0x00A800,
    junction: 0x00A800,
    grid: 0x496090,
    gridMajor: 0x3a5068,
    selection: 0x00FF88,
    powerGnd: 0x4488ff,
    powerVcc: 0xcc4444,
};

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
        this._dpr = window.devicePixelRatio || 1;

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

        // Layer containers
        this._gridLayer = new PIXI.Container();
        this._wireLayer = new PIXI.Container();
        this._symbolLayer = new PIXI.Container();
        this._pinLayer = new PIXI.Container();
        this._textLayer = new PIXI.Container();
        this._overlayLayer = new PIXI.Container();

        // World container flips Y axis (KiCad Y-up → PixiJS Y-down)
        this._world = new PIXI.Container();
        this._world.addChild(
            this._gridLayer,
            this._wireLayer,
            this._symbolLayer,
            this._pinLayer,
            this._textLayer,
            this._overlayLayer
        );
        this._app.stage.addChild(this._world);

        this._setupInteraction();
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
        this._app.destroy(true, { children: true });
    }

    // ── Full redraw ──────────────────────────────────────────────────────────

    _fullRedraw() {
        this._gridLayer.removeChildren();
        this._wireLayer.removeChildren();
        this._symbolLayer.removeChildren();
        this._pinLayer.removeChildren();
        this._textLayer.removeChildren();
        this._overlayLayer.removeChildren();

        if (!this._schematic) return;

        this._renderGrid();
        this._renderWires();
        this._renderJunctions();
        this._renderPowerLabels();

        const globalPinNames = [];
        for (const comp of this._schematic.components) {
            this._renderComponent(comp, globalPinNames);
        }

        this._renderSelection();
    }

    _renderComponent(comp, globalPinNames) {
        const ox = comp.x;
        const oy = comp.y;

        for (const op of comp.ops) {
            const type = op[0];

            if (type === 'rectangle' || type === 'polyline' || type === 'circle' || type === 'arc') {
                const g = new PIXI.Graphics();
                this._drawOpShape(g, op, ox, oy);
                this._symbolLayer.addChild(g);
            } else if (type === 'pin') {
                this._drawPin(op, ox, oy, globalPinNames);
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
            if (w) lineWidth = parseFloat(w[1]);
        }
        let fillColor = null;
        let fillAlpha = 0;
        if (fill) {
            const ft = fill[1];
            if (ft === '(type background)') {
                fillColor = SCH_COLORS.symbolFill;
                fillAlpha = 0.15;
            } else if (ft === '(type solid)') {
                fillColor = SCH_COLORS.symbolLine;
                fillAlpha = 1;
            }
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

    _drawPin(op, ox, oy, globalPinNames) {
        const at = getAttr(op, 'at');
        const lenNode = getAttr(op, 'length');
        if (!at || !lenNode) return;

        const x = parseFloat(at[1]) + ox, y = parseFloat(at[2]) + oy;
        const len = parseFloat(lenNode[1]);
        const angDeg = parseFloat(at[3] || 0);
        const ang = angDeg * Math.PI / 180;
        const ex = x + Math.cos(ang) * len;
        const ey = y + Math.sin(ang) * len;

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

        // Pin name (with dedup)
        const nameNode = getAttr(op, 'name');
        if (nameNode && nameNode[1] !== '"~"') {
            const nameText = nameNode[1];

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
                    fontFamily: '"JetBrains Mono", "Fira Code", monospace',
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
        const hide = getAttr(op, 'hide');
        if (!at || (hide && hide[1] === 'yes')) return;

        const txt = type === 'property' ? op[2] : op[1];
        if (!txt || txt === '"~"') return;

        const x = parseFloat(at[1]) + ox;
        const y = parseFloat(at[2]) + oy;
        const ang = parseFloat(at[3] || 0);
        const size = this._getFontSize(op);

        let fillColor = SCH_COLORS.propertyVal;
        if (op[1] === '"Reference"') fillColor = SCH_COLORS.propertyRef;
        if (type === 'text') fillColor = SCH_COLORS.text;

        const FONT_RES = 24;
        const pixiTxt = new PIXI.Text(txt, {
            fontFamily: '"JetBrains Mono", "Fira Code", monospace',
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

        // Dot grid — only when reasonably zoomed in
        if (this._zoom > 0.15) {
            const dotRadius = 0.06;
            g.beginFill(SCH_COLORS.grid, 0.25);
            for (let x = startX; x <= endX; x += gridMm) {
                for (let y = startY; y <= endY; y += gridMm) {
                    g.drawCircle(x, y, dotRadius);
                }
            }
            g.endFill();
        }

        // Major grid lines
        const majorStartX = Math.floor(bounds.minX / majorGrid) * majorGrid;
        const majorEndX = Math.ceil(bounds.maxX / majorGrid) * majorGrid;
        const majorStartY = Math.floor(bounds.minY / majorGrid) * majorGrid;
        const majorEndY = Math.ceil(bounds.maxY / majorGrid) * majorGrid;

        g.lineStyle(0.04, SCH_COLORS.gridMajor, 0.12);
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

        const g = new PIXI.Graphics();
        g.lineStyle(0.254, SCH_COLORS.wire, 1);

        for (const wire of this._schematic.wirePaths) {
            if (!wire.path || wire.path.length < 2) continue;
            g.moveTo(wire.path[0].x, wire.path[0].y);
            for (let i = 1; i < wire.path.length; i++) {
                const dx = Math.abs(wire.path[i].x - wire.path[i - 1].x);
                const dy = Math.abs(wire.path[i].y - wire.path[i - 1].y);
                if (dx > 0.001 && dy > 0.001) continue; // skip diagonal — shouldn't happen
                g.lineTo(wire.path[i].x, wire.path[i].y);
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
                    fontFamily: '"JetBrains Mono", "Fira Code", monospace',
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

    // ── Selection ─────────────────────────────────────────────────────────────

    selectComponent(comp) {
        this._selectedComp = comp;
        this._overlayLayer.removeChildren();
        this._renderSelection();
        if (this._callbacks.onSelect) {
            this._callbacks.onSelect(comp);
        }
    }

    clearSelection() {
        this._selectedComp = null;
        this._overlayLayer.removeChildren();
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

    _applyCamera() {
        const s = this._zoom;
        this._world.scale.set(s, -s);
        this._world.position.set(
            this._app.screen.width / 2 - this._contentCenter.x * s + this._panOffset.x,
            this._app.screen.height / 2 + this._contentCenter.y * s + this._panOffset.y
        );
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
        }, { passive: false });

        // Middle-button pan
        canvas.addEventListener('mousedown', (e) => {
            if (e.button === 1) {
                this._isPanning = true;
                this._panStart = { x: e.clientX, y: e.clientY };
                this._panOffsetStart = { x: this._panOffset.x, y: this._panOffset.y };
                canvas.style.cursor = 'grabbing';
            }
        });

        canvas.addEventListener('mousemove', (e) => {
            if (this._isPanning) {
                const dx = e.clientX - this._panStart.x;
                const dy = e.clientY - this._panStart.y;
                this._panOffset.x = this._panOffsetStart.x + dx;
                this._panOffset.y = this._panOffsetStart.y + dy;
                this._applyCamera();
                return;
            }

            // Update coordinate display
            if (this._callbacks.onCoordChange) {
                const rect = canvas.getBoundingClientRect();
                const sx = (e.clientX - rect.left) * (this._app.screen.width / rect.width);
                const sy = (e.clientY - rect.top) * (this._app.screen.height / rect.height);
                const world = this.screenToWorld(sx, sy);
                if (world) this._callbacks.onCoordChange(world.x, world.y);
            }
        });

        canvas.addEventListener('mouseup', (e) => {
            if (e.button === 1) {
                this._isPanning = false;
                canvas.style.cursor = '';
            }
        });

        canvas.addEventListener('mouseleave', () => {
            this._isPanning = false;
            canvas.style.cursor = '';
        });

        // Left-click selection (handle click, not mousedown, to avoid conflicts with pan)
        let _clickStart = null;
        canvas.addEventListener('mousedown', (e) => {
            if (e.button === 0) {
                _clickStart = { x: e.clientX, y: e.clientY };
            }
        });

        canvas.addEventListener('mouseup', (e) => {
            if (e.button === 0 && _clickStart) {
                const dx = e.clientX - _clickStart.x;
                const dy = e.clientY - _clickStart.y;
                if (Math.abs(dx) < 4 && Math.abs(dy) < 4) {
                    // It's a click, not a drag
                    this._handleClick(e);
                }
                _clickStart = null;
            }
        });
    }

    _handleClick(e) {
        const canvas = this._canvas;
        const rect = canvas.getBoundingClientRect();
        const sx = (e.clientX - rect.left) * (this._app.screen.width / rect.width);
        const sy = (e.clientY - rect.top) * (this._app.screen.height / rect.height);

        const world = this.screenToWorld(sx, sy);
        if (!world) return;

        const comp = this.hitTest(world.x, world.y);
        if (comp) {
            this.selectComponent(comp);
        } else {
            this.clearSelection();
        }
    }
}
