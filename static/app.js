let socket = null;
const CIRCUITBOT_LAYOUT_VERSION = 'v8-backend-routing';
console.log('%c[CircuitBot] Layout engine ' + CIRCUITBOT_LAYOUT_VERSION + ' loaded', 'color:#a371f7;font-weight:bold');

document.addEventListener('DOMContentLoaded', () => {
    const searchInput = document.getElementById('searchInput');
    const searchBtn = document.getElementById('searchBtn');
    const searchResults = document.getElementById('searchResults');
    const loading = document.getElementById('loading');
    const addBtn = document.getElementById('addBtn');
    const viewSchematicBtn = document.getElementById('viewSchematicBtn');
    const viewSymbolBtn = document.getElementById('viewSymbolBtn');
    const componentList = document.getElementById('componentList');
    const compCount = document.getElementById('compCount');
    const modeIndicator = document.getElementById('modeIndicator');
    const routePrompt = document.getElementById('routePrompt');
    const agentBtn = document.getElementById('agentBtn');
    const agentPrompt = document.getElementById('agentPrompt');
    const agentConversation = document.getElementById('agentConversation');
    const agentStatus = document.getElementById('agentStatus');
    const coordDisplay = document.getElementById('coordDisplay');
    const zoomLevelDisplay = document.getElementById('zoomLevel');
    const pcbToolbar = document.getElementById('pcbToolbar');
    const pcbPanToolBtn = document.getElementById('pcbPanToolBtn');
    const pcbSelectToolBtn = document.getElementById('pcbSelectToolBtn');
    const pcbRouteToolBtn = document.getElementById('pcbRouteToolBtn');
    const pcbViaToolBtn = document.getElementById('pcbViaToolBtn');
    const pcbOutlineToolBtn = document.getElementById('pcbOutlineToolBtn');
    const pcbHelpBtn = document.getElementById('pcbHelpBtn');
    const pcbLayerSelect = document.getElementById('pcbLayerSelect');
    const pcbWidthSelect = document.getElementById('pcbWidthSelect');
    const pcbLayersPanel = document.getElementById('pcbLayersPanel');
    const pcbLayersList = document.getElementById('pcbLayersList');

    let selectedComponent = null;
    let currentPreviewOps = null;
    let agentBusy = false;
    let currentSchematic = new Schematic();
    let schematicWireStart = null;
    let activeCanvasTool = 'pointer';
    let pendingImagePlacement = null;

    // Schematic grid snap (1.27mm) — distinct from PCB grid snap (0.254mm) in utils.js
    const snapToSchematicGrid = (v) => Math.round(v / 1.27) * 1.27;
    let schematicEditorMode = 'wire'; // 'wire' | 'netlabel'
    const MAX_CONVERSATION_MESSAGES = 2000;
    const MAX_CONVERSATION_ITEMS = 2000;
    const MAX_APPROVAL_BUTTONS = 10;

    // ── Canonical State Sync ──────────────────────────────────────────────────
    // Tracks the last acknowledged revision from the backend.
    // Used for optimistic locking on full-snapshot syncs.
    let lastSyncedRevision = 0;
    let _flushTimer = null;

    /**
     * Serialize the current schematic and POST to /api/sync_schematic_state.
     * Returns the new revision on success, null on failure/conflict.
     */
    async function flushSchematicState() {
        if (!currentSchematic) return null;
        const snapshot = currentSchematic.toDesignSnapshot(lastSyncedRevision);
        try {
            const res = await fetch(apiUrl('/api/sync_schematic_state'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    session_id: sessionId,
                    snapshot,
                    expected_revision: lastSyncedRevision,
                }),
            });
            const result = await res.json();
            if (res.status === 409) {
                // Stale revision — fetch current state from server
                lastSyncedRevision = result.current_revision || 0;
                return null;
            }
            if (result.ok) {
                lastSyncedRevision = result.revision;
                return result.revision;
            }
        } catch (e) {
            console.warn('[CircuitBot] Schematic sync failed:', e.message);
        }
        return null;
    }

    /**
     * Debounced flush — batches rapid edits into a single sync.
     * Call this after any local canvas mutation (add, remove, label change).
     */
    function scheduleSchematicFlush() {
        if (_flushTimer) clearTimeout(_flushTimer);
        _flushTimer = setTimeout(() => flushSchematicState(), 150);
    }

    // ── Tab Management ────────────────────────────────────────────────────────

    function setActiveTab(tabId) {
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        const tab = document.getElementById(tabId);
        if (tab) tab.classList.add('active');
    }

    function setPcbToolbarVisibility(visible) {
        if (!pcbToolbar) return;
        pcbToolbar.classList.toggle('hidden', !visible);
        if (pcbLayersPanel) {
            pcbLayersPanel.classList.toggle('hidden', !visible);
        }
    }

    function renderPcbLayersPanel() {
        if (!pcbLayersPanel || !pcbLayersList) return;
        const boardModel = window.pcbState ? pcbState.boardModel : null;
        const visible = !!boardModel && !!(viewPCBBtn && viewPCBBtn.classList.contains('active'));
        pcbLayersPanel.classList.toggle('hidden', !visible);
        if (!visible) {
            pcbLayersList.innerHTML = '';
            return;
        }
        ensurePcbLayerVisibility(boardModel);
        const layerNames = sortedBoardLayerNames(boardModel);
        pcbLayersList.innerHTML = '';
        const fragment = document.createDocumentFragment();
        for (const layerName of layerNames) {
            const isVisible = isPcbLayerVisible(layerName);
            const row = document.createElement('div');
            row.className = `pcb-layer-row${isVisible ? '' : ' is-hidden'}`;
            row.setAttribute('role', 'button');
            row.setAttribute('tabindex', '0');
            row.dataset.layerName = layerName;

            const swatch = document.createElement('span');
            swatch.className = 'pcb-layer-swatch';
            swatch.style.backgroundColor = getPcbLayerColor(layerName);

            const toggle = document.createElement('button');
            toggle.className = 'pcb-layer-toggle';
            toggle.type = 'button';
            toggle.textContent = isVisible ? '◉' : '◌';
            toggle.title = `${isVisible ? 'Hide' : 'Show'} ${layerName}`;

            const label = document.createElement('span');
            label.className = 'pcb-layer-name';
            label.textContent = getPcbLayerLabel(layerName);
            label.title = layerName;

            row.appendChild(swatch);
            row.appendChild(toggle);
            row.appendChild(label);
            fragment.appendChild(row);
        }
        pcbLayersList.appendChild(fragment);
    }

    function pcbLayersListClickHandler(event) {
        const row = event.target.closest('[data-layer-name]');
        if (!row) return;
        const layerName = row.dataset.layerName;
        if (!layerName) return;
        const isVisible = isPcbLayerVisible(layerName);
        setPcbLayerVisible(layerName, !isVisible);
        pcbDrawCurrent();
    }

    function pcbLayersListKeyHandler(event) {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        const row = event.target.closest('[data-layer-name]');
        if (!row) return;
        const layerName = row.dataset.layerName;
        if (!layerName) return;
        event.preventDefault();
        const isVisible = isPcbLayerVisible(layerName);
        setPcbLayerVisible(layerName, !isVisible);
        pcbDrawCurrent();
    }

    pcbLayersList.addEventListener('click', pcbLayersListClickHandler);
    pcbLayersList.addEventListener('keydown', pcbLayersListKeyHandler);

    function updatePcbToolbar(detail = {}) {
        const activeTool = detail.tool || (window.pcbState && window.pcbState.activeTool) || 'pan';
        const routeLayer = detail.routeLayer || (window.pcbState && window.pcbState.routeLayer) || 'F.Cu';
        const routeWidth = detail.routeWidth != null ? detail.routeWidth : (window.pcbState && window.pcbState.routeWidth) || 0.254;
        const enabled = detail.toolsEnabled != null ? detail.toolsEnabled : !!(window.pcbState && window.pcbState.boardModel);
        const tools = [
            [pcbPanToolBtn, window.PCB_TOOL ? window.PCB_TOOL.PAN : 'pan'],
            [pcbSelectToolBtn, window.PCB_TOOL ? window.PCB_TOOL.SELECT : 'select'],
            [pcbRouteToolBtn, window.PCB_TOOL ? window.PCB_TOOL.ROUTE : 'route'],
            [pcbViaToolBtn, window.PCB_TOOL ? window.PCB_TOOL.VIA : 'via'],
            [pcbOutlineToolBtn, window.PCB_TOOL ? window.PCB_TOOL.OUTLINE : 'outline'],
        ];
        tools.forEach(([button, tool]) => {
            if (!button) return;
            button.disabled = !enabled;
            button.classList.toggle('active', activeTool === tool);
        });
        if (pcbLayerSelect) {
            pcbLayerSelect.disabled = !enabled;
            pcbLayerSelect.value = routeLayer;
        }
        if (pcbWidthSelect) {
            pcbWidthSelect.disabled = !enabled;
            const normalizedWidth = String(Number(routeWidth));
            const matchingOption = Array.from(pcbWidthSelect.options).find((option) => String(Number(option.value)) === normalizedWidth);
            if (matchingOption) pcbWidthSelect.value = matchingOption.value;
        }
    }

    if (viewSymbolBtn) {
        viewSymbolBtn.addEventListener('click', () => {
            if (currentPreviewOps) {
                setActiveTab('viewSymbolBtn');
                showViewport('symbol');
                renderOps(currentPreviewOps);
                setPcbToolbarVisibility(false);
                modeIndicator.classList.add('hidden');
            }
        });
    }

    const viewPCBBtn = document.getElementById('viewPCBBtn');
    const exportPCBBtn = document.getElementById('exportPCBBtn');
    const importPCBBtn = document.getElementById('importPCBBtn');
    const pcbUploadArea = document.getElementById('pcbUploadArea');
    const pcbFileInput = document.getElementById('pcbFileInput');

    if (viewSchematicBtn) {
        viewSchematicBtn.addEventListener('click', () => {
            if (!currentSchematic) currentSchematic = new Schematic();
            setActiveTab('viewSchematicBtn');
            enterSchematicView();
            setPcbToolbarVisibility(false);
            if (pcbUploadArea) pcbUploadArea.classList.add('hidden');
            document.getElementById('routePrompt').classList.remove('hidden');
        });
    }

    if (viewPCBBtn) {
        viewPCBBtn.addEventListener('click', () => {
            if (pcbState.boardModel) {
                enterPCBView();
            } else {
                setActiveTab('viewPCBBtn');
                showViewport('pcb');
                setPcbToolbarVisibility(true);
                const routePromptContainer = document.getElementById('routePrompt')?.closest('.floating-route-input');
                if (routePromptContainer) routePromptContainer.classList.add('hidden');
                pcbSetupCanvas();
                showPcbUploadOverlay();
            }
        });
    }

    function showPcbUploadOverlay() {
        pcbUploadArea.classList.remove('hidden');
        const tsc = document.getElementById('tscircuit-container');
        if (tsc) tsc.style.display = 'none';

        // Update overlay content with helpful guidance
        const content = pcbUploadArea.querySelector('.pcb-upload-content');
        if (content) {
            content.innerHTML = `
                <div class="pcb-upload-icon">📤</div>
                <h3>Load a PCB Design</h3>
                <p>Upload a <code>.kicad_pcb</code> file to start editing your board</p>
                <div class="pcb-upload-drag-hint">
                    Drag & drop a file here, or click to browse
                </div>
                <p class="pcb-upload-sub">You can also ask the AI to design a circuit first</p>
            `;
        }
    }

    function ensurePcbBoardReady() {
        if (window.pcbState && pcbState.boardModel) return;
        pcbLoadBoard({
            components: [],
            traces: [],
            vias: [],
            nets: [],
            outline_segments: [],
            _render_from_model: true,
        }, { fetchRatsnest: false });
    }

    function enterPCBView(refreshGeometry = true) {
        setActiveTab('viewPCBBtn');
        showViewport('pcb');
        setPcbToolbarVisibility(true);
        const routePromptContainer = document.getElementById('routePrompt')?.closest('.floating-route-input');
        if (routePromptContainer) routePromptContainer.classList.add('hidden');
        pcbUploadArea.classList.add('hidden');
        ensurePcbBoardReady();
        pcbSetupCanvas();
        pcbDraw();
        renderPcbLayersPanel();
        if (refreshGeometry) {
            refreshPcbGeometryFromBackend().catch((err) => addLogEntry(`PCB geometry refresh failed: ${err.message}`, 'error'));
        }
    }

    if (pcbPanToolBtn) {
        pcbPanToolBtn.addEventListener('click', () => pcbSetTool(PCB_TOOL.PAN));
    }
    if (pcbSelectToolBtn) {
        pcbSelectToolBtn.addEventListener('click', () => pcbSetTool(PCB_TOOL.SELECT));
    }
    if (pcbRouteToolBtn) {
        pcbRouteToolBtn.addEventListener('click', () => pcbSetTool(PCB_TOOL.ROUTE));
    }
    if (pcbViaToolBtn) {
        pcbViaToolBtn.addEventListener('click', () => pcbSetTool(PCB_TOOL.VIA));
    }
    if (pcbOutlineToolBtn) {
        pcbOutlineToolBtn.addEventListener('click', () => pcbSetTool(PCB_TOOL.OUTLINE));
    }
    if (pcbHelpBtn) {
        pcbHelpBtn.addEventListener('click', () => pcbToggleShortcutHelp());
    }
    if (pcbLayerSelect) {
        pcbLayerSelect.addEventListener('change', () => pcbSetRouteStyle({ layer: pcbLayerSelect.value }));
    }
    if (pcbWidthSelect) {
        pcbWidthSelect.addEventListener('change', () => pcbSetRouteStyle({ width: Number(pcbWidthSelect.value) }));
    }
    window.addEventListener('pcb:interaction-updated', (event) => {
        updatePcbToolbar(event.detail || {});
        updatePcbInteractionSurface((event.detail || {}).tool || 'pan');
    });
    window.addEventListener('pcb:layers-updated', () => {
        renderPcbLayersPanel();
    });
    updatePcbToolbar({ toolsEnabled: false });
    updatePcbInteractionSurface('pan');
    if (importPCBBtn) importPCBBtn.disabled = false;

    // ── Renderer (PixiJS) ────────────────────────────────────────────────────

    let renderer = null;
    let _initialRenderZoom = 1;

    function getRenderer() {
        if (!renderer) {
            renderer = new SchematicRenderer('canvasContainer', {
                onSelect: () => {},
                onPinClick: (pin, world) => {
                    handleSchematicPinClick(pin, world);
                },
                onNetLabelClick: (nl, world) => {
                    handleNetLabelClick(nl, world);
                },
                onWireClick: (worldX, worldY) => {
                    return handleWireClick(worldX, worldY);
                },
                onWireDeselect: () => {
                    deselectWire();
                },
                onCoordChange: (wx, wy) => {
                    if (coordDisplay) coordDisplay.textContent = `X: ${wx.toFixed(2)} Y: ${wy.toFixed(2)}`;
                },
                onZoomChange: (zoom) => {
                    const pct = Math.round(zoom / (_initialRenderZoom || zoom) * 100);
                    if (zoomLevelDisplay) zoomLevelDisplay.textContent = `${pct}%`;
                },
                onComponentMoved: (comp, dx, dy) => {
                    const event = {
                        edit_event_type: 'schematic_move_component',
                        ref_des: comp.refDesignator,
                        new_center: { x: comp.x, y: comp.y },
                    };
                    applySchematicEditEvents([event]);
                },
                onContextMenu: (screenX, screenY, world) => {
                    openSchematicContextMenu(screenX, screenY, world);
                },
                onImageMarkerToggle: (marker) => {
                    if (marker._imageRevealed) {
                        showToast(`Marker #${marker.markerNumber} shown`, 'info', 1500);
                    } else {
                        showToast(`Marker #${marker.markerNumber} hidden`, 'info', 1000);
                    }
                    scheduleSchematicFlush();
                    updateMarkerList();
                },
                onImageMarkerDelete: (marker) => {
                    deleteImageMarker(marker);
                },
                onImageMarkerToolbarAction: (marker, action) => {
                    if (action === 'inspect') {
                        showImageInspector(marker);
                    } else if (action === 'toggle') {
                        marker._imageRevealed = !marker._imageRevealed;
                        getRenderer().refresh();
                        scheduleSchematicFlush();
                        updateMarkerList();
                    } else if (action === 'delete') {
                        deleteImageMarker(marker);
                    }
                },
                onImageMarkerDblClick: (marker) => {
                    showImageInspector(marker);
                },
                onMarkerMoved: (marker, dx, dy) => {
                    // Save original position for undo
                    const origX = marker.x - dx;
                    const origY = marker.y + dy;
                    scheduleSchematicFlush();
                    showToast(`Marker ${marker.markerNumber} moved`, 'info', 3000, {
                        label: 'Undo',
                        onClick: () => {
                            marker.x = origX;
                            marker.y = origY;
                            getRenderer().refresh();
                            scheduleSchematicFlush();
                        },
                    });
                },
            });
            _initialRenderZoom = renderer.zoom;
        }
        return renderer;
    }

    // ── Smart Wire Routing ─────────────────────────────────────────────────
    // Enhanced wire routing with multi-bend support and component avoidance

    /**
     * Check if a point is inside a component's bounding box (with clearance)
     */
    function isPointInComponent(px, py, comp, clearance = 1.27) {
        const bbox = comp.geomBBox;
        const cx = comp.x + bbox.x - clearance;
        const cy = comp.y + bbox.y - clearance;
        const cw = bbox.w + clearance * 2;
        const ch = bbox.h + clearance * 2;
        return px >= cx && px <= cx + cw && py >= cy && py <= cy + ch;
    }

    /**
     * Check if a wire segment intersects a component's bounding box
     */
    function segmentIntersectsComponent(x1, y1, x2, y2, comp, clearance = 1.27) {
        const bbox = comp.geomBBox;
        const cx = comp.x + bbox.x - clearance;
        const cy = comp.y + bbox.y - clearance;
        const cw = bbox.w + clearance * 2;
        const ch = bbox.h + clearance * 2;

        // Check if either endpoint is inside the box
        if (isPointInComponent(x1, y1, comp, clearance) ||
            isPointInComponent(x2, y2, comp, clearance)) {
            return true;
        }

        // Check segment vs rectangle intersection
        const left = cx, right = cx + cw, top = cy, bottom = cy + ch;

        // Vertical segment
        if (Math.abs(x1 - x2) < 0.001) {
            if (x1 >= left && x1 <= right) {
                const minY = Math.min(y1, y2), maxY = Math.max(y1, y2);
                return !(maxY < top || minY > bottom);
            }
            return false;
        }

        // Horizontal segment
        if (Math.abs(y1 - y2) < 0.001) {
            if (y1 >= top && y1 <= bottom) {
                const minX = Math.min(x1, x2), maxX = Math.max(x1, x2);
                return !(maxX < left || minX > right);
            }
            return false;
        }

        return false;
    }

    /**
     * Check if a path intersects any component
     */
    function pathIntersectsComponent(path, comp, clearance = 1.27) {
        for (let i = 0; i < path.length - 1; i++) {
            if (segmentIntersectsComponent(
                path[i].x, path[i].y,
                path[i + 1].x, path[i + 1].y,
                comp, clearance
            )) {
                return true;
            }
        }
        return false;
    }

    /**
     * Find available routing points around a component
     */
    function getRoutingPointsAroundComponent(comp, clearance = 2.54) {
        const bbox = comp.geomBBox;
        const cx = comp.x + bbox.x;
        const cy = comp.y + bbox.y;
        const cw = bbox.w;
        const ch = bbox.h;

        return [
            { x: cx - clearance, y: cy - clearance },           // Top-left
            { x: cx + cw + clearance, y: cy - clearance },      // Top-right
            { x: cx - clearance, y: cy + ch + clearance },      // Bottom-left
            { x: cx + cw + clearance, y: cy + ch + clearance }, // Bottom-right
            { x: cx + cw / 2, y: cy - clearance },              // Top-center
            { x: cx + cw / 2, y: cy + ch + clearance },         // Bottom-center
            { x: cx - clearance, y: cy + ch / 2 },              // Left-center
            { x: cx + cw + clearance, y: cy + ch / 2 },         // Right-center
        ];
    }

    /**
     * Smart wire routing with multi-bend support and component avoidance
     * Returns array of waypoints [{x, y}, ...]
     */
    function smartWireRoute(startPin, endPin, components = []) {
        const sx = snapToSchematicGrid(startPin.x);
        const sy = snapToSchematicGrid(startPin.y);
        const ex = snapToSchematicGrid(endPin.x);
        const ey = snapToSchematicGrid(endPin.y);

        // Simple case: same X or same Y - direct orthogonal path
        if (Math.abs(sx - ex) < 0.001 || Math.abs(sy - ey) < 0.001) {
            return [
                { x: sx, y: sy },
                { x: ex, y: ey }
            ].filter((pt, i, arr) => {
                if (i === 0) return true;
                return Math.abs(arr[i - 1].x - pt.x) > 0.001 || Math.abs(arr[i - 1].y - pt.y) > 0.001;
            });
        }

        // Try simple L-path first
        const lPath = [
            { x: sx, y: sy },
            { x: ex, y: sy },
            { x: ex, y: ey }
        ].filter((pt, i, arr) => {
            if (i === 0) return true;
            return Math.abs(arr[i - 1].x - pt.x) > 0.001 || Math.abs(arr[i - 1].y - pt.y) > 0.001;
        });

        // Check if L-path intersects any component
        let pathBlocked = false;
        for (const comp of components) {
            if (pathIntersectsComponent(lPath, comp)) {
                pathBlocked = true;
                break;
            }
        }

        // If no obstruction, use L-path
        if (!pathBlocked) {
            return lPath;
        }

        // Try alternative L-path
        const altLPath = [
            { x: sx, y: sy },
            { x: sx, y: ey },
            { x: ex, y: ey }
        ].filter((pt, i, arr) => {
            if (i === 0) return true;
            return Math.abs(arr[i - 1].x - pt.x) > 0.001 || Math.abs(arr[i - 1].y - pt.y) > 0.001;
        });

        pathBlocked = false;
        for (const comp of components) {
            if (pathIntersectsComponent(altLPath, comp)) {
                pathBlocked = true;
                break;
            }
        }

        if (!pathBlocked) {
            return altLPath;
        }

        // Complex routing needed - find waypoints around obstacles
        const waypoints = [];
        waypoints.push({ x: sx, y: sy });

        // Find nearest component blocking the path
        let blockingComp = null;
        let minDist = Infinity;
        for (const comp of components) {
            if (pathIntersectsComponent(lPath, comp)) {
                const dx = (comp.x + comp.geomBBox.w / 2) - sx;
                const dy = (comp.y + comp.geomBBox.h / 2) - sy;
                const dist = Math.sqrt(dx * dx + dy * dy);
                if (dist < minDist) {
                    minDist = dist;
                    blockingComp = comp;
                }
            }
        }

        if (blockingComp) {
            // Route around the component
            const bbox = blockingComp.geomBBox;
            const compCx = blockingComp.x + bbox.w / 2;
            const compCy = blockingComp.y + bbox.h / 2;
            const clearance = 2.54;

            // Determine routing direction
            const goHorizontalFirst = Math.abs(ex - sx) > Math.abs(ey - sy);

            if (goHorizontalFirst) {
                // Go horizontal first, then vertical, then horizontal
                const midY = sy < compCy ? blockingComp.y - clearance : blockingComp.y + bbox.h + clearance;
                waypoints.push({ x: sx, y: midY });
                waypoints.push({ x: ex, y: midY });
            } else {
                // Go vertical first, then horizontal, then vertical
                const midX = sx < compCx ? blockingComp.x - clearance : blockingComp.x + bbox.w + clearance;
                waypoints.push({ x: midX, y: sy });
                waypoints.push({ x: midX, y: ey });
            }
        } else {
            // Fallback: Z-path
            const midX = snapToSchematicGrid((sx + ex) / 2);
            waypoints.push({ x: midX, y: sy });
            waypoints.push({ x: midX, y: ey });
        }

        waypoints.push({ x: ex, y: ey });

        // Clean up path: remove collinear points
        const cleanedPath = [waypoints[0]];
        for (let i = 1; i < waypoints.length; i++) {
            const prev = cleanedPath[cleanedPath.length - 1];
            const curr = waypoints[i];
            if (Math.abs(prev.x - curr.x) > 0.001 || Math.abs(prev.y - curr.y) > 0.001) {
                cleanedPath.push(curr);
            }
        }

        return cleanedPath;
    }

    // Legacy function kept for backward compatibility
    function orthogonalWirePath(a, b) {
        const midX = snapToSchematicGrid((a.x + b.x) / 2);
        return [
            { x: snapToSchematicGrid(a.x), y: snapToSchematicGrid(a.y) },
            { x: midX, y: snapToSchematicGrid(a.y) },
            { x: midX, y: snapToSchematicGrid(b.y) },
            { x: snapToSchematicGrid(b.x), y: snapToSchematicGrid(b.y) },
        ].filter((pt, index, arr) => {
            if (index === 0) return true;
            const prev = arr[index - 1];
            return Math.abs(prev.x - pt.x) > 0.001 || Math.abs(prev.y - pt.y) > 0.001;
        });
    }

    async function applySchematicEditEvents(editEvents) {
        if (!Array.isArray(editEvents) || editEvents.length === 0) return false;
        const hasMoveEvents = editEvents.some(e =>
            (e.edit_event_type || '').includes('move') ||
            (e.edit_event_type || '').includes('location')
        );
        try {
            const res = await fetch(apiUrl('/api/apply_edits'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ edit_events: editEvents }),
            });
            const data = await res.json();
            if (!res.ok || !data.ok) {
                throw new Error(data.error || `apply_edits failed (${res.status})`);
            }
            if (currentSchematic && data.wire_paths) {
                currentSchematic.wirePaths = data.wire_paths;
                if (typeof currentSchematic.recomputeJunctions === 'function') {
                    currentSchematic.recomputeJunctions();
                }
            }
            if (hasMoveEvents && currentSchematic && data.component_placements) {
                for (const p of data.component_placements) {
                    const comp = currentSchematic.components.find(c => c.refDesignator === p.ref_des);
                    if (comp) { comp.x = p.x; comp.y = p.y; }
                }
            }
            if (renderer) renderer.refresh();
            updateCompletenessBadge(
                currentSchematic ? currentSchematic.wirePaths : [],
                currentSchematic ? currentSchematic.netlist : []
            );
            return true;
        } catch (err) {
            const saved = await persistSchematicLayoutFallback();
            addLogEntry(
                'Schematic edit sync failed: ' + (err.message || err) + (saved ? ' (layout saved)' : ''),
                saved ? 'log' : 'error'
            );
            return false;
        }
    }

    async function persistSchematicLayoutFallback() {
        if (!currentSchematic) return false;
        try {
            const res = await fetch(apiUrl('/api/save_layout'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    placements: currentSchematic.components.map(c => ({
                        ref_des: c.refDesignator,
                        x: c.x,
                        y: c.y,
                    })),
                    wire_paths: currentSchematic.wirePaths || [],
                    power_labels: currentSchematic.powerLabels || [],
                    net_labels: currentSchematic.netLabels.map(l => ({
                        id: l.id,
                        net: l.net,
                        x: l.x,
                        y: l.y,
                        orientation: l.orientation,
                        pin: l.pin,
                    })),
                }),
            });
            return res.ok;
        } catch (_) {
            return false;
        }
    }

    function handleSchematicPinClick(pin, world) {
        if (!currentSchematic || !renderer) return;

        // In net label mode, clicking a pin creates/assigns a net label
        if (schematicEditorMode === 'netlabel') {
            handleNetLabelPinClick(pin);
            return;
        }

        if (!schematicWireStart) {
            schematicWireStart = pin;
            renderer.setActivePin(pin);
            renderer.setWireDraft(pin, world);
            addLogEntry(`Wire start: ${pin.key}`, 'log');
            return;
        }
        if (schematicWireStart.key === pin.key) {
            schematicWireStart = null;
            renderer.clearWireDraft();
            return;
        }

        const start = schematicWireStart;
        const wireId = `schematic_wire_${Date.now()}`;
        // Use smart routing with component avoidance
        const components = currentSchematic ? currentSchematic.components : [];
        const path = smartWireRoute(start, pin, components);
        schematicWireStart = null;
        renderer.clearWireDraft();
        const optimisticWire = {
            wire_id: wireId,
            source: start.key,
            target: pin.key,
            path,
            manual: true,
        };
        if (!Array.isArray(currentSchematic.wirePaths)) currentSchematic.wirePaths = [];
        currentSchematic.wirePaths = currentSchematic.wirePaths.filter(w => w.wire_id !== wireId);
        currentSchematic.wirePaths.push(optimisticWire);
        if (typeof currentSchematic.recomputeJunctions === 'function') {
            currentSchematic.recomputeJunctions();
        }
        renderer.refresh();
        updateCompletenessBadge(currentSchematic.wirePaths, currentSchematic.netlist || []);
        const event = {
            edit_event_type: 'schematic_add_wire',
            source: start.key,
            target: pin.key,
            path,
            wire_id: wireId,
            edit_event_id: wireId,
            in_progress: false,
        };
        applySchematicEditEvents([event]).then(ok => {
            addLogEntry(`Wired ${start.key} to ${pin.key}${ok ? '' : ' locally'}`, ok ? 'success' : 'log');
        });
    }

    // ── Wire Selection & Deletion ──────────────────────────────────────────
    let _selectedWire = null;

    /**
     * Find the nearest wire to a world point
     */
    function findNearestWire(worldX, worldY, tolerance = 1.5) {
        if (!currentSchematic || !currentSchematic.wirePaths) return null;

        let bestWire = null;
        let bestDist = Infinity;
        let bestPoint = null;

        for (const wire of currentSchematic.wirePaths) {
            if (!wire.path || wire.path.length < 2) continue;

            // Check each segment of the wire
            for (let i = 0; i < wire.path.length - 1; i++) {
                const p1 = wire.path[i];
                const p2 = wire.path[i + 1];

                // Calculate distance from point to segment
                const dx = p2.x - p1.x;
                const dy = p2.y - p1.y;
                const lenSq = dx * dx + dy * dy;

                let t = 0;
                if (lenSq > 0.0001) {
                    t = Math.max(0, Math.min(1, ((worldX - p1.x) * dx + (worldY - p1.y) * dy) / lenSq));
                }

                const nearX = p1.x + t * dx;
                const nearY = p1.y + t * dy;
                const distX = worldX - nearX;
                const distY = worldY - nearY;
                const dist = Math.sqrt(distX * distX + distY * distY);

                if (dist <= tolerance && dist < bestDist) {
                    bestDist = dist;
                    bestWire = wire;
                    bestPoint = { x: nearX, y: nearY, segmentIndex: i, t };
                }
            }
        }

        return bestWire ? { wire: bestWire, point: bestPoint, distance: bestDist } : null;
    }

    /**
     * Handle wire click for selection
     */
    function handleWireClick(worldX, worldY) {
        const result = findNearestWire(worldX, worldY);
        if (result) {
            selectWire(result.wire);
            return true;
        }
        return false;
    }

    /**
     * Select a wire for editing
     */
    function selectWire(wire) {
        _selectedWire = wire;
        if (renderer) {
            renderer.setSelectedWire(wire);
        }
        updateWireOperationButtons();
        addLogEntry(`Selected wire: ${wire.wire_id}`, 'log');
    }

    /**
     * Deselect the current wire
     */
    function deselectWire() {
        _selectedWire = null;
        if (renderer) {
            renderer.setSelectedWire(null);
        }
        updateWireOperationButtons();
    }

    /**
     * Delete the selected wire
     */
    function deleteSelectedWire() {
        if (!_selectedWire || !currentSchematic) return;

        const wireId = _selectedWire.wire_id;
        const source = _selectedWire.source || '';
        const target = _selectedWire.target || '';

        // Remove from schematic
        currentSchematic.wirePaths = currentSchematic.wirePaths.filter(w => w.wire_id !== wireId);

        // Recompute junctions
        if (typeof currentSchematic.recomputeJunctions === 'function') {
            currentSchematic.recomputeJunctions();
        }

        // Clear selection
        deselectWire();

        // Refresh renderer
        if (renderer) renderer.refresh();

        // Update completeness badge
        updateCompletenessBadge(currentSchematic.wirePaths, currentSchematic.netlist || []);

        // Sync with backend
        const event = {
            edit_event_type: 'schematic_delete_wire',
            wire_id: wireId,
            source: source,
            target: target,
        };
        applySchematicEditEvents([event]).then(ok => {
            addLogEntry(`Deleted wire ${wireId}${ok ? '' : ' locally'}`, ok ? 'success' : 'log');
        });
    }

    /**
     * Handle wire joining at a point
     */
    function joinWiresAtPoint(worldX, worldY) {
        if (!currentSchematic || !currentSchematic.wirePaths) return false;

        const tolerance = 1.5;
        const wiresAtPoint = [];

        // Find all wires that have endpoints near this point
        for (const wire of currentSchematic.wirePaths) {
            if (!wire.path || wire.path.length < 2) continue;

            const startPt = wire.path[0];
            const endPt = wire.path[wire.path.length - 1];

            const distStart = Math.sqrt(
                Math.pow(worldX - startPt.x, 2) + Math.pow(worldY - startPt.y, 2)
            );
            const distEnd = Math.sqrt(
                Math.pow(worldX - endPt.x, 2) + Math.pow(worldY - endPt.y, 2)
            );

            if (distStart <= tolerance) {
                wiresAtPoint.push({ wire, endpoint: 'start', point: startPt });
            } else if (distEnd <= tolerance) {
                wiresAtPoint.push({ wire, endpoint: 'end', point: endPt });
            }
        }

        // Need at least 2 wires to join
        if (wiresAtPoint.length < 2) {
            addLogEntry('Need at least 2 wires at a point to join', 'log');
            return false;
        }

        // Join wires: connect the endpoints
        const wire1 = wiresAtPoint[0];
        const wire2 = wiresAtPoint[1];

        // Create a new wire connecting the free endpoints
        const newSource = wire1.endpoint === 'start' ? wire1.wire.source : wire1.wire.target;
        const newTarget = wire2.endpoint === 'start' ? wire2.wire.source : wire2.wire.target;

        // Get the free endpoints (the ones not at the join point)
        const freeEnd1 = wire1.endpoint === 'start' ?
            wire1.wire.path[wire1.wire.path.length - 1] : wire1.wire.path[0];
        const freeEnd2 = wire2.endpoint === 'start' ?
            wire2.wire.path[wire2.wire.path.length - 1] : wire2.wire.path[0];

        // Create new wire path through the join point
        const newPath = [
            { x: freeEnd1.x, y: freeEnd1.y },
            { x: snapToSchematicGrid(worldX), y: snapToSchematicGrid(worldY) },
            { x: freeEnd2.x, y: freeEnd2.y }
        ].filter((pt, i, arr) => {
            if (i === 0) return true;
            return Math.abs(arr[i - 1].x - pt.x) > 0.001 || Math.abs(arr[i - 1].y - pt.y) > 0.001;
        });

        const wireId = `schematic_wire_${Date.now()}`;
        const newWire = {
            wire_id: wireId,
            source: newSource,
            target: newTarget,
            path: newPath,
            manual: true,
        };

        // Remove the original wires
        currentSchematic.wirePaths = currentSchematic.wirePaths.filter(w =>
            w.wire_id !== wire1.wire.wire_id && w.wire_id !== wire2.wire.wire_id
        );

        // Add the new joined wire
        currentSchematic.wirePaths.push(newWire);

        // Recompute junctions
        if (typeof currentSchematic.recomputeJunctions === 'function') {
            currentSchematic.recomputeJunctions();
        }

        // Refresh renderer
        if (renderer) renderer.refresh();

        // Update completeness badge
        updateCompletenessBadge(currentSchematic.wirePaths, currentSchematic.netlist || []);

        // Sync with backend
        const event = {
            edit_event_type: 'schematic_join_wires',
            wire_ids: [wire1.wire.wire_id, wire2.wire.wire_id],
            new_wire_id: wireId,
            path: newPath,
            source: newSource,
            target: newTarget,
        };
        applySchematicEditEvents([event]).then(ok => {
            addLogEntry(`Joined wires at (${worldX.toFixed(2)}, ${worldY.toFixed(2)})${ok ? '' : ' locally'}`, ok ? 'success' : 'log');
        });

        return true;
    }

    // ── Net Label Interaction ──────────────────────────────────────────────

    let _netLabelRenameInput = null;

    function handleNetLabelPinClick(pin) {
        if (!currentSchematic || !renderer) return;

        // Check if pin already has a net label
        const existing = currentSchematic.getNetLabelsForPin(pin.key);
        if (existing.length > 0) {
            // Select existing label for rename
            const nl = existing[0];
            renderer.setActiveNetLabel({ id: nl.id, net: nl.net, x: nl.x, y: nl.y, label: nl });
            showNetLabelRenameInput(nl);
            return;
        }

        // Auto-generate net name
        const netName = currentSchematic.nextAutoNetName();

        // Determine orientation from pin direction
        let orientation = 0;
        if (pin.pinNum && currentSchematic) {
            for (const comp of currentSchematic.components) {
                if (comp.refDesignator === pin.refDes) {
                    for (const op of comp.ops) {
                        if (op[0] === 'pin') {
                            const numNode = op.find(a => Array.isArray(a) && a[0] === 'number');
                            const pinNum = numNode && numNode[1] ? String(numNode[1]).replace(/"/g, '') : '';
                            if (pinNum === pin.pinNum) {
                                const at = op.find(a => Array.isArray(a) && a[0] === 'at');
                                if (at) orientation = parseFloat(at[3] || 0);
                                break;
                            }
                        }
                    }
                    break;
                }
            }
        }

        const lbl = currentSchematic.addNetLabel(netName, pin.x, pin.y, orientation, pin.key);
        renderer.refresh();
        scheduleSchematicFlush();
        addLogEntry(`Net label ${netName} created for ${pin.key}`, 'log');
        showNetLabelRenameInput(lbl);
    }

    function handleNetLabelClick(nl, world) {
        if (!currentSchematic || !renderer) return;
        renderer.setActiveNetLabel(nl);
        showNetLabelRenameInput(nl.label);
    }

    function showNetLabelRenameInput(label) {
        if (!renderer || !label) return;
        hideNetLabelRenameInput();

        const screen = renderer.worldToScreen(label.x, label.y);
        if (!screen) return;

        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'netlabel-rename-input';
        input.value = label.net;
        input.dataset.labelId = label.id;

        // Position over the label in viewport coordinates
        const canvasRect = renderer.canvas.getBoundingClientRect();
        input.style.left = Math.round(canvasRect.left + screen.x) + 'px';
        input.style.top = Math.round(canvasRect.top + screen.y - 10) + 'px';

        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                commitNetLabelRename(input);
            }
            if (e.key === 'Escape') {
                e.preventDefault();
                hideNetLabelRenameInput();
            }
        });
        input.addEventListener('blur', () => {
            commitNetLabelRename(input);
        });

        document.body.appendChild(input);
        _netLabelRenameInput = input;
        input.focus();
        input.select();
    }

    function commitNetLabelRename(input) {
        if (!input || !currentSchematic) return;
        const labelId = input.dataset.labelId;
        const newName = input.value.trim();
        hideNetLabelRenameInput();

        const label = currentSchematic.netLabels.find(l => l.id === labelId);
        if (!label) return;
        if (!newName) {
            // Empty name → delete the label
            currentSchematic.removeNetLabel(labelId);
            renderer.setActiveNetLabel(null);
            scheduleSchematicFlush();
        } else if (newName !== label.net) {
            const oldName = label.net;
            currentSchematic.renameNet(oldName, newName);
            scheduleSchematicFlush();
            addLogEntry(`Net ${oldName} → ${newName}`, 'log');
        }
        if (renderer) {
            renderer.setActiveNetLabel(null);
            renderer.refresh();
        }
    }

    function hideNetLabelRenameInput() {
        if (_netLabelRenameInput) {
            _netLabelRenameInput.removeEventListener('blur', commitNetLabelRename);
            _netLabelRenameInput.remove();
            _netLabelRenameInput = null;
        }
    }

    function deleteNetLabel(labelId) {
        if (!currentSchematic) return;
        const label = currentSchematic.netLabels.find(l => l.id === labelId);
        if (label) {
            addLogEntry(`Net label ${label.net} deleted`, 'log');
            currentSchematic.removeNetLabel(labelId);
            renderer.setActiveNetLabel(null);
            renderer.refresh();
            scheduleSchematicFlush();
        }
    }

    function setSchematicEditorMode(mode) {
        schematicEditorMode = mode;
        if (modeIndicator) {
            modeIndicator.textContent = mode === 'wire' ? 'WIRE MODE' : 'NET LABEL MODE';
            modeIndicator.style.borderColor = mode === 'wire' ? 'var(--copper)' : 'var(--agent-amber)';
            modeIndicator.style.color = mode === 'wire' ? 'var(--copper)' : 'var(--agent-amber)';
        }
        // Update toggle button
        const modeBtn = document.getElementById('schematicModeBtn');
        if (modeBtn) {
            modeBtn.textContent = mode === 'wire' ? '🏷 Net Labels' : '〰 Wires';
            modeBtn.classList.toggle('active', mode === 'netlabel');
        }
        // Clear any in-progress wire
        if (mode === 'netlabel') {
            schematicWireStart = null;
            if (renderer) {
                renderer.clearWireDraft();
                renderer.setActivePin(null);
            }
        }
        // In net label mode, hide physical wires to reduce clutter
        if (renderer) {
            renderer.setShowWires(mode === 'wire');
        }
        // Add a visual cue about which mode is active
        const msg = mode === 'wire'
            ? 'Wire mode: click pins to draw physical wires'
            : 'Net Label mode: click a pin to create a logical net label (L)';
        addLogEntry(msg, 'log');
    }

    // ── Net Label Mode Toggle ──────────────────────────────────────────────

    const schematicModeBtn = document.getElementById('schematicModeBtn');
    if (schematicModeBtn) {
        schematicModeBtn.addEventListener('click', () => {
            setSchematicEditorMode(schematicEditorMode === 'wire' ? 'netlabel' : 'wire');
        });
    }

    // ── Wire Operations Buttons ──────────────────────────────────────────
    const deleteWireBtn = document.getElementById('deleteWireBtn');
    const joinWiresBtn = document.getElementById('joinWiresBtn');

    if (deleteWireBtn) {
        deleteWireBtn.addEventListener('click', () => {
            if (_selectedWire) {
                deleteSelectedWire();
            }
        });
    }

    if (joinWiresBtn) {
        joinWiresBtn.addEventListener('click', () => {
            if (_selectedWire && renderer) {
                // Join wires at the first endpoint of the selected wire
                const startPt = _selectedWire.path[0];
                joinWiresAtPoint(startPt.x, startPt.y);
            }
        });
    }

    // Update button states when wire selection changes
    function updateWireOperationButtons() {
        if (deleteWireBtn) {
            deleteWireBtn.disabled = !_selectedWire;
        }
        if (joinWiresBtn) {
            joinWiresBtn.disabled = !_selectedWire;
        }
    }

    // Keyboard shortcut: L toggles net label mode, W toggles wire mode
    document.addEventListener('keydown', (e) => {
        if (isPCBMode() || isSymbolPreviewMode()) return;
        // Don't trigger if typing in an input
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
        if (e.key === 'l' || e.key === 'L') {
            e.preventDefault();
            setSchematicEditorMode('netlabel');
        }
        if (e.key === 'w' || e.key === 'W') {
            e.preventDefault();
            setSchematicEditorMode('wire');
        }
        // Delete key removes active net label OR selected wire
        if (e.key === 'Delete' || e.key === 'Backspace') {
            if (renderer && renderer._activeNetLabel) {
                e.preventDefault();
                deleteNetLabel(renderer._activeNetLabel.id);
            } else if (_selectedWire) {
                e.preventDefault();
                deleteSelectedWire();
            }
        }
        // J key joins wires at a point (when two wire endpoints are near each other)
        if (e.key === 'j' || e.key === 'J') {
            if (renderer && _selectedWire) {
                e.preventDefault();
                // Find the nearest endpoint of the selected wire to join with another wire
                const result = findNearestWire(renderer._selectedWire.path[0].x, renderer._selectedWire.path[0].y);
                if (result && result.wire.wire_id !== _selectedWire.wire_id) {
                    joinWiresAtPoint(renderer._selectedWire.path[0].x, renderer._selectedWire.path[0].y);
                }
            }
        }
        // Escape key deselects wire
        if (e.key === 'Escape') {
            if (_selectedWire) {
                e.preventDefault();
                deselectWire();
            }
        }
    });

    function isPCBMode() {
        return document.getElementById('viewPCBBtn').classList.contains('active');
    }

    function isSymbolPreviewMode() {
        return document.getElementById('viewSymbolBtn').classList.contains('active');
    }

    if (typeof pcbSetRenderMode === 'function') {
        pcbSetRenderMode('full');
    }

    function updatePcbInteractionSurface(_tool) {
        const pcbCanvas = document.getElementById('pcbCanvas');
        if (!pcbCanvas) return;
        pcbCanvas.style.pointerEvents = 'auto';
    }

    async function refreshPcbGeometryFromBackend() {
        if (!window.pcbState || !pcbState.boardModel) return;
        // Don't fetch if board model already has real components
        const model = pcbState.boardModel;
        if (model.components && model.components.length > 0) return;
        const res = await fetch(apiUrl('/api/pcb_enriched_board_model'));
        if (!res.ok) return; // Silently handle 404/errors
        const data = await res.json();
        if (!data || !data.board_model) return;
        // Only load if the server has actual components
        const serverModel = data.board_model;
        if (serverModel.components && serverModel.components.length > 0) {
            pcbLoadBoard(serverModel, { fetchRatsnest: false });
        }
    }

    function setViewportSurfaceState(element, visible, display = 'block') {
        if (!element) return;
        element.style.display = visible ? display : 'none';
        element.style.visibility = visible ? 'visible' : 'hidden';
        element.style.pointerEvents = visible ? 'auto' : 'none';
        if (element.id === 'pcbCanvas') {
            const pcbOverlay = document.getElementById('pcbOverlayCanvas');
            if (pcbOverlay) {
                pcbOverlay.style.display = visible ? 'block' : 'none';
                pcbOverlay.style.visibility = visible ? 'visible' : 'hidden';
                pcbOverlay.style.pointerEvents = 'none';
            }
        }
    }

    function showViewport(active) {
        const container = document.getElementById('canvasContainer');
        const pcbCanvas = document.getElementById('pcbCanvas');
        const symbolCanvas = document.getElementById('symbolCanvas');
        const view3DContainer = document.getElementById('view3DContainer');
        const tscircuitContainer = document.getElementById('tscircuit-container');
        const completenessBadge = document.getElementById('completenessBadge');
        const pcbUploadArea = document.getElementById('pcbUploadArea');
        const pcb3dToolbar = document.getElementById('pcb3dToolbar');
        const routePromptContainer = routePrompt ? routePrompt.closest('.floating-route-input') : null;

        setViewportSurfaceState(container, false);
        setViewportSurfaceState(pcbCanvas, false);
        setViewportSurfaceState(symbolCanvas, false);
        setViewportSurfaceState(view3DContainer, false);
        setViewportSurfaceState(tscircuitContainer, false);

        if (modeIndicator) {
            modeIndicator.classList.toggle('hidden', active !== 'schematic');
        }
        if (completenessBadge) {
            completenessBadge.classList.toggle('hidden', active !== 'schematic');
        }
        if (routePromptContainer) {
            routePromptContainer.classList.toggle('hidden', active !== 'schematic');
        }
        if (pcbUploadArea) {
            pcbUploadArea.classList.add('hidden');
        }
        if (pcb3dToolbar) {
            pcb3dToolbar.classList.toggle('hidden', active !== '3d');
        }

        if (active === 'schematic') {
            setViewportSurfaceState(container, true);
        } else if (active === 'pcb') {
            setViewportSurfaceState(pcbCanvas, true);
            if (!window.pcbState || !pcbState.boardModel) {
                showPcbUploadOverlay();
            }
        } else if (active === '3d') {
            setViewportSurfaceState(view3DContainer, true);
        } else if (active === 'symbol') {
            setViewportSurfaceState(symbolCanvas, true);
        }
    }

    function enterSchematicView() {
        showViewport('schematic');
        if (!currentSchematic) currentSchematic = new Schematic();
        if (currentSchematic) {
            currentSchematic.mode = 'schematic';
            getRenderer().load(currentSchematic);
            if (currentSchematic.components.length > 0) {
                getRenderer().zoomToFit();
            } else {
                getRenderer().refresh();
            }
        }
        modeIndicator.classList.remove('hidden');
        // Ensure mode indicator reflects current mode
        setSchematicEditorMode(schematicEditorMode);
    }

    function inferBoardComponentCategory(component) {
        const ref = String(component && component.ref || '').toUpperCase();
        const footprint = String(component && component.footprint || '').toUpperCase();
        if (ref.startsWith('R') || footprint.includes('RESISTOR')) return 'Resistor';
        if (ref.startsWith('C') || footprint.includes('CAPACITOR')) return 'Capacitor';
        if (ref.startsWith('L') || footprint.includes('INDUCTOR')) return 'Inductor';
        if (ref.startsWith('D') || footprint.includes('DIODE') || footprint.includes('LED')) return 'Diode';
        if (ref.startsWith('Q') || footprint.includes('TRANSISTOR')) return 'Transistor';
        if (ref.startsWith('U') || footprint.includes('QFN') || footprint.includes('SOIC') || footprint.includes('TSSOP')) return 'IC';
        if (ref.startsWith('J') || footprint.includes('CONN') || footprint.includes('HEADER')) return 'Connector';
        return 'Board Component';
    }

    function buildGenericSchematicOpsFromBoardComponent(component) {
        const pads = Array.isArray(component && component.pads) ? component.pads : [];
        const pinCount = Math.max(pads.length, 2);
        const bodyHalfWidth = pinCount <= 2 ? 5.08 : 7.62;
        const bodyHalfHeight = Math.max(3.81, ((Math.ceil(pinCount / 2) - 1) * 2.54) + 2.54);
        const topY = bodyHalfHeight;
        const bottomY = -bodyHalfHeight;
        const ops = [
            ['rectangle', ['start', String(-bodyHalfWidth), String(topY)], ['end', String(bodyHalfWidth), String(bottomY)]],
            ['property', 'Reference', component.ref || 'U?', ['at', '0', String(topY + 2.54), '0']],
            ['property', 'Value', component.name || component.footprint || 'Component', ['at', '0', String(bottomY - 2.54), '0']],
        ];

        const leftPads = [];
        const rightPads = [];
        pads.forEach((pad, index) => {
            if (index % 2 === 0) leftPads.push(pad);
            else rightPads.push(pad);
        });
        if (!rightPads.length && leftPads.length === 1) {
            rightPads.push(leftPads.pop());
        }

        function pinY(index, total) {
            if (total <= 1) return 0;
            return ((total - 1) / 2 - index) * 5.08;
        }

        leftPads.forEach((pad, index) => {
            const y = pinY(index, leftPads.length);
            ops.push([
                'pin',
                'passive',
                'line',
                ['at', String(-(bodyHalfWidth + 2.54)), String(y), '0'],
                ['length', '2.54'],
                ['name', String(pad.net || pad.num || `P${index + 1}`)],
                ['number', String(pad.num || index + 1)],
            ]);
        });
        rightPads.forEach((pad, index) => {
            const y = pinY(index, rightPads.length);
            ops.push([
                'pin',
                'passive',
                'line',
                ['at', String(bodyHalfWidth + 2.54), String(y), '180'],
                ['length', '2.54'],
                ['name', String(pad.net || pad.num || `P${leftPads.length + index + 1}`)],
                ['number', String(pad.num || leftPads.length + index + 1)],
            ]);
        });
        return ops;
    }

    function _findNonOverlappingPosition(schematic) {
        const comps = schematic.components;
        if (comps.length === 0) return { x: 0, y: 0 };

        // Find the bounding box of all existing components
        let maxX = 0, maxY = 0;
        for (const c of comps) {
            const right = c.x + (c.width || 5);
            const bottom = c.y + (c.height || 5);
            if (right > maxX) maxX = right;
            if (bottom > maxY) maxY = bottom;
        }

        // Place in a grid pattern, wrapping to next row after 5 components
        const col = comps.length % 5;
        const row = Math.floor(comps.length / 5);
        const gridSpacing = 10;

        return {
            x: snapToSchematicGrid(col * gridSpacing),
            y: snapToSchematicGrid(maxY + gridSpacing + row * gridSpacing),
        };
    }

    function syncSchematicFromBoardModel(boardModel) {
        if (!boardModel || !Array.isArray(boardModel.components)) return;
        if (!currentSchematic) currentSchematic = new Schematic();
        let changed = false;
        for (const component of boardModel.components) {
            if (!component || !component.ref) continue;
            const existing = currentSchematic.components.find((item) => item.refDesignator === component.ref);
            if (existing) continue;
            const ops = buildGenericSchematicOpsFromBoardComponent(component);
            const category = inferBoardComponentCategory(component);
            const comp = currentSchematic.addRawComponent(
                `board:${component.ref}`,
                component.ref,
                ops,
                category,
                component.footprint || component.name || ''
            );
            if (comp) {
                // Place at non-overlapping position
                const pos = _findNonOverlappingPosition(currentSchematic);
                comp.x = pos.x;
                comp.y = pos.y;
                changed = true;
            }
        }
        if (!changed) return;
        updateComponentListUI();
        updateSchematicButtons();
        if (viewSchematicBtn && viewSchematicBtn.classList.contains('active')) {
            enterSchematicView();
        }
    }

    // ── PCB Canvas Events (keep for pcb_viewer.js) ──────────────────────────

    const pcbCanvas = document.getElementById('pcbCanvas');
    if (pcbCanvas) {
        pcbCanvas.addEventListener('contextmenu', (e) => {
            e.preventDefault();
        });
        pcbCanvas.addEventListener('mousemove', (e) => {
            if (isPCBMode()) pcbHandleMouseMove(e);
        });
        pcbCanvas.addEventListener('mousedown', (e) => {
            if (isPCBMode()) pcbHandleMouseDown(e);
        });
        pcbCanvas.addEventListener('mouseup', (e) => {
            if (isPCBMode()) pcbHandleMouseUp(e);
        });
        pcbCanvas.addEventListener('wheel', (e) => {
            if (isPCBMode()) { e.preventDefault(); pcbHandleWheel(e); }
        }, { passive: false });
        pcbCanvas.addEventListener('mouseleave', (e) => {
            if (isPCBMode()) pcbHandleMouseUp(e);
        });
    }

    // Global keyboard handler for PCB view shortcuts (Escape to cancel drawing)
    document.addEventListener('keydown', (e) => {
        if (isPCBMode()) pcbHandleKeyDown(e);
    });

    // ── SocketIO ──────────────────────────────────────────────────────────────

    function connectSocket() {
        if (socket) {
            socket.removeAllListeners();
            socket.disconnect();
        }
        socket = io();
        window.socket = socket;
        document.dispatchEvent(new CustomEvent('circuitbot:socket-ready', {
            detail: { socket: window.socket },
        }));
        // Set initial connection status
        const initStatus = document.getElementById('connectionStatus');
        if (initStatus) initStatus.className = 'connection-status connected';
        socket.on('connect', () => {
            addLogEntry('Connected to agent backend.', 'system');
            const statusEl = document.getElementById('connectionStatus');
            if (statusEl) { statusEl.className = 'connection-status connected'; statusEl.title = 'Connected to backend'; }
            if (!chatHydrated && window.circuitbotChatSessionId) {
                socket.emit('chat:resume', { session_id: window.circuitbotChatSessionId });
            }
        });
        socket.on('disconnect', () => {
            addLogEntry('Disconnected from agent backend.', 'system');
            const statusEl = document.getElementById('connectionStatus');
            if (statusEl) { statusEl.className = 'connection-status disconnected'; statusEl.title = 'Disconnected from backend'; }
            showToast('Disconnected from backend', 'error', 5000);
        });
        socket.on('agent:thinking', (data) => {
            showAgentStatus(data.message || 'Thinking...', 'thinking');
        });
        socket.on('agent:log', (data) => {
            addLogEntry(data.message || '', 'log');
        });
        socket.on('agent:component', (data) => {
            handleAgentComponent(data);
        });
        socket.on('agent:layout_ready', (data) => {
            handleAgentLayoutReady(data);
        });
        socket.on('agent:done', (data) => {
            agentBusy = false;
            updateAgentButton();
            showAgentStatus('Design complete', 'completed');
            showToast('Design complete', 'success');
            addConversationMessage('system', data.message || 'Design complete.');
            updateComponentListUI();
            updateSchematicButtons();
            const exportSchBtn = document.getElementById('exportSchBtn');
            const exportPCBBtn = document.getElementById('exportPCBBtn');
            if (exportSchBtn) exportSchBtn.disabled = false;
            if (exportPCBBtn) exportPCBBtn.disabled = false;
            // Ensure schematic is visible after agent completes
            if (currentSchematic && currentSchematic.components.length > 0) {
                setActiveTab('viewSchematicBtn');
                enterSchematicView();
                setPcbToolbarVisibility(false);
            }
        });
        socket.on('agent:persisted', (data) => {
            addLogEntry('Design saved — ready to export.', 'success');
        });
        socket.on('agent:pcb_approval', (data) => {
            const msg = data.message || `Schematic complete (${data.component_count || 0} components, ${data.wire_count || 0} wires). Proceed to PCB layout?`;
            addConversationMessage('assistant', msg);
            const existingButtons = agentConversation.querySelectorAll('.conv-approval-buttons');
            existingButtons.forEach(node => node.remove());
            const btnDiv = document.createElement('div');
            btnDiv.className = 'conv-approval-buttons';
            const approveBtn = document.createElement('button');
            approveBtn.className = 'btn-approve';
            approveBtn.textContent = 'Proceed to PCB';
            approveBtn.addEventListener('click', () => window.socket.emit('agent:pcb_approve', { approved: true }));
            const skipBtn = document.createElement('button');
            skipBtn.className = 'btn-skip';
            skipBtn.textContent = 'Skip PCB';
            skipBtn.addEventListener('click', () => window.socket.emit('agent:pcb_approve', { approved: false }));
            btnDiv.appendChild(approveBtn);
            btnDiv.appendChild(skipBtn);
            agentConversation.appendChild(btnDiv);
            while (agentConversation.querySelectorAll('.conv-approval-buttons').length > MAX_APPROVAL_BUTTONS) {
                agentConversation.querySelector('.conv-approval-buttons')?.remove();
            }
            agentConversation.scrollTop = agentConversation.scrollHeight;
        });
        socket.on('agent:board_config', (data) => {
            addConversationMessage('assistant', data.message);
            const options = data.options || [
                { layers: 2, label: '2-Layer', description: 'F.Cu + B.Cu (Standard)' },
                { layers: 4, label: '4-Layer', description: 'F.Cu + In1 + In2 + B.Cu (Recommended)' },
                { layers: 6, label: '6-Layer', description: 'F.Cu + In1-In4 + B.Cu (High-speed)' },
                { layers: 8, label: '8-Layer', description: 'F.Cu + In1-In6 + B.Cu (Advanced)' },
            ];
            const existingButtons = agentConversation.querySelectorAll('.conv-approval-buttons');
            existingButtons.forEach(node => node.remove());
            const btnDiv = document.createElement('div');
            btnDiv.className = 'conv-approval-buttons';
            btnDiv.style.cssText = 'display:flex;flex-wrap:wrap;gap:8px;align-items:flex-start;';
            btnDiv.innerHTML = options.map(opt =>
                `<div style="display:flex;flex-direction:column;align-items:center;">
                    <button class="btn-approve" data-layer-count="${opt.layers}" style="min-width:70px;margin-bottom:4px;">${opt.label}</button>
                    <span style="font-size:10px;color:#6b7280;text-align:center;max-width:80px;line-height:1.2;">${opt.description}</span>
                </div>`
            ).join('');
            btnDiv.querySelectorAll('button').forEach((btn, i) => {
                btn.addEventListener('click', () => {
                    window.socket.emit('agent:board_config', { layer_count: options[i].layers });
                });
            });
            agentConversation.appendChild(btnDiv);
            while (agentConversation.querySelectorAll('.conv-approval-buttons').length > MAX_APPROVAL_BUTTONS) {
                agentConversation.querySelector('.conv-approval-buttons')?.remove();
            }
            agentConversation.scrollTop = agentConversation.scrollHeight;
        });
        socket.on('agent:validation_help', (data) => {
            const errors = (data.errors || []);
            const errorList = errors.map(e => typeof e === 'string' ? e : e.message || String(e)).join('\\n');
            const msg = data.message || `Validation could not auto-fix ${errors.length} issue(s) after multiple retries.\\n\\nRemaining issues:\\n${errorList || '(none listed)'}\\n\\nHow would you like to proceed?`;
            addConversationMessage('assistant', msg.replace(/\\n/g, '<br>'));
            const existingButtons = agentConversation.querySelectorAll('.conv-approval-buttons');
            existingButtons.forEach(node => node.remove());
            const btnDiv = document.createElement('div');
            btnDiv.className = 'conv-approval-buttons';
            const actions = [
                { label: 'Retry', cls: 'btn-approve', action: 'retry' },
                { label: 'Skip & Continue', cls: 'btn-approve', action: 'skip' },
                { label: 'Force Continue', cls: 'btn-approve', action: 'force' },
                { label: 'Terminate', cls: 'btn-skip', action: 'terminate' },
            ];
            for (const a of actions) {
                const btn = document.createElement('button');
                btn.className = a.cls;
                btn.textContent = a.label;
                btn.addEventListener('click', () => window.socket.emit('agent:validation_help_response', { action: a.action }));
                btnDiv.appendChild(btn);
            }
            agentConversation.appendChild(btnDiv);
            while (agentConversation.querySelectorAll('.conv-approval-buttons').length > MAX_APPROVAL_BUTTONS) {
                agentConversation.querySelector('.conv-approval-buttons')?.remove();
            }
            agentConversation.scrollTop = agentConversation.scrollHeight;
        });

        // Clarification questions from clarify node
        socket.on('agent:clarify', (data) => {
            const questions = data.questions || [];
            if (!questions.length) return;

            const answers = {};
            let containerEl = null;

            function renderClarificationUI() {
                if (containerEl) containerEl.remove();

                containerEl = document.createElement('div');
                containerEl.className = 'conv-clarify-card';
                containerEl.style.cssText = 'margin: 8px 0; border: 1px solid rgba(255,255,255,0.12); border-radius: 8px; overflow: hidden; background: rgba(20,20,20,0.9); max-height: 70vh; display: flex; flex-direction: column;';

                // Header
                const header = document.createElement('div');
                header.style.cssText = 'padding: 10px 14px; background: rgba(255,255,255,0.04); border-bottom: 1px solid rgba(255,255,255,0.08); display: flex; align-items: center; gap: 8px; flex-shrink: 0;';
                const icon = document.createElement('span');
                icon.textContent = '\u2728';
                icon.style.fontSize = '14px';
                const title = document.createElement('span');
                title.style.cssText = 'color: #e0e0e0; font-weight: 600; font-size: 13px;';
                title.textContent = data.message || 'A few quick questions to get the design right';
                header.appendChild(icon);
                header.appendChild(title);
                containerEl.appendChild(header);

                // Questions (scrollable)
                const body = document.createElement('div');
                body.style.cssText = 'padding: 10px 14px; display: flex; flex-direction: column; gap: 10px; overflow-y: auto; flex: 1; min-height: 0;';

                for (const q of questions) {
                    const qBlock = document.createElement('div');
                    qBlock.className = 'clarify-q';

                    const qLabel = document.createElement('div');
                    qLabel.style.cssText = 'color: #aaaaaa; font-size: 12px; margin-bottom: 6px; font-weight: 500;';
                    qLabel.textContent = q.question;
                    qBlock.appendChild(qLabel);

                    const optRow = document.createElement('div');
                    optRow.style.cssText = 'display: flex; flex-wrap: wrap; gap: 5px;';

                    const options = q.options || ['Yes', 'No', 'No preference'];
                    for (const opt of options) {
                        const chip = document.createElement('button');
                        chip.textContent = opt;
                        chip.style.cssText = 'font-size: 11px; padding: 4px 10px; border-radius: 12px; border: 1px solid rgba(255,255,255,0.15); background: transparent; color: #aaaaaa; cursor: pointer; transition: all 0.15s;';

                        if (answers[q.id] === opt) {
                            chip.style.background = 'rgba(255,255,255,0.1)';
                            chip.style.borderColor = 'rgba(255,255,255,0.5)';
                            chip.style.color = '#ffffff';
                        }

                        chip.addEventListener('mouseenter', () => {
                            if (answers[q.id] !== opt) chip.style.borderColor = 'rgba(255,255,255,0.35)';
                        });
                        chip.addEventListener('mouseleave', () => {
                            if (answers[q.id] !== opt) chip.style.borderColor = 'rgba(255,255,255,0.15)';
                        });
                        chip.addEventListener('click', () => {
                            answers[q.id] = opt;
                            optRow.querySelectorAll('button').forEach(b => {
                                b.style.background = 'transparent';
                                b.style.borderColor = 'rgba(255,255,255,0.15)';
                                b.style.color = '#aaaaaa';
                            });
                            chip.style.background = 'rgba(255,255,255,0.1)';
                            chip.style.borderColor = 'rgba(255,255,255,0.5)';
                            chip.style.color = '#ffffff';
                            updateSubmitBtn();
                        });
                        optRow.appendChild(chip);
                    }
                    qBlock.appendChild(optRow);
                    body.appendChild(qBlock);
                }

                containerEl.appendChild(body);

                // Footer with submit button (sticky at bottom)
                const footer = document.createElement('div');
                footer.style.cssText = 'padding: 10px 14px; border-top: 1px solid rgba(255,255,255,0.08); display: flex; justify-content: flex-end; flex-shrink: 0; background: rgba(20,20,20,0.95);';

                const submitBtn = document.createElement('button');
                submitBtn.className = 'btn-approve';
                submitBtn.textContent = 'Submit Answers';
                submitBtn.style.cssText = 'padding: 8px 20px; font-size: 12px; font-weight: 600; border-radius: 6px; border: 1px solid rgba(255,255,255,0.2); background: rgba(255,255,255,0.08); color: #ffffff; cursor: pointer; opacity: 0.35; pointer-events: none; transition: all 0.15s;';
                submitBtn.dataset.btnId = 'clarify-submit';
                footer.appendChild(submitBtn);
                containerEl.appendChild(footer);

                agentConversation.appendChild(containerEl);
                agentConversation.scrollTop = agentConversation.scrollHeight;

                return submitBtn;
            }

            function updateSubmitBtn() {
                const btn = containerEl?.querySelector('[data-btn-id="clarify-submit"]');
                if (!btn) return;
                const allAnswered = questions.every(q => answers[q.id]);
                btn.style.opacity = allAnswered ? '1' : '0.35';
                btn.style.pointerEvents = allAnswered ? 'auto' : 'none';
                btn.style.background = allAnswered ? 'rgba(255,255,255,0.15)' : 'rgba(255,255,255,0.08)';
                btn.style.borderColor = allAnswered ? 'rgba(255,255,255,0.4)' : 'rgba(255,255,255,0.2)';
            }

            const submitBtn = renderClarificationUI();
            submitBtn.addEventListener('click', () => {
                // Send all answers at once
                window.socket.emit('agent:clarify_response', { answers });

                // Replace the card with a compact summary
                if (containerEl) containerEl.remove();

                const summary = document.createElement('div');
                summary.className = 'conv-clarify-summary';
                summary.style.cssText = 'margin: 8px 0; padding: 8px 12px; background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.1); border-radius: 6px;';

                const sumTitle = document.createElement('div');
                sumTitle.style.cssText = 'color: #ffffff; font-size: 12px; font-weight: 600; margin-bottom: 6px;';
                sumTitle.textContent = '\u2714 Preferences submitted';
                summary.appendChild(sumTitle);

                for (const q of questions) {
                    const a = answers[q.id] || '(skipped)';
                    const row = document.createElement('div');
                    row.style.cssText = 'font-size: 11px; color: #aab8c8; padding: 2px 0;';
                    row.innerHTML = `<span style="color:#6a8a7a;">${q.question.replace(/\?$/, ':')}</span> <span style="color:#e0f0ed; font-weight:500;">${a}</span>`;
                    summary.appendChild(row);
                }

                agentConversation.appendChild(summary);
                agentConversation.scrollTop = agentConversation.scrollHeight;
            });
        });

        socket.on('agent:pcb_ready', (data) => {
            if (data.board_model) {
                setActiveTab('viewPCBBtn');
                showViewport('pcb');
                pcbSetupCanvas();
                pcbLoadBoard(data.board_model);
                pcbDraw();
                renderPcbLayersPanel();
                refreshPcbGeometryFromBackend().catch((err) => addLogEntry(`PCB geometry refresh failed: ${err.message}`, 'error'));
                updatePcbToolbar({ toolsEnabled: true });
                setPcbToolbarVisibility(true);
                if (routePrompt) routePrompt.classList.add('hidden');
                if (pcbUploadArea) pcbUploadArea.classList.add('hidden');
                addLogEntry('PCB editor loaded with white airwires for manual routing.', 'log');
                addLogEntry('PCB model loaded for board view.', 'success');
                // Export buttons enabled only after agent:persisted — NOT here
            }
        });
        socket.on('agent:error', (data) => {
            agentBusy = false;
            updateAgentButton();
            showAgentStatus('Error: ' + (data.message || 'Unknown error'), 'error');
            addLogEntry('Error: ' + (data.message || 'Unknown error'), 'error');
            showToast('Error: ' + (data.message || 'Unknown error'), 'error', 5000);
        });
        socket.on('agent:conversation', (data) => {
            handleConversationEvent(data);
        });

        // ── Agentic Thought Stream (structured event log) ──────────────
        socket.on('agent:thought_stream', (data) => {
            handleThoughtStreamEvent(data);
        });

        // Design review suggestion cards
        socket.on('agent:review_suggestion', (data) => {
            const card = document.createElement('div');
            card.className = 'review-card';

            const category = data.category || 'general';
            const severity = data.severity || 'low';

            card.innerHTML = `
                <div class="review-card-header">
                    <span class="review-category review-severity-${severity}">${category}</span>
                </div>
                <div class="review-description">${_escapeHtml(data.description || '')}</div>
                <div class="review-suggestion-text">${_escapeHtml(data.suggestion || '')}</div>
                <div class="review-actions">
                    <button class="btn-apply">Apply</button>
                    <button class="btn-dismiss">Dismiss</button>
                </div>
            `;

            card.querySelector('.btn-dismiss').addEventListener('click', () => card.remove());
            card.querySelector('.btn-apply').addEventListener('click', () => {
                const suggestion = data.suggestion || '';
                if (suggestion) {
                    agentPrompt.value = suggestion;
                    agentBtn.click();
                }
                card.remove();
            });

            agentConversation.appendChild(card);
            agentConversation.scrollTop = agentConversation.scrollHeight;
        });

        socket.on('agent:review_complete', (data) => {
            const summary = document.createElement('div');
            summary.className = 'review-summary';
            summary.textContent = `Review complete: ${data.count || 0} suggestion(s)`;
            agentConversation.appendChild(summary);
            agentConversation.scrollTop = agentConversation.scrollHeight;
        });
    }

    function _renderMarkdown(text) {
        // Lightweight markdown: bold, italic, code, bullet lists
        let html = text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
        // Fenced code blocks: ```lang\ncode\n```
        html = html.replace(/```(\w*)\n([\s\S]*?)```/g,
            '<pre class="conv-code-block"><code>$2</code></pre>');
        // Bold: **text**
        html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        // Italic: *text*
        html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
        // Inline code: `text`
        html = html.replace(/`(.+?)`/g, '<code class="conv-inline-code">$1</code>');
        // Bullet lines: lines starting with - or *
        html = html.replace(/^[\-\*] (.+)$/gm, '<div class="conv-bullet">• $1</div>');
        // Line breaks
        html = html.replace(/\n/g, '<br>');
        return html;
    }

    function addLogEntry(text, type) {
        addConversationMessage(type || 'log', text);
    }

    function _timeStamp() {
        const d = new Date();
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }

    function addConversationMessage(type, text) {
        if (type !== 'log' && type !== 'system') {
            const empty = agentConversation.querySelector('.conv-empty');
            if (empty) empty.remove();
        }

        // Route log/detail messages into the last tool_call or thought card
        if (type === 'log' || type === 'system') {
            const cards = agentConversation.querySelectorAll('.conv-tool-call, .conv-thought');
            const lastCard = cards.length ? cards[cards.length - 1] : null;
            if (lastCard) {
                // Expand the parent so logs are visible
                const body = lastCard.querySelector('.tool-call-body, .thought-body');
                if (body) {
                    body.classList.add('open');
                }
                const chevron = lastCard.querySelector('.thought-chevron, .tool-call-chevron');
                if (chevron) chevron.classList.add('open');

                const isDetail = typeof text === 'string' && (text.startsWith('  ') || text.includes('='));
                const entryClass = isDetail ? 'conv-log-line' : 'conv-log-line milestone';
                let targetBody = body;
                if (!targetBody) {
                    targetBody = document.createElement('div');
                    targetBody.className = 'thought-body open';
                    lastCard.appendChild(targetBody);
                }
                const logLine = document.createElement('div');
                logLine.className = entryClass;
                logLine.innerHTML = renderBadges(isDetail ? text.trimStart() : text);
                targetBody.appendChild(logLine);

                agentConversation.scrollTop = agentConversation.scrollHeight;
                return;
            }
        }

        // Fallback: render as standalone (legacy)
        const row = document.createElement('div');
        row.className = 'conv-msg-row';

        if (type !== 'log' && type !== 'system' && type !== 'error') {
            const avatar = document.createElement('div');
            avatar.className = 'conv-avatar bot';
            avatar.innerHTML = '<svg><use href="#icon-bot"/></svg>';
            row.appendChild(avatar);
        }

        const entry = document.createElement('div');
        const isDetail = typeof text === 'string' && (text.startsWith('  ') || text.includes('='));
        if (type === 'error') {
            entry.className = 'conv-error-msg';
            entry.innerHTML = '<span class="conv-error-icon">\u26a0</span> ' + _escapeHtml(text);
        } else if (isDetail) {
            entry.className = 'conv-detail';
            entry.innerHTML = renderBadges(text.trimStart());
        } else {
            entry.className = 'conv-milestone';
            entry.innerHTML = renderBadges(text);
        }
        const ts = document.createElement('span');
        ts.className = 'conv-timestamp';
        ts.textContent = _timeStamp();
        entry.appendChild(ts);
        row.appendChild(entry);
        agentConversation.appendChild(row);
        trimConversationDom();
        agentConversation.scrollTop = agentConversation.scrollHeight;
    }

    function _escapeHtml(text) {
        return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    function showToast(message, type = 'info', duration = 3000, action = null) {
        const container = document.getElementById('toastContainer');
        if (!container) return;
        const toast = document.createElement('div');
        toast.className = `toast ${type}`;
        const msgSpan = document.createElement('span');
        msgSpan.textContent = message;
        toast.appendChild(msgSpan);
        if (action && action.label) {
            const btn = document.createElement('button');
            btn.className = 'toast-action';
            btn.textContent = action.label;
            btn.addEventListener('click', () => {
                if (action.onClick) action.onClick();
                toast.remove();
            });
            toast.appendChild(btn);
        }
        container.appendChild(toast);
        setTimeout(() => {
            toast.classList.add('fade-out');
            setTimeout(() => toast.remove(), 300);
        }, duration);
    }

    function trimConversationDom() {
        while (agentConversation.children.length > MAX_CONVERSATION_ITEMS) {
            agentConversation.removeChild(agentConversation.firstElementChild);
        }
    }

    function showAgentStatus(text, state) {
        if (agentStatus) {
            agentStatus.textContent = text || 'Ready';
            agentStatus.className = 'agent-status-text';
            if (state === 'thinking') agentStatus.classList.add('thinking');
        }
        const bar = document.getElementById('agentThinkingBar');
        if (bar) {
            bar.classList.toggle('active', state === 'thinking');
        }
    }

    function updateAgentButton() {
        agentBtn.disabled = agentBusy || !agentPrompt.value.trim();
        agentBtn.classList.toggle('hidden', agentBusy || !agentPrompt.value.trim());
    }

    const conversation = [];
    let _toolCardIdCounter = 0;

    function handleConversationEvent(data) {
        if (!data || !data.type) return;
        conversation.push(data);
        if (conversation.length > MAX_CONVERSATION_MESSAGES) {
            conversation.splice(0, conversation.length - MAX_CONVERSATION_MESSAGES);
        }
        const empty = agentConversation.querySelector('.conv-empty');
        if (empty) empty.remove();

        if (data.type === 'assistant') {
            const row = document.createElement('div');
            row.className = 'conv-msg-row';
            const avatar = document.createElement('div');
            avatar.className = 'conv-avatar bot';
            avatar.innerHTML = '<svg><use href="#icon-bot"/></svg>';
            row.appendChild(avatar);
            const msg = document.createElement('div');
            msg.className = 'conv-agent-msg';
            msg.textContent = data.content || '';
            row.appendChild(msg);
            agentConversation.appendChild(row);
        } else if (data.type === 'tool_card') {
            const tcId = data.id || `tc_${++_toolCardIdCounter}`;
            if (data.status === 'running') {
                const row = document.createElement('div');
                row.className = 'conv-msg-row';
                const avatar = document.createElement('div');
                avatar.className = 'conv-avatar bot';
                avatar.innerHTML = '<svg><use href="#icon-bot"/></svg>';
                row.appendChild(avatar);
                const p = document.createElement('div');
                p.className = 'conv-progress';
                const label = document.createTextNode((data.title || 'Working') + '... ');
                p.appendChild(label);
                const dots = document.createElement('span');
                dots.className = 'typing-dots';
                dots.innerHTML = '<span></span><span></span><span></span>';
                p.appendChild(dots);
                p.dataset.toolCardId = tcId;
                row.appendChild(p);
                agentConversation.appendChild(row);
            } else if (data.status === 'completed' || data.status === 'failed') {
                const existing = agentConversation.querySelector(`[data-tool-card-id="${tcId}"]`);
                if (existing) {
                    existing.className = 'conv-agent-msg';
                    existing.textContent = data.summary || data.title || '';
                } else {
                    const row = document.createElement('div');
                    row.className = 'conv-msg-row';
                    const avatar = document.createElement('div');
                    avatar.className = 'conv-avatar bot';
                    avatar.innerHTML = '<svg><use href="#icon-bot"/></svg>';
                    row.appendChild(avatar);
                    const msg = document.createElement('div');
                    msg.className = data.status === 'failed' ? 'conv-error-msg' : 'conv-agent-msg';
                    msg.textContent = data.summary || data.title || '';
                    row.appendChild(msg);
                    agentConversation.appendChild(row);
                }
            }
        }

        agentConversation.scrollTop = agentConversation.scrollHeight;
        trimConversationDom();
    }

    // ── Thought Stream Event Handler ────────────────────────────────────────

    let toolCallCards = {};

    function scrollToBottom() {
        agentConversation.scrollTop = agentConversation.scrollHeight;
    }

    function getBadgeIcon(status) {
        if (status === 'running') return '⟳';
        if (status === 'completed') return '✓';
        if (status === 'failed') return '✕';
        return '○';
    }

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function _badgeReplace(s) {
        // REF_DES (U1, R2, C3, SW1, J1, etc.)
        s = s.replace(/\b([A-Z]{1,3})(\d+)\b/g,
            '<span class="comp-badge ref">$1$2</span>');
        // score=N or score=N.N
        s = s.replace(/\b(score\s*=\s*\d+(?:\.\d+)?)/g,
            '<span class="comp-badge score">$1</span>');
        // status keywords
        s = s.replace(/\b(completed|running|failed|pending|skipped)\b/gi,
            '<span class="comp-badge status">$1</span>');
        return s;
    }

    function renderBadges(text) {
        let result = escapeHtml(text);
        // 1. Library:ComponentName → badge lib + badge part (highest priority)
        result = result.replace(/\b([A-Z][A-Za-z0-9_]+):([A-Za-z0-9][A-Za-z0-9._\-\/]+)\b/g,
            '<span class="comp-badge lib">$1</span><span class="comp-badge part">:$2</span>');
        // 2. Apply remaining badge replacements only on text outside HTML tags
        let output = '';
        let lastIdx = 0;
        const tagRe = /<[^>]*>/g;
        let match;
        while ((match = tagRe.exec(result)) !== null) {
            output += _badgeReplace(result.slice(lastIdx, match.index));
            output += match[0];
            lastIdx = match.index + match[0].length;
        }
        output += _badgeReplace(result.slice(lastIdx));
        return output;
    }

    function renderThought(data) {
        const empty = agentConversation.querySelector('.conv-empty');
        if (empty) empty.remove();
        const status = data.status || 'completed';
        const card = document.createElement('div');
        card.className = 'conv-thought';
        card.dataset.thoughtId = data.id || '';

        const header = document.createElement('div');
        header.className = 'thought-header';
        header.innerHTML = `
            <span class="thought-chevron">▶</span>
            <span class="thought-badge ${status}">${getBadgeIcon(status)}</span>
            <span class="thought-title">${renderBadges(data.content)}</span>
        `;
        card.appendChild(header);

        const body = document.createElement('div');
        body.className = 'thought-body';
        if (data.details) {
            const detailsDiv = document.createElement('div');
            detailsDiv.className = 'thought-body-content';
            detailsDiv.innerHTML = renderBadges(data.details);
            body.appendChild(detailsDiv);
        }
        card.appendChild(body);

        header.addEventListener('click', () => {
            const isOpen = body.classList.toggle('open');
            header.querySelector('.thought-chevron').classList.toggle('open', isOpen);
        });

        agentConversation.appendChild(card);
        scrollToBottom();
        trimConversationDom();
    }

    function renderToolCall(data) {
        const empty = agentConversation.querySelector('.conv-empty');
        if (empty) empty.remove();
        const { id, content, status, details, expand } = data;

        // If already exists, update it
        if (toolCallCards[id]) {
            updateToolCall(id, status, details);
            return;
        }

        const card = document.createElement('div');
        card.className = 'conv-tool-call';
        card.dataset.toolId = id;

        const header = document.createElement('div');
        header.className = 'tool-call-header';
        header.innerHTML = `
            <span class="tool-call-chevron">▶</span>
            <span class="tool-call-badge ${status}">${getBadgeIcon(status)}</span>
            <span class="tool-call-title">${renderBadges(content)}</span>
        `;
        card.appendChild(header);

        const body = document.createElement('div');
        body.className = 'tool-call-body';
        if (details) {
            const pre = document.createElement('pre');
            pre.className = 'tool-call-details';
            pre.innerHTML = renderBadges(details);
            body.appendChild(pre);
        }
        const steps = document.createElement('div');
        steps.className = 'tool-call-steps';
        body.appendChild(steps);
        card.appendChild(body);

        header.addEventListener('click', () => {
            const isOpen = body.classList.toggle('open');
            header.querySelector('.tool-call-chevron').classList.toggle('open', isOpen);
        });

        // Auto-expand web search results
        if (expand || id.startsWith('websearch_')) {
            body.classList.add('open');
            header.querySelector('.tool-call-chevron').classList.add('open');
        }

        agentConversation.appendChild(card);
        toolCallCards[id] = card;
        scrollToBottom();
        trimConversationDom();
    }

    function updateToolCall(id, status, details) {
        const card = toolCallCards[id];
        if (!card) return;
        const badge = card.querySelector('.tool-call-badge');
        badge.className = `tool-call-badge ${status}`;
        badge.textContent = getBadgeIcon(status);
        if (details) {
            let pre = card.querySelector('.tool-call-details');
            if (!pre) {
                const body = card.querySelector('.tool-call-body');
                pre = document.createElement('pre');
                pre.className = 'tool-call-details';
                const steps = body.querySelector('.tool-call-steps');
                if (steps) {
                    body.insertBefore(pre, steps);
                } else {
                    body.appendChild(pre);
                }
            }
            pre.innerHTML = renderBadges(details);
        }
    }

    function renderStep(data) {
        const { parent_id, content, status } = data;
        const parent = document.querySelector(`[data-tool-id="${parent_id}"]`);
        if (!parent) return;

        const stepsContainer = parent.querySelector('.tool-call-steps');
        if (!stepsContainer) return;

        // Check if this step already exists (match by label text)
        const existing = stepsContainer.querySelector(`[data-step-label="${escapeHtml(content)}"]`);
        if (existing) {
            existing.className = `step ${status}`;
            existing.querySelector('.step-marker').textContent = getBadgeIcon(status);
            return;
        }

        const step = document.createElement('div');
        step.className = `step ${status}`;
        step.dataset.stepLabel = content;
        step.innerHTML = `
            <div class="step-marker">${getBadgeIcon(status)}</div>
            <div class="step-label">${renderBadges(content)}</div>
        `;
        stepsContainer.appendChild(step);
        scrollToBottom();
    }

    function handleThoughtStreamEvent(data) {
        if (!data || !data.type) return;
        switch (data.type) {
            case 'thought':
                renderThought(data);
                break;
            case 'tool_call':
                renderToolCall(data);
                break;
            case 'step':
                renderStep(data);
                break;
        }
    }

    function clearConversation() {
        conversation.length = 0;
        agentConversation.innerHTML = '';
        toolCallCards = {};
    }

    function exportConversation() {
        const messages = agentConversation.querySelectorAll('.conv-milestone, .conv-user-msg, .conv-error-msg, .conv-detail');
        let text = '# CircuitBot Conversation Export\n\n';
        for (const msg of messages) {
            const ts = msg.querySelector('.conv-timestamp');
            const time = ts ? ts.textContent : '';
            const content = msg.textContent.replace(ts ? ts.textContent : '', '').trim();
            const role = msg.classList.contains('conv-user-msg') ? 'User' : 'Agent';
            text += `[${time}] ${role}: ${content}\n\n`;
        }
        const blob = new Blob([text], { type: 'text/markdown' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `circuitbot-chat-${new Date().toISOString().slice(0, 10)}.md`;
        a.click();
        URL.revokeObjectURL(url);
        showToast('Conversation exported', 'success');
    }

    if (searchResults) {
        searchResults.addEventListener('click', (e) => {
            const li = e.target.closest('li[data-id-str]');
            if (!li || !searchResults.contains(li)) return;
            document.querySelectorAll('#searchResults li').forEach(el => el.classList.remove('selected'));
            li.classList.add('selected');
            previewComponent(li.dataset.idStr || '', li.dataset.text || '');
        });
    }

    if (componentList) {
        componentList.addEventListener('click', (e) => {
            const removeBtn = e.target.closest('.comp-remove');
            if (!removeBtn) return;
            const li = e.target.closest('li[data-comp-id]');
            if (!li || !componentList.contains(li) || !currentSchematic) return;
            e.stopPropagation();
            currentSchematic.removeComponent(li.dataset.compId);
            updateComponentListUI();
            updateSchematicButtons();
            scheduleSchematicFlush();
            if (currentSchematic.mode === 'schematic') enterSchematicView();
        });
    }

    function handleAgentComponent(data) {
        if (!currentSchematic) currentSchematic = new Schematic();
        const { id_str, category, ref_des, description, ops } = data;
        if (!ops || ops.length === 0) {
            addLogEntry(`  Skipped ${ref_des}: no ops parsed.`, 'error');
            return;
        }
        const comp = currentSchematic.addRawComponent(id_str, ref_des, ops, category, description || '');
        if (comp) {
            comp.lib_id = id_str;
            addLogEntry(`  Placed ${comp.refDesignator} (${comp.name})`, 'log');
        }
        updateComponentListUI();
        updateSchematicButtons();
    }

    function updateCompletenessBadge(traces, netlist) {
        const badge = document.getElementById('completenessBadge');
        if (!badge) return;
        const nWires = (traces || []).filter(t => (t.path || []).length >= 2).length;
        // Net labels count as connections too
        const nLabels = (currentSchematic && currentSchematic.netLabels) ? currentSchematic.netLabels.length : 0;
        const nExpected = (netlist || []).length;
        const nConnected = nWires + nLabels;
        if (nExpected === 0 && nConnected === 0) {
            badge.classList.add('hidden');
            return;
        }
        const pct = nExpected > 0 ? Math.round(nConnected / nExpected * 100) : 100;
        badge.textContent = `${nConnected}/${nExpected} (${pct}%)`;
        badge.className = 'completeness-badge';
        if (pct >= 95) badge.classList.add('good');
        else if (pct >= 70) badge.classList.add('warn');
        else badge.classList.add('bad');
        badge.classList.remove('hidden');
    }

    function handleAgentLayoutReady(data) {
        if (!currentSchematic) return;
        const placements = data.placements || [];
        const traces = data.traces || [];
        const powerLabels = data.power_labels || [];
        const netLabels = data.net_labels || [];

        // Apply backend placements directly
        placements.forEach(p => {
            const comp = currentSchematic.components.find(c => c.refDesignator === p.ref_des);
            if (comp) {
                comp.x = p.x;
                comp.y = p.y;
                if (typeof p.rotation === 'number') comp.rotation = p.rotation;
            }
        });
        currentSchematic.wirePaths = traces;
        currentSchematic.powerLabels = powerLabels;
        currentSchematic.netlist = data.netlist || [];
        // Apply net labels from backend
        if (netLabels.length > 0) {
            currentSchematic.netLabels = netLabels.map(nl => new NetLabel(nl.id, nl.net, nl.x, nl.y, nl.orientation || 0, nl.pin || null));
            currentSchematic._netLabelCounter = currentSchematic.netLabels.reduce((max, l) => {
                const num = parseInt(l.id.replace('nl_', ''), 10);
                return isNaN(num) ? max : Math.max(max, num);
            }, 0);
        }
        if (typeof currentSchematic.recomputeJunctions === 'function') {
            currentSchematic.recomputeJunctions();
        }
        updateCompletenessBadge(traces, data.netlist || []);
        displayNetlist(data.netlist || [], data.power_pins || []);
        enterSchematicView();
        addLogEntry(`Laid out ${placements.length} components with ${traces.length} wires (backend routing).`, 'success');
    }

    function displayNetlist(signalNetlist, powerPins) {
        const container = document.getElementById('netlistDisplay');
        const countBadge = document.getElementById('netlistCount');
        if (!container) return;

        const hasSignal = signalNetlist && signalNetlist.length > 0;
        const hasPower = powerPins && powerPins.length > 0;
        if (!hasSignal && !hasPower) {
            container.innerHTML = '<div class="netlist-placeholder">No netlist generated yet.</div>';
            if (countBadge) countBadge.textContent = '0';
            return;
        }

        let totalCount = 0;
        let html = '';

        // Signal connections
        if (hasSignal) {
            const groups = {};
            for (const conn of signalNetlist) {
                const net = conn.net || '(unnamed)';
                if (!groups[net]) groups[net] = [];
                groups[net].push(`${conn.source} → ${conn.target}`);
                totalCount++;
            }
            for (const [net, conns] of Object.entries(groups)) {
                html += `<div class="netlist-entry">`;
                html += `<span class="net-name">${net}</span>`;
                html += `<span class="net-conn">${conns.join(', ')}</span>`;
                html += `</div>`;
            }
        }

        // Power pins — grouped by net name, shown with a visual separator
        if (hasPower) {
            const powerGroups = {};
            for (const pp of powerPins) {
                const net = pp.net || '(unnamed)';
                if (!powerGroups[net]) powerGroups[net] = [];
                powerGroups[net].push(pp.pin);
            }
            for (const [net, pins] of Object.entries(powerGroups)) {
                html += `<div class="netlist-entry power-net">`;
                html += `<span class="net-name">${net}</span>`;
                html += `<span class="net-conn">${pins.join(', ')}</span>`;
                html += `</div>`;
                totalCount++;
            }
        }

        container.innerHTML = html;
        if (countBadge) countBadge.textContent = totalCount;
    }

    function copyNetlistToClipboard() {
        const container = document.getElementById('netlistDisplay');
        if (!container) return;
        const entries = container.querySelectorAll('.netlist-entry');
        if (!entries.length) return;
        let text = '';
        for (const entry of entries) {
            const net = entry.querySelector('.net-name')?.textContent || '';
            const conn = entry.querySelector('.net-conn')?.textContent || '';
            text += `${net}: ${conn}\n`;
        }
        navigator.clipboard.writeText(text.trim()).then(() => {
            const btn = document.getElementById('copyNetlistBtn');
            if (!btn) return;
            const orig = btn.textContent;
            btn.textContent = '✓';
            setTimeout(() => { btn.textContent = orig; }, 1500);
        }).catch(() => {
            // Fallback for older browsers
            const ta = document.createElement('textarea');
            ta.value = text.trim();
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
        });
    }

    connectSocket();

    // ── Edit-sync event listeners (tscircuit + legacy) ─────────────────────────

    window.addEventListener('tscircuit:edit-sync', (e) => {
        const detail = e.detail || {};
        if (detail.ok) {
            addLogEntry(`Edit sync OK: ${detail.applied} applied, ${detail.ignored} ignored`, 'success');
        } else {
            const fb = detail.fallback_saved ? ' (fallback saved)' : ' (fallback failed)';
            addLogEntry(`Edit sync failed: ${detail.error}${fb}`, 'error');
        }
    });

    window.addEventListener('tscircuit:board-model-updated', (e) => {
        const detail = e.detail || {};
        if (detail.board_model) {
            if (pcbState) {
                pcbState.boardModel = detail.board_model;
            }
            syncSchematicFromBoardModel(detail.board_model);
            if (typeof pcbDraw === 'function') {
                pcbDraw();
            }
            renderPcbLayersPanel();
            refreshPcbGeometryFromBackend().catch((err) => addLogEntry(`PCB geometry refresh failed: ${err.message}`, 'error'));
        }
    });

    // ── Netlist copy button ────────────────────────────────────────────────────

    const copyNetlistBtn = document.getElementById('copyNetlistBtn');
    if (copyNetlistBtn) {
        copyNetlistBtn.addEventListener('click', copyNetlistToClipboard);
    }

    // ── Export .kicad_sch ─────────────────────────────────────────────────────

    const exportSchBtn = document.getElementById('exportSchBtn');
    if (exportSchBtn) {
        exportSchBtn.addEventListener('click', async () => {
            addLogEntry('Exporting KiCad schematic...', 'log');
            try {
                const res = await fetch(apiUrl('/api/export_sch'));
                if (!res.ok) {
                    const text = await res.text();
                    addLogEntry('Export failed: ' + text, 'error');
                    showToast('Export failed: ' + text, 'error', 5000);
                    return;
                }
                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'circuitbot.kicad_sch';
                a.click();
                URL.revokeObjectURL(url);
                addLogEntry('Schematic exported successfully.', 'success');
            } catch (err) {
                addLogEntry('Export error: ' + err.message, 'error');
                showToast('Export error: ' + err.message, 'error', 5000);
            }
        });
    }

    if (exportPCBBtn) {
        exportPCBBtn.addEventListener('click', async () => {
            addLogEntry('Exporting KiCad PCB...', 'log');
            try {
                const res = await fetch(apiUrl('/api/export_pcb'));
                if (!res.ok) {
                    const text = await res.text();
                    addLogEntry('Export failed: ' + text, 'error');
                    showToast('Export failed: ' + text, 'error', 5000);
                    return;
                }
                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'circuitbot.kicad_pcb';
                a.click();
                URL.revokeObjectURL(url);
                addLogEntry('PCB exported successfully.', 'success');
            } catch (err) {
                addLogEntry('Export error: ' + err.message, 'error');
                showToast('Export error: ' + err.message, 'error', 5000);
            }
        });
    }

    if (importPCBBtn) {
        importPCBBtn.addEventListener('click', () => {
            pcbFileInput.click();
        });
    }

    if (pcbFileInput) {
        pcbFileInput.addEventListener('change', async (e) => {
            const file = e.target.files[0];
            if (!file) return;
            addLogEntry(`Importing ${file.name}...`, 'log');
            const formData = new FormData();
            formData.append('pcb_file', file);
            try {
                const res = await fetch(apiUrl('/api/import_pcb'), { method: 'POST', body: formData });
                const data = await res.json();
                if (data.error) {
                    addLogEntry(`Import failed: ${data.error}`, 'error');
                    showToast('PCB import failed: ' + data.error, 'error', 5000);
                    return;
                }
                setActiveTab('viewPCBBtn');
                showViewport('pcb');
                setPcbToolbarVisibility(true);
                document.getElementById('routePrompt').classList.add('hidden');
                pcbUploadArea.classList.add('hidden');
                pcbSetupCanvas();
                pcbLoadBoard(data.board_model, { fetchRatsnest: false });
                updatePcbToolbar({ toolsEnabled: true });
                exportPCBBtn.disabled = false;
                importPCBBtn.disabled = false;
                pcbDraw();
                renderPcbLayersPanel();
                refreshPcbGeometryFromBackend().catch((err) => addLogEntry(`PCB geometry refresh failed: ${err.message}`, 'error'));
                addLogEntry(`Imported ${file.name}: ${data.board_model.components.length} components, ${data.board_model.traces.length} traces.`, 'success');
            } catch (err) {
                addLogEntry(`Import error: ${err.message}`, 'error');
            } finally {
                pcbFileInput.value = '';
            }
        });
    }

    // PCB Upload area click-to-browse
    if (pcbUploadArea) {
        pcbUploadArea.addEventListener('click', () => {
            pcbFileInput.click();
        });
        pcbUploadArea.addEventListener('dragover', (e) => {
            e.preventDefault();
            pcbUploadArea.style.borderColor = '#C40000';
        });
        pcbUploadArea.addEventListener('dragleave', () => {
            pcbUploadArea.style.borderColor = '#2A2A3E';
        });
        pcbUploadArea.addEventListener('drop', (e) => {
            e.preventDefault();
            pcbUploadArea.style.borderColor = '#2A2A3E';
            const file = e.dataTransfer.files[0];
            if (file && file.name.endsWith('.kicad_pcb')) {
                pcbFileInput.files = e.dataTransfer.files;
                pcbFileInput.dispatchEvent(new Event('change'));
            } else {
                addLogEntry('Please drop a .kicad_pcb file.', 'error');
            }
        });
    }

    // ── Search (legacy — element may be absent) ───────────────────────────────

    if (searchBtn) searchBtn.addEventListener('click', performSearch);
    if (searchInput) searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') performSearch();
    });

    // ── Sidebar Add Component Section ──────────────────────────────────────────

    const toggleAddSection = document.getElementById('toggleAddSection');
    const addComponentBody = document.getElementById('addComponentBody');
    const manualSearchInput = document.getElementById('manualSearchInput');
    const manualSearchBtn = document.getElementById('manualSearchBtn');
    const manualSearchResults = document.getElementById('manualSearchResults');

    if (toggleAddSection && addComponentBody) {
        toggleAddSection.addEventListener('click', () => {
            addComponentBody.classList.toggle('hidden');
            toggleAddSection.textContent = addComponentBody.classList.contains('hidden') ? '+' : '−';
            if (!addComponentBody.classList.contains('hidden') && manualSearchInput) {
                manualSearchInput.focus();
            }
        });
    }

    if (manualSearchBtn) {
        manualSearchBtn.addEventListener('click', performManualSearch);
    }
    if (manualSearchInput) {
        manualSearchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') performManualSearch();
        });
    }

    async function performManualSearch() {
        if (!manualSearchInput || !manualSearchResults) return;
        const query = manualSearchInput.value.trim();
        if (!query) return;

        manualSearchResults.innerHTML = '<div class="search-loading">Searching...</div>';

        try {
            const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
            const data = await res.json();
            if (data.length === 0) {
                manualSearchResults.innerHTML = '<div class="search-empty">No results found</div>';
            } else {
                manualSearchResults.innerHTML = '';
                // Fetch s-expressions and render thumbnails in parallel
                const cards = await Promise.all(data.map(async (item) => {
                    const card = document.createElement('div');
                    card.className = 'comp-card';
                    card.dataset.idStr = item.id_str;
                    card.dataset.text = item.text;

                    const name = item.id_str.split(':').pop();
                    const category = item.id_str.split(':')[0];
                    // Extract description from text field
                    const descMatch = item.text.match(/Description:\s*(.+?)(?:\.\s*Keywords|$)/i);
                    const desc = descMatch ? descMatch[1].trim() : item.text;

                    let thumbnailDataUrl = null;
                    try {
                        const sexpr = await fetchSExpr(item.id_str);
                        const ops = await resolveAndParse(sexpr, category);
                        if (ops && ops.length > 0) {
                            thumbnailDataUrl = renderSymbolThumbnail(ops, 80, 60);
                        }
                    } catch (e) { /* thumbnail optional */ }

                    card.innerHTML = `
                        <div class="comp-card-thumb">
                            ${thumbnailDataUrl
                                ? `<img src="${thumbnailDataUrl}" alt="${name}">`
                                : `<div class="comp-card-thumb-placeholder">${name.charAt(0)}</div>`}
                        </div>
                        <div class="comp-card-info">
                            <div class="comp-card-name">${escapeHtml(name)}</div>
                            <div class="comp-card-category">${escapeHtml(category)}</div>
                            <div class="comp-card-desc">${escapeHtml(desc)}</div>
                        </div>
                        <button class="comp-card-add" title="Add to schematic">+</button>
                    `;
                    return card;
                }));

                cards.forEach(card => manualSearchResults.appendChild(card));
            }
        } catch (err) {
            manualSearchResults.innerHTML = `<div class="search-error">Error: ${err.message}</div>`;
        }
    }

    if (manualSearchResults) {
        manualSearchResults.addEventListener('click', (e) => {
            // Handle "Add" button click — directly add to schematic
            const addBtn = e.target.closest('.comp-card-add');
            if (addBtn) {
                const card = addBtn.closest('.comp-card');
                if (!card) return;
                e.stopPropagation();
                const idStr = card.dataset.idStr;
                const text = card.dataset.text;
                addComponentFromSearch(idStr, text);
                return;
            }
            // Handle card click — select and preview
            const card = e.target.closest('.comp-card');
            if (!card || !manualSearchResults.contains(card)) return;
            document.querySelectorAll('#manualSearchResults .comp-card').forEach(el => el.classList.remove('selected'));
            card.classList.add('selected');
            const idStr = card.dataset.idStr || '';
            const text = card.dataset.text || '';
            selectedComponent = null;
            previewComponent(idStr, text);
        });
    }

    function addComponentFromSearch(idStr, textDesc) {
        const category = idStr.split(':')[0];
        // Fetch and add directly
        (async () => {
            try {
                const sexpr = await fetchSExpr(idStr);
                const ops = await resolveAndParse(sexpr, category);
                if (!currentSchematic) currentSchematic = new Schematic();
                const comp = currentSchematic.addComponent(idStr, idStr.split(':').pop(), ops, category, textDesc);
                setActiveTab('viewSchematicBtn');
                enterSchematicView();
                updateComponentListUI();
                updateSchematicButtons();
                scheduleSchematicFlush();
                showToast(`Added ${idStr.split(':').pop()} to schematic`, 'success');
            } catch (err) {
                showToast(`Failed to add component: ${err.message}`, 'error');
            }
        })();
    }

    async function performSearch() {
        if (!searchInput || !searchResults || !loading) return;
        const query = searchInput.value.trim();
        if (!query) return;

        loading.classList.remove('hidden');
        searchResults.innerHTML = '';
        selectedComponent = null;
        updateAddButton();

        try {
            const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
            const data = await res.json();
            if (data.length === 0) {
                searchResults.innerHTML = '<li>No results found</li>';
            } else {
                data.forEach(item => {
                    const li = document.createElement('li');
                    li.dataset.idStr = item.id_str;
                    li.dataset.text = item.text;
                    li.innerHTML = `<div class="result-id">${item.id_str}</div>
                                    <div class="result-text">${item.text}</div>`;
                    searchResults.appendChild(li);
                });
            }
        } catch (err) {
            searchResults.innerHTML = `<li>Error: ${err.message}</li>`;
        } finally {
            loading.classList.add('hidden');
        }
    }

    async function previewComponent(id_str, textDesc) {
        try {
            const sexpr = await fetchSExpr(id_str);
            const category = id_str.split(':')[0];
            const ops = await resolveAndParse(sexpr, category);
            selectedComponent = { id_str, textDesc, ops, category };
            currentPreviewOps = ops;
            if (currentSchematic) currentSchematic.mode = 'single';
            setActiveTab('viewSymbolBtn');
            showViewport('symbol');
            renderOps(ops);
            updateAddButton();
        } catch (err) {
            selectedComponent = null;
            updateAddButton();
        }
    }

    function updateAddButton() {
        if (selectedComponent) {
            addBtn.style.opacity = '1';
            addBtn.style.pointerEvents = 'auto';
            addBtn.title = `Add ${selectedComponent.id_str.split(':').pop()} to schematic`;
        } else {
            addBtn.style.opacity = '0.5';
            addBtn.title = 'Search for a component first, then click a result to select it';
        }
        updateSchematicButtons();
    }

    function updateSchematicButtons() {
        const hasSchematic = !!currentSchematic;
        viewSchematicBtn.disabled = !hasSchematic;
        compCount.textContent = (currentSchematic ? currentSchematic.components.length : 0);
    }

    function updateComponentListUI() {
        componentList.innerHTML = '';
        if (!currentSchematic) return;
        currentSchematic.components.forEach(comp => {
            const li = document.createElement('li');
            li.className = `placed-comp-card`;
            li.dataset.compId = comp.id;

            // Generate thumbnail from stored ops
            let thumbHtml = '';
            try {
                if (comp.ops && comp.ops.length > 0) {
                    const dataUrl = renderSymbolThumbnail(comp.ops, 56, 42);
                    if (dataUrl) thumbHtml = `<img src="${dataUrl}" alt="${comp.name}">`;
                }
            } catch (e) { /* thumbnail optional */ }

            if (!thumbHtml) {
                const initial = (comp.refDesignator || comp.name || '?').charAt(0);
                thumbHtml = `<div class="placed-thumb-placeholder">${initial}</div>`;
            }

            const categoryLabel = comp.category || '';
            const descText = comp.description || '';
            const footprintText = comp.footprint || '';
            const valueText = comp.value || '';

            li.innerHTML = `
                <div class="placed-card-thumb">${thumbHtml}</div>
                <div class="placed-card-info">
                    <div class="placed-card-header">
                        <span class="placed-card-ref">${escapeHtml(comp.refDesignator)}</span>
                        <span class="placed-card-name">${escapeHtml(comp.name.split(':').pop())}</span>
                    </div>
                    ${categoryLabel ? `<div class="placed-card-category">${escapeHtml(categoryLabel)}</div>` : ''}
                    ${descText ? `<div class="placed-card-desc">${escapeHtml(descText)}</div>` : ''}
                    <div class="placed-card-meta">
                        ${valueText ? `<span class="placed-card-value">${escapeHtml(valueText)}</span>` : ''}
                        ${footprintText ? `<span class="placed-card-footprint">${escapeHtml(footprintText)}</span>` : ''}
                    </div>
                </div>
                <button class="comp-remove" title="Remove">&times;</button>
            `;
            componentList.appendChild(li);
        });
        document.getElementById('componentCountStatus').textContent = `Components: ${currentSchematic.components.length}`;
    }

    // ── Add to Schematic ─────────────────────────────────────────────────────

    addBtn.addEventListener('click', () => {
        if (!selectedComponent) {
            if (addComponentBody) {
                addComponentBody.classList.remove('hidden');
                if (toggleAddSection) toggleAddSection.textContent = '−';
                if (manualSearchInput) manualSearchInput.focus();
            }
            return;
        }
        const { id_str, textDesc, ops, category } = selectedComponent;
        if (!currentSchematic) currentSchematic = new Schematic();
        const comp = currentSchematic.addComponent(id_str, id_str.split(':').pop(), ops, category, textDesc);
        setActiveTab('viewSchematicBtn');
        enterSchematicView();
        updateComponentListUI();
        updateSchematicButtons();
        showToast(`Added ${id_str.split(':').pop()} to schematic`, 'success');
    });

    // ── AI Agent ──────────────────────────────────────────────────────────────
    
    let sessionId = localStorage.getItem('circuitbot_chat_session');
    if (!sessionId) {
        sessionId = Math.random().toString(36).substring(2, 15);
        localStorage.setItem('circuitbot_chat_session', sessionId);
    }
    window.circuitbotChatSessionId = sessionId;

    // ── Session-aware URL helper ──────────────────────────────────────────────
    function apiUrl(path) {
        const sid = window.circuitbotChatSessionId || '';
        return sid ? path + '?session_id=' + encodeURIComponent(sid) : path;
    }

    const activeProposals = {};
    let chatHydrated = false;

    function setProposalStatus(card, text) {
        const actions = card ? card.querySelector('.proposal-actions') : null;
        if (actions) actions.innerHTML = `<em>${text}</em>`;
    }

    function rejectProposal(id, card) {
        socket.emit('chat:reject_proposal', { session_id: sessionId, proposal_id: id });
        delete activeProposals[id];
        if (card) {
            card.style.opacity = '0.5';
            setProposalStatus(card, 'Rejected');
        }
    }

    function approveProposal(id, card) {
        const proposal = activeProposals[id];
        if (!proposal || typeof pcbState === 'undefined') return;

        // If already in PCB view, do ghost placement there
        // Otherwise, add directly to the current view's schematic
        const currentView = getCurrentView();
        if (currentView === 'pcb') {
            pcbState.ghostProposal = proposal;
            pcbState.lastPointerWorld = pcbState.lastPointerWorld || { x: 0, y: 0 };
            pcbSetMode(PCB_MODE.GHOST_PLACEMENT);
            pcbEditor.requestOverlayRefresh();
        } else {
            // Add to schematic directly
            _commitProposalToSchematic(proposal);
        }

        if (card) setProposalStatus(card, 'Placing...');
    }

    function _commitProposalToSchematic(proposal) {
        if (!currentSchematic || !proposal) return;
        const comp = proposal.component || {};
        const symbolId = comp.symbol_id || comp.id_str || '';
        const refDes = comp.ref || comp.ref_des || symbolId.split(':').pop().substring(0, 8);
        const ops = comp.schematic_ops || comp.ops || [];
        const category = comp.category || comp.name || 'Component';

        if (ops.length > 0) {
            const pos = _findNonOverlappingPosition(currentSchematic);
            const schematicComp = currentSchematic.addRawComponent(
                symbolId, refDes, ops, category, comp.description || ''
            );
            schematicComp.x = pos.x;
            schematicComp.y = pos.y;

            enterSchematicView();
            updateComponentListUI();
            showToast(`Added ${refDes} to schematic at (${pos.x.toFixed(1)}, ${pos.y.toFixed(1)})`, 'success');
        } else {
            showToast(`Added ${refDes} — no schematic symbol available, PCB only`, 'info');
        }
    }

    function getCurrentView() {
        if (isPCBMode()) return 'pcb';
        if (isSymbolPreviewMode()) return 'symbol';
        return 'schematic';
    }

    function renderProposalCard(data) {
        activeProposals[data.id] = data;

        const card = document.createElement('div');
        card.className = 'proposal-card';
        const header = document.createElement('div');
        header.className = 'proposal-header';
        header.textContent = 'AI Proposal';
        const body = document.createElement('div');
        body.className = 'proposal-body';
        const comp = data.component || {};
        let bodyHtml = `<strong>${comp.name || 'Component'}</strong>`;
        if (comp.footprint) bodyHtml += `<br><span style="font-size:11px;color:var(--agent-text-dim);">Footprint: ${comp.footprint}</span>`;
        const pinCount = (comp.pins || []).length;
        if (pinCount) bodyHtml += `<br><span style="font-size:11px;color:var(--agent-text-dim);">Pins: ${pinCount}</span>`;
        if (comp.symbol_id) bodyHtml += `<br><span style="font-size:11px;color:var(--agent-text-dim);">Symbol: ${comp.symbol_id}</span>`;
        body.innerHTML = bodyHtml;
        const actions = document.createElement('div');
        actions.className = 'proposal-actions';
        const approveBtn = document.createElement('button');
        approveBtn.className = 'btn-approve';
        approveBtn.type = 'button';
        approveBtn.textContent = 'Approve';
        approveBtn.addEventListener('click', () => approveProposal(data.id, card));
        const rejectBtn = document.createElement('button');
        rejectBtn.className = 'btn-reject';
        rejectBtn.type = 'button';
        rejectBtn.textContent = 'Reject';
        rejectBtn.addEventListener('click', () => rejectProposal(data.id, card));
        actions.appendChild(approveBtn);
        actions.appendChild(rejectBtn);
        card.appendChild(header);
        card.appendChild(body);
        card.appendChild(actions);
        agentConversation.appendChild(card);
        agentConversation.scrollTop = agentConversation.scrollHeight;
    }

    function hydrateChatState(data) {
        if (chatHydrated) return;
        chatHydrated = true;

        const history = Array.isArray(data.history) ? data.history : [];
        const proposals = Array.isArray(data.proposals) ? data.proposals : [];
        const thoughtStream = Array.isArray(data.thought_stream) ? data.thought_stream : [];

        // If no content at all, keep the suggestion chips
        if (!history.length && !proposals.length && !thoughtStream.length) {
            return;
        }

        // Has real content — clear and rebuild
        clearConversation();
        Object.keys(activeProposals).forEach((key) => delete activeProposals[key]);

        // Replay thought stream events first (restores tool cards, steps, thoughts)
        for (const evt of thoughtStream) {
            handleThoughtStreamEvent(evt);
        }

        // Then render text history (supplementary — avoids double-render of assistant replies)
        for (const message of history) {
            if (message.role === 'user') {
                const userMsg = document.createElement('div');
                userMsg.className = 'conv-user-msg';
                userMsg.textContent = message.content || '';
                agentConversation.appendChild(userMsg);
            } else if (message.role === 'assistant') {
                // Check if this assistant reply is already covered by a thought card
                // (skip if a thought with matching content exists)
                const thoughts = agentConversation.querySelectorAll('.conv-thought');
                let covered = false;
                for (const t of thoughts) {
                    const title = t.querySelector('.thought-title');
                    if (title && title.textContent.trim() === (message.content || '').trim()) {
                        covered = true;
                        break;
                    }
                }
                if (!covered) {
                    const agentMsg = document.createElement('div');
                    agentMsg.className = 'conv-thought';
                    agentMsg.innerHTML = `<div class="thought-header"><span class="thought-chevron">▶</span><span class="thought-badge completed">✓</span><span class="thought-title">${escapeHtml(message.content || '')}</span></div>`;
                    const body = document.createElement('div');
                    body.className = 'thought-body';
                    agentMsg.appendChild(body);
                    agentConversation.appendChild(agentMsg);
                }
            } else if (message.role === 'system') {
                addConversationMessage('log', message.content || '');
            }
        }

        for (const proposal of proposals) {
            renderProposalCard(proposal);
        }

        if (history.length || proposals.length || thoughtStream.length) {
            trimConversationDom();
            agentConversation.scrollTop = agentConversation.scrollHeight;
        }

        if (data.board_model) {
            pcbLoadBoard(data.board_model, { fetchRatsnest: false });
            renderPcbLayersPanel();
        }
    }

    agentBtn.addEventListener('click', async () => {
        const text = agentPrompt.value.trim();
        if (!text || agentBusy) return;

        agentBusy = true;
        updateAgentButton();

        agentPrompt.value = '';
        agentPrompt.style.height = 'auto';

        // Remove suggestion chips when user sends first message
        const empty = agentConversation.querySelector('.conv-empty');
        if (empty) empty.remove();

        const row = document.createElement('div');
        const userMsg = document.createElement('div');
        userMsg.className = 'conv-user-msg';
        userMsg.textContent = text;
        row.appendChild(userMsg);
        agentConversation.appendChild(row);
        
        agentConversation.scrollTop = agentConversation.scrollHeight;
        showAgentStatus('Thinking...', 'thinking');

        // Flush pending schematic edits before sending chat message
        await flushSchematicState();
        socket.emit('chat:message', { session_id: sessionId, text: text, design_revision: lastSyncedRevision });
    });

    socket.on('chat:reply', (data) => {
        agentBusy = false;
        updateAgentButton();
        showAgentStatus('Ready', 'ready');
        
        const row = document.createElement('div');
        const msgDiv = document.createElement('div');
        msgDiv.className = 'conv-thought';
        msgDiv.innerHTML = _renderMarkdown(data.text || '');
        row.appendChild(msgDiv);
        agentConversation.appendChild(row);
    });

    socket.on('chat:proposal', (data) => {
        agentBusy = false;
        updateAgentButton();
        showAgentStatus('Ready', 'ready');
        renderProposalCard(data);
    });

    socket.on('chat:state', (data) => {
        hydrateChatState(data || {});
    });

    socket.on('tscircuit:board-model-updated', (data) => {
        if (data.board_model) {
            pcbLoadBoard(data.board_model);
            syncSchematicFromBoardModel(data.board_model);
            pcbEditor.requestOverlayRefresh();
            renderPcbLayersPanel();
        }
    });

    if (socket && socket.connected && !chatHydrated) {
        socket.emit('chat:resume', { session_id: sessionId });
    }

    // ── Command Palette (Professional Command Menu) ──────────────────────────

    const COMMANDS = [
        { name: '/design', desc: 'Full PCB design pipeline', icon: '⚡' },
        { name: '/add', desc: 'Add a component to the board', icon: '+' },
        { name: '/modify', desc: 'Modify existing component', icon: '✎' },
        { name: '/remove', desc: 'Remove a component', icon: '×' },
        { name: '/status', desc: 'Show current design status', icon: '●' },
        { name: '/export', desc: 'Export the design', icon: '↓' },
        { name: '/help', desc: 'Show available commands', icon: '?' },
    ];

    const commandPalette = document.getElementById('commandPalette');
    const commandPaletteList = document.getElementById('commandPaletteList');
    let commandMode = false;
    let activeCommandIndex = 0;
    let filteredCommands = [];

    function showCommandPalette() {
        commandPalette.classList.remove('hidden');
        commandMode = true;
        updateFilteredCommands();
    }

    function hideCommandPalette() {
        commandPalette.classList.add('hidden');
        commandMode = false;
        activeCommandIndex = 0;
    }

    function updateFilteredCommands() {
        const text = agentPrompt.value;

        if (text === '/') {
            filteredCommands = [...COMMANDS];
        } else {
            const query = text.toLowerCase();
            filteredCommands = COMMANDS.filter(cmd => cmd.name.startsWith(query));
        }

        if (filteredCommands.length === 0) {
            hideCommandPalette();
            return;
        }

        activeCommandIndex = Math.min(activeCommandIndex, filteredCommands.length - 1);
        activeCommandIndex = Math.max(activeCommandIndex, 0);

        renderCommandPalette();
    }

    function renderCommandPalette() {
        commandPaletteList.innerHTML = '';

        filteredCommands.forEach((cmd, index) => {
            const item = document.createElement('div');
            item.className = 'command-item' + (index === activeCommandIndex ? ' active' : '');

            const isExactMatch = agentPrompt.value.toLowerCase() === cmd.name;

            item.innerHTML = `
                <span class="command-icon">${cmd.icon}</span>
                <span class="command-name">${cmd.name}</span>
                <span class="command-desc">${cmd.desc}</span>
                ${isExactMatch ? '<span class="command-shortcut">Enter ↵</span>' : ''}
            `;

            item.addEventListener('click', () => executeCommand(cmd));
            item.addEventListener('mouseenter', () => {
                activeCommandIndex = index;
                renderCommandPalette();
            });

            commandPaletteList.appendChild(item);
        });
    }

    function executeCommand(cmd) {
        agentPrompt.value = cmd.name + ' ';
        hideCommandPalette();
        agentPrompt.focus();
        updateAgentButton();
    }

    function handleInput() {
        const text = agentPrompt.value;

        if (text.startsWith('/') && !commandMode) {
            showCommandPalette();
        } else if (commandMode) {
            if (!text.startsWith('/')) {
                hideCommandPalette();
            } else {
                updateFilteredCommands();
            }
        }

        updateAgentButton();

        agentPrompt.style.height = 'auto';
        agentPrompt.style.height = Math.min(agentPrompt.scrollHeight, 120) + 'px';
    }

    function handleKeydown(e) {
        if (!commandMode) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                agentBtn.click();
            }
            return;
        }

        switch (e.key) {
            case 'ArrowDown':
                e.preventDefault();
                activeCommandIndex = Math.min(activeCommandIndex + 1, filteredCommands.length - 1);
                renderCommandPalette();
                const activeItem = commandPaletteList.querySelector('.active');
                if (activeItem) activeItem.scrollIntoView({ block: 'nearest' });
                break;

            case 'ArrowUp':
                e.preventDefault();
                activeCommandIndex = Math.max(activeCommandIndex - 1, 0);
                renderCommandPalette();
                const activeItemUp = commandPaletteList.querySelector('.active');
                if (activeItemUp) activeItemUp.scrollIntoView({ block: 'nearest' });
                break;

            case 'Enter':
                e.preventDefault();
                if (filteredCommands.length > 0) {
                    executeCommand(filteredCommands[activeCommandIndex]);
                }
                break;

            case 'Escape':
                e.preventDefault();
                hideCommandPalette();
                break;

            case 'Tab':
                e.preventDefault();
                if (filteredCommands.length > 0) {
                    const cmd = filteredCommands[activeCommandIndex];
                    agentPrompt.value = cmd.name + ' ';
                    updateFilteredCommands();
                }
                break;
        }
    }

    agentPrompt.addEventListener('keydown', handleKeydown);
    agentPrompt.addEventListener('input', handleInput);

    document.addEventListener('click', (e) => {
        if (!e.target.closest('.agent-composer')) {
            hideCommandPalette();
        }
    });

    agentPrompt.addEventListener('focus', () => {
        if (agentPrompt.value.startsWith('/')) {
            showCommandPalette();
        }
    });

    // ── Agent Prompt Suggestions ─────────────────────────────────────────────

    const suggestions = [
        'temperature sensor with ADC',
        '3.3V power supply with LED indicator',
        'ESP32 with button and LED',
        'battery charger with status LED',
        'motor driver with ESP32',
    ];
    agentPrompt.addEventListener('focus', () => {
        if (!agentPrompt.value.trim()) {
            agentPrompt.placeholder = suggestions[Math.floor(Math.random() * suggestions.length)];
        }
    });

    // Suggestion chips — click to fill prompt and send
    document.addEventListener('click', (e) => {
        const chip = e.target.closest('.chip');
        if (chip) {
            const prompt = chip.dataset.prompt;
            if (prompt) {
                agentPrompt.value = prompt;
                agentPrompt.style.height = 'auto';
                agentPrompt.style.height = Math.min(agentPrompt.scrollHeight, 120) + 'px';
                updateAgentButton();
                agentBtn.click();
            }
        }
    });
    // Hide suggestion chips when conversation gets messages

    // ── Zoom & UI ─────────────────────────────────────────────────────────────

    document.getElementById('zoomInBtn').addEventListener('click', () => {
        if (isPCBMode()) {
            pcbZoomBy(1.18);
        }
        else if (isSymbolPreviewMode() && currentPreviewOps) { zoomLevel = Math.min(zoomLevel * 1.3, 20); drawSymbol(); if (zoomLevelDisplay) zoomLevelDisplay.textContent = `${Math.round(zoomLevel * 100)}%`; }
        else if (renderer) renderer.setZoom(renderer.zoom * 1.3);
    });
    document.getElementById('zoomOutBtn').addEventListener('click', () => {
        if (isPCBMode()) {
            pcbZoomBy(1 / 1.18);
        }
        else if (isSymbolPreviewMode() && currentPreviewOps) { zoomLevel = Math.max(zoomLevel / 1.3, 0.05); drawSymbol(); if (zoomLevelDisplay) zoomLevelDisplay.textContent = `${Math.round(zoomLevel * 100)}%`; }
        else if (renderer) renderer.setZoom(renderer.zoom / 1.3);
    });
    document.getElementById('zoomResetBtn').addEventListener('click', () => {
        if (isPCBMode()) {
            pcbResetView();
        }
        else if (isSymbolPreviewMode() && currentPreviewOps) { renderOps(currentPreviewOps); if (zoomLevelDisplay) zoomLevelDisplay.textContent = '100%'; }
        else if (currentSchematic && currentSchematic.components.length > 0) { enterSchematicView(); }
    });

    let resizeTimer;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => {
            if (isPCBMode()) {
                pcbSetupCanvas();
                if (pcbState.boardModel) pcbDraw();
            } else if (isSymbolPreviewMode() && currentPreviewOps) {
                setupCanvasSize();
                renderOps(currentPreviewOps);
            } else if (currentSchematic && currentSchematic.components.length > 0) {
                enterSchematicView();
            }
        }, 150);
    });

    async function fetchSExpr(id_str) {
        const res = await fetch(`/api/sexpr?id_str=${encodeURIComponent(id_str)}`);
        if (!res.ok) {
            const errText = await res.text().catch(() => res.statusText);
            showToast('Symbol fetch failed: ' + errText.slice(0, 100), 'error', 4000);
            throw new Error(errText);
        }
        return await res.text();
    }

    // ── Resizable sidebars ──────────────────────────────────────────────
    function setupResize(handleId, panelId, side) {
        const handle = document.getElementById(handleId);
        const panel = document.getElementById(panelId);
        if (!handle || !panel) return;

        let startX, startWidth;

        handle.addEventListener('mousedown', (e) => {
            e.preventDefault();
            startX = e.clientX;
            startWidth = panel.offsetWidth;
            handle.classList.add('active');
            document.body.style.cursor = 'col-resize';
            document.body.style.userSelect = 'none';

            const onMouseMove = (e) => {
                const dx = side === 'left' ? e.clientX - startX : startX - e.clientX;
                const newWidth = Math.min(Math.max(startWidth + dx, 180), 500);
                panel.style.width = newWidth + 'px';
                if (typeof pcbEditor !== 'undefined' && pcbEditor._applyCamera) {
                    pcbEditor._applyCamera();
                }
            };

            const onMouseUp = () => {
                handle.classList.remove('active');
                document.body.style.cursor = '';
                document.body.style.userSelect = '';
                document.removeEventListener('mousemove', onMouseMove);
                document.removeEventListener('mouseup', onMouseUp);
                if (typeof pcbEditor !== 'undefined' && pcbEditor.requestRefresh) {
                    pcbEditor.requestRefresh();
                }
            };

            document.addEventListener('mousemove', onMouseMove);
            document.addEventListener('mouseup', onMouseUp);
        });
    }

    setupResize('leftResize', 'leftPanel', 'left');
    setupResize('rightResize', 'rightPanel', 'right');

    // ── Sidebar collapse/expand ──────────────────────────────────────────
    function setupCollapse(toggleId, panelId, expandId, resizeId, side) {
        const toggle = document.getElementById(toggleId);
        const panel = document.getElementById(panelId);
        const expand = document.getElementById(expandId);
        const resize = document.getElementById(resizeId);
        if (!toggle || !panel || !expand || !resize) return;

        let savedWidth = null;

        function collapse() {
            savedWidth = panel.offsetWidth;
            panel.style.width = '0px';
            panel.classList.add('collapsed');
            resize.style.display = 'none';
            expand.style.display = 'flex';
            toggle.title = 'Expand panel';
            toggle.innerHTML = side === 'left'
                ? '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><polyline points="4,2 8,6 4,10"/></svg>'
                : '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><polyline points="8,2 4,6 8,10"/></svg>';
            if (typeof pcbEditor !== 'undefined' && pcbEditor.requestRefresh) {
                pcbEditor.requestRefresh();
            }
        }

        function expand_panel() {
            panel.classList.remove('collapsed');
            panel.style.width = (savedWidth || 300) + 'px';
            resize.style.display = '';
            expand.style.display = 'none';
            toggle.title = 'Collapse panel';
            toggle.innerHTML = side === 'left'
                ? '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><polyline points="8,2 4,6 8,10"/></svg>'
                : '<svg viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><polyline points="4,2 8,6 4,10"/></svg>';
            if (typeof pcbEditor !== 'undefined' && pcbEditor.requestRefresh) {
                pcbEditor.requestRefresh();
            }
        }

        toggle.addEventListener('click', () => {
            if (panel.classList.contains('collapsed')) {
                expand_panel();
            } else {
                collapse();
            }
        });

        expand.addEventListener('click', () => {
            expand_panel();
        });
    }

    setupCollapse('leftToggle', 'leftPanel', 'leftExpand', 'leftResize', 'left');
    setupCollapse('rightToggle', 'rightPanel', 'rightExpand', 'rightResize', 'right');

    // ── Import Schematic Button ────────────────────────────────────────────────

    const importSchBtn = document.getElementById('importSchBtn');
    const schFileInput = document.getElementById('schFileInput');

    if (importSchBtn) {
        importSchBtn.disabled = false;
        importSchBtn.addEventListener('click', () => {
            if (schFileInput) schFileInput.click();
        });
    }

    if (schFileInput) {
        schFileInput.addEventListener('change', async (e) => {
            const file = e.target.files[0];
            if (!file) return;
            addLogEntry(`Importing schematic ${file.name}...`, 'log');
            const formData = new FormData();
            formData.append('sch_file', file);
            try {
                const res = await fetch(apiUrl('/api/import_sch'), { method: 'POST', body: formData });
                const data = await res.json();
                if (data.error) {
                    addLogEntry(`Import failed: ${data.error}`, 'error');
                    showToast('Schematic import failed: ' + data.error, 'error', 5000);
                    return;
                }
                if (!currentSchematic) currentSchematic = new Schematic();
                if (data.components && data.components.length > 0) {
                    data.components.forEach(c => {
                        currentSchematic.addComponent(c.id_str, c.ref_des || c.id_str.split(':').pop(), c.ops || [], c.category || '', c.description || '');
                    });
                }
                if (data.wires) currentSchematic.wires = data.wires;
                if (data.net_labels) currentSchematic.netLabels = data.net_labels;
                setActiveTab('viewSchematicBtn');
                enterSchematicView();
                updateComponentListUI();
                updateSchematicButtons();
                addLogEntry(`Imported ${file.name}: ${data.components ? data.components.length : 0} components.`, 'success');
                showToast(`Imported ${file.name}`, 'success');
            } catch (err) {
                addLogEntry(`Import error: ${err.message}`, 'error');
            } finally {
                schFileInput.value = '';
            }
        });
    }

    // ── 3D View Tab ────────────────────────────────────────────────────────────

    const view3DBtn = document.getElementById('view3DBtn');

    if (view3DBtn) {
        view3DBtn.addEventListener('click', () => {
            if (!window.pcbState || !pcbState.boardModel) {
                showToast('Load a PCB first to use 3D view', 'info', 3000);
                return;
            }
            setActiveTab('view3DBtn');
            showViewport('3d');
            setPcbToolbarVisibility(false);
            const routePromptContainer = routePrompt ? routePrompt.closest('.floating-route-input') : null;
            if (routePromptContainer) routePromptContainer.classList.add('hidden');
            if (typeof window._load3DScripts === 'function') {
                window._load3DScripts().then(() => {
                    if (typeof window.init3DViewer === 'function') {
                        window.init3DViewer(pcbState.boardModel);
                    } else {
                        showToast('3D viewer not ready', 'info', 2000);
                    }
                });
            }
        });
    }

    // 3D Toolbar Controls
    const view3dTopBtn = document.getElementById('view3dTopBtn');
    const view3dFrontBtn = document.getElementById('view3dFrontBtn');
    const view3dBottomBtn = document.getElementById('view3dBottomBtn');
    const view3dFitBtn = document.getElementById('view3dFitBtn');
    const view3dWireframeBtn = document.getElementById('view3dWireframeBtn');
    const view3dExplodeBtn = document.getElementById('view3dExplodeBtn');
    const view3dBoardOpacity = document.getElementById('view3dBoardOpacity');

    if (view3dTopBtn) view3dTopBtn.addEventListener('click', () => window.pcbViewer3DInstance?.viewTop());
    if (view3dFrontBtn) view3dFrontBtn.addEventListener('click', () => window.pcbViewer3DInstance?.viewFront());
    if (view3dBottomBtn) view3dBottomBtn.addEventListener('click', () => window.pcbViewer3DInstance?.viewBottom());
    if (view3dFitBtn) view3dFitBtn.addEventListener('click', () => window.pcbViewer3DInstance?.fitToBoard());
    if (view3dWireframeBtn) view3dWireframeBtn.addEventListener('click', () => window.pcbViewer3DInstance?.toggleWireframe());
    if (view3dExplodeBtn) view3dExplodeBtn.addEventListener('click', () => window.pcbViewer3DInstance?.toggleExplode());
    if (view3dBoardOpacity) {
        view3dBoardOpacity.addEventListener('input', (e) => {
            const val = parseFloat(e.target.value) / 100;
            window.pcbViewer3DInstance?.setBoardOpacity(val);
        });
    }

    // ── Component Detail Panel ─────────────────────────────────────────────────

    const compDetailPanel = document.getElementById('compDetailPanel');
    const compDetailClose = document.getElementById('compDetailClose');
    const compDetailBody = document.getElementById('compDetailBody');

    if (compDetailClose && compDetailPanel) {
        compDetailClose.addEventListener('click', () => {
            compDetailPanel.classList.add('hidden');
        });
    }

    if (componentList) {
        componentList.addEventListener('click', (e) => {
            const removeBtn = e.target.closest('.comp-remove');
            if (removeBtn) return;
            const li = e.target.closest('li[data-comp-id]');
            if (!li || !compDetailPanel || !compDetailBody || !currentSchematic) return;
            const comp = currentSchematic.components.find(c => c.id === li.dataset.compId);
            if (!comp) return;
            compDetailBody.innerHTML = `
                <div class="comp-detail-field"><strong>ID:</strong> ${escapeHtml(comp.id)}</div>
                <div class="comp-detail-field"><strong>Name:</strong> ${escapeHtml(comp.name)}</div>
                <div class="comp-detail-field"><strong>Ref:</strong> ${escapeHtml(comp.refDesignator)}</div>
                ${comp.category ? `<div class="comp-detail-field"><strong>Category:</strong> ${escapeHtml(comp.category)}</div>` : ''}
                ${comp.description ? `<div class="comp-detail-field"><strong>Description:</strong> ${escapeHtml(comp.description)}</div>` : ''}
            `;
            compDetailPanel.classList.remove('hidden');
        });
    }

    // ── Context Menu (right-click on canvas) ──────────────────────────────────
    // The renderer fires onContextMenu with world coordinates.
    // This function positions the menu and stores the placement target.

    const contextMenu = document.getElementById('contextMenu');
    const markerContextMenu = document.getElementById('markerContextMenu');
    const imageFileInput = document.getElementById('imageFileInput');
    let _contextMenuWorld = null;
    let _contextMenuMarker = null;

    function openSchematicContextMenu(screenX, screenY, world, marker) {
        _contextMenuMarker = marker || null;
        if (marker) {
            // Marker-specific context menu
            if (!markerContextMenu) return;
            _contextMenuWorld = world;
            const menuW = 180;
            const menuH = 120;
            const vw = window.innerWidth;
            const vh = window.innerHeight;
            let left = Math.min(screenX, vw - menuW - 10);
            let top = Math.min(screenY, vh - menuH - 10);
            left = Math.max(10, left);
            top = Math.max(10, top);
            markerContextMenu.style.left = left + 'px';
            markerContextMenu.style.top = top + 'px';
            markerContextMenu.classList.remove('hidden');
            if (contextMenu) contextMenu.classList.add('hidden');
        } else {
            // Generic canvas context menu
            if (!contextMenu) return;
            _contextMenuWorld = world;
            pendingImagePlacement = {
                worldX: world.x,
                worldY: world.y,
                screenX: screenX,
                screenY: screenY,
            };
            const menuW = 220;
            const menuH = 130;
            const vw = window.innerWidth;
            const vh = window.innerHeight;
            let left = Math.min(screenX, vw - menuW - 10);
            let top = Math.min(screenY, vh - menuH - 10);
            left = Math.max(10, left);
            top = Math.max(10, top);
            contextMenu.style.left = left + 'px';
            contextMenu.style.top = top + 'px';
            contextMenu.classList.remove('hidden');
            if (markerContextMenu) markerContextMenu.classList.add('hidden');
        }
    }

    document.addEventListener('click', () => {
        if (contextMenu) contextMenu.classList.add('hidden');
        if (markerContextMenu) markerContextMenu.classList.add('hidden');
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            if (contextMenu) contextMenu.classList.add('hidden');
            if (markerContextMenu) markerContextMenu.classList.add('hidden');
        }
    });

    if (contextMenu) {
        contextMenu.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-action]');
            if (!btn) return;
            const action = btn.dataset.action;
            contextMenu.classList.add('hidden');
            if (action === 'add-image' && imageFileInput) {
                imageFileInput.click();
            } else if (action === 'paste-image') {
                navigator.clipboard.read().then(items => {
                    for (const item of items) {
                        const imgType = item.types.find(t => t.startsWith('image/'));
                        if (imgType) {
                            item.getType(imgType).then(blob => {
                                placeImageFromBlob(blob, 'Pasted Image');
                            });
                            return;
                        }
                    }
                    showToast('No image found in clipboard', 'info', 2000);
                }).catch(() => showToast('Clipboard access denied', 'error', 2000));
            } else if (action === 'fit-view') {
                if (currentSchematic && currentSchematic.components.length > 0) {
                    getRenderer().zoomToFit();
                }
            }
        });
    }

    // Marker-specific context menu
    if (markerContextMenu) {
        markerContextMenu.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-action]');
            if (!btn || !_contextMenuMarker) return;
            const action = btn.dataset.action;
            markerContextMenu.classList.add('hidden');
            const marker = _contextMenuMarker;
            if (action === 'marker-toggle') {
                marker._imageRevealed = !marker._imageRevealed;
                getRenderer().refresh();
                if (marker._imageRevealed) {
                    showToast(`Marker #${marker.markerNumber}: image on canvas`, 'info', 1500);
                }
            } else if (action === 'marker-inspect') {
                showImageInspector(marker);
            } else if (action === 'marker-rename') {
                const name = prompt('Label for marker #' + marker.markerNumber + ':', marker.label || '');
                if (name !== null) {
                    marker.label = name;
                    getRenderer().refresh();
                    scheduleSchematicFlush();
                    showImageInspector(marker);
                }
            } else if (action === 'marker-resize') {
                const w = prompt('Width (world units):', marker.width);
                const h = prompt('Height (world units):', marker.height);
                if (w && h) {
                    marker.width = parseFloat(w) || 20;
                    marker.height = parseFloat(h) || 15;
                    getRenderer().refresh();
                    scheduleSchematicFlush();
                    showImageInspector(marker);
                }
            } else if (action === 'marker-rotate') {
                const deg = prompt('Rotation (degrees):', marker.rotation * 180 / Math.PI);
                if (deg !== null) {
                    marker.rotation = (parseFloat(deg) || 0) * Math.PI / 180;
                    getRenderer().refresh();
                    scheduleSchematicFlush();
                }
            } else if (action === 'marker-delete') {
                deleteImageMarker(marker);
            }
            _contextMenuMarker = null;
        });
    }

    // ── Image Placement Flow ───────────────────────────────────────────────────

    async function placeImageFromBlob(blob, label) {
        let worldX, worldY;
        if (pendingImagePlacement) {
            worldX = pendingImagePlacement.worldX;
            worldY = pendingImagePlacement.worldY;
        } else if (getRenderer()) {
            const center = getRenderer().getCanvasCenterWorld();
            worldX = center.x;
            worldY = center.y;
        } else {
            showToast('Cannot place image: no canvas', 'error', 2000);
            return;
        }
        const reader = new FileReader();
        reader.onload = async () => {
            const dataUrl = reader.result;
            // Upload as asset to the backend
            let assetId = null;
            try {
                const resp = await fetch('/api/schematic_assets', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ image_data: dataUrl, mime_type: blob.type || 'image/png' }),
                });
                const result = await resp.json();
                if (result.ok) assetId = result.asset_id;
            } catch (err) {
                console.warn('Asset upload failed, storing inline:', err);
            }
            // Create marker — image is visible immediately
            const marker = currentSchematic.addImageMarkerAt(worldX, worldY, dataUrl, label, 20, 15, assetId);
            marker._imageRevealed = true;
            getRenderer().refresh();
            scheduleSchematicFlush();
            updateMarkerList();
            showToast(`Marker #${marker.markerNumber} placed`, 'success', 2500);
            pendingImagePlacement = null;
            activeCanvasTool = 'pointer';
        };
        reader.readAsDataURL(blob);
    }

    // ── Image Inspector Overlay (marker-aware) ─────────────────────────────────

    const imagePreviewOverlay = document.getElementById('imagePreviewOverlay');
    const imagePreviewClose = document.getElementById('imagePreviewClose');
    const imagePreviewDelete = document.getElementById('imagePreviewDelete');
    const imagePreviewImg = document.getElementById('imagePreviewImg');
    let _inspectedMarker = null;

    function showImageInspector(marker) {
        if (!imagePreviewOverlay || !imagePreviewImg || !marker) return;
        _inspectedMarker = marker;
        imagePreviewImg.src = marker.imageDataUrl || '';
        const labelEl = document.getElementById('imagePreviewLabel');
        if (labelEl) {
            labelEl.value = marker.label || '';
            labelEl.placeholder = `Marker #${marker.markerNumber}`;
        }
        const numEl = document.getElementById('imagePreviewMarkerNum');
        if (numEl) numEl.textContent = marker.markerNumber;
        // Populate controls
        const wInput = document.getElementById('markerWidthInput');
        const hInput = document.getElementById('markerHeightInput');
        const sInput = document.getElementById('markerScaleInput');
        const rInput = document.getElementById('markerRotationInput');
        if (wInput) wInput.value = marker.width;
        if (hInput) hInput.value = marker.height;
        if (sInput) sInput.value = marker.scale;
        if (rInput) rInput.value = Math.round(marker.rotation * 180 / Math.PI);
        imagePreviewOverlay.classList.remove('hidden');
    }

    function hideImageInspector() {
        if (imagePreviewOverlay) imagePreviewOverlay.classList.add('hidden');
        _inspectedMarker = null;
        getRenderer().clearImageMarkerSelection();
        activeCanvasTool = 'pointer';
    }

    if (imagePreviewClose) {
        imagePreviewClose.addEventListener('click', hideImageInspector);
    }

    if (imagePreviewDelete) {
        imagePreviewDelete.addEventListener('click', () => {
            if (_inspectedMarker) {
                deleteImageMarker(_inspectedMarker);
            }
        });
    }

    if (imagePreviewOverlay) {
        imagePreviewOverlay.addEventListener('click', (e) => {
            if (e.target === imagePreviewOverlay) {
                hideImageInspector();
            }
        });
    }

    // Label input live update
    const labelInput = document.getElementById('imagePreviewLabel');
    if (labelInput) {
        labelInput.addEventListener('input', () => {
            if (!_inspectedMarker) return;
            _inspectedMarker.label = labelInput.value;
            scheduleSchematicFlush();
            updateMarkerList();
        });
    }

    // Inspector control listeners
    function _bindInspectorInput(id, prop, parseFn) {
        const el = document.getElementById(id);
        if (!el) return;
        el.addEventListener('change', () => {
            if (!_inspectedMarker) return;
            _inspectedMarker[prop] = parseFn(el.value);
            getRenderer().refresh();
            scheduleSchematicFlush();
        });
    }
    _bindInspectorInput('markerWidthInput', 'width', parseFloat);
    _bindInspectorInput('markerHeightInput', 'height', parseFloat);
    _bindInspectorInput('markerScaleInput', 'scale', parseFloat);
    _bindInspectorInput('markerRotationInput', 'rotation', (v) => (parseFloat(v) || 0) * Math.PI / 180);

    // ── Image Marker Delete ────────────────────────────────────────────────────

    async function deleteImageMarker(marker) {
        if (!marker || !currentSchematic) return;
        hideImageInspector();
        const snapshot = {
            markerNumber: marker.markerNumber,
            label: marker.label,
            x: marker.x,
            y: marker.y,
            width: marker.width,
            height: marker.height,
            scale: marker.scale,
            rotation: marker.rotation,
            imageDataUrl: marker.imageDataUrl,
            assetId: marker.assetId,
        };
        currentSchematic.removeImageMarker(marker.id);
        getRenderer().clearImageMarkerSelection();
        getRenderer().refresh();
        scheduleSchematicFlush();
        updateMarkerList();
        showToast(`Marker ${marker.markerNumber} deleted`, 'info', 4000, {
            label: 'Undo',
            onClick: () => {
                const restored = currentSchematic.addImageMarkerAt(
                    snapshot.x, snapshot.y, snapshot.imageDataUrl,
                    snapshot.label, snapshot.width, snapshot.height, snapshot.assetId, snapshot.markerNumber
                );
                restored.scale = snapshot.scale;
                restored.rotation = snapshot.rotation;
                getRenderer().refresh();
                scheduleSchematicFlush();
                updateMarkerList();
                showToast(`Marker ${restored.markerNumber} restored`, 'success', 1500);
            },
        });
    }

    // ── Marker List Panel Update ───────────────────────────────────────────────

    function updateMarkerList() {
        const list = document.getElementById('markerList');
        const badge = document.getElementById('markerCountBadge');
        if (!list) return;
        if (!currentSchematic || !currentSchematic.imageMarkers || currentSchematic.imageMarkers.length === 0) {
            list.innerHTML = '<div class="marker-list-empty">No image markers placed.</div>';
            if (badge) badge.textContent = '0';
            return;
        }
        if (badge) badge.textContent = String(currentSchematic.imageMarkers.length);
        let html = '';
        for (const m of currentSchematic.imageMarkers) {
            const label = m.label || '';
            html += `<div class="marker-list-item" data-marker-id="${m.id}">
                <span class="marker-num">${m.markerNumber}</span>
                <span class="marker-label">${label ? label : 'Marker #' + m.markerNumber}</span>
                <button class="marker-zoom-btn" data-marker-id="${m.id}" title="Zoom to marker">\u{1F50D}</button>
            </div>`;
        }
        list.innerHTML = html;
        list.querySelectorAll('.marker-list-item').forEach(item => {
            item.addEventListener('click', (e) => {
                if (e.target.closest('.marker-zoom-btn')) return;
                const id = item.dataset.markerId;
                const marker = currentSchematic.getImageMarkerById(id);
                if (marker && getRenderer()) {
                    getRenderer().selectImageMarker(marker);
                    marker._imageRevealed = !marker._imageRevealed;
                    getRenderer().refresh();
                    showImageInspector(marker);
                }
            });
        });
        list.querySelectorAll('.marker-zoom-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const id = btn.dataset.markerId;
                const marker = currentSchematic.getImageMarkerById(id);
                if (marker && getRenderer()) {
                    getRenderer().selectImageMarker(marker);
                    getRenderer().zoomTo(marker.x, marker.y, 4);
                }
            });
        });
    }

    // ── "Add Image" Tool Button ────────────────────────────────────────────────

    const addImageToolBtn = document.querySelector('.sch-tool[data-tool="add-image"]');
    if (addImageToolBtn) {
        addImageToolBtn.addEventListener('click', () => {
            if (imageFileInput) imageFileInput.click();
        });
    }

    // ── Image File Input ───────────────────────────────────────────────────────

    if (imageFileInput) {
        imageFileInput.addEventListener('change', (e) => {
            const file = e.target.files[0];
            if (!file) return;
            placeImageFromBlob(file, file.name);
            imageFileInput.value = '';
        });
    }

    // ── Drag-and-drop image placement on canvas ──────────────────────────────
    const canvasContainer = document.getElementById('canvasContainer');
    if (canvasContainer) {
        canvasContainer.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'copy';
            canvasContainer.classList.add('drag-over');
        });
        canvasContainer.addEventListener('dragleave', () => {
            canvasContainer.classList.remove('drag-over');
        });
        canvasContainer.addEventListener('drop', (e) => {
            e.preventDefault();
            canvasContainer.classList.remove('drag-over');
            const files = e.dataTransfer.files;
            if (!files || files.length === 0) return;
            const file = files[0];
            if (!file.type.startsWith('image/')) {
                showToast('Only image files can be placed on the canvas', 'info', 2000);
                return;
            }
            // Convert drop position to world coordinates
            if (!renderer) return;
            const rect = canvasContainer.getBoundingClientRect();
            const sx = (e.clientX - rect.left) * (renderer._app.screen.width / rect.width);
            const sy = (e.clientY - rect.top) * (renderer._app.screen.height / rect.height);
            const world = renderer.screenToWorld(sx, sy);
            if (!world) return;
            pendingImagePlacement = { worldX: world.x, worldY: world.y, screenX: e.clientX, screenY: e.clientY };
            placeImageFromBlob(file, file.name);
        });
    }

    // ── PCB Floating HUD ───────────────────────────────────────────────────────

    const hudLayerBtn = document.getElementById('hudLayerBtn');
    const hudLayerText = document.getElementById('hudLayerText');
    const hudAngleBtn = document.getElementById('hudAngleBtn');
    const hudAngleText = document.getElementById('hudAngleText');
    const hudPostureBtn = document.getElementById('hudPostureBtn');
    const hudPostureText = document.getElementById('hudPostureText');
    const hudWidthChips = document.getElementById('hudWidthChips');

    let hudCurrentLayer = 'F.Cu';
    let hudCurrentAngle = '45';
    let hudCurrentPosture = 'H/V';

    if (hudLayerBtn) {
        hudLayerBtn.addEventListener('click', () => {
            hudCurrentLayer = hudCurrentLayer === 'F.Cu' ? 'B.Cu' : 'F.Cu';
            if (hudLayerText) hudLayerText.textContent = hudCurrentLayer === 'F.Cu' ? 'F.Cu (Top)' : 'B.Cu (Bottom)';
            hudLayerBtn.classList.toggle('layer-fcu', hudCurrentLayer === 'F.Cu');
            hudLayerBtn.classList.toggle('layer-bcu', hudCurrentLayer === 'B.Cu');
            if (window.pcbState) pcbState.activeLayer = hudCurrentLayer;
        });
    }

    if (hudAngleBtn) {
        const angleModes = ['45', '90', 'Free'];
        let angleIdx = 0;
        hudAngleBtn.addEventListener('click', () => {
            angleIdx = (angleIdx + 1) % angleModes.length;
            hudCurrentAngle = angleModes[angleIdx];
            if (hudAngleText) hudAngleText.textContent = hudCurrentAngle === 'Free' ? 'Free Angle' : `${hudCurrentAngle}° Octagonal`;
            if (window.pcbState) pcbState.routeAngle = hudCurrentAngle;
        });
    }

    if (hudPostureBtn) {
        const postures = ['H/V', 'Diagonal'];
        let postureIdx = 0;
        hudPostureBtn.addEventListener('click', () => {
            postureIdx = (postureIdx + 1) % postures.length;
            hudCurrentPosture = postures[postureIdx];
            if (hudPostureText) hudPostureText.textContent = hudCurrentPosture === 'H/V' ? 'H/V First' : 'Diagonal First';
            if (window.pcbState) pcbState.routePosture = hudCurrentPosture;
        });
    }

    if (hudWidthChips) {
        hudWidthChips.addEventListener('click', (e) => {
            const chip = e.target.closest('.hud-chip');
            if (!chip) return;
            hudWidthChips.querySelectorAll('.hud-chip').forEach(c => c.classList.remove('active'));
            chip.classList.add('active');
            const width = parseFloat(chip.dataset.width);
            if (window.pcbState) pcbState.routeWidth = width;
        });
    }

    window.appContext = { fetchSExpr };
});
