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

function normalizeCopperLayerName(layer, fallback = 'F.Cu') {
    const raw = String(layer || fallback).trim();
    const aliases = {
        front_c: 'F.Cu',
        front_copper: 'F.Cu',
        f_cu: 'F.Cu',
        top: 'F.Cu',
        top_copper: 'F.Cu',
        back_c: 'B.Cu',
        back_copper: 'B.Cu',
        b_cu: 'B.Cu',
        bottom: 'B.Cu',
        bottom_copper: 'B.Cu',
    };
    return aliases[raw.toLowerCase()] || raw;
}

/**
 * Expand KiCad wildcard layer names used in pad definitions.
 * KiCad uses '*.Cu' to mean all copper layers, '*.Mask' for both solder masks, etc.
 * Since our renderer only checks F.Cu and B.Cu visibility, we expand wildcards
 * so that through-hole pads (which use '*.Cu') are correctly treated as visible.
 */
function expandPadLayers(layers) {
    const result = [];
    for (const layer of layers) {
        if (layer === '*.Cu') {
            result.push('F.Cu', 'B.Cu');
        } else if (layer === '*.Mask') {
            result.push('F.Mask', 'B.Mask');
        } else if (layer === '*.Paste') {
            result.push('F.Paste', 'B.Paste');
        } else if (layer === 'F&B.Cu') {
            result.push('F.Cu', 'B.Cu');
        } else {
            result.push(normalizeCopperLayerName(layer));
        }
    }
    return result;
}

function normalizeBoardModel(boardModel) {
    const model = deepClone(boardModel || {});
    model.components = (Array.isArray(model.components) ? model.components : [])
        .filter(c => {
            const isOrigin = Math.abs(toFiniteNumber(c.x)) < 0.1 && Math.abs(toFiniteNumber(c.y)) < 0.1;
            return !isOrigin;
        });
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
            pad.number = String(pad.number ?? pad.num ?? '');
            pad.num = pad.number;
            pad.x = toFiniteNumber(pad.x);
            pad.y = toFiniteNumber(pad.y);
            pad.width = toFiniteNumber(pad.width, 1);
            pad.height = toFiniteNumber(pad.height, 1);
            pad.rotation = toFiniteNumber(pad.rotation);
            if (pad.drill != null) pad.drill = toFiniteNumber(pad.drill, 0);
            if (pad.drill_width != null) pad.drill_width = toFiniteNumber(pad.drill_width, 0);
            pad.drill_offset_x = toFiniteNumber(pad.drill_offset_x, 0);
            pad.drill_offset_y = toFiniteNumber(pad.drill_offset_y, 0);
            if (pad.roundrect_rratio != null) pad.roundrect_rratio = toFiniteNumber(pad.roundrect_rratio);
            pad.rect_delta_x = toFiniteNumber(pad.rect_delta_x, 0);
            pad.rect_delta_y = toFiniteNumber(pad.rect_delta_y, 0);
            pad.layers = expandPadLayers(Array.isArray(pad.layers) ? pad.layers : ['F.Cu']);
        }
    }
    for (const trace of model.traces) {
        trace.layer = normalizeCopperLayerName(trace.layer, 'F.Cu');
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
        via.layers = (Array.isArray(via.layers) ? via.layers : ['F.Cu', 'B.Cu'])
            .map((layer) => normalizeCopperLayerName(layer));
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

function snapToGrid(value, grid = 0.254) {
    return Math.round(value / grid) * grid;
}

function rotatePoint(x, y, angleDeg) {
    const angle = new KiCMath.Angle(KiCMath.Angle.deg_to_rad(angleDeg || 0));
    return angle.rotate_point({ x, y }, { x: 0, y: 0 });
}

function routePoint(point) {
    if (point.noSnap) {
        return { x: point.x, y: point.y };
    }
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

function findNearbyPad(screenX, screenY, radiusMm) {
    if (!pcbState.boardModel) return null;
    const world = pcbEditor.screenToWorld(screenX, screenY);
    let best = null;
    let bestDist = radiusMm;
    for (const comp of pcbState.boardModel.components || []) {
        for (const pad of comp.pads || []) {
            const center = getComponentPadPosition(comp, pad);
            const dist = Math.hypot(world.x - center.x, world.y - center.y);
            if (dist < bestDist) {
                bestDist = dist;
                best = { pad, component: comp, key: `${comp.ref}:${pad.number}`, x: center.x, y: center.y };
            }
        }
    }
    return best;
}

function dedupePath(path) {
    const out = [];
    for (const point of path || []) {
        if (!point) continue;
        const next = { x: point.x, y: point.y, noSnap: point.noSnap };
        const prev = out[out.length - 1];
        if (prev && Math.abs(prev.x - next.x) < 0.001 && Math.abs(prev.y - next.y) < 0.001) {
            continue;
        }
        out.push(next);
    }
    return out;
}

function getComponentPadPosition(component, pad) {
    const rotated = rotatePoint(pad.x || 0, pad.y || 0, -(component.rotation || 0));
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

function getPadPositionByPinKey(model, pinKey) {
    if (!model || !pinKey) return null;
    const [ref, padNumber] = String(pinKey).split(':');
    if (!ref || padNumber == null) return null;
    const component = (model.components || []).find((item) => item.ref === ref);
    if (!component) return null;
    const pad = (component.pads || []).find((item) => String(item.number) === String(padNumber));
    if (!pad) return null;
    return getComponentPadPosition(component, pad);
}

function isBottomCopperLayer(layer) {
    const name = String(layer || '').trim();
    return name === 'B.Cu' || name.startsWith('B.');
}

function isFrontCopperLayer(layer) {
    const name = String(layer || '').trim();
    return name === 'F.Cu' || name.startsWith('F.');
}

function copperColorForLayer(layer) {
    return isBottomCopperLayer(layer) ? PCB_COLORS.bottomCopper : PCB_COLORS.topCopper;
}

function compactFootprintName(footprint) {
    const raw = String(footprint || '').trim();
    if (!raw) return '';
    const name = raw.includes(':') ? raw.split(':').pop() : raw;
    return name.replace(/^.*?_(?=\d)/, '').replace(/_/g, ' ');
}

function getPcbLayerMeta(layerName) {
    return (PCB_LAYER_CATALOG || []).find((entry) => entry.name === layerName) || null;
}

function getPcbLayerLabel(layerName) {
    const meta = getPcbLayerMeta(layerName);
    return meta ? meta.label : layerName;
}

function getPcbLayerColor(layerName) {
    const meta = getPcbLayerMeta(layerName);
    if (meta && meta.color) return meta.color;
    if (layerName === 'F.Cu') return '#ff563d';
    if (layerName === 'B.Cu') return '#356cff';
    if (layerName === 'Edge.Cuts') return '#19d7b0';
    if (layerName.includes('Silk')) return '#e0f0ed';
    if (layerName.includes('Fab')) return '#8eb0aa';
    return '#9aa6b2';
}

function defaultPcbLayerVisibility(layerName) {
    const meta = getPcbLayerMeta(layerName);
    return meta ? meta.visible !== false : false;
}

function collectBoardLayerNames(model) {
    const names = new Set((PCB_LAYER_CATALOG || []).map((entry) => entry.name));
    if (!model) return Array.from(names);
    for (const trace of model.traces || []) {
        if (trace.layer) names.add(trace.layer);
    }
    for (const via of model.vias || []) {
        for (const layer of via.layers || []) names.add(layer);
    }
    for (const component of model.components || []) {
        if (component.layer) names.add(component.layer);
        for (const pad of component.pads || []) {
            for (const layer of pad.layers || []) names.add(layer);
        }
        for (const item of component.graphics || []) {
            if (item.layer) names.add(item.layer);
        }
    }
    for (const segment of model.outline_segments || []) {
        if (segment.layer) names.add(segment.layer);
    }
    return Array.from(names);
}

function sortedBoardLayerNames(model) {
    const names = collectBoardLayerNames(model);
    const order = new Map((PCB_LAYER_CATALOG || []).map((entry, index) => [entry.name, index]));
    return names.sort((a, b) => {
        const ai = order.has(a) ? order.get(a) : 1000;
        const bi = order.has(b) ? order.get(b) : 1000;
        if (ai !== bi) return ai - bi;
        return a.localeCompare(b);
    });
}

function ensurePcbLayerVisibility(model) {
    pcbState.visibleLayers = pcbState.visibleLayers || {};
    for (const layerName of collectBoardLayerNames(model)) {
        if (!(layerName in pcbState.visibleLayers)) {
            pcbState.visibleLayers[layerName] = defaultPcbLayerVisibility(layerName);
        }
    }
}

function isPcbLayerVisible(layerName) {
    if (!layerName) return true;
    ensurePcbLayerVisibility(pcbState.boardModel);
    if (!(layerName in pcbState.visibleLayers)) {
        pcbState.visibleLayers[layerName] = defaultPcbLayerVisibility(layerName);
    }
    return pcbState.visibleLayers[layerName] !== false;
}

function setPcbLayerVisible(layerName, visible) {
    ensurePcbLayerVisibility(pcbState.boardModel);
    pcbState.visibleLayers[layerName] = !!visible;
    dispatchPcbLayerVisibilityUpdated();
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

function buildPadKey(component, pad, index) {
    return `${component.ref}:${pad.number}:${index ?? 0}`;
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

function pcbToolsEnabled() {
    return !!pcbState.boardModel;
}

function hasPointerExceededThreshold(event) {
    if (!pcbState.pointerDownScreen || !event) return false;
    const dx = event.clientX - pcbState.pointerDownScreen.x;
    const dy = event.clientY - pcbState.pointerDownScreen.y;
    return Math.hypot(dx, dy) >= PCB_POINTER_DRAG_THRESHOLD_PX;
}

function syncRouteStyleFromAnchor(anchorLike) {
    if (!anchorLike) return;
    if (anchorLike.pad) {
        const layers = anchorLike.pad.layers || [];
        const isMulti = layers.includes('*.Cu') || (layers.some(isBottomCopperLayer) && layers.some(isFrontCopperLayer));
        if (isMulti) {
            if (pcbState.routeLayer !== 'F.Cu' && pcbState.routeLayer !== 'B.Cu') {
                pcbState.routeLayer = 'F.Cu';
            }
        } else {
            pcbState.routeLayer = layers.some(isBottomCopperLayer) ? 'B.Cu' : 'F.Cu';
        }
        pcbState.routeNetName = getNetNameForPad(pcbState.boardModel, anchorLike.component.ref, anchorLike.pad.number);
        return;
    }
    if (anchorLike.trace) {
        pcbState.routeLayer = anchorLike.trace.layer || pcbState.routeLayer || 'F.Cu';
        pcbState.routeNetName = anchorLike.trace.net || pcbState.routeNetName || '_manual';
    }
}

function beginRoute(anchor) {
    if (!anchor) return false;
    const requestedLayer = pcbState.routeLayer;
    syncRouteStyleFromAnchor(anchor);
    pcbState.routeStartAnchor = {
        kind: anchor.trace ? 'trace' : 'pad',
        key: anchor.key,
        x: anchor.x,
        y: anchor.y,
        noSnap: anchor.noSnap,
    };
    pcbState.routePoints = [routePoint(anchor)];
    pcbState.routeVias = [];
    pcbState.routeCursor = routePoint(anchor);

    if (requestedLayer && requestedLayer !== pcbState.routeLayer && (requestedLayer === 'F.Cu' || requestedLayer === 'B.Cu')) {
        const via = buildViaDraft(anchor, pcbState.routeNetName);
        if (anchor.noSnap) {
            via.x = anchor.x;
            via.y = anchor.y;
        }
        pcbState.routeVias.push(via);
        pcbState.routeLayer = requestedLayer;
    }

    pcbSetMode(PCB_MODE.ROUTE);
    pcbEditor.requestOverlayRefresh();
    return true;
}

function buildViaDraft(point, netName = '') {
    return {
        x: snapToGrid(point.x),
        y: snapToGrid(point.y),
        drill: 0.3,
        diameter: 0.7,
        layers: ['F.Cu', 'B.Cu'],
        net: netName || '',
    };
}

function getComponentBounds(component) {
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    for (const pad of component.pads || []) {
        const center = getComponentPadPosition(component, pad);
        if (!Number.isFinite(center.x) || !Number.isFinite(center.y)) continue;
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
            const rotated = rotatePoint(pt.x || 0, pt.y || 0, -(component.rotation || 0));
            const wx = component.x + rotated.x;
            const wy = component.y + rotated.y;
            if (!Number.isFinite(wx) || !Number.isFinite(wy)) continue;
            minX = Math.min(minX, wx);
            minY = Math.min(minY, wy);
            maxX = Math.max(maxX, wx);
            maxY = Math.max(maxY, wy);
        }
    }
    if (minX === Infinity || !Number.isFinite(minX) || !Number.isFinite(minY)) {
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

function arcPoints(start, mid, end, segments) {
    const arc = KiCMath.MathArc.from_three_points(start, mid, end, 1);
    return arc.to_polyline(segments || 32);
}

function getRoundRectPoints(w, h, r, steps = 6) {
    const points = [];
    const r_num = Number(r);
    const r_val = (Number.isFinite(r_num) && r_num > 0) ? Math.min(r_num, Math.min(w, h) / 2) : 0;
    if (r_val <= 0) {
        return [
            { x: -w/2, y: -h/2 },
            { x: w/2, y: -h/2 },
            { x: w/2, y: h/2 },
            { x: -w/2, y: h/2 }
        ];
    }
    
    // Top-Right corner arc
    const cx_tr = w / 2 - r_val;
    const cy_tr = -h / 2 + r_val;
    for (let i = 0; i <= steps; i++) {
        const angle = -Math.PI / 2 + (Math.PI / 2) * (i / steps);
        points.push({ x: cx_tr + Math.cos(angle) * r_val, y: cy_tr + Math.sin(angle) * r_val });
    }

    // Bottom-Right corner arc
    const cx_br = w / 2 - r_val;
    const cy_br = h / 2 - r_val;
    for (let i = 0; i <= steps; i++) {
        const angle = (Math.PI / 2) * (i / steps);
        points.push({ x: cx_br + Math.cos(angle) * r_val, y: cy_br + Math.sin(angle) * r_val });
    }

    // Bottom-Left corner arc
    const cx_bl = -w / 2 + r_val;
    const cy_bl = h / 2 - r_val;
    for (let i = 0; i <= steps; i++) {
        const angle = Math.PI / 2 + (Math.PI / 2) * (i / steps);
        points.push({ x: cx_bl + Math.cos(angle) * r_val, y: cy_bl + Math.sin(angle) * r_val });
    }

    // Top-Left corner arc
    const cx_tl = -w / 2 + r_val;
    const cy_tl = -h / 2 + r_val;
    for (let i = 0; i <= steps; i++) {
        const angle = Math.PI + (Math.PI / 2) * (i / steps);
        points.push({ x: cx_tl + Math.cos(angle) * r_val, y: cy_tl + Math.sin(angle) * r_val });
    }

    return points;
}

function drawPadShape(graphics, x, y, width, height, shape, rotation, roundrectRratio) {
    const w = Math.max(width, 0.2);
    const h = Math.max(height, 0.2);
    if (shape === 'circle') {
        graphics.drawCircle(x, y, Math.max(w, h) / 2);
        return;
    }
    
    let points;
    if (shape === 'oval') {
        points = getRoundRectPoints(w, h, Math.min(w, h) / 2, 8);
    } else if (shape === 'roundrect') {
        const rratio = (roundrectRratio != null) ? roundrectRratio : 0.25;
        points = getRoundRectPoints(w, h, Math.min(w, h) * rratio, 8);
    } else {
        points = [
            { x: -w/2, y: -h/2 },
            { x: w/2, y: -h/2 },
            { x: w/2, y: h/2 },
            { x: -w/2, y: h/2 }
        ];
    }
    
    const rotatedPoints = points.map(pt => {
        const rot = rotatePoint(pt.x, pt.y, rotation || 0);
        const px = x + rot.x;
        const py = y + rot.y;
        return {
            x: Number.isFinite(px) ? px : x,
            y: Number.isFinite(py) ? py : y
        };
    });
    
    const polyPoints = [];
    for (let i = 0; i < rotatedPoints.length; i++) {
        polyPoints.push(rotatedPoints[i].x, rotatedPoints[i].y);
    }
    graphics.drawPolygon(polyPoints);
}