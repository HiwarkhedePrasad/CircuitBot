(function initKiCBoardPainter(globalThis) {
    if (globalThis.KiCBoard) return;

    const { Color, Vec2, BBox, Mat3, Angle } = KiCMath;
    const { Circle, Polyline, Polygon } = KiCVec;

    function toColor(hexOrCss, fallback) {
        if (hexOrCss == null) return fallback ? Color.from_css(fallback) : Color.transparent_black;
        if (hexOrCss instanceof Color) return hexOrCss;
        if (typeof hexOrCss === 'number') {
            const r = ((hexOrCss >> 16) & 0xff) / 255;
            const g = ((hexOrCss >> 8) & 0xff) / 255;
            const b = (hexOrCss & 0xff) / 255;
            return new Color(r, g, b, 1);
        }
        return Color.from_css(String(hexOrCss));
    }

    function isLayerVisible(name, visibleLayers) {
        if (!name) return true;
        if (!visibleLayers) return true;
        // Treat undefined (not set) as visible — matches the Canvas2D fallback behavior
        // where layers default to visible unless explicitly hidden.
        return visibleLayers[name] !== false;
    }

    function isBottomLayer(layer) {
        const n = String(layer || '').trim();
        return n === 'B.Cu' || n.startsWith('B.');
    }

    function isFrontLayer(layer) {
        const n = String(layer || '').trim();
        return n === 'F.Cu' || n.startsWith('F.');
    }

    function copperColor(layer) {
        return isBottomLayer(layer) ? '#356cff' : '#ff563d';
    }

    function rotatePoint(x, y, angleDeg) {
        const a = new Angle(Angle.deg_to_rad(angleDeg || 0));
        return a.rotate_point({ x, y }, { x: 0, y: 0 });
    }

    function getPadWorldPos(comp, pad) {
        const r = rotatePoint(pad.x || 0, pad.y || 0, -(comp.rotation || 0));
        return new Vec2((comp.x || 0) + r.x, (comp.y || 0) + r.y);
    }

    class BoardPainter {
        constructor(renderer, boardModel, visibleLayers) {
            this.r = renderer;
            this.model = boardModel;
            this.visibleLayers = visibleLayers || {};
        }

        paint() {
            const r = this.r;
            const model = this.model;
            if (!model) return;

            this._paintBoardOutline(r, model);
            this._paintTraces(r, model);
            this._paintVias(r, model);
            this._paintComponents(r, model);
        }

        _paintBoardOutline(r, model) {
            const segs = model.outline_segments || [];
            if (!segs.length || !isLayerVisible('Edge.Cuts', this.visibleLayers)) return;

            // Board substrate fill
            const fillColor = toColor('#0d1f1a', '#0d1f1a');
            fillColor.a = 0.92;

            const allPoints = [];
            for (const seg of segs) {
                const pts = this._segPoints(seg);
                if (pts.length >= 2) allPoints.push(...pts);
            }
            if (allPoints.length >= 3) {
                r.polygon(allPoints, fillColor);
            }

            // Board edge line
            const edgeColor = toColor('#19d7b0', '#19d7b0');
            const lineWidth = 0.15;
            for (const seg of segs) {
                const pts = this._segPoints(seg);
                if (pts.length >= 2) {
                    r.line(pts, lineWidth, edgeColor);
                }
            }
        }

        _segPoints(seg) {
            if (Array.isArray(seg.points) && seg.points.length > 1) return seg.points.map(p => new Vec2(p));
            if (seg.kind === 'gr_rect' && seg.start && seg.end) {
                const s = seg.start, e = seg.end;
                return [
                    new Vec2(s.x, s.y), new Vec2(e.x, s.y),
                    new Vec2(e.x, e.y), new Vec2(s.x, e.y),
                    new Vec2(s.x, s.y),
                ];
            }
            if (seg.kind === 'gr_arc' && seg.start && seg.mid && seg.end) {
                const arc = KiCMath.MathArc.from_three_points(seg.start, seg.mid, seg.end);
                return arc.to_polyline(28).map(p => new Vec2(p));
            }
            const pts = [];
            if (seg.start) pts.push(new Vec2(seg.start));
            if (seg.end) pts.push(new Vec2(seg.end));
            return pts;
        }

        _paintTraces(r, model) {
            const traces = model.traces || [];
            const sorted = [...traces].sort((a, b) => isBottomLayer(a.layer) ? 0 : 1);
            for (const trace of sorted) {
                if (!isLayerVisible(trace.layer || 'F.Cu', this.visibleLayers)) continue;
                const path = trace.path || [];
                if (path.length < 2) continue;
                const color = toColor(copperColor(trace.layer), '#c87533');
                const w = Math.max(trace.width || 0.254, 0.14);
                r.line(path, w, color);
            }
        }

        _paintVias(r, model) {
            const vias = model.vias || [];
            for (const via of vias) {
                const viaLayers = via.layers || ['F.Cu', 'B.Cu'];
                if (!viaLayers.some(l => isLayerVisible(l, this.visibleLayers))) continue;
                const center = new Vec2(via.x, via.y);
                const outerD = Math.max(via.diameter || 0.6, 0.6);
                const hasTop = viaLayers.some(isFrontLayer);
                const hasBot = viaLayers.some(isBottomLayer);
                const viaColor = hasBot && !hasTop ? '#7f9bff' : hasTop && !hasBot ? '#b87333' : '#caa15f';
                r.circle(center, outerD / 2, toColor(viaColor, '#caa15f'));
            }
        }

        _paintComponents(r, model) {
            const comps = model.components || [];
            for (const comp of comps) {
                this._paintComponentBody(r, comp);
                this._paintComponentPads(r, comp);
                this._paintComponentGraphics(r, comp);
            }
        }

        _paintComponentBody(r, comp) {
            const pads = comp.pads || [];
            if (!pads.length) return;
            let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
            for (const pad of pads) {
                const c = getPadWorldPos(comp, pad);
                const hw = (pad.width || 1) / 2;
                const hh = (pad.height || 1) / 2;
                if (c.x - hw < minX) minX = c.x - hw;
                if (c.x + hw > maxX) maxX = c.x + hw;
                if (c.y - hh < minY) minY = c.y - hh;
                if (c.y + hh > maxY) maxY = c.y + hh;
            }
            if (!Number.isFinite(minX)) return;
            const bodyColor = new Color(14/255, 17/255, 24/255, 0.4);
            r.polygon([
                new Vec2(minX, minY), new Vec2(maxX, minY),
                new Vec2(maxX, maxY), new Vec2(minX, maxY),
            ], bodyColor);
        }

        _paintComponentPads(r, comp) {
            const pads = comp.pads || [];

            // Masks
            for (const pad of pads) {
                const maskLayers = (pad.layers || ['F.Cu']).filter(l => l === 'F.Mask' || l === 'B.Mask');
                if (maskLayers.length && !maskLayers.some(l => isLayerVisible(l, this.visibleLayers))) continue;
                if (!maskLayers.length && !(pad.layers || ['F.Cu']).some(l => isLayerVisible(l, this.visibleLayers))) continue;
                const c = getPadWorldPos(comp, pad);
                const rot = (comp.rotation || 0) + (pad.rotation || 0);
                const w = (pad.width || 1) + 0.1;
                const h = (pad.height || 1) + 0.1;
                this._drawPadShape(r, c, w, h, pad.shape || 'rect', rot, '#0f3b32', 0.95, pad.roundrect_rratio, null, pad.rect_delta_x, pad.rect_delta_y);
            }

            // Copper
            for (const pad of pads) {
                if (!(pad.layers || ['F.Cu']).some(l => isLayerVisible(l, this.visibleLayers))) continue;
                const c = getPadWorldPos(comp, pad);
                const rot = (comp.rotation || 0) + (pad.rotation || 0);
                const w = pad.width || 1;
                const h = pad.height || 1;
                const isBottom = (pad.layers || []).some(isBottomLayer);
                const isThrough = pad.type === 'thru_hole' || pad.type === 'np_thru_hole' || pad.type === 'tht';
                const cuColor = isThrough ? '#caa15f' : (isBottom ? '#7f9bff' : '#b87333');
                const stColor = isThrough ? '#dbb875' : (isBottom ? '#8db1ff' : '#c98543');
                this._drawPadShape(r, c, w, h, pad.shape || 'rect', rot, cuColor, 1, pad.roundrect_rratio, stColor, pad.rect_delta_x, pad.rect_delta_y);
            }

            // Drills
            for (const pad of pads) {
                const isThrough = pad.type === 'thru_hole' || pad.type === 'np_thru_hole' || pad.type === 'tht';
                if (!isThrough) continue;
                if (!(pad.layers || ['F.Cu']).some(l => isLayerVisible(l, this.visibleLayers))) continue;
                const c = getPadWorldPos(comp, pad);
                const w = pad.width || 1;
                const h = pad.height || 1;
                const maxDrill = Math.min(w, h) * 0.65;
                const rawDrill = pad.drill || Math.min(w, h) * 0.45;
                const drill = Math.max(Math.min(rawDrill, maxDrill), 0.2);
                const drillW = Math.min(pad.drill_width || drill, maxDrill);
                const off = rotatePoint(pad.drill_offset_x || 0, pad.drill_offset_y || 0, -(comp.rotation || 0));
                const dc = new Vec2(c.x + off.x, c.y + off.y);
                if (Math.abs(drill - drillW) < 0.001) {
                    r.circle(dc, drill / 2, toColor('#0b1116', '#0b1116'));
                } else {
                    const halfLenX = (drill / 2) - (Math.min(drill, drillW) / 2);
                    const halfLenY = (drillW / 2) - (Math.min(drill, drillW) / 2);
                    if (Math.abs(halfLenX) < 0.001 && Math.abs(halfLenY) < 0.001) {
                        r.circle(dc, drill / 2, toColor('#0b1116', '#0b1116'));
                    }
                }
            }
        }

        _drawPadShape(r, center, width, height, shape, rotation, fillColor, alpha, roundrectRratio, strokeColor, rectDeltaX, rectDeltaY) {
            const c = new Vec2(center);
            const rot = rotation || 0;
            const w = Math.max(width, 0.05);
            const h = Math.max(height, 0.05);
            const fill = toColor(fillColor, '#c87533');
            fill.a = alpha;

            if (shape === 'circle') {
                r.circle(c, Math.max(w, h) / 2, fill);
                if (strokeColor) {
                    const stroke = toColor(strokeColor, fillColor);
                    const pts = this._circleToPolyline(c, Math.max(w, h) / 2, 24);
                    r.line(pts, 0.05, stroke);
                }
                return;
            }

            const corners = this._padCorners(w, h, shape, roundrectRratio, rectDeltaX, rectDeltaY);
            const transformed = corners.map(pt => {
                const rp = rotatePoint(pt.x, pt.y, rot);
                return new Vec2(c.x + rp.x, c.y + rp.y);
            });
            r.polygon(transformed, fill);

            if (strokeColor) {
                const stroke = toColor(strokeColor, fillColor);
                const outline = [...transformed, transformed[0]];
                r.line(outline, 0.03, stroke);
            }
        }

        _circleToPolyline(center, radius, segs) {
            const pts = [];
            for (let i = 0; i <= segs; i++) {
                const a = (i / segs) * Math.PI * 2;
                pts.push(new Vec2(center.x + Math.cos(a) * radius, center.y + Math.sin(a) * radius));
            }
            return pts;
        }

        _padCorners(w, h, shape, roundrectRratio, rectDeltaX, rectDeltaY) {
            const hw = w / 2;
            const hh = h / 2;
            if (shape === 'roundrect' || shape === 'trapezoid') {
                const tdX = rectDeltaX || 0;
                const tdY = rectDeltaY || 0;
                return [
                    { x: -hw - tdY, y:  hh + tdX },
                    { x:  hw + tdY, y:  hh - tdX },
                    { x:  hw - tdY, y: -hh + tdX },
                    { x: -hw + tdY, y: -hh - tdX },
                ];
            }
            if (shape === 'oval') {
                const half = Math.min(hw, hh);
                const halfLenX = hw - half;
                const halfLenY = hh - half;
                if (Math.abs(halfLenX) < 0.01 && Math.abs(halfLenY) < 0.01) {
                    return [{ x: -hw, y: -hh }, { x: hw, y: -hh }, { x: hw, y: hh }, { x: -hw, y: hh }];
                }
                const corners = [
                    { x: -halfLenX, y: -halfLenY - half },
                    { x: halfLenX, y: -halfLenY - half },
                    { x: halfLenX, y: -halfLenY },
                    { x: halfLenX + half, y: -halfLenY },
                    { x: halfLenX + half, y: halfLenY },
                    { x: halfLenX, y: halfLenY },
                    { x: halfLenX, y: halfLenY + half },
                    { x: -halfLenX, y: halfLenY + half },
                    { x: -halfLenX, y: halfLenY },
                    { x: -halfLenX - half, y: halfLenY },
                    { x: -halfLenX - half, y: -halfLenY },
                    { x: -halfLenX, y: -halfLenY },
                ];
                return corners;
            }
            return [
                { x: -hw, y: -hh }, { x: hw, y: -hh },
                { x: hw, y: hh }, { x: -hw, y: hh },
            ];
        }

        _paintComponentGraphics(r, comp) {
            const items = comp.graphics || [];
            const layerGroups = { 'F.CrtYd': [], 'B.CrtYd': [], 'F.Fab': [], 'B.Fab': [], 'F.SilkS': [], 'B.SilkS': [] };
            for (const item of items) {
                if (item.kind === 'property') continue;
                if (!isLayerVisible(item.layer, this.visibleLayers)) continue;
                if (layerGroups[item.layer]) layerGroups[item.layer].push(item);
            }

            for (const [layer, layerItems] of Object.entries(layerGroups)) {
                if (!layerItems.length) continue;
                let strokeCss, fillCss, alpha;
                if (layer.includes('CrtYd')) { strokeCss = '#3d7570'; alpha = 0.7; }
                else if (layer.includes('Fab')) { strokeCss = '#8eb0aa'; fillCss = 'rgba(58,104,96,0.18)'; alpha = 0.9; }
                else { strokeCss = '#f2f5f4'; fillCss = 'rgba(220,240,235,0.10)'; alpha = 1; }

                for (const item of layerItems) {
                    this._drawGraphicItem(r, comp, item, strokeCss, alpha, fillCss);
                }
            }

            // Fallback outline if no silkscreen
            const hasSilk = layerGroups['F.SilkS'].length + layerGroups['B.SilkS'].length > 0;
            if (!hasSilk && (isLayerVisible('F.SilkS', this.visibleLayers) || isLayerVisible('B.SilkS', this.visibleLayers))) {
                this._drawFallbackOutline(r, comp);
            }
        }

        _drawGraphicItem(r, comp, item, strokeCss, alpha, fillCss) {
            const sw = item.width || 0.12;
            const stroke = toColor(strokeCss, '#e0f0ed');
            stroke.a = alpha;
            const fill = fillCss ? toColor(fillCss, strokeCss) : null;
            if (fill) fill.a = alpha * 0.6;

            const tf = (pts) => pts.map(p => {
                const rp = rotatePoint(p.x || 0, p.y || 0, -(comp.rotation || 0));
                return new Vec2((comp.x || 0) + rp.x, (comp.y || 0) + rp.y);
            });

            if (item.kind === 'fp_circle') {
                const center = tf([item.center])[0];
                const radius = Math.hypot((item.end.x || 0) - (item.center.x || 0), (item.end.y || 0) - (item.center.y || 0));
                if (fill) r.circle(center, radius + sw / 2, fill);
                const circlePts = this._circleToPolyline(center, radius, 32);
                r.line(circlePts, sw, stroke);
                return;
            }

            let points;
            if (item.kind === 'fp_rect' && item.start && item.end) {
                const s = item.start, e = item.end;
                points = tf([
                    s, { x: e.x, y: s.y },
                    e, { x: s.x, y: e.y },
                    s,
                ]);
            } else if (item.kind === 'fp_poly') {
                const pts = (item.points || []).slice();
                if (pts.length) pts.push(pts[0]);
                points = tf(pts);
            } else if (item.kind === 'fp_arc' && item.start && item.mid && item.end) {
                const arc = KiCMath.MathArc.from_three_points(item.start, item.mid, item.end);
                points = tf(arc.to_polyline(32));
            } else {
                const pts = [];
                if (item.start) pts.push(item.start);
                if (item.end) pts.push(item.end);
                points = tf(pts);
            }

            if (points.length < 2) return;

            const isFilled = item.fill === true || item.fill === 'yes' || item.fill === 'outline' ||
                (typeof item.fill === 'string' && item.fill !== 'none' && item.fill !== 'no');

            if (isFilled && fill && (item.kind === 'fp_rect' || item.kind === 'fp_poly')) {
                r.polygon(points, fill);
            }
            r.line(points, sw, stroke);
        }

        _drawFallbackOutline(r, comp) {
            const pads = comp.pads || [];
            if (!pads.length) return;
            let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
            for (const pad of pads) {
                const c = getPadWorldPos(comp, pad);
                const hw = (pad.width || 1) / 2 + 0.25;
                const hh = (pad.height || 1) / 2 + 0.25;
                if (c.x - hw < minX) minX = c.x - hw;
                if (c.x + hw > maxX) maxX = c.x + hw;
                if (c.y - hh < minY) minY = c.y - hh;
                if (c.y + hh > maxY) maxY = c.y + hh;
            }
            if (!Number.isFinite(minX)) return;
            const fillColor = new Color(18/255, 22/255, 30/255, 0.55);
            r.polygon([
                new Vec2(minX, minY), new Vec2(maxX, minY),
                new Vec2(maxX, maxY), new Vec2(minX, maxY),
            ], fillColor);
            const strokeColor = toColor('#e0f0ed', '#e0f0ed');
            strokeColor.a = 0.9;
            r.line([
                new Vec2(minX, minY), new Vec2(maxX, minY),
                new Vec2(maxX, maxY), new Vec2(minX, maxY),
                new Vec2(minX, minY),
            ], 0.15, strokeColor);
        }
    }

    globalThis.KiCBoard = { BoardPainter };
})(window);
