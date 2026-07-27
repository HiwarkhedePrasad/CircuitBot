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
    hoveredSegmentIndex: null,
    selectedTraceIndices: [],
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
    routeAngleMode: '45',
    routePosture: 0,
    snapPadTarget: null,
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
    // M3: Findings (constraint violations displayed on objects)
    findings: [],
    findingsVisible: true,
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

// ── M4: Real-time constraint checking (debounced) ────────────────────────
let _constraintCheckTimer = null;
let _constraintCheckPending = false;

function pcbScheduleConstraintCheck() {
    if (_constraintCheckPending) return;
    _constraintCheckPending = true;
    clearTimeout(_constraintCheckTimer);
    _constraintCheckTimer = setTimeout(async () => {
        _constraintCheckPending = false;
        if (!pcbState.boardModel) return;
        try {
            const sessionId = window.circuitbotChatSessionId || 'default';
            const resp = await fetch('/api/constraint_check', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: sessionId }),
            });
            const data = await resp.json();
            if (data.violations && data.violations.length > 0) {
                pcbState.findings = data.violations.map(v => ({
                    id: v.id,
                    entity_type: v.entity_ids && v.entity_ids.length ? 'component' : 'net',
                    entity_id: v.entity_ids && v.entity_ids.length ? v.entity_ids[0] : '',
                    severity: v.severity,
                    title: v.code,
                    description: v.description,
                }));
                if (typeof pcbEditor !== 'undefined') {
                    pcbEditor.requestOverlayRefresh();
                }
            } else {
                if (pcbState.findings.length > 0) {
                    pcbState.findings = [];
                    if (typeof pcbEditor !== 'undefined') {
                        pcbEditor.requestOverlayRefresh();
                    }
                }
            }
        } catch (_) {}
    }, 1000); // 1 second debounce
}
