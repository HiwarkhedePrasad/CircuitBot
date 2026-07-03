const PCB_COLORS = {
    background: 0x07100f,
    gridMinor: 0x13201f,
    gridMajor: 0x25413d,
    boardFill: 0x10251f,
    outline: 0x19d7b0,
    outlineShadow: 0x063b32,
    airwire: 0xffffff,
    airwireDim: 0xaab8c8,
    topCopper: 0xff563d,
    bottomCopper: 0x356cff,
    copperEdge: 0xffb199,
    viaCopper: 0xdce8ef,
    viaDrill: 0x071019,
    smdTop: 0xf04f3a,
    smdBottom: 0x3d7bff,
    throughPad: 0xf2ded4,
    maskPad: 0x12392f,
    silkscreen: 0xe9f7f4,
    fab: 0x7e9691,
    courtyard: 0x2d5e57,
    text: 0xe9f7f4,
    textDim: 0x91aaa4,
    selection: 0x4df1c2,
    routeGhost: 0xfff1a8,
    hoverFill: 0x0d1716,
    hole: 0x071019,
};

const PCB_TEXT_STYLE = {
    fontFamily: '"JetBrains Mono", "Cascadia Mono", Consolas, monospace',
    fontSize: 14,
};

const PCB_MODE = {
    IDLE: 'idle',
    PANNING: 'panning',
    DRAG_COMPONENT: 'drag_component',
    ROUTE: 'route',
};

let pcbState = {
    boardModel: null,
    mode: PCB_MODE.IDLE,
    zoom: 1,
    panX: 0,
    panY: 0,
    baseScale: 1,
    midX: 0,
    midY: 0,
    cx: 0,
    cy: 0,
    ratsnest: {},
    listenersAttached: false,
    selectedComponentRef: null,
    hoveredPadKey: null,
    hoveredComponentRef: null,
    dragComponentRef: null,
    dragOrigin: null,
    dragPointerStart: null,
    routeStartAnchor: null,
    routeNetName: '',
    routeLayer: 'F.Cu',
    routeWidth: 0.254,
    routePoints: [],
    routeVias: [],
    routeCursor: null,
    lastPointerWorld: null,
    undoStack: [],
    redoStack: [],
};

function deepClone(value) {
    return JSON.parse(JSON.stringify(value));
}

function toFiniteNumber(value, fallback = 0) {
    const num = Number(value);
    return Number.isFinite(num) ? num : fallback;
}

function normalizePoint(point) {
    if (Array.isArray(point) && point.length >= 2) {
        return { x: toFiniteNumber(point[0]), y: toFiniteNumber(point[1]) };
    }
    if (point && typeof point === 'object' && 'x' in point && 'y' in point) {
        return { x: toFiniteNumber(point.x), y: toFiniteNumber(point.y) };
    }
    return null;
}

function normalizeBoardModel(boardModel) {
    const model = deepClone(boardModel || {});
    model.components = Array.isArray(model.components) ? model.components : [];
    model.traces = Array.isArray(model.traces) ? model.traces : [];
    model.vias = Array.isArray(model.vias) ? model.vias : [];
    model.nets = Array.isArray(model.nets) ? model.nets : [];
    model.outline_segments = Array.isArray(model.outline_segments) ? model.outline_segments : [];
    for (const component of model.components) {
        component.x = toFiniteNumber(component.x);
        component.y = toFiniteNumber(component.y);
        component.rotation = toFiniteNumber(component.rotation);
        component.pads = Array.isArray(component.pads) ? component.pads : [];
        component.graphics = Array.isArray(component.graphics) ? component.graphics : [];
        for (const pad of component.pads) {
            pad.x = toFiniteNumber(pad.x);
            pad.y = toFiniteNumber(pad.y);
            pad.width = toFiniteNumber(pad.width, 1);
            pad.height = toFiniteNumber(pad.height, 1);
            pad.rotation = toFiniteNumber(pad.rotation);
            if (pad.drill != null) pad.drill = toFiniteNumber(pad.drill, 0);
            pad.layers = Array.isArray(pad.layers) ? pad.layers : ['F.Cu'];
        }
    }
    for (const trace of model.traces) {
        trace.width = toFiniteNumber(trace.width, 0.254);
        trace.path = (Array.isArray(trace.path) ? trace.path : [])
            .map(normalizePoint)
            .filter(Boolean);
    }
    for (const via of model.vias) {
        via.x = toFiniteNumber(via.x);
        via.y = toFiniteNumber(via.y);
        via.drill = toFiniteNumber(via.drill, 0.3);
        via.diameter = toFiniteNumber(via.diameter, 0.6);
        via.layers = Array.isArray(via.layers) ? via.layers : ['F.Cu', 'B.Cu'];
    }
    for (const segment of model.outline_segments) {
        for (const key of ['start', 'end', 'center', 'mid']) {
            if (segment[key]) segment[key] = normalizePoint(segment[key]);
        }
        segment.points = (Array.isArray(segment.points) ? segment.points : [])
            .map(normalizePoint)
            .filter(Boolean);
    }
    return model;
}

function snapToGrid(value, grid = 1.27) {
    return Math.round(value / grid) * grid;
}

function rotatePoint(x, y, angleDeg) {
    const angle = (angleDeg || 0) * Math.PI / 180;
    const cos = Math.cos(angle);
    const sin = Math.sin(angle);
    return {
        x: x * cos - y * sin,
        y: x * sin + y * cos,
    };
}

function routePoint(point) {
    return {
        x: snapToGrid(point.x),
        y: snapToGrid(point.y),
    };
}

function appendRoutePoint(path, target) {
    const out = Array.isArray(path) ? path.slice() : [];
    const point = routePoint(target);
    const prev = out[out.length - 1];
    if (prev && Math.abs(prev.x - point.x) < 0.001 && Math.abs(prev.y - point.y) < 0.001) {
        return out;
    }
    out.push(point);
    return out;
}

function dedupePath(path) {
    const out = [];
    for (const point of path || []) {
        if (!point) continue;
        const next = { x: snapToGrid(point.x), y: snapToGrid(point.y) };
        const prev = out[out.length - 1];
        if (prev && Math.abs(prev.x - next.x) < 0.001 && Math.abs(prev.y - next.y) < 0.001) {
            continue;
        }
        out.push(next);
    }
    return out;
}

function getComponentPadPosition(component, pad) {
    const rotated = rotatePoint(pad.x || 0, pad.y || 0, (component.rotation || 0) + (pad.rotation || 0));
    return {
        x: component.x + rotated.x,
        y: component.y + rotated.y,
    };
}

function getNetNameForPad(model, ref, padNumber) {
    const pinKey = `${ref}:${padNumber}`;
    for (const net of model.nets || []) {
        if ((net.pins || []).includes(pinKey)) {
            return net.name || net.net || '_manual';
        }
    }
    return '_manual';
}

function compactFootprintName(footprint) {
    const raw = String(footprint || '').trim();
    if (!raw) return '';
    const name = raw.includes(':') ? raw.split(':').pop() : raw;
    return name.replace(/^.*?_(?=\d)/, '').replace(/_/g, ' ');
}

function modelBounds(model) {
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (const component of model.components || []) {
        const bounds = getComponentBounds(component);
        minX = Math.min(minX, bounds.minX);
        minY = Math.min(minY, bounds.minY);
        maxX = Math.max(maxX, bounds.maxX);
        maxY = Math.max(maxY, bounds.maxY);
    }
    for (const segment of outlineSegments(model)) {
        for (const point of segment.points || []) {
            minX = Math.min(minX, point.x);
            minY = Math.min(minY, point.y);
            maxX = Math.max(maxX, point.x);
            maxY = Math.max(maxY, point.y);
        }
        for (const key of ['start', 'end', 'center', 'mid']) {
            const point = segment[key];
            if (!point) continue;
            minX = Math.min(minX, point.x);
            minY = Math.min(minY, point.y);
            maxX = Math.max(maxX, point.x);
            maxY = Math.max(maxY, point.y);
        }
    }
    if (minX === Infinity) return { minX: -30, minY: -20, maxX: 30, maxY: 20 };
    return { minX, minY, maxX, maxY };
}

function buildPadKey(component, pad) {
    return `${component.ref}:${pad.number}`;
}

function pointToSegmentDistance(point, start, end) {
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    if (Math.abs(dx) < 1e-9 && Math.abs(dy) < 1e-9) {
        return {
            distance: Math.hypot(point.x - start.x, point.y - start.y),
            point: { x: start.x, y: start.y },
        };
    }
    const t = Math.max(0, Math.min(1, ((point.x - start.x) * dx + (point.y - start.y) * dy) / (dx * dx + dy * dy)));
    const hit = { x: start.x + t * dx, y: start.y + t * dy };
    return {
        distance: Math.hypot(point.x - hit.x, point.y - hit.y),
        point: hit,
    };
}

function getComponentBounds(component) {
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (const pad of component.pads || []) {
        const center = getComponentPadPosition(component, pad);
        const width = Math.max(pad.width || 0.8, pad.drill || 0);
        const height = Math.max(pad.height || 0.8, pad.drill || 0);
        minX = Math.min(minX, center.x - width);
        minY = Math.min(minY, center.y - height);
        maxX = Math.max(maxX, center.x + width);
        maxY = Math.max(maxY, center.y + height);
    }
    for (const item of component.graphics || []) {
        const points = [];
        if (item.start) points.push(item.start);
        if (item.end) points.push(item.end);
        if (item.center) points.push(item.center);
        if (item.mid) points.push(item.mid);
        for (const pt of item.points || []) points.push(pt);
        for (const pt of points) {
            const rotated = rotatePoint(pt.x || 0, pt.y || 0, component.rotation || 0);
            minX = Math.min(minX, component.x + rotated.x);
            minY = Math.min(minY, component.y + rotated.y);
            maxX = Math.max(maxX, component.x + rotated.x);
            maxY = Math.max(maxY, component.y + rotated.y);
        }
    }
    if (minX === Infinity) {
        minX = component.x - 2;
        minY = component.y - 2;
        maxX = component.x + 2;
        maxY = component.y + 2;
    }
    return {
        minX: minX - 1.5,
        minY: minY - 1.5,
        maxX: maxX + 1.5,
        maxY: maxY + 1.5,
    };
}

function outlineSegments(model) {
    return Array.isArray(model.outline_segments) ? model.outline_segments : [];
}

class PcbEditor {
    constructor(canvasId) {
        this._canvasId = canvasId;
        this._app = null;
        this._canvas = null;
        this._world = null;
        this._gridLayer = null;
        this._outlineLayer = null;
        this._airwireLayer = null;
        this._traceLayer = null;
        this._footprintLayer = null;
        this._overlayLayer = null;
        this._textLayer = null;
        this._resizeHandler = () => this._resize();
        this._refreshFrame = null;
        this._overlayFrame = null;
        this._settleRefreshTimer = null;
    }

    ensure() {
        if (this._app) return;
        this._canvas = document.getElementById(this._canvasId);
        if (!this._canvas || !window.PIXI) return;
        const parent = this._canvas.parentElement;
        this._app = new PIXI.Application({
            view: this._canvas,
            width: parent ? parent.clientWidth : 1200,
            height: parent ? parent.clientHeight : 700,
            antialias: true,
            autoDensity: true,
            backgroundColor: PCB_COLORS.background,
        });
        this._canvas.style.background = '#0b1116';
        this._world = new PIXI.Container();
        this._gridLayer = new PIXI.Container();
        this._outlineLayer = new PIXI.Container();
        this._airwireLayer = new PIXI.Container();
        this._traceLayer = new PIXI.Container();
        this._footprintLayer = new PIXI.Container();
        this._textLayer = new PIXI.Container();
        this._overlayLayer = new PIXI.Container();
        this._world.addChild(
            this._gridLayer,
            this._outlineLayer,
            this._airwireLayer,
            this._traceLayer,
            this._footprintLayer,
            this._textLayer,
            this._overlayLayer
        );
        this._app.stage.addChild(this._world);
        window.addEventListener('resize', this._resizeHandler);
        this._resize();
    }

    destroy() {
        if (!this._app) return;
        window.removeEventListener('resize', this._resizeHandler);
        if (this._refreshFrame) {
            cancelAnimationFrame(this._refreshFrame);
            this._refreshFrame = null;
        }
        if (this._overlayFrame) {
            cancelAnimationFrame(this._overlayFrame);
            this._overlayFrame = null;
        }
        if (this._settleRefreshTimer) {
            clearTimeout(this._settleRefreshTimer);
            this._settleRefreshTimer = null;
        }
        if (this._canvas && this._canvas.parentNode) {
            this._canvas.parentNode.removeChild(this._canvas);
        }
        this._canvas = null;
        this._app.destroy(false, { children: true });
        this._app = null;
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

    load(boardModel) {
        this.ensure();
        if (!this._app) return;
        pcbState.boardModel = normalizeBoardModel(boardModel || { components: [], traces: [], vias: [], nets: [] });
        pcbState.selectedComponentRef = null;
        pcbState.hoveredPadKey = null;
        pcbState.hoveredComponentRef = null;
        pcbState.routeStartAnchor = null;
        pcbState.routeNetName = '';
        pcbState.routePoints = [];
        pcbState.routeVias = [];
        pcbState.routeCursor = null;
        this._computeView();
        this.refresh();
    }

    _resize() {
        if (!this._app || !this._canvas) return;
        const parent = this._canvas.parentElement;
        const width = Math.max(parent ? parent.clientWidth : 1200, 100);
        const height = Math.max(parent ? parent.clientHeight : 700, 100);
        this._app.renderer.resize(width, height);
        pcbState.cx = width / 2;
        pcbState.cy = height / 2;
        this._applyCamera();
        this.refresh();
    }

    _computeView() {
        const model = pcbState.boardModel || { components: [], traces: [], vias: [] };
        let minX = Infinity;
        let minY = Infinity;
        let maxX = -Infinity;
        let maxY = -Infinity;
        const expand = (x, y) => {
            minX = Math.min(minX, x);
            minY = Math.min(minY, y);
            maxX = Math.max(maxX, x);
            maxY = Math.max(maxY, y);
        };
        for (const component of model.components || []) {
            const bounds = getComponentBounds(component);
            expand(bounds.minX, bounds.minY);
            expand(bounds.maxX, bounds.maxY);
        }
        for (const trace of model.traces || []) {
            for (const point of trace.path || []) {
                expand(point.x, point.y);
            }
        }
        for (const via of model.vias || []) {
            expand(via.x - via.diameter, via.y - via.diameter);
            expand(via.x + via.diameter, via.y + via.diameter);
        }
        for (const segment of outlineSegments(model)) {
            for (const key of ['start', 'end', 'center', 'mid']) {
                if (segment[key]) expand(segment[key].x, segment[key].y);
            }
            for (const point of segment.points || []) expand(point.x, point.y);
        }
        if (minX === Infinity) {
            minX = -40;
            minY = -30;
            maxX = 40;
            maxY = 30;
        }
        const margin = 10;
        minX -= margin;
        minY -= margin;
        maxX += margin;
        maxY += margin;
        const width = Math.max(maxX - minX, 10);
        const height = Math.max(maxY - minY, 10);
        pcbState.midX = (minX + maxX) / 2;
        pcbState.midY = (minY + maxY) / 2;
        const screenWidth = this._app ? this._app.screen.width : 1200;
        const screenHeight = this._app ? this._app.screen.height : 700;
        pcbState.baseScale = Math.min(screenWidth / width, screenHeight / height) * 0.92;
        pcbState.zoom = 1;
        pcbState.panX = 0;
        pcbState.panY = 0;
        pcbState.cx = screenWidth / 2;
        pcbState.cy = screenHeight / 2;
        this._applyCamera();
    }

    _applyCamera() {
        if (!this._world) return;
        const scale = pcbState.baseScale * pcbState.zoom;
        this._world.scale.set(scale, -scale);
        this._world.position.set(
            pcbState.cx + pcbState.panX - pcbState.midX * scale,
            pcbState.cy + pcbState.panY + pcbState.midY * scale
        );
    }

    screenToWorld(screenX, screenY) {
        const rect = this._canvas.getBoundingClientRect();
        const sx = (screenX - rect.left) * (this._app.screen.width / rect.width);
        const sy = (screenY - rect.top) * (this._app.screen.height / rect.height);
        const scale = pcbState.baseScale * pcbState.zoom;
        return {
            x: (sx - pcbState.cx - pcbState.panX) / scale + pcbState.midX,
            y: -(sy - pcbState.cy - pcbState.panY) / scale + pcbState.midY,
        };
    }

    refresh() {
        if (!this._app) return;
        if (this._refreshFrame) {
            cancelAnimationFrame(this._refreshFrame);
            this._refreshFrame = null;
        }
        if (this._overlayFrame) {
            cancelAnimationFrame(this._overlayFrame);
            this._overlayFrame = null;
        }
        if (this._settleRefreshTimer) {
            clearTimeout(this._settleRefreshTimer);
            this._settleRefreshTimer = null;
        }
        try {
            for (const layer of [
                this._gridLayer,
                this._outlineLayer,
                this._airwireLayer,
                this._traceLayer,
                this._footprintLayer,
                this._textLayer,
                this._overlayLayer,
            ]) {
                this._clearLayer(layer);
            }
            this._drawGrid();
            this._drawBoardOutline();
            this._drawBoardTitle();
            this._drawAirwires();
            this._drawTraces();
            this._drawFootprints();
            this._drawOverlay();
        } catch (error) {
            console.error('PCB editor refresh failed', error);
            dispatchBoardSync(false, { error: error.message || String(error), fallback_saved: false });
        }
    }

    requestRefresh() {
        if (!this._app || this._refreshFrame) return;
        this._refreshFrame = requestAnimationFrame(() => {
            this._refreshFrame = null;
            this.refresh();
        });
    }

    refreshOverlay() {
        if (!this._app) return;
        try {
            this._clearLayer(this._overlayLayer);
            this._drawOverlay();
        } catch (error) {
            console.error('PCB overlay refresh failed', error);
        }
    }

    requestOverlayRefresh() {
        if (!this._app || this._refreshFrame || this._overlayFrame) return;
        this._overlayFrame = requestAnimationFrame(() => {
            this._overlayFrame = null;
            this.refreshOverlay();
        });
    }

    requestSettledRefresh(delay = 90) {
        if (!this._app) return;
        if (this._settleRefreshTimer) clearTimeout(this._settleRefreshTimer);
        this._settleRefreshTimer = setTimeout(() => {
            this._settleRefreshTimer = null;
            this.requestRefresh();
        }, delay);
    }

    _drawGrid() {
        const g = new PIXI.Graphics();
        const bounds = this._visibleBounds();
        const grid = 1.27;
        const startX = Math.floor(bounds.minX / grid) * grid;
        const startY = Math.floor(bounds.minY / grid) * grid;
        for (let x = startX; x <= bounds.maxX; x += grid) {
            for (let y = startY; y <= bounds.maxY; y += grid) {
                const isMajor = Math.round(x / grid) % 10 === 0 && Math.round(y / grid) % 10 === 0;
                g.beginFill(isMajor ? PCB_COLORS.gridMajor : PCB_COLORS.gridMinor, 1);
                g.drawRect(x - 0.04, y - 0.04, 0.08, 0.08);
                g.endFill();
            }
        }
        this._gridLayer.addChild(g);
    }

    _visibleBounds() {
        const scale = pcbState.baseScale * pcbState.zoom;
        const halfW = this._app.screen.width / scale / 2;
        const halfH = this._app.screen.height / scale / 2;
        return {
            minX: pcbState.midX - halfW - 5,
            maxX: pcbState.midX + halfW + 5,
            minY: pcbState.midY - halfH - 5,
            maxY: pcbState.midY + halfH + 5,
        };
    }

    _drawBoardOutline() {
        const model = pcbState.boardModel || {};
        const g = new PIXI.Graphics();
        const bounds = this._fallbackOutline(model);
        g.beginFill(PCB_COLORS.boardFill, 0.96);
        g.drawRoundedRect(bounds.minX, bounds.minY, bounds.maxX - bounds.minX, bounds.maxY - bounds.minY, 1.6);
        g.endFill();
        g.lineStyle(0.46, PCB_COLORS.outlineShadow, 0.95);
        g.drawRoundedRect(bounds.minX, bounds.minY, bounds.maxX - bounds.minX, bounds.maxY - bounds.minY, 1.6);
        g.lineStyle(0.18, PCB_COLORS.outline, 1);
        let drewOutline = false;
        for (const segment of outlineSegments(model)) {
            const points = this._segmentPoints(segment);
            if (points.length < 2) continue;
            drewOutline = true;
            g.moveTo(points[0].x, points[0].y);
            for (let index = 1; index < points.length; index += 1) {
                g.lineTo(points[index].x, points[index].y);
            }
        }
        if (!drewOutline) {
            g.drawRoundedRect(bounds.minX, bounds.minY, bounds.maxX - bounds.minX, bounds.maxY - bounds.minY, 1.6);
        }
        this._outlineLayer.addChild(g);
    }

    _fallbackOutline(model) {
        const bounds = modelBounds(model);
        return { minX: bounds.minX - 5, minY: bounds.minY - 5, maxX: bounds.maxX + 5, maxY: bounds.maxY + 5 };
    }

    _makeText(label, options = {}) {
        const text = new PIXI.Text(String(label || ''), {
            ...PCB_TEXT_STYLE,
            fontSize: options.fontSize || PCB_TEXT_STYLE.fontSize,
            fontWeight: options.fontWeight || '500',
            fill: options.fill || PCB_COLORS.text,
            align: options.align || 'center',
        });
        text.anchor.set(options.anchorX ?? 0.5, options.anchorY ?? 0.5);
        const scale = options.scale || 0.045;
        text.scale.set(scale, -scale);
        text.x = options.x || 0;
        text.y = options.y || 0;
        if (options.rotation) text.rotation = -options.rotation * Math.PI / 180;
        return text;
    }

    _drawBoardTitle() {
        const model = pcbState.boardModel || {};
        const bounds = this._fallbackOutline(model);
        const title = this._makeText('CircuitBot PCB', {
            x: bounds.minX + 2.4,
            y: bounds.maxY - 2.2,
            anchorX: 0,
            fontSize: 18,
            fontWeight: '700',
            fill: PCB_COLORS.silkscreen,
            scale: 0.052,
        });
        const meta = this._makeText(`${(model.components || []).length} parts / ${(model.traces || []).length} traces`, {
            x: bounds.minX + 2.4,
            y: bounds.maxY - 4.1,
            anchorX: 0,
            fontSize: 12,
            fill: PCB_COLORS.textDim,
            scale: 0.044,
        });
        this._textLayer.addChild(title, meta);
    }

    _segmentPoints(segment) {
        if (!segment) return [];
        if (segment.kind === 'gr_rect' && segment.start && segment.end) {
            return [
                segment.start,
                { x: segment.end.x, y: segment.start.y },
                segment.end,
                { x: segment.start.x, y: segment.end.y },
                segment.start,
            ];
        }
        if (segment.kind === 'gr_poly') {
            return (segment.points || []).concat(segment.points && segment.points.length ? [segment.points[0]] : []);
        }
        if (segment.kind === 'gr_circle' && segment.center && segment.end) {
            const radius = Math.hypot(segment.end.x - segment.center.x, segment.end.y - segment.center.y);
            const points = [];
            for (let step = 0; step <= 32; step += 1) {
                const angle = (Math.PI * 2 * step) / 32;
                points.push({
                    x: segment.center.x + Math.cos(angle) * radius,
                    y: segment.center.y + Math.sin(angle) * radius,
                });
            }
            return points;
        }
        if (segment.kind === 'gr_arc' && segment.start && segment.mid && segment.end) {
            return arcPoints(segment.start, segment.mid, segment.end, 24);
        }
        if (segment.start && segment.end) {
            return [segment.start, segment.end];
        }
        return [];
    }

    _drawAirwires() {
        const g = new PIXI.Graphics();
        g.alpha = 0.88;
        for (const edges of Object.values(pcbState.ratsnest || {})) {
            for (const edge of edges) {
                this._drawDashedLine(g, { x: edge.x1, y: edge.y1 }, { x: edge.x2, y: edge.y2 }, 1.25, 0.6, PCB_COLORS.airwire, 0.13);
            }
        }
        this._airwireLayer.addChild(g);
    }

    _drawDashedLine(g, start, end, dash, gap, color, width) {
        const dx = end.x - start.x;
        const dy = end.y - start.y;
        const length = Math.hypot(dx, dy);
        if (length < 0.001) return;
        const ux = dx / length;
        const uy = dy / length;
        g.lineStyle(width, color, 0.95);
        for (let dist = 0; dist < length; dist += dash + gap) {
            const next = Math.min(dist + dash, length);
            g.moveTo(start.x + ux * dist, start.y + uy * dist);
            g.lineTo(start.x + ux * next, start.y + uy * next);
        }
    }

    _drawTraces() {
        const model = pcbState.boardModel || {};
        for (const trace of model.traces || []) {
            const g = new PIXI.Graphics();
            const color = trace.layer === 'B.Cu' ? PCB_COLORS.bottomCopper : PCB_COLORS.topCopper;
            g.lineStyle({
                width: Math.max(trace.width || 0.254, 0.18),
                color,
                alpha: 1,
                cap: PIXI.LINE_CAP.ROUND,
                join: PIXI.LINE_JOIN.ROUND,
            });
            const points = trace.path || [];
            if (points.length < 2) continue;
            g.moveTo(points[0].x, points[0].y);
            for (let index = 1; index < points.length; index += 1) {
                g.lineTo(points[index].x, points[index].y);
            }
            this._traceLayer.addChild(g);
        }
        for (const via of model.vias || []) {
            const outer = new PIXI.Graphics();
            outer.beginFill(PCB_COLORS.viaCopper, 1);
            outer.drawCircle(via.x, via.y, Math.max(via.diameter || 0.6, 0.6) / 2);
            outer.endFill();
            outer.beginFill(PCB_COLORS.viaDrill, 1);
            outer.drawCircle(via.x, via.y, Math.max(via.drill || 0.3, 0.25) / 2);
            outer.endFill();
            this._traceLayer.addChild(outer);
        }
    }

    _drawFootprints() {
        const model = pcbState.boardModel || {};
        for (const component of model.components || []) {
            this._drawComponentGraphics(component);
            this._drawComponentPads(component);
            this._drawComponentTexts(component);
        }
    }

    _drawComponentGraphics(component) {
        const grouped = {
            'F.SilkS': [],
            'F.Fab': [],
            'F.CrtYd': [],
        };
        for (const item of component.graphics || []) {
            if (grouped[item.layer]) grouped[item.layer].push(item);
        }
        const drawGraphicItem = (item, color, alpha) => {
            const g = new PIXI.Graphics();
            g.lineStyle(item.width || 0.15, color, alpha);
            const points = this._componentGraphicPoints(component, item);
            if (points.length >= 2) {
                g.moveTo(points[0].x, points[0].y);
                for (let index = 1; index < points.length; index += 1) {
                    g.lineTo(points[index].x, points[index].y);
                }
            }
            this._footprintLayer.addChild(g);
        };
        for (const item of grouped['F.CrtYd']) drawGraphicItem(item, PCB_COLORS.courtyard, 0.8);
        for (const item of grouped['F.Fab']) drawGraphicItem(item, PCB_COLORS.fab, 0.9);
        for (const item of grouped['F.SilkS']) drawGraphicItem(item, PCB_COLORS.silkscreen, 1);
        if ((component.graphics || []).length === 0) {
            const bounds = getComponentBounds(component);
            const fallback = new PIXI.Graphics();
            fallback.lineStyle(0.14, PCB_COLORS.silkscreen, 0.8);
            fallback.drawRoundedRect(bounds.minX + 0.7, bounds.minY + 0.7, (bounds.maxX - bounds.minX) - 1.4, (bounds.maxY - bounds.minY) - 1.4, 0.6);
            this._footprintLayer.addChild(fallback);
        }
    }

    _componentGraphicPoints(component, item) {
        if (!item) return [];
        if (item.kind === 'fp_rect' && item.start && item.end) {
            return this._transformGraphicPoints(component, [
                item.start,
                { x: item.end.x, y: item.start.y },
                item.end,
                { x: item.start.x, y: item.end.y },
                item.start,
            ]);
        }
        if (item.kind === 'fp_poly') {
            const points = (item.points || []).slice();
            if (points.length) points.push(points[0]);
            return this._transformGraphicPoints(component, points);
        }
        if (item.kind === 'fp_circle' && item.center && item.end) {
            const radius = Math.hypot(item.end.x - item.center.x, item.end.y - item.center.y);
            const points = [];
            for (let step = 0; step <= 24; step += 1) {
                const angle = (Math.PI * 2 * step) / 24;
                points.push({
                    x: item.center.x + Math.cos(angle) * radius,
                    y: item.center.y + Math.sin(angle) * radius,
                });
            }
            return this._transformGraphicPoints(component, points);
        }
        if (item.kind === 'fp_arc' && item.start && item.mid && item.end) {
            return this._transformGraphicPoints(component, arcPoints(item.start, item.mid, item.end, 24));
        }
        const points = [];
        if (item.start) points.push(item.start);
        if (item.end) points.push(item.end);
        return this._transformGraphicPoints(component, points);
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

    _drawComponentPads(component) {
        for (const pad of component.pads || []) {
            const center = getComponentPadPosition(component, pad);
            const padRotation = (component.rotation || 0) + (pad.rotation || 0);
            const padWidth = pad.width || 1.0;
            const padHeight = pad.height || 1.0;
            const padGraphic = new PIXI.Graphics();
            const isBottom = (pad.layers || []).some((layer) => String(layer).startsWith('B.'));
            const isThrough = pad.type === 'thru_hole' || pad.type === 'np_thru_hole' || pad.type === 'tht' || pad.drill;
            const fill = isThrough ? PCB_COLORS.throughPad : (isBottom ? PCB_COLORS.smdBottom : PCB_COLORS.smdTop);
            const mask = new PIXI.Graphics();
            mask.beginFill(PCB_COLORS.maskPad, 0.92);
            drawPadShape(mask, center.x, center.y, padWidth + 0.34, padHeight + 0.34, pad.shape || 'rect', padRotation);
            mask.endFill();
            this._footprintLayer.addChild(mask);
            padGraphic.lineStyle(0.08, PCB_COLORS.copperEdge, 0.95);
            padGraphic.beginFill(fill, 1);
            drawPadShape(padGraphic, center.x, center.y, padWidth, padHeight, pad.shape || 'rect', padRotation);
            padGraphic.endFill();
            if (isThrough) {
                const hole = new PIXI.Graphics();
                hole.beginFill(PCB_COLORS.hole, 1);
                hole.drawCircle(center.x, center.y, Math.max(pad.drill || Math.min(padWidth, padHeight) * 0.5, 0.2) / 2);
                hole.endFill();
                this._footprintLayer.addChild(padGraphic, hole);
            } else {
                this._footprintLayer.addChild(padGraphic);
            }
            if (pad.number != null && Math.max(padWidth, padHeight) >= 0.75) {
                const label = this._makeText(pad.number, {
                    x: center.x,
                    y: center.y,
                    fontSize: 9,
                    fontWeight: '700',
                    fill: isThrough ? PCB_COLORS.hole : PCB_COLORS.silkscreen,
                    scale: 0.028,
                    rotation: component.rotation || 0,
                });
                this._textLayer.addChild(label);
            }
        }
    }

    _drawComponentTexts(component) {
        const bounds = getComponentBounds(component);
        const height = Math.max(bounds.maxY - bounds.minY, 2.5);
        const referenceText = component.ref || '';
        const valueText = component.value || compactFootprintName(component.footprint);
        const refOffset = rotatePoint(0, -Math.max(height / 2, 2.4), component.rotation || 0);
        const valueOffset = rotatePoint(0, Math.max(height / 2, 2.4), component.rotation || 0);
        const reference = this._makeText(referenceText, {
            x: component.x + refOffset.x,
            y: component.y + refOffset.y,
            fontSize: 15,
            fontWeight: '800',
            fill: PCB_COLORS.silkscreen,
            scale: 0.045,
            rotation: component.rotation || 0,
        });
        this._textLayer.addChild(reference);
        if (valueText && valueText !== referenceText) {
            const value = this._makeText(valueText, {
                x: component.x + valueOffset.x,
                y: component.y + valueOffset.y,
                fontSize: 10,
                fill: PCB_COLORS.textDim,
                scale: 0.037,
                rotation: component.rotation || 0,
            });
            this._textLayer.addChild(value);
        }
    }

    _drawOverlay() {
        this._drawControlStatus();
        if (pcbState.selectedComponentRef) {
            const component = (pcbState.boardModel.components || []).find((item) => item.ref === pcbState.selectedComponentRef);
            if (component) {
                const bounds = getComponentBounds(component);
                const g = new PIXI.Graphics();
                g.lineStyle(0.16, PCB_COLORS.selection, 0.95);
                g.beginFill(PCB_COLORS.selection, 0.05);
                g.drawRoundedRect(bounds.minX, bounds.minY, bounds.maxX - bounds.minX, bounds.maxY - bounds.minY, 0.8);
                g.endFill();
                this._overlayLayer.addChild(g);
            }
        }
        if (pcbState.hoveredPadKey) {
            this._drawPadHover(pcbState.hoveredPadKey);
        }
        if (pcbState.routePoints.length > 0) {
            const preview = new PIXI.Graphics();
            preview.lineStyle({
                width: Math.max(pcbState.routeWidth, 0.18),
                color: PCB_COLORS.routeGhost,
                alpha: 0.95,
                cap: PIXI.LINE_CAP.ROUND,
                join: PIXI.LINE_JOIN.ROUND,
            });
            const points = pcbState.routeCursor ? appendRoutePoint(pcbState.routePoints, pcbState.routeCursor) : pcbState.routePoints;
            preview.moveTo(points[0].x, points[0].y);
            for (let index = 1; index < points.length; index += 1) {
                preview.lineTo(points[index].x, points[index].y);
            }
            this._overlayLayer.addChild(preview);
        }
    }

    _drawControlStatus() {
        const bounds = this._visibleBounds();
        const parts = [];
        if (pcbState.mode === PCB_MODE.ROUTE) {
            parts.push(`Route ${pcbState.routeNetName || 'net'}`);
            parts.push('left click waypoint');
            parts.push('right click finish');
            parts.push('V via');
        } else if (pcbState.mode === PCB_MODE.DRAG_COMPONENT) {
            parts.push(`Move ${pcbState.dragComponentRef || 'component'}`);
            parts.push('release to save');
        } else if (pcbState.mode === PCB_MODE.PANNING) {
            parts.push('Pan');
        } else if (pcbState.hoveredPadKey) {
            parts.push('Click pad to route');
            parts.push(pcbState.hoveredPadKey);
        } else if (pcbState.selectedComponentRef) {
            parts.push(`Selected ${pcbState.selectedComponentRef}`);
        } else {
            parts.push('PCB editor');
            parts.push('drag board');
            parts.push('click pad to route');
        }
        const label = parts.join('  |  ');
        const boxW = Math.min(Math.max(label.length * 0.32, 13), Math.max(bounds.maxX - bounds.minX - 3, 13));
        const boxH = 1.65;
        const x = bounds.minX + 1.4;
        const y = bounds.minY + 1.4;
        const panel = new PIXI.Graphics();
        panel.lineStyle(0.08, PCB_COLORS.selection, 0.65);
        panel.beginFill(PCB_COLORS.hoverFill, 0.92);
        panel.drawRoundedRect(x, y, boxW, boxH, 0.32);
        panel.endFill();
        this._overlayLayer.addChild(panel);
        this._overlayLayer.addChild(this._makeText(label, {
            x: x + 0.46,
            y: y + boxH * 0.5,
            anchorX: 0,
            fontSize: 10,
            fontWeight: '700',
            fill: PCB_COLORS.silkscreen,
            scale: 0.036,
        }));
    }

    _findPadByKey(key) {
        for (const component of pcbState.boardModel.components || []) {
            for (const pad of component.pads || []) {
                if (buildPadKey(component, pad) === key) {
                    const center = getComponentPadPosition(component, pad);
                    return { component, pad, center };
                }
            }
        }
        return null;
    }

    _drawPadHover(key) {
        const hit = this._findPadByKey(key);
        if (!hit) return;
        const { component, pad, center } = hit;
        const netName = getNetNameForPad(pcbState.boardModel || {}, component.ref, pad.number);
        const halo = new PIXI.Graphics();
        halo.lineStyle(0.14, PCB_COLORS.selection, 1);
        halo.beginFill(PCB_COLORS.selection, 0.16);
        halo.drawCircle(center.x, center.y, Math.max(pad.width || 1, pad.height || 1, 1.2) * 0.7);
        halo.endFill();
        this._overlayLayer.addChild(halo);

        const label = `${component.ref}:${pad.number}  ${netName}`;
        const boxW = Math.max(7.5, label.length * 0.32);
        const boxH = 1.55;
        const boxX = center.x + 1.2;
        const boxY = center.y - 2.2;
        const tooltip = new PIXI.Graphics();
        tooltip.lineStyle(0.08, PCB_COLORS.selection, 0.9);
        tooltip.beginFill(PCB_COLORS.hoverFill, 0.96);
        tooltip.drawRoundedRect(boxX, boxY, boxW, boxH, 0.28);
        tooltip.endFill();
        tooltip.moveTo(center.x + 0.45, center.y - 0.45);
        tooltip.lineTo(boxX, boxY + boxH * 0.55);
        this._overlayLayer.addChild(tooltip);
        const text = this._makeText(label, {
            x: boxX + 0.42,
            y: boxY + boxH * 0.5,
            anchorX: 0,
            fontSize: 10,
            fontWeight: '700',
            fill: PCB_COLORS.silkscreen,
            scale: 0.036,
        });
        this._overlayLayer.addChild(text);
    }

    hitTestPad(screenX, screenY) {
        const world = this.screenToWorld(screenX, screenY);
        const tolerance = 0.9 / Math.max(pcbState.zoom, 0.4);
        for (const component of (pcbState.boardModel.components || []).slice().reverse()) {
            for (const pad of component.pads || []) {
                const center = getComponentPadPosition(component, pad);
                const rx = Math.max((pad.width || 1.0) / 2, tolerance);
                const ry = Math.max((pad.height || 1.0) / 2, tolerance);
                if (Math.abs(world.x - center.x) <= rx && Math.abs(world.y - center.y) <= ry) {
                    return {
                        component,
                        pad,
                        key: buildPadKey(component, pad),
                        x: center.x,
                        y: center.y,
                    };
                }
            }
        }
        return null;
    }

    hitTestComponent(screenX, screenY) {
        const world = this.screenToWorld(screenX, screenY);
        for (const component of (pcbState.boardModel.components || []).slice().reverse()) {
            const bounds = getComponentBounds(component);
            if (world.x >= bounds.minX && world.x <= bounds.maxX && world.y >= bounds.minY && world.y <= bounds.maxY) {
                return component;
            }
        }
        return null;
    }

    hitTestTrace(screenX, screenY) {
        const world = this.screenToWorld(screenX, screenY);
        let best = null;
        let bestDistance = Infinity;
        const tolerance = 0.9 / Math.max(pcbState.zoom, 0.4);
        for (const trace of pcbState.boardModel.traces || []) {
            const points = trace.path || [];
            for (let index = 0; index < points.length - 1; index += 1) {
                const hit = pointToSegmentDistance(world, points[index], points[index + 1]);
                if (hit.distance <= tolerance && hit.distance < bestDistance) {
                    bestDistance = hit.distance;
                    best = {
                        trace,
                        x: hit.point.x,
                        y: hit.point.y,
                    };
                }
            }
        }
        return best;
    }

    async saveBoardModel() {
        pcbState.boardModel = normalizeBoardModel(pcbState.boardModel);
        const response = await fetch('/api/save_board_model', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ board_model: pcbState.boardModel }),
        });
        if (!response.ok) {
            throw new Error(`save_board_model failed (${response.status})`);
        }
        await this.fetchRatsnest();
        dispatchBoardSync(true, { board_model: pcbState.boardModel, applied: 1, ignored: 0 });
    }

    async fetchRatsnest() {
        if (!pcbState.boardModel) return;
        const response = await fetch('/api/ratsnest', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(pcbState.boardModel),
        });
        if (!response.ok) {
            throw new Error(`ratsnest failed (${response.status})`);
        }
        pcbState.ratsnest = await response.json();
        dispatchBoardModelUpdated();
        this.refresh();
    }

    pushHistory(label, beforeModel, afterModel) {
        pcbState.undoStack.push({
            label,
            before: deepClone(beforeModel),
            after: deepClone(afterModel),
        });
        if (pcbState.undoStack.length > 100) {
            pcbState.undoStack.shift();
        }
        pcbState.redoStack = [];
    }

    async undo() {
        const action = pcbState.undoStack.pop();
        if (!action) return;
        pcbState.redoStack.push(action);
        pcbState.boardModel = deepClone(action.before);
        await this.saveBoardModel();
        this.refresh();
    }

    async redo() {
        const action = pcbState.redoStack.pop();
        if (!action) return;
        pcbState.undoStack.push(action);
        pcbState.boardModel = deepClone(action.after);
        await this.saveBoardModel();
        this.refresh();
    }
}

function arcPoints(start, mid, end, segments) {
    const x1 = start.x;
    const y1 = start.y;
    const x2 = mid.x;
    const y2 = mid.y;
    const x3 = end.x;
    const y3 = end.y;
    const determinant = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2));
    if (Math.abs(determinant) < 1e-6) {
        return [start, end];
    }
    const cx = (((x1 * x1 + y1 * y1) * (y2 - y3)) + ((x2 * x2 + y2 * y2) * (y3 - y1)) + ((x3 * x3 + y3 * y3) * (y1 - y2))) / determinant;
    const cy = (((x1 * x1 + y1 * y1) * (x3 - x2)) + ((x2 * x2 + y2 * y2) * (x1 - x3)) + ((x3 * x3 + y3 * y3) * (x2 - x1))) / determinant;
    const radius = Math.hypot(x1 - cx, y1 - cy);
    const startAngle = Math.atan2(y1 - cy, x1 - cx);
    const midAngle = Math.atan2(y2 - cy, x2 - cx);
    let endAngle = Math.atan2(y3 - cy, x3 - cx);
    let sweep = endAngle - startAngle;
    while (sweep <= -Math.PI) sweep += Math.PI * 2;
    while (sweep > Math.PI) sweep -= Math.PI * 2;
    const midDelta = midAngle - startAngle;
    const normalizedMid = ((midDelta % (Math.PI * 2)) + Math.PI * 2) % (Math.PI * 2);
    const normalizedSweep = ((sweep % (Math.PI * 2)) + Math.PI * 2) % (Math.PI * 2);
    if ((sweep > 0 && normalizedMid > normalizedSweep) || (sweep < 0 && normalizedMid < normalizedSweep)) {
        endAngle -= sweep > 0 ? Math.PI * 2 : -Math.PI * 2;
        sweep = endAngle - startAngle;
    }
    const points = [];
    for (let index = 0; index <= segments; index += 1) {
        const t = index / segments;
        const angle = startAngle + sweep * t;
        points.push({ x: cx + Math.cos(angle) * radius, y: cy + Math.sin(angle) * radius });
    }
    return points;
}

function drawPadShape(graphics, x, y, width, height, shape, rotation) {
    const w = Math.max(width, 0.2);
    const h = Math.max(height, 0.2);
    if (shape === 'circle') {
        graphics.drawCircle(x, y, Math.max(w, h) / 2);
        return;
    }
    if (shape === 'oval' || shape === 'roundrect') {
        const radius = Math.min(w, h) * 0.32;
        graphics.drawRoundedRect(x - w / 2, y - h / 2, w, h, radius);
        return;
    }
    if (!rotation) {
        graphics.drawRect(x - w / 2, y - h / 2, w, h);
        return;
    }
    const corners = [
        rotatePoint(-w / 2, -h / 2, rotation),
        rotatePoint(w / 2, -h / 2, rotation),
        rotatePoint(w / 2, h / 2, rotation),
        rotatePoint(-w / 2, h / 2, rotation),
    ];
    graphics.moveTo(x + corners[0].x, y + corners[0].y);
    for (let index = 1; index < corners.length; index += 1) {
        graphics.lineTo(x + corners[index].x, y + corners[index].y);
    }
    graphics.lineTo(x + corners[0].x, y + corners[0].y);
}

function dispatchBoardSync(ok, detail) {
    try {
        window.dispatchEvent(new CustomEvent('tscircuit:edit-sync', {
            detail: ok ? { ok: true, ...(detail || {}) } : { ok: false, ...(detail || {}) },
        }));
    } catch (_) {}
}

function dispatchBoardModelUpdated() {
    try {
        window.dispatchEvent(new CustomEvent('tscircuit:board-model-updated', {
            detail: { board_model: normalizeBoardModel(pcbState.boardModel) },
        }));
    } catch (_) {}
}

const pcbEditor = new PcbEditor('pcbCanvas');

function pcbGetCanvas() {
    return document.getElementById('pcbCanvas');
}

function pcbSetupCanvas() {
    pcbEditor.ensure();
    pcbEditor._resize();
}

function pcbLoadBoard(boardModel) {
    pcbEditor.load(boardModel);
    pcbEditor.fetchRatsnest().catch(() => {
        pcbState.ratsnest = {};
        pcbEditor.refresh();
    });
}

function pcbDraw() {
    pcbEditor.refresh();
}

function pcbDrawCurrent() {
    pcbEditor.refresh();
}

function pcbScreenToWorld(sx, sy) {
    return pcbEditor.screenToWorld(sx, sy);
}

function pcbResetView() {
    if (!pcbState.boardModel) return;
    pcbEditor._computeView();
    pcbEditor.refresh();
}

function pcbZoomBy(factor) {
    if (!pcbState.boardModel) return;
    pcbState.zoom = Math.min(Math.max(pcbState.zoom * factor, 0.1), 25);
    pcbEditor._applyCamera();
    pcbEditor.requestOverlayRefresh();
    pcbEditor.requestSettledRefresh();
}

function pcbSetCursor(cursor) {
    const canvas = pcbGetCanvas();
    if (canvas) canvas.style.cursor = cursor;
}

function pcbSetMode(mode) {
    pcbState.mode = mode;
    if (mode === PCB_MODE.ROUTE) {
        pcbSetCursor('crosshair');
    } else if (mode === PCB_MODE.PANNING) {
        pcbSetCursor('grabbing');
    } else if (mode === PCB_MODE.DRAG_COMPONENT) {
        pcbSetCursor('move');
    } else {
        pcbSetCursor(pcbState.hoveredPadKey ? 'crosshair' : 'grab');
    }
}

function pcbCancelDraw() {
    pcbSetMode(PCB_MODE.IDLE);
    pcbState.routeStartAnchor = null;
    pcbState.routeNetName = '';
    pcbState.routePoints = [];
    pcbState.routeVias = [];
    pcbState.routeCursor = null;
    pcbEditor.requestOverlayRefresh();
}

async function commitRouteToBoard(targetPad) {
    if (!pcbState.boardModel || pcbState.routePoints.length < 1 || !pcbState.routeStartAnchor) return;
    const before = deepClone(pcbState.boardModel);
    const completed = dedupePath(appendRoutePoint(pcbState.routePoints, targetPad));
    if (completed.length < 2) {
        pcbCancelDraw();
        return;
    }
    const trace = {
        net: pcbState.routeNetName || '_manual',
        layer: pcbState.routeLayer,
        width: pcbState.routeWidth,
        path: completed,
    };
    pcbState.boardModel.traces = pcbState.boardModel.traces || [];
    pcbState.boardModel.vias = pcbState.boardModel.vias || [];
    pcbState.boardModel.traces.push(trace);
    for (const via of pcbState.routeVias) {
        pcbState.boardModel.vias.push(via);
    }
    const after = deepClone(pcbState.boardModel);
    pcbCancelDraw();
    try {
        await pcbEditor.saveBoardModel();
        pcbEditor.pushHistory('route trace', before, after);
    } catch (error) {
        pcbState.boardModel = before;
        pcbEditor.refresh();
        dispatchBoardSync(false, { error: error.message, fallback_saved: false });
    }
}

function pcbHandleWheel(event) {
    event.preventDefault();
    const delta = event.deltaY > 0 ? 0.92 : 1.08;
    pcbZoomBy(delta);
}

function pcbHandleMouseDown(event) {
    if (!pcbState.boardModel) return;
    const padHit = pcbEditor.hitTestPad(event.clientX, event.clientY);
    const traceHit = pcbEditor.hitTestTrace(event.clientX, event.clientY);
    const compHit = pcbEditor.hitTestComponent(event.clientX, event.clientY);
    if (pcbState.mode === PCB_MODE.ROUTE) {
        if (event.button === 2) {
            if (pcbState.routePoints.length >= 2) {
                const finalTarget = pcbState.routeCursor || pcbState.routePoints[pcbState.routePoints.length - 1];
                commitRouteToBoard(finalTarget);
            } else {
                pcbCancelDraw();
            }
            return;
        }
        if (padHit && pcbState.routeStartAnchor && pcbState.routeStartAnchor.key !== padHit.key) {
            commitRouteToBoard({ x: padHit.x, y: padHit.y });
            return;
        }
        if (traceHit) {
            commitRouteToBoard({ x: traceHit.x, y: traceHit.y });
            return;
        }
        const world = pcbEditor.screenToWorld(event.clientX, event.clientY);
        pcbState.routePoints = appendRoutePoint(pcbState.routePoints, world);
        pcbEditor.requestOverlayRefresh();
        return;
    }
    if (event.button !== 0) return;
    if (padHit) {
        pcbState.routeStartAnchor = { kind: 'pad', key: padHit.key, x: padHit.x, y: padHit.y };
        pcbState.routeNetName = getNetNameForPad(pcbState.boardModel, padHit.component.ref, padHit.pad.number);
        pcbState.routeLayer = (padHit.pad.layers || []).some((layer) => String(layer).startsWith('B.')) ? 'B.Cu' : 'F.Cu';
        pcbState.routePoints = [routePoint(padHit)];
        pcbState.routeVias = [];
        pcbState.routeCursor = routePoint(padHit);
        pcbSetMode(PCB_MODE.ROUTE);
        pcbEditor.requestOverlayRefresh();
        return;
    }
    if (traceHit) {
        pcbState.routeStartAnchor = { kind: 'trace', key: `trace:${traceHit.x}:${traceHit.y}`, x: traceHit.x, y: traceHit.y };
        pcbState.routeNetName = traceHit.trace.net || '_manual';
        pcbState.routeLayer = traceHit.trace.layer || 'F.Cu';
        pcbState.routePoints = [routePoint(traceHit)];
        pcbState.routeVias = [];
        pcbState.routeCursor = routePoint(traceHit);
        pcbSetMode(PCB_MODE.ROUTE);
        pcbEditor.requestOverlayRefresh();
        return;
    }
    if (compHit) {
        pcbState.selectedComponentRef = compHit.ref;
        pcbState.dragComponentRef = compHit.ref;
        pcbState.dragOrigin = { x: compHit.x, y: compHit.y };
        pcbState.dragPointerStart = pcbEditor.screenToWorld(event.clientX, event.clientY);
        pcbSetMode(PCB_MODE.DRAG_COMPONENT);
        pcbEditor.requestOverlayRefresh();
        return;
    }
    pcbSetMode(PCB_MODE.PANNING);
    pcbState.dragPointerStart = { x: event.clientX, y: event.clientY };
}

function pcbHandleMouseMove(event) {
    if (!pcbState.boardModel) return;
    const world = pcbEditor.screenToWorld(event.clientX, event.clientY);
    const padHit = pcbEditor.hitTestPad(event.clientX, event.clientY);
    const prevHoveredPadKey = pcbState.hoveredPadKey;
    pcbState.hoveredPadKey = padHit ? padHit.key : null;
    pcbState.lastPointerWorld = world;
    if (pcbState.mode === PCB_MODE.PANNING && pcbState.dragPointerStart) {
        pcbState.panX += event.clientX - pcbState.dragPointerStart.x;
        pcbState.panY += event.clientY - pcbState.dragPointerStart.y;
        pcbState.dragPointerStart = { x: event.clientX, y: event.clientY };
        pcbEditor._applyCamera();
        return;
    }
    if (pcbState.mode === PCB_MODE.DRAG_COMPONENT && pcbState.dragComponentRef && pcbState.dragPointerStart) {
        const component = (pcbState.boardModel.components || []).find((item) => item.ref === pcbState.dragComponentRef);
        if (component) {
            component.x = snapToGrid(pcbState.dragOrigin.x + (world.x - pcbState.dragPointerStart.x));
            component.y = snapToGrid(pcbState.dragOrigin.y + (world.y - pcbState.dragPointerStart.y));
            pcbEditor.requestRefresh();
        }
        return;
    }
    if (pcbState.mode === PCB_MODE.ROUTE) {
        const traceHit = padHit ? null : pcbEditor.hitTestTrace(event.clientX, event.clientY);
        pcbState.routeCursor = padHit && pcbState.routeStartAnchor && pcbState.routeStartAnchor.key !== padHit.key
            ? { x: padHit.x, y: padHit.y }
            : traceHit
                ? { x: traceHit.x, y: traceHit.y }
            : routePoint(world);
        pcbEditor.requestOverlayRefresh();
        return;
    }
    pcbSetCursor(pcbState.hoveredPadKey ? 'crosshair' : 'grab');
    if (prevHoveredPadKey !== pcbState.hoveredPadKey) {
        pcbEditor.requestOverlayRefresh();
    }
}

function pcbHandleMouseUp(event) {
    if (pcbState.mode === PCB_MODE.PANNING) {
        pcbSetMode(PCB_MODE.IDLE);
        pcbEditor.requestSettledRefresh(20);
        return;
    }
    if (pcbState.mode === PCB_MODE.DRAG_COMPONENT && pcbState.dragComponentRef) {
        const after = deepClone(pcbState.boardModel);
        const component = (pcbState.boardModel.components || []).find((item) => item.ref === pcbState.dragComponentRef);
        const changed = component && (Math.abs(component.x - pcbState.dragOrigin.x) > 0.001 || Math.abs(component.y - pcbState.dragOrigin.y) > 0.001);
        pcbSetMode(PCB_MODE.IDLE);
        pcbState.dragComponentRef = null;
        pcbState.dragPointerStart = null;
        if (!changed) {
            pcbEditor.requestOverlayRefresh();
            return;
        }
        const before = deepClone(after);
        const original = before.components.find((item) => item.ref === component.ref);
        if (original) {
            original.x = pcbState.dragOrigin.x;
            original.y = pcbState.dragOrigin.y;
        }
        pcbEditor.saveBoardModel().then(() => {
            pcbEditor.pushHistory('move component', before, after);
        }).catch((error) => {
            pcbState.boardModel = before;
            pcbEditor.refresh();
            dispatchBoardSync(false, { error: error.message, fallback_saved: false });
        });
        return;
    }
    if (event && event.button === 2) {
        pcbCancelDraw();
    }
}

function pcbRefreshRatsnest() {
    pcbEditor.fetchRatsnest().catch(() => {});
}

function pcbHandleKeyDown(event) {
    if (event.key === 'Escape') {
        pcbCancelDraw();
        return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z' && event.shiftKey) {
        event.preventDefault();
        pcbEditor.redo().catch((error) => dispatchBoardSync(false, { error: error.message, fallback_saved: false }));
        return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
        event.preventDefault();
        pcbEditor.undo().catch((error) => dispatchBoardSync(false, { error: error.message, fallback_saved: false }));
        return;
    }
    if (event.key.toLowerCase() === 'v' && pcbState.mode === PCB_MODE.ROUTE && pcbState.routeCursor) {
        const via = {
            x: pcbState.routeCursor.x,
            y: pcbState.routeCursor.y,
            drill: 0.3,
            diameter: 0.7,
            layers: ['F.Cu', 'B.Cu'],
            net: pcbState.routeNetName || '',
        };
        pcbState.routeVias.push(via);
        pcbState.routePoints = appendRoutePoint(pcbState.routePoints, pcbState.routeCursor);
        pcbState.routeLayer = pcbState.routeLayer === 'F.Cu' ? 'B.Cu' : 'F.Cu';
        pcbEditor.refresh();
    }
}

window.pcbLoadBoard = pcbLoadBoard;
window.pcbDraw = pcbDraw;
window.pcbDrawCurrent = pcbDrawCurrent;
window.pcbSetupCanvas = pcbSetupCanvas;
window.pcbScreenToWorld = pcbScreenToWorld;
window.pcbResetView = pcbResetView;
window.pcbZoomBy = pcbZoomBy;
window.pcbHandleWheel = pcbHandleWheel;
window.pcbHandleMouseDown = pcbHandleMouseDown;
window.pcbHandleMouseMove = pcbHandleMouseMove;
window.pcbHandleMouseUp = pcbHandleMouseUp;
window.pcbHandleKeyDown = pcbHandleKeyDown;
window.pcbCancelDraw = pcbCancelDraw;
window.pcbRefreshRatsnest = pcbRefreshRatsnest;
window.pcbFetchRatsnest = pcbRefreshRatsnest;
window.pcbState = pcbState;
window.PCB_MODE = PCB_MODE;
window.__PCB_TEST__ = {
    snapToGrid,
    normalizePoint,
    normalizeBoardModel,
    toFiniteNumber,
    compactFootprintName,
    modelBounds,
    rotatePoint,
    routePoint,
    appendRoutePoint,
    dedupePath,
    getComponentPadPosition,
    getComponentBounds,
    arcPoints,
};
