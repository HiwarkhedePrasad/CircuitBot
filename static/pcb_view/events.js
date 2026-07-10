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

function pcbUpdateZoomDisplay() {
    const el = document.getElementById('zoomLevel');
    if (el) el.textContent = Math.round(pcbState.zoom * 100) + '%';
}

let _zoomAnimFrame = null;
let _zoomTarget = null;

function pcbZoomBy(factor) {
    if (!pcbState.boardModel) return;
    const target = Math.min(Math.max(pcbState.zoom * factor, 0.1), 25);
    // Animated zoom
    if (_zoomAnimFrame) cancelAnimationFrame(_zoomAnimFrame);
    const startZoom = pcbState.zoom;
    const startTime = performance.now();
    const duration = 120; // ms
    function animate(now) {
        const t = Math.min((now - startTime) / duration, 1);
        const ease = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; // easeInOutQuad
        pcbState.zoom = startZoom + (target - startZoom) * ease;
        pcbUpdateZoomDisplay();
        pcbEditor._applyCamera();
        if (t < 1) {
            _zoomAnimFrame = requestAnimationFrame(animate);
        } else {
            _zoomAnimFrame = null;
            pcbEditor.requestOverlayRefresh();
            pcbEditor.requestSettledRefresh();
        }
    }
    _zoomAnimFrame = requestAnimationFrame(animate);
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
        pcbSetCursor(pcbState.hoveredPadKey || pcbState.hoveredTraceIndex != null ? 'pointer' : 'default');
    } else if (pcbState.hoveredTraceIndex != null) {
        pcbSetCursor('pointer');
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
    if (pcbState.activeTool === tool && pcbState.mode !== PCB_MODE.ROUTE && pcbState.mode !== PCB_MODE.DRAW_OUTLINE) {
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
    if (tool !== PCB_TOOL.OUTLINE) {
        pcbState.outlinePoints = [];
        pcbState.outlineDraft = null;
    }
    pcbState.hoveredViaIndex = null;
    pcbState.dragViaIndex = null;
    pcbState.activeTool = tool;
    pcbSetMode(PCB_MODE.IDLE);
    pcbEditor.requestOverlayRefresh();

    // Show tool selection feedback
    const toolNames = {
        [PCB_TOOL.PAN]: 'Pan',
        [PCB_TOOL.SELECT]: 'Select',
        [PCB_TOOL.ROUTE]: 'Route',
        [PCB_TOOL.VIA]: 'Via',
        [PCB_TOOL.OUTLINE]: 'Outline'
    };
    const toolName = toolNames[tool] || tool;
    if (typeof showToast === 'function') {
        showToast(`${toolName} tool active`, 'info');
    }
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
    pcbState.outlinePoints = [];
    pcbState.outlineDraft = null;
    pcbState.pointerDownScreen = null;
    pcbState.pointerDownWorld = null;
    pcbState.pointerDragMoved = false;
    pcbEditor.requestOverlayRefresh();
}

function pcbFinalizeOutline() {
    if (!pcbState.boardModel) return;
    const pts = pcbState.outlinePoints;
    if (pts.length < 2) {
        pcbCancelDraw();
        return;
    }

    // Build outline segments from the placed points
    const segments = [];
    for (let i = 0; i < pts.length; i++) {
        const a = pts[i];
        const b = pts[(i + 1) % pts.length];
        segments.push({
            kind: 'gr_line',
            layer: 'Edge.Cuts',
            width: 0.1,
            start: { x: a.x, y: a.y },
            end: { x: b.x, y: b.y },
        });
    }

    pcbState.boardModel.outline_segments = segments;
    pcbState.outlinePoints = [];
    pcbState.outlineDraft = null;
    pcbSetMode(PCB_MODE.IDLE);
    pcbEditor.refresh();
    pcbEditor.saveBoardModel().catch(() => {});
    dispatchBoardSync(true, { saved: true });
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
    // Force recompute ratsnest with the new trace
    pcbState.ratsnest = pcbEditor._computeClientRatsnest(pcbState.boardModel);
    pcbEditor.requestOverlayRefresh();
    pcbEditor.refresh();
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

async function placeViaAt(point) {
    if (!pcbState.boardModel) return;
    const before = deepClone(pcbState.boardModel);
    const via = buildViaDraft(point);
    pcbState.boardModel.vias = pcbState.boardModel.vias || [];
    pcbState.boardModel.vias.push(via);
    const after = deepClone(pcbState.boardModel);
    pcbEditor.refresh();
    try {
        await pcbEditor.saveBoardModel();
        pcbEditor.pushHistory('place via', before, after);
    } catch (error) {
        pcbState.boardModel = before;
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
    pcbUpdateZoomDisplay();

    pcbEditor._applyCamera();
    pcbEditor.requestOverlayRefresh();
    pcbEditor.requestSettledRefresh();
}

function pcbHandleMouseDown(event) {
    if (pcbState.mode === PCB_MODE.GHOST_PLACEMENT) {
        if (event.button !== 0) return;
        const finalWorld = pcbEditor.screenToWorld(event.clientX, event.clientY);
        const activeSocket = window.socket;
        if (!activeSocket || !pcbState.ghostProposal) return;
        activeSocket.emit('chat:commit_proposal', {
            session_id: window.circuitbotChatSessionId || null,
            id: pcbState.ghostProposal.id, 
            x: finalWorld.x, 
            y: finalWorld.y 
        });

        pcbSetMode(PCB_MODE.IDLE);
        pcbState.ghostProposal = null;
        pcbEditor.requestOverlayRefresh();
        return;
    }

    if (!pcbState.boardModel) return;
    pcbState.pointerDownScreen = { x: event.clientX, y: event.clientY };
    pcbState.pointerDownWorld = pcbEditor.screenToWorld(event.clientX, event.clientY);
    pcbState.pointerDragMoved = false;
    const padHit = pcbEditor.hitTestPad(event.clientX, event.clientY);
    const traceHit = pcbEditor.hitTestTrace(event.clientX, event.clientY);
    const viaHit = pcbEditor.hitTestVia(event.clientX, event.clientY);
    const compHit = pcbEditor.hitTestComponent(event.clientX, event.clientY);

    // ── Outline drawing mode ───────────────────────────────────────
    if (pcbState.mode === PCB_MODE.DRAW_OUTLINE || pcbState.activeTool === PCB_TOOL.OUTLINE) {
        if (event.button === 2) {
            // Right-click: finalize the outline
            pcbFinalizeOutline();
            return;
        }
        if (event.button !== 0) return;
        const world = pcbEditor.screenToWorld(event.clientX, event.clientY);
        const snapped = { x: snapToGrid(world.x), y: snapToGrid(world.y) };
        pcbState.outlinePoints.push(snapped);
        pcbState.outlineDraft = { ...snapped };
        if (pcbState.mode !== PCB_MODE.DRAW_OUTLINE) {
            pcbSetMode(PCB_MODE.DRAW_OUTLINE);
        }
        pcbEditor.requestOverlayRefresh();
        return;
    }

    if (pcbState.mode === PCB_MODE.ROUTE || pcbState.activeTool === PCB_TOOL.ROUTE) {
        if (event.button === 2) {
            // Show via modal instead of committing
            pcbShowViaModal(event.clientX, event.clientY);
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
        // Auto-snap to nearby pad if click is within 1.5mm
        const nearbyPad = findNearbyPad(event.clientX, event.clientY, 1.5);
        if (nearbyPad && pcbState.routeStartAnchor && pcbState.routeStartAnchor.key !== nearbyPad.key) {
            commitRouteToBoard({ x: nearbyPad.x, y: nearbyPad.y, noSnap: true, pad: nearbyPad.pad });
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
    // Right-click context menu in Select mode
    if (pcbState.activeTool === PCB_TOOL.SELECT && event.button === 2) {
        event.preventDefault();
        pcbShowContextMenu(event.clientX, event.clientY, compHit);
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
    const world = pcbEditor.screenToWorld(event.clientX, event.clientY);
    pcbState.lastPointerWorld = world;
    // Update coordinate display
    const coordEl = document.getElementById('coordDisplay');
    if (coordEl) coordEl.textContent = `X: ${world.x.toFixed(2)} Y: ${world.y.toFixed(2)}`;
    if (pcbState.mode === PCB_MODE.GHOST_PLACEMENT) {
        pcbEditor.requestOverlayRefresh();
        return;
    }
    if (!pcbState.boardModel) return;
    const padHit = pcbEditor.hitTestPad(event.clientX, event.clientY);
    const viaHit = pcbEditor.hitTestVia(event.clientX, event.clientY);
    const compHit = pcbEditor.hitTestComponent(event.clientX, event.clientY);
    const prevHoveredPadKey = pcbState.hoveredPadKey;
    pcbState.hoveredPadKey = pcbState.activeTool === PCB_TOOL.ROUTE ? (padHit ? padHit.key : null) : null;
    pcbState.hoveredViaIndex = pcbState.activeTool === PCB_TOOL.VIA && viaHit ? viaHit.index : null;
    // Track hovered trace for deletion (when not routing or using other tools)
    const traceHit = pcbEditor.hitTestTrace(event.clientX, event.clientY);
    pcbState.hoveredTraceIndex = traceHit ? pcbState.boardModel.traces.indexOf(traceHit.trace) : null;
    const prevHoveredComp = pcbState.hoveredComponentRef;
    pcbState.hoveredComponentRef = compHit ? compHit.ref : null;
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
            pcbEditor.refreshAirwires();
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
            pcbEditor.requestRefresh();
        }
        return;
    }
    if (pcbState.mode === PCB_MODE.DRAW_OUTLINE) {
        const world = pcbEditor.screenToWorld(event.clientX, event.clientY);
        pcbState.outlineDraft = { x: snapToGrid(world.x), y: snapToGrid(world.y) };
        pcbEditor.requestOverlayRefresh();
        return;
    }
    if (pcbState.mode === PCB_MODE.ROUTE) {
        let routeTarget = routePoint(world);
        if (pcbState.routePoints && pcbState.routePoints.length > 0) {
            const prev = pcbState.routePoints[pcbState.routePoints.length - 1];
            const dx = Math.abs(routeTarget.x - prev.x);
            const dy = Math.abs(routeTarget.y - prev.y);
            if (dx < 0.6 && dx < dy) {
                routeTarget.x = prev.x;
                routeTarget.noSnap = true;
            } else if (dy < 0.6 && dy <= dx) {
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
    if (pcbState.mode === PCB_MODE.DRAW_OUTLINE) {
        // Double-click to finalize outline
        if (event.detail === 2 && pcbState.outlinePoints.length >= 2) {
            pcbFinalizeOutline();
        }
        return;
    }
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

function pcbFetchRatsnest() {
    pcbEditor.fetchRatsnest().catch(() => {});
}

function pcbShowViaModal(screenX, screenY) {
    // Remove existing modal if any
    const existing = document.getElementById('pcbViaModal');
    if (existing) existing.remove();

    const modal = document.createElement('div');
    modal.id = 'pcbViaModal';
    modal.style.cssText = `
        position: fixed;
        top: 50%;
        left: 50%;
        transform: translate(-50%, -50%);
        z-index: 10001;
        background: #1a1d23;
        border: 1px solid #3c3c3c;
        border-radius: 12px;
        padding: 20px;
        min-width: 240px;
        box-shadow: 0 8px 32px rgba(0,0,0,0.6);
        font-family: var(--font-ui);
    `;

    modal.innerHTML = `
        <div style="text-align:center;margin-bottom:16px;">
            <h3 style="margin:0;color:#e0f0ed;font-size:14px;">Place Via & Switch Layer</h3>
            <p style="margin:8px 0 0;color:#666;font-size:12px;">Select target layer for trace</p>
        </div>
        <div style="display:flex;flex-direction:column;gap:8px;">
            <button id="viaFcBtn" style="
                padding:12px 16px;
                background:#1e2a3a;
                border:2px solid #ff4444;
                border-radius:8px;
                color:#ff4444;
                font-weight:bold;
                cursor:pointer;
                transition:all 0.2s;
            ">F.Cu (Front)</button>
            <button id="viaBcBtn" style="
                padding:12px 16px;
                background:#1e2a3a;
                border:2px solid #4488ff;
                border-radius:8px;
                color:#4488ff;
                font-weight:bold;
                cursor:pointer;
                transition:all 0.2s;
            ">B.Cu (Back)</button>
            <button id="viaCancelBtn" style="
                padding:8px 16px;
                background:transparent;
                border:1px solid #3c3c3c;
                border-radius:6px;
                color:#666;
                cursor:pointer;
                margin-top:8px;
            ">Cancel</button>
        </div>
    `;

    document.body.appendChild(modal);

    // Add hover effects
    const fcBtn = document.getElementById('viaFcBtn');
    const bcBtn = document.getElementById('viaBcBtn');

    fcBtn.addEventListener('mouseenter', () => {
        fcBtn.style.background = 'rgba(255, 68, 68, 0.2)';
    });
    fcBtn.addEventListener('mouseleave', () => {
        fcBtn.style.background = '#1e2a3a';
    });

    bcBtn.addEventListener('mouseenter', () => {
        bcBtn.style.background = 'rgba(68, 136, 255, 0.2)';
    });
    bcBtn.addEventListener('mouseleave', () => {
        bcBtn.style.background = '#1e2a3a';
    });

    // Click handlers
    fcBtn.addEventListener('click', () => {
        modal.remove();
        pcbPlaceViaAndSwitchLayer('F.Cu');
    });

    bcBtn.addEventListener('click', () => {
        modal.remove();
        pcbPlaceViaAndSwitchLayer('B.Cu');
    });

    document.getElementById('viaCancelBtn').addEventListener('click', () => {
        modal.remove();
    });

    // Close on backdrop click
    modal.addEventListener('click', (e) => {
        if (e.target === modal) modal.remove();
    });
}

function pcbPlaceViaAndSwitchLayer(targetLayer) {
    if (!pcbState.routeCursor) return;

    // Place via at cursor
    const via = {
        x: pcbState.routeCursor.x,
        y: pcbState.routeCursor.y,
        drill: 0.3,
        diameter: 0.7,
        layers: ['F.Cu', 'B.Cu'],
        net: pcbState.routeNetName || '',
    };
    pcbState.routeVias.push(via);

    // Append cursor to route points
    pcbState.routePoints = appendRoutePoint(pcbState.routePoints, pcbState.routeCursor);

    // Switch to target layer
    pcbState.routeLayer = targetLayer;

    // Update UI
    dispatchPcbInteractionUpdated();
    pcbEditor.refresh();

    // Show toast
    if (typeof showToast === 'function') {
        showToast(`Switched to ${targetLayer}`, 'info');
    }
}

function pcbShowContextMenu(screenX, screenY, compHit) {
    pcbHideContextMenu();
    const menu = document.createElement('div');
    menu.id = 'pcbContextMenu';
    menu.style.cssText = `position:fixed;left:${screenX}px;top:${screenY}px;z-index:10000;background:#1a1d23;border:1px solid #3c3c3c;border-radius:6px;padding:4px 0;min-width:160px;box-shadow:0 4px 16px rgba(0,0,0,0.5);font-family:var(--font-ui);font-size:12px;`;
    const items = [];
    if (compHit) {
        items.push({ label: 'Rotate 90°', action: () => { pcbState.selectedComponentRef = compHit.ref; pcbRotateSelectedComponent(); }});
        items.push({ label: 'Delete', action: () => { pcbState.selectedComponentRef = compHit.ref; pcbDeleteSelectedComponent(); }});
        items.push({ type: 'separator' });
        items.push({ label: `Ref: ${compHit.ref}`, disabled: true });
    }
    items.push({ label: 'Zoom to Fit', action: () => pcbResetView() });
    items.push({ label: 'Select Tool (S)', action: () => pcbSetTool(PCB_TOOL.SELECT) });
    items.push({ label: 'Route Tool (R)', action: () => pcbSetTool(PCB_TOOL.ROUTE) });
    for (const item of items) {
        if (item.type === 'separator') {
            const sep = document.createElement('div');
            sep.style.cssText = 'height:1px;background:#3c3c3c;margin:4px 0;';
            menu.appendChild(sep);
            continue;
        }
        const btn = document.createElement('div');
        btn.style.cssText = `padding:6px 14px;cursor:${item.disabled ? 'default' : 'pointer'};color:${item.disabled ? '#666' : '#ccc'};transition:background 0.1s;`;
        btn.textContent = item.label;
        if (!item.disabled) {
            btn.addEventListener('mouseenter', () => btn.style.background = 'rgba(77,241,194,0.1)');
            btn.addEventListener('mouseleave', () => btn.style.background = 'transparent');
            btn.addEventListener('click', () => { pcbHideContextMenu(); item.action(); });
        }
        menu.appendChild(btn);
    }
    document.body.appendChild(menu);

    // Boundary detection - keep menu within viewport
    const rect = menu.getBoundingClientRect();
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;

    if (screenX + rect.width > viewportWidth) {
        menu.style.left = `${viewportWidth - rect.width - 10}px`;
    }
    if (screenY + rect.height > viewportHeight) {
        menu.style.top = `${viewportHeight - rect.height - 10}px`;
    }

    // Close on next click
    setTimeout(() => {
        document.addEventListener('click', pcbHideContextMenu, { once: true });
        document.addEventListener('contextmenu', pcbHideContextMenu, { once: true });
    }, 0);
}

function pcbHideContextMenu() {
    const menu = document.getElementById('pcbContextMenu');
    if (menu) menu.remove();
}

// ── Copy/Paste ──────────────────────────────────────────────────────────────

function pcbCopySelected() {
    if (!pcbState.boardModel || !pcbState.selectedComponentRef) return;
    const comp = pcbState.boardModel.components.find(c => c.ref === pcbState.selectedComponentRef);
    if (!comp) return;
    pcbState.clipboard = deepClone(comp);
    showToast('Copied ' + comp.ref, 'info', 1500);
}

async function pcbPasteClipboard() {
    if (!pcbState.boardModel || !pcbState.clipboard) return;
    const before = deepClone(pcbState.boardModel);
    const orig = pcbState.clipboard;
    // Find next available ref
    const existingRefs = new Set(pcbState.boardModel.components.map(c => c.ref));
    let baseRef = orig.ref.replace(/\d+$/, '');
    let num = parseInt(orig.ref.replace(/\D/g, '')) || 1;
    while (existingRefs.has(baseRef + num)) num++;
    const newRef = baseRef + num;
    const offset = 2; // 2mm offset from original
    const newComp = deepClone(orig);
    newComp.ref = newRef;
    newComp.x = orig.x + offset;
    newComp.y = orig.y + offset;
    pcbState.boardModel.components.push(newComp);
    pcbState.selectedComponentRef = newRef;
    pcbEditor.refresh();
    try {
        await pcbEditor.saveBoardModel();
        pcbEditor.pushHistory('paste component', before, deepClone(pcbState.boardModel));
        showToast('Pasted as ' + newRef, 'info', 1500);
    } catch (error) {
        pcbState.boardModel = before;
        pcbEditor.refresh();
        dispatchBoardSync(false, { error: error.message, fallback_saved: false });
    }
}

// ── Net Highlighting ────────────────────────────────────────────────────────

function pcbHighlightNet(netName) {
    pcbState.highlightedNet = netName;
    pcbEditor.requestOverlayRefresh();
}

function pcbClearNetHighlight() {
    pcbState.highlightedNet = null;
    pcbEditor.requestOverlayRefresh();
}

// ── Layer Solo ──────────────────────────────────────────────────────────────

function pcbToggleSoloLayer(layerName) {
    if (pcbState.soloLayer === layerName) {
        pcbState.soloLayer = null;
        // Restore all layers to their previous visibility
        for (const key in pcbState.visibleLayers) {
            pcbState.visibleLayers[key] = true;
        }
    } else {
        pcbState.soloLayer = layerName;
        // Hide all layers except the soloed one
        for (const key in pcbState.visibleLayers) {
            pcbState.visibleLayers[key] = (key === layerName);
        }
    }
    dispatchPcbLayerVisibilityUpdated();
    pcbEditor.requestOverlayRefresh();
}

// ── Trace Measurement ──────────────────────────────────────────────────────

function pcbMeasureDistance() {
    if (!pcbState.boardModel || !pcbState.lastPointerWorld) return null;
    // Find nearest pad to cursor
    const world = pcbState.lastPointerWorld;
    let nearest = null;
    let minDist = Infinity;
    for (const comp of pcbState.boardModel.components || []) {
        for (const pad of comp.pads || []) {
            const center = getComponentPadPosition(comp, pad);
            const d = Math.hypot(world.x - center.x, world.y - center.y);
            if (d < minDist && d < 2) { // within 2mm
                minDist = d;
                nearest = { x: center.x, y: center.y, ref: comp.ref, pad: pad.number };
            }
        }
    }
    return nearest;
}

// ── Board Dimensions ──────────────────────────────────────────────────────

function pcbGetBoardDimensions() {
    if (!pcbState.boardModel) return null;
    const model = pcbState.boardModel;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    // Include components
    for (const comp of model.components || []) {
        const bounds = getComponentBounds(comp);
        minX = Math.min(minX, bounds.minX);
        minY = Math.min(minY, bounds.minY);
        maxX = Math.max(maxX, bounds.maxX);
        maxY = Math.max(maxY, bounds.maxY);
    }
    // Include outline segments
    for (const seg of model.outline_segments || []) {
        for (const pt of seg.points || []) {
            minX = Math.min(minX, pt.x); minY = Math.min(minY, pt.y);
            maxX = Math.max(maxX, pt.x); maxY = Math.max(maxY, pt.y);
        }
        for (const key of ['start', 'end', 'center', 'mid']) {
            if (seg[key]) {
                minX = Math.min(minX, seg[key].x); minY = Math.min(minY, seg[key].y);
                maxX = Math.max(maxX, seg[key].x); maxY = Math.max(maxY, seg[key].y);
            }
        }
    }
    if (!Number.isFinite(minX)) return null;
    return { width: maxX - minX, height: maxY - minY, x: minX, y: minY };
}

function pcbToggleUndoHistory() {
    let overlay = document.getElementById('pcbUndoOverlay');
    if (overlay) { overlay.remove(); return; }
    overlay = document.createElement('div');
    overlay.id = 'pcbUndoOverlay';
    overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;z-index:9999;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;backdrop-filter:blur(4px);';
    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });

    const history = pcbEditor._history || [];
    const redoStack = pcbEditor._redoStack || [];
    const card = document.createElement('div');
    card.style.cssText = 'background:#1a1d23;border:1px solid #3c3c3c;border-radius:8px;padding:24px;max-width:420px;width:90%;max-height:70vh;overflow-y:auto;box-shadow:0 8px 32px rgba(0,0,0,0.5);';
    let html = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
        <h3 style="margin:0;color:#e0f0ed;font-size:15px;font-family:var(--font-ui);">Undo History</h3>
        <button onclick="this.closest('#pcbUndoOverlay').remove()" style="background:none;border:none;color:#889;font-size:18px;cursor:pointer;padding:4px 8px;">&times;</button>
    </div>`;
    if (history.length === 0 && redoStack.length === 0) {
        html += '<div style="color:#666;text-align:center;padding:20px;">No actions yet</div>';
    } else {
        html += '<div style="display:grid;gap:4px;">';
        for (let i = history.length - 1; i >= 0; i--) {
            html += `<div style="padding:6px 10px;background:#252830;border-radius:4px;font-size:12px;color:#ccc;font-family:var(--font-mono);">
                <span style="color:#4df1c2;">${i + 1}</span> ${history[i].name}
            </div>`;
        }
        for (let i = redoStack.length - 1; i >= 0; i--) {
            html += `<div style="padding:6px 10px;background:#252830;border-radius:4px;font-size:12px;color:#666;font-family:var(--font-mono);text-decoration:line-through;">
                redo: ${redoStack[i].name}
            </div>`;
        }
        html += '</div>';
    }
    card.innerHTML = html;
    overlay.appendChild(card);
    document.body.appendChild(overlay);
}

function pcbToggleShortcutHelp() {
    let overlay = document.getElementById('pcbShortcutOverlay');
    if (overlay) {
        overlay.remove();
        return;
    }
    overlay = document.createElement('div');
    overlay.id = 'pcbShortcutOverlay';
    overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;z-index:9999;background:rgba(0,0,0,0.7);display:flex;align-items:center;justify-content:center;backdrop-filter:blur(4px);';
    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });

    const shortcuts = [
        ['H', 'Pan tool'],
        ['S', 'Select tool'],
        ['X', 'Route tool'],
        ['V', 'Via tool (or place via while routing)'],
        ['O', 'Outline tool'],
        ['Del', 'Delete selected component/via/trace'],
        ['R', 'Rotate selected component 90°'],
        ['Ctrl+C', 'Copy selected component'],
        ['Ctrl+V', 'Paste component'],
        ['Ctrl+Z', 'Undo'],
        ['Ctrl+Shift+Z', 'Redo'],
        ['Shift+F', 'Fit view to board'],
        ['N', 'Highlight net (hover a pad first)'],
        ['D', 'Show board dimensions'],
        ['M', 'Measure distance to nearest pad'],
        ['U', 'Show undo history'],
        ['Esc', 'Cancel / clear highlights'],
        ['?', 'Toggle this help overlay'],
    ];

    const card = document.createElement('div');
    card.style.cssText = 'background:#1a1d23;border:1px solid #3c3c3c;border-radius:8px;padding:24px;max-width:420;width:90%;box-shadow:0 8px 32px rgba(0,0,0,0.5);';
    card.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
            <h3 style="margin:0;color:#e0f0ed;font-size:15px;font-family:var(--font-ui);">Keyboard Shortcuts</h3>
            <button onclick="this.closest('#pcbShortcutOverlay').remove()" style="background:none;border:none;color:#889;font-size:18px;cursor:pointer;padding:4px 8px;">&times;</button>
        </div>
        <div style="display:grid;gap:6px;">
            ${shortcuts.map(([key, desc]) => `
                <div style="display:flex;align-items:center;gap:12px;padding:6px 0;border-bottom:1px solid #2a2b30;">
                    <kbd style="background:#252830;border:1px solid #3c3c3c;border-radius:4px;padding:2px 8px;font-family:var(--font-mono);font-size:12px;color:#e0f0ed;min-width:90px;text-align:center;white-space:nowrap;">${key}</kbd>
                    <span style="color:#9aa6b2;font-size:13px;font-family:var(--font-ui);">${desc}</span>
                </div>
            `).join('')}
        </div>
    `;
    overlay.appendChild(card);
    document.body.appendChild(overlay);
}

async function pcbDeleteSelectedComponent() {
    if (!pcbState.boardModel || !pcbState.selectedComponentRef) return;
    const before = deepClone(pcbState.boardModel);
    const ref = pcbState.selectedComponentRef;
    pcbState.boardModel.components = (pcbState.boardModel.components || []).filter(c => c.ref !== ref);
    pcbState.selectedComponentRef = null;
    pcbEditor.refreshAirwires();
    pcbEditor.refresh();
    try {
        await pcbEditor.saveBoardModel();
        pcbEditor.pushHistory('delete component', before, deepClone(pcbState.boardModel));
    } catch (error) {
        pcbState.boardModel = before;
        pcbEditor.refresh();
        dispatchBoardSync(false, { error: error.message, fallback_saved: false });
    }
}

async function pcbDeleteHoveredVia() {
    if (!pcbState.boardModel || pcbState.hoveredViaIndex == null) return;
    const before = deepClone(pcbState.boardModel);
    const idx = pcbState.hoveredViaIndex;
    pcbState.boardModel.vias.splice(idx, 1);
    pcbState.hoveredViaIndex = null;
    pcbEditor.refreshAirwires();
    pcbEditor.refresh();
    try {
        await pcbEditor.saveBoardModel();
        pcbEditor.pushHistory('delete via', before, deepClone(pcbState.boardModel));
    } catch (error) {
        pcbState.boardModel = before;
        pcbEditor.refresh();
        dispatchBoardSync(false, { error: error.message, fallback_saved: false });
    }
}

async function pcbDeleteHoveredTrace() {
    if (!pcbState.boardModel || pcbState.hoveredTraceIndex == null) return;
    const before = deepClone(pcbState.boardModel);
    const idx = pcbState.hoveredTraceIndex;
    pcbState.boardModel.traces.splice(idx, 1);
    pcbState.hoveredTraceIndex = null;
    pcbEditor.refreshAirwires();
    pcbEditor.refresh();
    try {
        await pcbEditor.saveBoardModel();
        pcbEditor.pushHistory('delete trace', before, deepClone(pcbState.boardModel));
    } catch (error) {
        pcbState.boardModel = before;
        pcbEditor.refresh();
        dispatchBoardSync(false, { error: error.message, fallback_saved: false });
    }
}

async function pcbRotateSelectedComponent() {
    if (!pcbState.boardModel || !pcbState.selectedComponentRef) return;
    const before = deepClone(pcbState.boardModel);
    const comp = pcbState.boardModel.components.find(c => c.ref === pcbState.selectedComponentRef);
    if (!comp) return;
    comp.rotation = ((comp.rotation || 0) + 90) % 360;
    pcbEditor.refresh();
    try {
        await pcbEditor.saveBoardModel();
        pcbEditor.pushHistory('rotate component', before, deepClone(pcbState.boardModel));
    } catch (error) {
        pcbState.boardModel = before;
        pcbEditor.refresh();
        dispatchBoardSync(false, { error: error.message, fallback_saved: false });
    }
}

function pcbHandleKeyDown(event) {
    // Don't intercept keyboard shortcuts when typing in input fields
    if (event.target.tagName === 'TEXTAREA' || event.target.tagName === 'INPUT') {
        // Show hint when user tries to use shortcuts while typing
        const pcbShortcuts = ['h','s','r','v','o','n','d','m','u','?'];
        if (pcbShortcuts.includes(event.key.toLowerCase())) {
            if (typeof showToast === 'function') {
                showToast('Keyboard shortcuts disabled while typing. Press Escape first.', 'info');
            }
        }
        return;
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
        event.preventDefault();
        pcbEditor.saveBoardModel()
            .then(() => {
                dispatchBoardSync(true, { saved: true });
            })
            .catch((error) => {
                dispatchBoardSync(false, { error: error.message, fallback_saved: false });
            });
        return;
    }
    if (event.key === 'Escape') {
        pcbClearNetHighlight();
        pcbCancelDraw();
        return;
    }
    // Delete: remove selected component, hovered via, or hovered trace
    if (event.key === 'Delete' || event.key === 'Backspace') {
        if (!pcbState.boardModel) return;
        event.preventDefault();
        if (pcbState.selectedComponentRef) {
            pcbDeleteSelectedComponent();
        } else if (pcbState.hoveredViaIndex != null) {
            pcbDeleteHoveredVia();
        } else if (pcbState.hoveredTraceIndex != null) {
            pcbDeleteHoveredTrace();
        }
        return;
    }
    // R: rotate selected component by 90° (when component selected)
    if (event.key.toLowerCase() === 'r' && !event.ctrlKey && !event.metaKey && !event.altKey) {
        if (!pcbState.boardModel || !pcbState.selectedComponentRef) return;
        event.preventDefault();
        pcbRotateSelectedComponent();
        return;
    }
    // Ctrl+C: copy selected component
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'c') {
        if (!pcbState.boardModel || !pcbState.selectedComponentRef) return;
        event.preventDefault();
        pcbCopySelected();
        return;
    }
    // Ctrl+V: paste component
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'v' && pcbState.mode !== PCB_MODE.ROUTE) {
        if (!pcbState.boardModel || !pcbState.clipboard) return;
        event.preventDefault();
        pcbPasteClipboard();
        return;
    }
    // N: highlight net of hovered pad
    if (event.key.toLowerCase() === 'n' && !event.ctrlKey && !event.metaKey) {
        if (pcbState.hoveredPadKey) {
            const [ref, padNum] = pcbState.hoveredPadKey.split(':');
            const netName = pcbState.boardModel ? getNetNameForPad(pcbState.boardModel, ref, padNum) : '';
            if (netName && netName !== '_manual') {
                pcbHighlightNet(netName);
            }
        } else {
            pcbClearNetHighlight();
        }
        return;
    }
    // D: show board dimensions
    if (event.key.toLowerCase() === 'd' && !event.ctrlKey && !event.metaKey) {
        const dims = pcbGetBoardDimensions();
        if (dims) {
            showToast(`Board: ${dims.width.toFixed(1)}mm × ${dims.height.toFixed(1)}mm`, 'info', 3000);
        }
        return;
    }
    // M: measure distance from nearest pad to cursor
    if (event.key.toLowerCase() === 'm' && !event.ctrlKey && !event.metaKey) {
        const nearest = pcbMeasureDistance();
        if (nearest && pcbState.lastPointerWorld) {
            const d = Math.hypot(nearest.x - pcbState.lastPointerWorld.x, nearest.y - pcbState.lastPointerWorld.y);
            showToast(`${nearest.ref}:${nearest.pad} → cursor: ${d.toFixed(2)}mm`, 'info', 3000);
        }
        return;
    }
    // U: show undo history
    if (event.key.toLowerCase() === 'u' && !event.ctrlKey && !event.metaKey) {
        event.preventDefault();
        pcbToggleUndoHistory();
        return;
    }
    // ?: show keyboard shortcut help overlay
    if (event.key === '?' || (event.shiftKey && event.key === '/')) {
        event.preventDefault();
        pcbToggleShortcutHelp();
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
    if (event.key.toLowerCase() === 'x') {
        pcbSetTool(PCB_TOOL.ROUTE);
        return;
    }
    if (event.key.toLowerCase() === 'v' && !event.ctrlKey && !event.metaKey && pcbState.mode !== PCB_MODE.ROUTE) {
        pcbSetTool(PCB_TOOL.VIA);
        return;
    }
    if (event.key.toLowerCase() === 'o' && !event.ctrlKey && !event.metaKey) {
        pcbSetTool(PCB_TOOL.OUTLINE);
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
window.pcbSetMode = pcbSetMode;
window.pcbSetTool = pcbSetTool;
window.pcbSetRouteStyle = pcbSetRouteStyle;
window.pcbHandleWheel = pcbHandleWheel;
window.pcbHandleMouseDown = pcbHandleMouseDown;
window.pcbHandleMouseMove = pcbHandleMouseMove;
window.pcbHandleMouseUp = pcbHandleMouseUp;
window.pcbHandleKeyDown = pcbHandleKeyDown;
window.pcbCancelDraw = pcbCancelDraw;
window.pcbFetchRatsnest = pcbFetchRatsnest;
window.pcbDeleteHoveredTrace = pcbDeleteHoveredTrace;
window.pcbState = pcbState;
window.PCB_MODE = PCB_MODE;
window.PCB_TOOL = PCB_TOOL;