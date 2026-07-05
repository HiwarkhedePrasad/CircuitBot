const pcbEditor = new PcbEditorWebGL('pcbCanvas');

function pcbGetCanvas() {
    return document.getElementById('pcbCanvas');
}

function pcbSetupCanvas() {
    pcbEditor.ensure();
    pcbEditor._resize();
}

function pcbLoadBoard(boardModel, options = {}) {
    const shouldFetchRatsnest = options.fetchRatsnest !== false;
    pcbEditor.load(boardModel);
    if (!shouldFetchRatsnest) {
        pcbEditor.refresh();
        return;
    }
    pcbEditor.fetchRatsnest().catch(() => {
        pcbState.ratsnest = boardModel && typeof boardModel === 'object' && boardModel.ratsnest
            ? boardModel.ratsnest
            : {};
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

function pcbSetRenderMode(mode) {
    pcbState.renderMode = mode === 'overlay' ? 'overlay' : 'full';
}

function pcbGetViewBounds() {
    const canvas = pcbEditor && pcbEditor._canvas;
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    const topLeft = pcbEditor.screenToWorld(rect.left, rect.top);
    const bottomRight = pcbEditor.screenToWorld(rect.right, rect.bottom);
    return {
        minX: Math.min(topLeft.x, bottomRight.x),
        minY: Math.min(topLeft.y, bottomRight.y),
        maxX: Math.max(topLeft.x, bottomRight.x),
        maxY: Math.max(topLeft.y, bottomRight.y),
    };
}

function pcbSetViewBounds(bounds) {
    if (!bounds || !pcbEditor) return;
    const viewport = pcbEditor.getViewportSize();
    if (!viewport.width || !viewport.height) return;
    const width = Math.max(bounds.maxX - bounds.minX, 1);
    const height = Math.max(bounds.maxY - bounds.minY, 1);
    pcbState.midX = (bounds.minX + bounds.maxX) / 2;
    pcbState.midY = (bounds.minY + bounds.maxY) / 2;
    pcbState.baseScale = Math.min(
        viewport.width / width,
        viewport.height / height
    );
    pcbState.zoom = 1;
    pcbState.panX = 0;
    pcbState.panY = 0;
    pcbState.cx = viewport.width / 2;
    pcbState.cy = viewport.height / 2;
    pcbEditor._applyCamera();
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

function pcbUpdateCursor() {
    if (pcbState.mode === PCB_MODE.ROUTE || pcbState.activeTool === PCB_TOOL.ROUTE) {
        pcbSetCursor('crosshair');
    } else if (pcbState.activeTool === PCB_TOOL.VIA) {
        pcbSetCursor('copy');
    } else if (pcbState.mode === PCB_MODE.PANNING) {
        pcbSetCursor('grabbing');
    } else if (pcbState.mode === PCB_MODE.DRAG_COMPONENT) {
        pcbSetCursor('move');
    } else if (pcbState.activeTool === PCB_TOOL.SELECT) {
        pcbSetCursor(pcbState.hoveredPadKey ? 'pointer' : 'default');
    } else {
        pcbSetCursor('grab');
    }
}

function pcbSetMode(mode) {
    pcbState.mode = mode;
    pcbUpdateCursor();
    dispatchPcbInteractionUpdated();
}

function pcbSetTool(tool) {
    if (!Object.values(PCB_TOOL).includes(tool)) return;
    if (pcbState.activeTool === tool && pcbState.mode !== PCB_MODE.ROUTE) {
        dispatchPcbInteractionUpdated();
        return;
    }
    if (tool !== PCB_TOOL.ROUTE) {
        pcbState.routeStartAnchor = null;
        pcbState.routeNetName = '';
        pcbState.routePoints = [];
        pcbState.routeVias = [];
        pcbState.routeCursor = null;
    }
    pcbState.hoveredViaIndex = null;
    pcbState.dragViaIndex = null;
    pcbState.activeTool = tool;
    pcbSetMode(PCB_MODE.IDLE);
    pcbEditor.requestOverlayRefresh();
}

function pcbSetRouteStyle(style = {}) {
    if (style.layer === 'F.Cu' || style.layer === 'B.Cu') {
        pcbState.routeLayer = style.layer;
    }
    if (style.width != null) {
        pcbState.routeWidth = Math.max(toFiniteNumber(style.width, pcbState.routeWidth), 0.1);
    }
    dispatchPcbInteractionUpdated();
}

function pcbCancelDraw() {
    pcbSetMode(PCB_MODE.IDLE);
    pcbState.routeStartAnchor = null;
    pcbState.routeNetName = '';
    pcbState.routePoints = [];
    pcbState.routeVias = [];
    pcbState.routeCursor = null;
    pcbState.pointerDownScreen = null;
    pcbState.pointerDownWorld = null;
    pcbState.pointerDragMoved = false;
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
    
    // Auto-via at the end if layer mismatches
    if (targetPad.pad || targetPad.trace) {
        const layers = targetPad.pad ? (targetPad.pad.layers || []) : [targetPad.trace.layer || 'F.Cu'];
        const isMulti = layers.includes('*.Cu') || (layers.some(isBottomCopperLayer) && layers.some(isFrontCopperLayer));
        const targetIsBottom = layers.some(isBottomCopperLayer);
        const routeIsBottom = pcbState.routeLayer === 'B.Cu';
        if (!isMulti && (targetIsBottom !== routeIsBottom)) {
            const via = buildViaDraft(targetPad, pcbState.routeNetName);
            if (targetPad.noSnap) {
                via.x = targetPad.x;
                via.y = targetPad.y;
            }
            pcbState.boardModel.vias.push(via);
        }
    }
    const after = deepClone(pcbState.boardModel);
    pcbEditor.markDirty('trace', 'footprint', 'overlay');
    pcbEditor.refresh();
    pcbCancelDraw();
    try {
        await pcbEditor.saveBoardModel();
        pcbEditor.pushHistory('route trace', before, after);
    } catch (error) {
        pcbState.boardModel = before;
        pcbEditor.markDirty('trace', 'footprint', 'overlay');
        pcbEditor.refresh();
        dispatchBoardSync(false, { error: error.message, fallback_saved: false });
    }
}

async function placeViaAt(point) {
    if (!pcbState.boardModel) return;
    const before = deepClone(pcbState.boardModel);
    const via = buildViaDraft(point);
    pcbState.boardModel.vias = pcbState.boardModel.vias || [];
    pcbState.boardModel.vias.push(via);
    const after = deepClone(pcbState.boardModel);
    pcbEditor.markDirty('trace', 'footprint', 'overlay');
    pcbEditor.refresh();
    try {
        await pcbEditor.saveBoardModel();
        pcbEditor.pushHistory('place via', before, after);
    } catch (error) {
        pcbState.boardModel = before;
        pcbEditor.markDirty('trace', 'footprint', 'overlay');
        pcbEditor.refresh();
        dispatchBoardSync(false, { error: error.message, fallback_saved: false });
    }
}

function pcbHandleWheel(event) {
    event.preventDefault();
    if (!pcbState.boardModel) return;
    const canvas = pcbEditor._canvas;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const sx = (event.clientX - rect.left) * (canvas.width / rect.width);
    const sy = (event.clientY - rect.top) * (canvas.height / rect.height);

    const factor = event.deltaY > 0 ? 0.92 : 1.08;
    const oldZoom = pcbState.zoom;
    const newZoom = Math.min(Math.max(oldZoom * factor, 0.1), 25);
    const actualFactor = newZoom / oldZoom;

    pcbState.panX = (sx - pcbState.cx) * (1 - actualFactor) + pcbState.panX * actualFactor;
    pcbState.panY = (sy - pcbState.cy) * (1 - actualFactor) + pcbState.panY * actualFactor;
    pcbState.zoom = newZoom;

    pcbEditor._applyCamera();
    pcbEditor.requestOverlayRefresh();
    pcbEditor.requestSettledRefresh();
}

function pcbHandleMouseDown(event) {
    if (!pcbState.boardModel) return;
    pcbState.pointerDownScreen = { x: event.clientX, y: event.clientY };
    pcbState.pointerDownWorld = pcbEditor.screenToWorld(event.clientX, event.clientY);
    pcbState.pointerDragMoved = false;
    const padHit = pcbEditor.hitTestPad(event.clientX, event.clientY);
    const traceHit = pcbEditor.hitTestTrace(event.clientX, event.clientY);
    const viaHit = pcbEditor.hitTestVia(event.clientX, event.clientY);
    const compHit = pcbEditor.hitTestComponent(event.clientX, event.clientY);
    if (pcbState.mode === PCB_MODE.ROUTE || pcbState.activeTool === PCB_TOOL.ROUTE) {
        if (event.button === 2) {
            if (pcbState.routePoints.length >= 2) {
                const finalTarget = pcbState.routeCursor || pcbState.routePoints[pcbState.routePoints.length - 1];
                commitRouteToBoard(finalTarget);
            } else {
                pcbCancelDraw();
            }
            return;
        }
        if (event.button !== 0) return;
        if (pcbState.mode !== PCB_MODE.ROUTE) {
            if (padHit && beginRoute(padHit)) return;
            if (traceHit) {
                beginRoute({
                    trace: traceHit.trace,
                    key: `trace:${traceHit.x}:${traceHit.y}`,
                    x: traceHit.x,
                    y: traceHit.y,
                });
            }
            return;
        }
        if (padHit && pcbState.routeStartAnchor && pcbState.routeStartAnchor.key !== padHit.key) {
            commitRouteToBoard({ x: padHit.x, y: padHit.y, noSnap: true, pad: padHit.pad });
            return;
        }
        if (traceHit) {
            commitRouteToBoard({ x: traceHit.x, y: traceHit.y, noSnap: true, trace: traceHit.trace });
            return;
        }
        const target = pcbState.routeCursor || pcbEditor.screenToWorld(event.clientX, event.clientY);
        pcbState.routePoints = appendRoutePoint(pcbState.routePoints, target);
        pcbEditor.requestOverlayRefresh();
        return;
    }
    if (event.button !== 0 && event.button !== 1) return;
    if (pcbState.activeTool === PCB_TOOL.VIA && event.button === 0) {
        if (viaHit) {
            pcbState.dragViaIndex = viaHit.index;
            pcbState.dragOrigin = { x: viaHit.via.x, y: viaHit.via.y };
            pcbState.dragPointerStart = pcbState.pointerDownWorld;
            pcbSetMode(PCB_MODE.DRAG_COMPONENT);
            pcbEditor.requestOverlayRefresh();
            return;
        }
        placeViaAt(pcbState.pointerDownWorld);
        return;
    }
    if (pcbState.activeTool === PCB_TOOL.SELECT && event.button === 0 && compHit) {
        pcbState.selectedComponentRef = compHit.ref;
        pcbState.dragComponentRef = compHit.ref;
        pcbState.dragOrigin = { x: compHit.x, y: compHit.y };
        pcbState.dragPointerStart = pcbState.pointerDownWorld;
        pcbSetMode(PCB_MODE.DRAG_COMPONENT);
        pcbEditor.requestOverlayRefresh();
        return;
    }
    if (pcbState.activeTool === PCB_TOOL.SELECT && event.button === 0) {
        pcbState.selectedComponentRef = compHit ? compHit.ref : null;
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
    const viaHit = pcbEditor.hitTestVia(event.clientX, event.clientY);
    const prevHoveredPadKey = pcbState.hoveredPadKey;
    pcbState.hoveredPadKey = pcbState.activeTool === PCB_TOOL.ROUTE ? (padHit ? padHit.key : null) : null;
    pcbState.hoveredViaIndex = pcbState.activeTool === PCB_TOOL.VIA && viaHit ? viaHit.index : null;
    pcbState.lastPointerWorld = world;
    if (!pcbState.pointerDragMoved && hasPointerExceededThreshold(event)) {
        pcbState.pointerDragMoved = true;
    }
    if (pcbState.mode === PCB_MODE.PANNING && pcbState.dragPointerStart) {
        pcbState.panX += event.clientX - pcbState.dragPointerStart.x;
        pcbState.panY += event.clientY - pcbState.dragPointerStart.y;
        pcbState.dragPointerStart = { x: event.clientX, y: event.clientY };
        pcbEditor._applyCamera();
        pcbEditor.requestOverlayRefresh();
        return;
    }
    if (pcbState.mode === PCB_MODE.DRAG_COMPONENT && pcbState.dragComponentRef && pcbState.dragPointerStart) {
        if (!pcbState.pointerDragMoved) {
            return;
        }
        const component = (pcbState.boardModel.components || []).find((item) => item.ref === pcbState.dragComponentRef);
        if (component) {
            component.x = pcbState.dragOrigin.x + (world.x - pcbState.dragPointerStart.x);
            component.y = pcbState.dragOrigin.y + (world.y - pcbState.dragPointerStart.y);
            pcbEditor.markDirty('footprint', 'text', 'airwire');
            pcbEditor.requestRefresh();
        }
        return;
    }
    if (pcbState.mode === PCB_MODE.DRAG_COMPONENT && pcbState.dragViaIndex != null && pcbState.dragPointerStart) {
        if (!pcbState.pointerDragMoved) {
            return;
        }
        const via = (pcbState.boardModel.vias || [])[pcbState.dragViaIndex];
        if (via) {
            via.x = snapToGrid(pcbState.dragOrigin.x + (world.x - pcbState.dragPointerStart.x));
            via.y = snapToGrid(pcbState.dragOrigin.y + (world.y - pcbState.dragPointerStart.y));
            pcbEditor.markDirty('trace', 'airwire');
            pcbEditor.requestRefresh();
        }
        return;
    }
    if (pcbState.mode === PCB_MODE.ROUTE) {
        let routeTarget = routePoint(world);
        if (pcbState.routePoints && pcbState.routePoints.length > 0) {
            const prev = pcbState.routePoints[pcbState.routePoints.length - 1];
            if (Math.abs(routeTarget.x - prev.x) < 0.6) {
                routeTarget.x = prev.x;
                routeTarget.noSnap = true;
            }
            if (Math.abs(routeTarget.y - prev.y) < 0.6) {
                routeTarget.y = prev.y;
                routeTarget.noSnap = true;
            }
        }
        const traceHit = padHit ? null : pcbEditor.hitTestTrace(event.clientX, event.clientY);
        pcbState.routeCursor = padHit && pcbState.routeStartAnchor && pcbState.routeStartAnchor.key !== padHit.key
            ? { x: padHit.x, y: padHit.y, noSnap: true }
            : traceHit
                ? { x: traceHit.x, y: traceHit.y, noSnap: true }
            : routeTarget;
        pcbEditor.requestOverlayRefresh();
        return;
    }
    pcbUpdateCursor();
    if (prevHoveredPadKey !== pcbState.hoveredPadKey) {
        pcbEditor.requestOverlayRefresh();
    }
}

function pcbHandleMouseUp(event) {
    if (pcbState.mode === PCB_MODE.PANNING) {
        pcbSetMode(PCB_MODE.IDLE);
        pcbEditor.requestSettledRefresh(20);
        pcbState.pointerDownScreen = null;
        pcbState.pointerDownWorld = null;
        pcbState.pointerDragMoved = false;
        return;
    }
    if (pcbState.mode === PCB_MODE.DRAG_COMPONENT && pcbState.dragComponentRef) {
        const after = deepClone(pcbState.boardModel);
        const component = (pcbState.boardModel.components || []).find((item) => item.ref === pcbState.dragComponentRef);
        const changed = component && (Math.abs(component.x - pcbState.dragOrigin.x) > 0.001 || Math.abs(component.y - pcbState.dragOrigin.y) > 0.001);
        pcbSetMode(PCB_MODE.IDLE);
        pcbState.dragComponentRef = null;
        pcbState.dragPointerStart = null;
        pcbState.pointerDownScreen = null;
        pcbState.pointerDownWorld = null;
        pcbState.pointerDragMoved = false;
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
            pcbEditor.markDirty('footprint', 'trace', 'overlay');
            pcbEditor.refresh();
            dispatchBoardSync(false, { error: error.message, fallback_saved: false });
        });
        return;
    }
    if (pcbState.mode === PCB_MODE.DRAG_COMPONENT && pcbState.dragViaIndex != null) {
        const viaIndex = pcbState.dragViaIndex;
        const after = deepClone(pcbState.boardModel);
        const via = (pcbState.boardModel.vias || [])[viaIndex];
        const changed = via && (Math.abs(via.x - pcbState.dragOrigin.x) > 0.001 || Math.abs(via.y - pcbState.dragOrigin.y) > 0.001);
        pcbSetMode(PCB_MODE.IDLE);
        pcbState.dragViaIndex = null;
        pcbState.dragPointerStart = null;
        pcbState.pointerDownScreen = null;
        pcbState.pointerDownWorld = null;
        pcbState.pointerDragMoved = false;
        if (!changed) {
            pcbEditor.requestOverlayRefresh();
            return;
        }
        const before = deepClone(after);
        const original = (before.vias || [])[viaIndex];
        if (original) {
            original.x = pcbState.dragOrigin.x;
            original.y = pcbState.dragOrigin.y;
        }
        pcbEditor.saveBoardModel().then(() => {
            pcbEditor.pushHistory('move via', before, after);
        }).catch((error) => {
            pcbState.boardModel = before;
            pcbEditor.markDirty('trace', 'overlay');
            pcbEditor.refresh();
            dispatchBoardSync(false, { error: error.message, fallback_saved: false });
        });
        return;
    }
    if (event && event.button === 2) {
        pcbCancelDraw();
        return;
    }
    pcbState.pointerDownScreen = null;
    pcbState.pointerDownWorld = null;
    pcbState.pointerDragMoved = false;
}

function pcbRefreshRatsnest() {
    pcbEditor.fetchRatsnest().catch(() => {});
}

function pcbHandleKeyDown(event) {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
        event.preventDefault();
        pcbEditor.saveBoardModel()
            .then(() => {
                dispatchBoardSync(true, { saved: true });
                window.location.href = '/api/export_pcb';
            })
            .catch((error) => {
                dispatchBoardSync(false, { error: error.message, fallback_saved: false });
            });
        return;
    }
    if (event.key === 'Escape') {
        pcbCancelDraw();
        return;
    }
    if (event.shiftKey && event.key.toLowerCase() === 'f') {
        event.preventDefault();
        pcbResetView();
        return;
    }
    if (event.key.toLowerCase() === 'h') {
        pcbSetTool(PCB_TOOL.PAN);
        return;
    }
    if (event.key.toLowerCase() === 's') {
        pcbSetTool(PCB_TOOL.SELECT);
        return;
    }
    if (event.key.toLowerCase() === 'r') {
        pcbSetTool(PCB_TOOL.ROUTE);
        return;
    }
    if (event.key.toLowerCase() === 'v' && !event.ctrlKey && !event.metaKey && pcbState.mode !== PCB_MODE.ROUTE) {
        pcbSetTool(PCB_TOOL.VIA);
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
        dispatchPcbInteractionUpdated();
        pcbEditor.refresh();
    }
}

window.pcbLoadBoard = pcbLoadBoard;
window.pcbDraw = pcbDraw;
window.pcbDrawCurrent = pcbDrawCurrent;
window.pcbSetupCanvas = pcbSetupCanvas;
window.pcbScreenToWorld = pcbScreenToWorld;
window.pcbResetView = pcbResetView;
window.pcbSetRenderMode = pcbSetRenderMode;
window.pcbGetViewBounds = pcbGetViewBounds;
window.pcbSetViewBounds = pcbSetViewBounds;
window.pcbZoomBy = pcbZoomBy;
window.pcbSetTool = pcbSetTool;
window.pcbSetRouteStyle = pcbSetRouteStyle;
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
window.PCB_TOOL = PCB_TOOL;
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
    pcbGetViewBounds,
    pcbSetViewBounds,
    PCB_TOOL,
};
