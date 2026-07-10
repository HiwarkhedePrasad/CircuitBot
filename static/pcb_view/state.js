let _boardModel = null;
let pcbState = {
    get boardModel() {
        return _boardModel;
    },
    set boardModel(val) {
        if (typeof normalizeBoardModel === 'function') {
            _boardModel = val ? normalizeBoardModel(val) : null;
        } else {
            _boardModel = val;
        }
    },
    renderMode: 'full',
    mode: PCB_MODE.IDLE,
    activeTool: PCB_TOOL.PAN,
    ghostProposal: null,
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
    hoveredViaIndex: null,
    hoveredTraceIndex: null,
    dragComponentRef: null,
    dragViaIndex: null,
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
    pointerDownScreen: null,
    pointerDownWorld: null,
    pointerDragMoved: false,
    undoStack: [],
    redoStack: [],
    visibleLayers: {},
    outlinePoints: [],
    outlineDraft: null,
    clipboard: null,
    highlightedNet: null,
    soloLayer: null,
};

function dispatchPcbViewChanged() {
    try {
        window.dispatchEvent(new CustomEvent('pcb:view-changed', {
            detail: { bounds: pcbGetViewBounds() },
        }));
    } catch (_) {}
}

function dispatchPcbInteractionUpdated() {
    try {
        window.dispatchEvent(new CustomEvent('pcb:interaction-updated', {
            detail: {
                tool: pcbState.activeTool,
                mode: pcbState.mode,
                routeLayer: pcbState.routeLayer,
                routeWidth: pcbState.routeWidth,
                toolsEnabled: pcbToolsEnabled(),
            },
        }));
    } catch (_) {}
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

function dispatchPcbLayerVisibilityUpdated() {
    try {
        window.dispatchEvent(new CustomEvent('pcb:layers-updated', {
            detail: { visibleLayers: { ...(pcbState.visibleLayers || {}) } },
        }));
    } catch (_) {}
}
