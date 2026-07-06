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
        this._drillLayer = null;
        this._overlayLayer = null;
        this._textLayer = null;
        this._resizeHandler = () => this._resize();
        this._refreshFrame = null;
        this._overlayFrame = null;
        this._settleRefreshTimer = null;
        this._layerKeys = ['grid', 'outline', 'airwire', 'trace', 'footprint', 'drill', 'text', 'overlay'];
        this._dirtyLayers = new Set(this._layerKeys);
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
            resolution: window.devicePixelRatio || 1,
            backgroundColor: PCB_COLORS.background,
            backgroundAlpha: pcbState.renderMode === 'overlay' ? 0 : 1,
        });
        this._canvas.style.background = pcbState.renderMode === 'overlay' ? 'transparent' : '#0b1116';
        this._world = new PIXI.Container();
        this._gridLayer = new PIXI.Container();
        this._outlineLayer = new PIXI.Container();
        this._airwireLayer = new PIXI.Container();
        this._traceLayer = new PIXI.Container();
        this._footprintLayer = new PIXI.Container();
        this._drillLayer = new PIXI.Container();
        this._textLayer = new PIXI.Container();
        this._overlayLayer = new PIXI.Container();
        this._world.addChild(
            this._gridLayer,
            this._outlineLayer,
            this._airwireLayer,
            this._traceLayer,
            this._footprintLayer,
            this._drillLayer,
            this._textLayer,
            this._overlayLayer
        );
        this._app.stage.addChild(this._world);
        window.addEventListener('resize', this._resizeHandler);
        this.markAllDirty();
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
        while (layer.children.length > 0) {
            const child = layer.children[0];
            layer.removeChild(child);
            if (child.destroy) {
                try { child.destroy({ children: true }); } catch (e) {}
            }
        }
    }

    _layerForKey(key) {
        return {
            grid: this._gridLayer,
            outline: this._outlineLayer,
            airwire: this._airwireLayer,
            trace: this._traceLayer,
            footprint: this._footprintLayer,
            drill: this._drillLayer,
            text: this._textLayer,
            overlay: this._overlayLayer,
        }[key] || null;
    }

    markDirty(...keys) {
        for (const key of keys) {
            if (this._layerKeys.includes(key)) this._dirtyLayers.add(key);
        }
    }

    markAllDirty() {
        for (const key of this._layerKeys) this._dirtyLayers.add(key);
    }

    _redrawDirtyLayers() {
        const drawPlan = [
            ['grid', () => this._drawGrid()],
            ['outline', () => this._drawBoardOutline()],
            ['airwire', () => this._drawAirwires()],
            ['trace', () => this._drawTraces()],
            ['footprint', () => this._drawFootprints()],
            ['drill', () => {}],
            ['text', () => this._drawTextLayer()],
            ['overlay', () => this._drawOverlay()],
        ];
        for (const [key, draw] of drawPlan) {
            if (!this._dirtyLayers.has(key)) continue;
            this._clearLayer(this._layerForKey(key));
            draw();
            this._dirtyLayers.delete(key);
        }
    }

    load(boardModel) {
        this.ensure();
        if (!this._app) return;
        pcbState.boardModel = normalizeBoardModel(boardModel || { components: [], traces: [], vias: [], nets: [] });
        pcbState.ratsnest = boardModel && typeof boardModel === 'object' && boardModel.ratsnest
            ? boardModel.ratsnest
            : {};
        pcbState.activeTool = PCB_TOOL.PAN;
        pcbState.selectedComponentRef = null;
        pcbState.hoveredPadKey = null;
        pcbState.hoveredComponentRef = null;
        pcbState.hoveredViaIndex = null;
        pcbState.dragViaIndex = null;
        pcbState.routeStartAnchor = null;
        pcbState.routeNetName = '';
        pcbState.routePoints = [];
        pcbState.routeVias = [];
        pcbState.routeCursor = null;
        pcbState.pointerDownScreen = null;
        pcbState.pointerDownWorld = null;
        pcbState.pointerDragMoved = false;
        this._computeView();
        this.markAllDirty();
        this.refresh();
        dispatchPcbInteractionUpdated();
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
        this.markDirty('grid', 'overlay');
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
        this.markDirty('grid', 'text', 'overlay');
    }

    _applyCamera() {
        if (!this._world) return;
        const scale = pcbState.baseScale * pcbState.zoom;
        this._world.scale.set(scale, -scale);
        this._world.position.set(
            pcbState.cx + pcbState.panX - pcbState.midX * scale,
            pcbState.cy + pcbState.panY + pcbState.midY * scale
        );
        dispatchPcbViewChanged();
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
            if (pcbState.renderMode === 'overlay') {
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
                this._drawOverlay();
                return;
            }
            this._redrawDirtyLayers();
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
            this.markDirty('overlay');
            this._redrawDirtyLayers();
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
            this.markDirty('grid', 'text', 'overlay');
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
            resolution: 4,
        });
        text.resolution = 4; // High-resolution render scale for crystal clear zoomed text
        text.anchor.set(options.anchorX ?? 0.5, options.anchorY ?? 0.5);
        const scale = options.scale || 0.045;
        text.scale.set(scale, -scale);
        
        const tx = Number(options.x);
        const ty = Number(options.y);
        text.x = Number.isFinite(tx) ? tx : 0;
        text.y = Number.isFinite(ty) ? ty : 0;
        
        let rot = Number(options.rotation) || 0;
        if (!Number.isFinite(rot)) rot = 0;
        text.rotation = -rot * Math.PI / 180;
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
                this._drawDashedLine(g, { x: edge.x1, y: edge.y1 }, { x: edge.x2, y: edge.y2 }, 1.25, 0.6, PCB_COLORS.airwireDim, 0.18);
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
        const top = new PIXI.Graphics();
        const bottom = new PIXI.Graphics();
        for (const trace of model.traces || []) {
            const g = isBottomCopperLayer(trace.layer) ? bottom : top;
            const points = trace.path || [];
            if (points.length < 2) continue;
            g.lineStyle({
                width: Math.max(trace.width || 0.254, 0.18),
                color: copperColorForLayer(trace.layer),
                alpha: 0.96,
                cap: PIXI.LINE_CAP.ROUND,
                join: PIXI.LINE_JOIN.ROUND,
            });
            g.moveTo(points[0].x, points[0].y);
            for (let index = 1; index < points.length; index += 1) {
                g.lineTo(points[index].x, points[index].y);
            }
        }
        const vias = new PIXI.Graphics();
        for (const via of model.vias || []) {
            vias.beginFill(PCB_COLORS.viaCopper, 1);
            vias.drawCircle(via.x, via.y, Math.max(via.diameter || 0.6, 0.6) / 2);
            vias.endFill();
            vias.beginFill(PCB_COLORS.viaDrill, 1);
            vias.drawCircle(via.x, via.y, Math.max(via.drill || 0.3, 0.25) / 2);
            vias.endFill();
        }
        this._traceLayer.addChild(bottom, top, vias);
    }

    _drawFootprints() {
        const model = pcbState.boardModel || {};
        // Clear drillLayer alongside footprintLayer since they are always redrawn together
        this._clearLayer(this._drillLayer);
        for (const component of model.components || []) {
            try {
                this._drawComponentPads(component);
            } catch (e) {
                console.error("Error drawing pads for component:", component.ref, e);
                try {
                    this._textLayer.addChild(this._makeText(`ERR_PAD ${component.ref}: ${e.message}`, {
                        x: component.x,
                        y: component.y - 1.2,
                        fontSize: 10,
                        fill: 0xff3333,
                        scale: 0.04
                    }));
                } catch (_) {}
            }
            try {
                this._drawComponentGraphics(component);
            } catch (e) {
                console.error("Error drawing graphics for component:", component.ref, e);
                try {
                    this._textLayer.addChild(this._makeText(`ERR_GFX ${component.ref}: ${e.message}`, {
                        x: component.x,
                        y: component.y + 1.2,
                        fontSize: 10,
                        fill: 0xff3333,
                        scale: 0.04
                    }));
                } catch (_) {}
            }
        }
    }

    _drawTextLayer() {
        this._drawBoardTitle();
        const model = pcbState.boardModel || {};
        for (const component of model.components || []) {
            try {
                this._drawComponentPadLabels(component);
            } catch (e) {
                console.error("Error drawing pad labels for component:", component.ref, e);
            }
            try {
                this._drawComponentTexts(component);
            } catch (e) {
                console.error("Error drawing texts for component:", component.ref, e);
            }
        }
    }

    _drawComponentGraphics(component) {
        const geometry = { 'F.SilkS': [], 'F.Fab': [], 'F.CrtYd': [], 'B.SilkS': [], 'B.Fab': [], 'B.CrtYd': [] };
        const textItems = { 'F.SilkS': [], 'F.Fab': [], 'B.SilkS': [], 'B.Fab': [] };
        for (const item of component.graphics || []) {
            if (item.kind === 'property') {
                if (item.hidden) continue;
                if (textItems[item.layer]) textItems[item.layer].push(item);
            } else {
                if (geometry[item.layer]) geometry[item.layer].push(item);
            }
        }
        const drawGraphicItem = (item, color, fillColor, alpha) => {
            const g = new PIXI.Graphics();
            const isFilled = item.fill === 'solid' || item.fill === 'yes';
            const isClosed = item.kind === 'fp_rect' || item.kind === 'fp_circle' || item.kind === 'fp_poly';
            const points = this._componentGraphicPoints(component, item);
            if (points.length < 2) return;
            /* Fill closed shapes when KiCad data says fill:solid, or for
               fab-layer shapes that are typically filled (rect, circle). */
            if (isFilled && isClosed && fillColor != null) {
                g.beginFill(fillColor, alpha * 0.35);
                g.moveTo(points[0].x, points[0].y);
                for (let index = 1; index < points.length; index += 1) {
                    g.lineTo(points[index].x, points[index].y);
                }
                g.closePath();
                g.endFill();
            }
            /* Stroke */
            g.lineStyle(item.width || 0.15, color, alpha);
            g.moveTo(points[0].x, points[0].y);
            for (let index = 1; index < points.length; index += 1) {
                g.lineTo(points[index].x, points[index].y);
            }
            this._footprintLayer.addChild(g);
        };
        const drawCourtyardItem = (item, color, alpha) => {
            /* Courtyard uses dashed lines for authentic KiCad look */
            const g = new PIXI.Graphics();
            const points = this._componentGraphicPoints(component, item);
            if (points.length < 2) return;
            g.lineStyle(0.06, color, alpha * 0.7);
            for (let index = 0; index < points.length - 1; index += 1) {
                this._drawDashedLine(g, points[index], points[index + 1], 0.6, 0.35, color, 0.06);
            }
            this._footprintLayer.addChild(g);
        };
        const drawTextItem = (item, color) => {
            const pos = this._transformGraphicPoints(component, [{ x: item.x, y: item.y }])[0];
            const fontSize = Math.max(item.size || 1.0, 0.5) * 14;
            const text = this._makeText(item.text || '', {
                x: pos.x,
                y: pos.y,
                fontSize: fontSize,
                fontWeight: '500',
                fill: color,
                scale: 0.035,
                rotation: (component.rotation || 0) + (item.rotation || 0),
            });
            this._footprintLayer.addChild(text);
        };
        for (const item of geometry['F.CrtYd'] || []) drawCourtyardItem(item, PCB_COLORS.courtyard, 0.8);
        for (const item of geometry['B.CrtYd'] || []) drawCourtyardItem(item, PCB_COLORS.courtyard, 0.5);
        for (const item of geometry['F.Fab'] || []) drawGraphicItem(item, PCB_COLORS.fab, PCB_COLORS.fabFill, 0.9);
        for (const item of geometry['B.Fab'] || []) drawGraphicItem(item, PCB_COLORS.fab, PCB_COLORS.fabFill, 0.55);
        for (const item of geometry['F.SilkS'] || []) drawGraphicItem(item, PCB_COLORS.silkscreen, PCB_COLORS.silkscreenFill, 1);
        for (const item of geometry['B.SilkS'] || []) drawGraphicItem(item, PCB_COLORS.silkscreen, PCB_COLORS.silkscreenFill, 0.6);
        for (const item of textItems['F.Fab'] || []) drawTextItem(item, PCB_COLORS.fab);
        for (const item of textItems['F.SilkS'] || []) drawTextItem(item, PCB_COLORS.silkscreen);
        for (const item of textItems['B.Fab'] || []) drawTextItem(item, PCB_COLORS.fab);
        for (const item of textItems['B.SilkS'] || []) drawTextItem(item, PCB_COLORS.silkscreen);
        const hasGeometry = Object.values(geometry).some(arr => arr && arr.length > 0);
        const hasText = Object.values(textItems).some(arr => arr && arr.length > 0);
        if (!hasGeometry && !hasText) {
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
            const tx = component.x + rotated.x;
            const ty = component.y + rotated.y;
            return {
                x: Number.isFinite(tx) ? tx : component.x,
                y: Number.isFinite(ty) ? ty : component.y,
            };
        });
    }

    _drawComponentPads(component) {
        const pads = (component.pads || []).slice().sort((a, b) => {
            const areaA = (a.width || 0) * (a.height || 0);
            const areaB = (b.width || 0) * (b.height || 0);
            return areaB - areaA; // Sort descending (largest first)
        });
        if (pads.length === 0) return;

        const _isExposedPad = (pad) => {
            if (pad.type !== 'smd') return false;
            const area = (pad.width || 1) * (pad.height || 1);
            if (area < 4) return false;
            const otherPads = pads.filter(p => p !== pad);
            if (otherPads.length === 0) return false;
            const avgArea = otherPads.reduce((sum, p) => sum + (p.width || 1) * (p.height || 1), 0) / otherPads.length;
            return area > avgArea * 3;
        };

        const g = new PIXI.Graphics();

        /* Pass 1: all mask expansions (bottom) */
        for (const pad of pads) {
            const center = getComponentPadPosition(component, pad);
            const padRotation = (component.rotation || 0) + (pad.rotation || 0);
            const padWidth = pad.width || 1.0;
            const padHeight = pad.height || 1.0;
            g.beginFill(PCB_COLORS.maskPad, 0.92);
            drawPadShape(g, center.x, center.y, padWidth + 0.34, padHeight + 0.34, pad.shape || 'rect', padRotation, pad.roundrect_rratio);
            g.endFill();
        }

        /* Pass 2: all copper pads (middle) */
        for (const pad of pads) {
            const center = getComponentPadPosition(component, pad);
            const padRotation = (component.rotation || 0) + (pad.rotation || 0);
            const padWidth = pad.width || 1.0;
            const padHeight = pad.height || 1.0;
            const isBottom = (pad.layers || []).some((layer) => isBottomCopperLayer(layer));
            const isThrough = pad.type === 'thru_hole' || pad.type === 'np_thru_hole' || pad.type === 'tht' || pad.drill;
            const isExposed = _isExposedPad(pad);
            let fill;
            if (isThrough) {
                fill = PCB_COLORS.throughPad;
            } else if (isExposed) {
                fill = PCB_COLORS.exposedPad || PCB_COLORS.smdTop;
            } else if (isBottom) {
                fill = PCB_COLORS.smdBottom;
            } else {
                fill = PCB_COLORS.smdTop;
            }
            g.lineStyle(0.08, PCB_COLORS.copperEdge, 0.95);
            g.beginFill(fill, 1);
            drawPadShape(g, center.x, center.y, padWidth, padHeight, pad.shape || 'rect', padRotation, pad.roundrect_rratio);
            g.endFill();
        }

        this._footprintLayer.addChild(g);

        /* Pass 3: drill holes drawn into dedicated drillLayer (always on top of all copper) */
        const drillG = new PIXI.Graphics();
        for (const pad of pads) {
            const center = getComponentPadPosition(component, pad);
            const isThrough = pad.type === 'thru_hole' || pad.type === 'np_thru_hole' || pad.type === 'tht' || pad.drill;
            if (isThrough) {
                const padWidth = pad.width || 1.0;
                const padHeight = pad.height || 1.0;
                const drillDia = pad.drill || Math.min(padWidth, padHeight) * 0.5;
                drillG.lineStyle(0);
                drillG.beginFill(PCB_COLORS.hole, 1);
                drillG.drawCircle(center.x, center.y, Math.max(drillDia, 0.2) / 2);
                drillG.endFill();
            }
        }
        this._drillLayer.addChild(drillG);

        /* Pass 4: exposed pad thermal relief pattern */
        for (const pad of pads) {
            if (!_isExposedPad(pad)) continue;
            const center = getComponentPadPosition(component, pad);
            const padWidth = pad.width || 1.0;
            const padHeight = pad.height || 1.0;
            const gridCount = Math.max(Math.round(Math.sqrt(padWidth * padHeight) / 1.1), 2);
            const stepX = padWidth / (gridCount + 1);
            const stepY = padHeight / (gridCount + 1);
            const dotRadius = Math.min(stepX, stepY) * 0.22;
            const thermal = new PIXI.Graphics();
            thermal.beginFill(PCB_COLORS.hole, 0.6);
            for (let ix = 1; ix <= gridCount; ix++) {
                for (let iy = 1; iy <= gridCount; iy++) {
                    const localX = -padWidth / 2 + ix * stepX;
                    const localY = -padHeight / 2 + iy * stepY;
                    const rotated = rotatePoint(localX, localY, (component.rotation || 0) + (pad.rotation || 0));
                    thermal.drawCircle(center.x + rotated.x, center.y + rotated.y, dotRadius);
                }
            }
            thermal.endFill();
            this._footprintLayer.addChild(thermal);
        }

        /* Pass 5: pin-1 marker (small triangle) */
        if (pin1) {
            const pin1Center = getComponentPadPosition(component, pin1);
            const markerSize = Math.max(Math.min(pin1.width || 1, pin1.height || 1) * 0.55, 0.4);
            const offsetDist = Math.max(pin1.width || 1, pin1.height || 1) * 0.7 + 0.3;
            const offsetLocal = { x: -offsetDist, y: -offsetDist };
            const offsetWorld = rotatePoint(offsetLocal.x, offsetLocal.y, component.rotation || 0);
            const mx = pin1Center.x + offsetWorld.x;
            const my = pin1Center.y + offsetWorld.y;
            const marker = new PIXI.Graphics();
            marker.beginFill(PCB_COLORS.pin1Marker || PCB_COLORS.silkscreen, 0.95);
            const t0 = rotatePoint(0, -markerSize * 0.6, component.rotation || 0);
            const t1 = rotatePoint(-markerSize * 0.5, markerSize * 0.35, component.rotation || 0);
            const t2 = rotatePoint(markerSize * 0.5, markerSize * 0.35, component.rotation || 0);
            marker.moveTo(mx + t0.x, my + t0.y);
            marker.lineTo(mx + t1.x, my + t1.y);
            marker.lineTo(mx + t2.x, my + t2.y);
            marker.closePath();
            marker.endFill();
            this._footprintLayer.addChild(marker);
        }
    }

    _drawComponentPadLabels(component) {
        for (const pad of component.pads || []) {
            const padWidth = pad.width || 1.0;
            const padHeight = pad.height || 1.0;
            const padMaxDim = Math.max(padWidth, padHeight);
            
            /* Show labels at lower zoom for large pads, higher zoom for small pads */
            const minZoom = padMaxDim >= 2.5 ? 0.4 : (padMaxDim >= 1.2 ? 1.0 : 1.8);
            if (pad.number == null || padMaxDim < 0.4 || pcbState.zoom < minZoom) continue;

            const center = getComponentPadPosition(component, pad);
            const padRotation = (component.rotation || 0) + (pad.rotation || 0);

            let max_width = padWidth;
            let max_font_size = padHeight;
            let text_rotated = false;

            // Rotate text 90° for tall-narrow pads to match KiCanvas
            if (padWidth < padHeight * 0.95) {
                text_rotated = true;
                max_width = padHeight;
                max_font_size = padWidth;
            }

            max_font_size = Math.min(max_font_size, 1.8); // Cap max size in mm

            const netName = getNetNameForPad(pcbState.boardModel, component.ref, pad.number);
            const hasNet = netName && netName !== '_manual' && netName !== '';
            const padNumText = pad.number !== undefined ? String(pad.number) : '';

            let labelFontSize = max_font_size * 10;
            let scale = 0.04;

            let y_offset_pad_net = 0;
            let y_offset_pad_num = 0;

            if (hasNet && padNumText !== "") {
                labelFontSize = (max_font_size / 2.8) * 10;
                y_offset_pad_net = max_font_size / 3.4;   // Offset down in pad space
                y_offset_pad_num = -max_font_size / 3.4;  // Offset up in pad space
            }

            // Calculate auto-scale factor to prevent text clipping
            const maxTextLength = Math.max(padNumText.length, hasNet ? netName.length : 0, 3);
            const widthScale = max_width / (maxTextLength * 0.45);
            const finalScale = Math.min(scale, widthScale * 0.08);

            // Apply upright text rotation: clamp final text rotation angle to (-90, 90]
            let textRot = padRotation + (text_rotated ? 90 : 0);
            while (textRot > 90) textRot -= 180;
            while (textRot <= -90) textRot += 180;

            if (padNumText !== "") {
                // Number local offset is along pad-local y-axis
                const localOffset = { x: 0, y: y_offset_pad_num };
                const rotated = rotatePoint(localOffset.x, localOffset.y, padRotation);
                const label = this._makeText(padNumText, {
                    x: center.x + rotated.x,
                    y: center.y + rotated.y,
                    fontSize: labelFontSize,
                    fontWeight: '700',
                    fill: PCB_COLORS.padNumber,
                    scale: finalScale,
                    rotation: textRot,
                });
                this._textLayer.addChild(label);
            }

            if (hasNet) {
                // Net name local offset is along pad-local y-axis
                const localOffset = { x: 0, y: y_offset_pad_net };
                const rotated = rotatePoint(localOffset.x, localOffset.y, padRotation);
                const label = this._makeText(netName, {
                    x: center.x + rotated.x,
                    y: center.y + rotated.y,
                    fontSize: labelFontSize,
                    fontWeight: '700',
                    fill: PCB_COLORS.padNumber,
                    scale: finalScale,
                    rotation: textRot,
                });
                this._textLayer.addChild(label);
            }
        }
    }

    _drawComponentTexts(component) {
        const graphics = component.graphics || [];
        const hasRefText = graphics.some(g => g.kind === 'property' && g.name === 'Reference' && !g.hidden);
        const hasValueText = graphics.some(g => g.kind === 'property' && g.name === 'Value' && !g.hidden);
        const bounds = getComponentBounds(component);
        const height = Math.max(bounds.maxY - bounds.minY, 2.5);

        // Compute keep-upright rotation angle
        let textRot = component.rotation || 0;
        while (textRot > 90) textRot -= 180;
        while (textRot <= -90) textRot += 180;

        if (!hasRefText) {
            const refOffset = rotatePoint(0, -Math.max(height / 2, 2.4), component.rotation || 0);
            const reference = this._makeText(component.ref || '', {
                x: component.x + refOffset.x,
                y: component.y + refOffset.y,
                fontSize: 15,
                fontWeight: '800',
                fill: PCB_COLORS.silkscreen,
                scale: 0.045,
                rotation: textRot,
            });
            this._textLayer.addChild(reference);
        }
        if (!hasValueText && pcbState.zoom >= 0.9) {
            const valueText = component.value || compactFootprintName(component.footprint);
            if (valueText && valueText !== component.ref) {
                const valueOffset = rotatePoint(0, Math.max(height / 2, 2.4), component.rotation || 0);
                const value = this._makeText(valueText, {
                    x: component.x + valueOffset.x,
                    y: component.y + valueOffset.y,
                    fontSize: 10,
                    fill: PCB_COLORS.textDim,
                    scale: 0.037,
                    rotation: textRot,
                });
                this._textLayer.addChild(value);
            }
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
        if (pcbState.hoveredViaIndex != null) {
            this._drawViaHover(pcbState.hoveredViaIndex);
        }
        if (pcbState.routePoints.length > 0) {
            const preview = new PIXI.Graphics();
            preview.lineStyle({
                width: Math.max(pcbState.routeWidth, 0.18),
                color: copperColorForLayer(pcbState.routeLayer),
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
        if (pcbState.activeTool === PCB_TOOL.VIA && pcbState.lastPointerWorld) {
            const via = buildViaDraft(pcbState.lastPointerWorld);
            const preview = new PIXI.Graphics();
            preview.lineStyle(0.08, PCB_COLORS.selection, 0.95);
            preview.beginFill(PCB_COLORS.viaCopper, 0.92);
            preview.drawCircle(via.x, via.y, via.diameter / 2);
            preview.endFill();
            preview.beginFill(PCB_COLORS.viaDrill, 1);
            preview.drawCircle(via.x, via.y, via.drill / 2);
            preview.endFill();
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
        } else if (pcbState.activeTool === PCB_TOOL.VIA) {
            parts.push('Via tool');
            parts.push('click to place');
        } else if (pcbState.mode === PCB_MODE.DRAG_COMPONENT) {
            parts.push(`Move ${pcbState.dragComponentRef || 'component'}`);
            parts.push('release to save');
        } else if (pcbState.mode === PCB_MODE.PANNING) {
            parts.push('Pan');
        } else if (pcbState.activeTool === PCB_TOOL.ROUTE) {
            parts.push('Route tool');
            parts.push('click pad or trace');
        } else if (pcbState.activeTool === PCB_TOOL.SELECT) {
            parts.push('Select tool');
            parts.push('drag component');
        } else if (pcbState.hoveredPadKey) {
            parts.push('Pan tool');
            parts.push(pcbState.hoveredPadKey);
        } else if (pcbState.selectedComponentRef) {
            parts.push(`Selected ${pcbState.selectedComponentRef}`);
        } else {
            parts.push('PCB editor');
            parts.push(pcbState.activeTool === PCB_TOOL.PAN ? 'drag board' : `${pcbState.activeTool} tool`);
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
            const pads = component.pads || [];
            for (let i = 0; i < pads.length; i++) {
                if (buildPadKey(component, pads[i], i) === key) {
                    const center = getComponentPadPosition(component, pads[i]);
                    return { component, pad: pads[i], center };
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

    _drawViaHover(index) {
        const via = (pcbState.boardModel.vias || [])[index];
        if (!via) return;
        const halo = new PIXI.Graphics();
        halo.lineStyle(0.14, PCB_COLORS.selection, 1);
        halo.beginFill(PCB_COLORS.selection, 0.12);
        halo.drawCircle(via.x, via.y, Math.max(via.diameter || 0.7, 0.9));
        halo.endFill();
        this._overlayLayer.addChild(halo);
    }

    hitTestPad(screenX, screenY) {
        const world = this.screenToWorld(screenX, screenY);
        const tolerance = 0.9 / Math.max(pcbState.zoom, 0.4);
        for (const component of (pcbState.boardModel.components || []).slice().reverse()) {
            const pads = component.pads || [];
            for (let i = 0; i < pads.length; i++) {
                const pad = pads[i];
                const center = getComponentPadPosition(component, pad);
                const rx = Math.max((pad.width || 1.0) / 2, tolerance);
                const ry = Math.max((pad.height || 1.0) / 2, tolerance);
                if (Math.abs(world.x - center.x) <= rx && Math.abs(world.y - center.y) <= ry) {
                    return {
                        component,
                        pad,
                        key: buildPadKey(component, pad, i),
                        x: center.x,
                        y: center.y,
                        noSnap: true,
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
                        noSnap: true,
                    };
                }
            }
        }
        return best;
    }

    hitTestVia(screenX, screenY) {
        const world = this.screenToWorld(screenX, screenY);
        const vias = pcbState.boardModel.vias || [];
        let best = null;
        let bestDistance = Infinity;
        const tolerance = 1.1 / Math.max(pcbState.zoom, 0.4);
        for (let index = 0; index < vias.length; index += 1) {
            const via = vias[index];
            const distance = Math.hypot(world.x - via.x, world.y - via.y);
            const radius = Math.max(via.diameter || 0.6, 0.6) / 2;
            if (distance <= Math.max(radius, tolerance) && distance < bestDistance) {
                bestDistance = distance;
                best = {
                    index,
                    via,
                    x: via.x,
                    y: via.y,
                    noSnap: true,
                };
            }
        }
        return best;
    }

    async saveBoardModel() {
        pcbState.boardModel = normalizeBoardModel(pcbState.boardModel);
        this.markDirty('outline', 'trace', 'footprint', 'text', 'overlay');
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
        this.markDirty('airwire', 'overlay');
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
        this.markAllDirty();
        this.refresh();
    }

    async redo() {
        const action = pcbState.redoStack.pop();
        if (!action) return;
        pcbState.undoStack.push(action);
        pcbState.boardModel = deepClone(action.after);
        await this.saveBoardModel();
        this.markAllDirty();
        this.refresh();
    }
}
