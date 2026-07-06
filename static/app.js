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
    const agentPulse = document.getElementById('agentPulse');
    const agentDot = document.getElementById('agentDot');
    const agentStatus = document.getElementById('agentStatus');
    const coordDisplay = document.getElementById('coordDisplay');
    const zoomLevelDisplay = document.getElementById('zoomLevel');
    const pcbToolbar = document.getElementById('pcbToolbar');
    const pcbPanToolBtn = document.getElementById('pcbPanToolBtn');
    const pcbSelectToolBtn = document.getElementById('pcbSelectToolBtn');
    const pcbRouteToolBtn = document.getElementById('pcbRouteToolBtn');
    const pcbViaToolBtn = document.getElementById('pcbViaToolBtn');
    const pcbLayerSelect = document.getElementById('pcbLayerSelect');
    const pcbWidthSelect = document.getElementById('pcbWidthSelect');
    const pcbLayersPanel = document.getElementById('pcbLayersPanel');
    const pcbLayersList = document.getElementById('pcbLayersList');

    let selectedComponent = null;
    let currentPreviewOps = null;
    let agentBusy = false;
    let currentSchematic = null;
    let schematicWireStart = null;
    const MAX_CONVERSATION_MESSAGES = 500;
    const MAX_CONVERSATION_ITEMS = 300;
    const MAX_APPROVAL_BUTTONS = 10;

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

            const swatch = document.createElement('span');
            swatch.className = 'pcb-layer-swatch';
            swatch.style.backgroundColor = getPcbLayerColor(layerName);

            const toggle = document.createElement('button');
            toggle.className = 'pcb-layer-toggle';
            toggle.type = 'button';
            toggle.textContent = isVisible ? '◉' : '◌';
            toggle.title = `${isVisible ? 'Hide' : 'Show'} ${layerName}`;
            const toggleLayer = () => {
                setPcbLayerVisible(layerName, !isVisible);
                pcbDrawCurrent();
            };
            row.addEventListener('click', toggleLayer);
            row.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    toggleLayer();
                }
            });

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
            if (currentSchematic && currentSchematic.components.length > 0) {
                setActiveTab('viewSchematicBtn');
                enterSchematicView();
                setPcbToolbarVisibility(false);
                pcbUploadArea.classList.add('hidden');
                document.getElementById('routePrompt').classList.remove('hidden');
            }
        });
    }

    if (viewPCBBtn) {
        viewPCBBtn.addEventListener('click', () => {
            setActiveTab('viewPCBBtn');
            showViewport('pcb');
            setPcbToolbarVisibility(true);
            document.getElementById('routePrompt').classList.add('hidden');
            pcbUploadArea.classList.add('hidden');
            if (pcbState.boardModel) {
                pcbSetupCanvas();
                pcbDraw();
                renderPcbLayersPanel();
                refreshPcbGeometryFromBackend().catch((err) => addLogEntry(`PCB geometry refresh failed: ${err.message}`, 'error'));
            } else {
                showPcbUploadOverlay();
            }
        });
    }

    function showPcbUploadOverlay() {
        pcbUploadArea.classList.remove('hidden');
        const tsc = document.getElementById('tscircuit-container');
        if (tsc) tsc.style.display = 'none';
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
            });
            _initialRenderZoom = renderer.zoom;
        }
        return renderer;
    }

    function orthogonalWirePath(a, b) {
        const midX = snapToGrid((a.x + b.x) / 2);
        return [
            { x: snapToGrid(a.x), y: snapToGrid(a.y) },
            { x: midX, y: snapToGrid(a.y) },
            { x: midX, y: snapToGrid(b.y) },
            { x: snapToGrid(b.x), y: snapToGrid(b.y) },
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
            const res = await fetch('/api/apply_edits', {
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
            const res = await fetch('/api/save_layout', {
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
                }),
            });
            return res.ok;
        } catch (_) {
            return false;
        }
    }

    function handleSchematicPinClick(pin, world) {
        if (!currentSchematic || !renderer) return;
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
        const path = orthogonalWirePath(start, pin);
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
        const res = await fetch('/api/pcb_enriched_board_model');
        if (!res.ok) throw new Error(`pcb_enriched_board_model failed (${res.status})`);
        const data = await res.json();
        if (!data || !data.board_model) return;
        pcbLoadBoard(data.board_model, { fetchRatsnest: false });
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
        const tscircuitContainer = document.getElementById('tscircuit-container');
        const completenessBadge = document.getElementById('completenessBadge');
        const pcbUploadArea = document.getElementById('pcbUploadArea');
        const routePromptContainer = routePrompt ? routePrompt.closest('.floating-route-input') : null;

        setViewportSurfaceState(container, false);
        setViewportSurfaceState(pcbCanvas, false);
        setViewportSurfaceState(symbolCanvas, false);
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

        if (active === 'schematic') {
            setViewportSurfaceState(container, true);
        } else if (active === 'pcb') {
            setViewportSurfaceState(pcbCanvas, true);
            if (!window.pcbState || !pcbState.boardModel) {
                showPcbUploadOverlay();
            }
        } else if (active === 'symbol') {
            setViewportSurfaceState(symbolCanvas, true);
        }
    }

    function enterSchematicView() {
        showViewport('schematic');
        if (currentSchematic) {
            currentSchematic.mode = 'schematic';
            if (currentSchematic.components.length > 0) {
                getRenderer().load(currentSchematic);
                getRenderer().zoomToFit();
            }
        }
        modeIndicator.classList.remove('hidden');
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
        socket.on('connect', () => {
            addLogEntry('Connected to agent backend.', 'system');
        });
        socket.on('disconnect', () => {
            addLogEntry('Disconnected from agent backend.', 'system');
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
            addConversationMessage('system', data.message || 'Design complete.');
            updateComponentListUI();
            updateSchematicButtons();
            const exportBtn = document.getElementById('exportSchBtn');
            if (exportBtn) exportBtn.disabled = false;
            // Ensure schematic is visible after agent completes
            if (currentSchematic && currentSchematic.components.length > 0) {
                setActiveTab('viewSchematicBtn');
                enterSchematicView();
                setPcbToolbarVisibility(false);
            }
        });
        socket.on('agent:pcb_approval', (data) => {
            addConversationMessage('assistant', data.message || 'Schematic complete. Proceed to PCB layout?');
            const existingButtons = agentConversation.querySelectorAll('.conv-approval-buttons');
            existingButtons.forEach(node => node.remove());
            const btnDiv = document.createElement('div');
            btnDiv.className = 'conv-approval-buttons';
            btnDiv.innerHTML = `
                <button class="btn-approve" onclick="socket.emit('agent:pcb_approve', {approved: true})">Proceed to PCB</button>
                <button class="btn-skip" onclick="socket.emit('agent:pcb_approve', {approved: false})">Skip PCB</button>
            `;
            agentConversation.appendChild(btnDiv);
            while (agentConversation.querySelectorAll('.conv-approval-buttons').length > MAX_APPROVAL_BUTTONS) {
                agentConversation.querySelector('.conv-approval-buttons')?.remove();
            }
            agentConversation.scrollTop = agentConversation.scrollHeight;
        });
        socket.on('agent:pcb_ready', (data) => {
            if (data.board_model) {
                setActiveTab('viewPCBBtn');
                showViewport('pcb');
                pcbLoadBoard(data.board_model);
                pcbSetupCanvas();
                pcbDraw();
                renderPcbLayersPanel();
                refreshPcbGeometryFromBackend().catch((err) => addLogEntry(`PCB geometry refresh failed: ${err.message}`, 'error'));
                updatePcbToolbar({ toolsEnabled: true });
                setPcbToolbarVisibility(true);
                if (routePrompt) routePrompt.classList.add('hidden');
                if (pcbUploadArea) pcbUploadArea.classList.add('hidden');
                addLogEntry('PCB editor loaded with white airwires for manual routing.', 'log');
                addLogEntry('PCB model loaded for board view.', 'success');
                exportPCBBtn.disabled = false;
                importPCBBtn.disabled = false;
            }
        });
        socket.on('agent:error', (data) => {
            agentBusy = false;
            updateAgentButton();
            showAgentStatus('Error: ' + (data.message || 'Unknown error'), 'error');
            addLogEntry('Error: ' + (data.message || 'Unknown error'), 'error');
        });
        socket.on('agent:conversation', (data) => {
            handleConversationEvent(data);
        });
    }

    function addLogEntry(text, type) {
        addConversationMessage(type || 'log', text);
    }

    function addConversationMessage(type, text) {
        const empty = agentConversation.querySelector('.conv-empty');
        if (empty) empty.remove();
        const entry = document.createElement('div');
        const isDetail = typeof text === 'string' && (text.startsWith('  ') || text.includes('='));
        if (type === 'error') {
            entry.className = 'conv-error-msg';
        } else if (isDetail) {
            entry.className = 'conv-detail';
            entry.textContent = text.trimStart();
        } else {
            entry.className = 'conv-milestone';
            entry.textContent = text;
        }
        agentConversation.appendChild(entry);
        trimConversationDom();
        agentConversation.scrollTop = agentConversation.scrollHeight;
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
        agentBtn.textContent = agentBusy ? 'Building...' : 'Build';
        agentBtn.className = 'btn-send' + (agentBusy ? ' running' : '');
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
            const msg = document.createElement('div');
            msg.className = 'conv-agent-msg';
            msg.textContent = data.content || '';
            agentConversation.appendChild(msg);
        } else if (data.type === 'tool_card') {
            const tcId = data.id || `tc_${++_toolCardIdCounter}`;
            if (data.status === 'running') {
                const p = document.createElement('div');
                p.className = 'conv-progress';
                p.textContent = (data.title || 'Working') + '...';
                p.dataset.toolCardId = tcId;
                agentConversation.appendChild(p);
            } else if (data.status === 'completed' || data.status === 'failed') {
                const existing = agentConversation.querySelector(`[data-tool-card-id="${tcId}"]`);
                if (existing) {
                    existing.className = 'conv-agent-msg';
                    existing.textContent = data.summary || data.title || '';
                } else {
                    const msg = document.createElement('div');
                    msg.className = data.status === 'failed' ? 'conv-error-msg' : 'conv-agent-msg';
                    msg.textContent = data.summary || data.title || '';
                    agentConversation.appendChild(msg);
                }
            }
        }

        agentConversation.scrollTop = agentConversation.scrollHeight;
        trimConversationDom();
    }

    function clearConversation() {
        conversation.length = 0;
        agentConversation.innerHTML = '';
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
            if (currentSchematic.mode === 'schematic') enterSchematicView();
        });
    }

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.appendChild(document.createTextNode(str));
        return div.innerHTML;
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
            addLogEntry(`  Placed ${comp.refDesignator} (${comp.name})`, 'log');
        }
        updateComponentListUI();
        updateSchematicButtons();
    }

    function updateCompletenessBadge(traces, netlist) {
        const badge = document.getElementById('completenessBadge');
        if (!badge) return;
        const nWires = (traces || []).filter(t => (t.path || []).length >= 2).length;
        const nExpected = (netlist || []).length;
        if (nExpected === 0) {
            badge.classList.add('hidden');
            return;
        }
        const pct = Math.round(nWires / nExpected * 100);
        badge.textContent = `${nWires}/${nExpected} (${pct}%)`;
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

        // Apply backend placements directly
        placements.forEach(p => {
            const comp = currentSchematic.components.find(c => c.refDesignator === p.ref_des);
            if (comp) { comp.x = p.x; comp.y = p.y; }
        });
        currentSchematic.wirePaths = traces;
        currentSchematic.powerLabels = powerLabels;
        currentSchematic.netlist = data.netlist || [];
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
        exportSchBtn.addEventListener('click', () => {
            addLogEntry('Exporting KiCad schematic...', 'log');
            window.location.href = '/api/export_sch';
        });
    }

    if (exportPCBBtn) {
        exportPCBBtn.addEventListener('click', () => {
            addLogEntry('Exporting KiCad PCB...', 'log');
            window.location.href = '/api/export_pcb';
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
                const res = await fetch('/api/import_pcb', { method: 'POST', body: formData });
                const data = await res.json();
                if (data.error) {
                    addLogEntry(`Import failed: ${data.error}`, 'error');
                    return;
                }
                pcbLoadBoard(data.board_model, { fetchRatsnest: false });
                updatePcbToolbar({ toolsEnabled: true });
                exportPCBBtn.disabled = false;
                importPCBBtn.disabled = false;
                setActiveTab('viewPCBBtn');
                showViewport('pcb');
                setPcbToolbarVisibility(true);
                document.getElementById('routePrompt').classList.add('hidden');
                pcbUploadArea.classList.add('hidden');
                pcbSetupCanvas();
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
        addBtn.disabled = !selectedComponent;
        updateSchematicButtons();
    }

    function updateSchematicButtons() {
        const hasComponents = currentSchematic && currentSchematic.components.length > 0;
        viewSchematicBtn.disabled = !hasComponents;
        compCount.textContent = (currentSchematic ? currentSchematic.components.length : 0);
    }

    function updateComponentListUI() {
        componentList.innerHTML = '';
        if (!currentSchematic) return;
        currentSchematic.components.forEach(comp => {
            const li = document.createElement('li');
            li.className = `col-${comp.column}`;
            li.dataset.compId = comp.id;
            li.innerHTML = `
                <div class="comp-label">
                    <div class="comp-name">${comp.refDesignator} - ${comp.name.split(':').pop()}</div>
                    <div class="comp-id">${comp.id}</div>
                </div>
                <button class="comp-remove" title="Remove">&times;</button>
            `;
            componentList.appendChild(li);
        });
    }

    // ── Add to Schematic ─────────────────────────────────────────────────────

    addBtn.addEventListener('click', () => {
        if (!selectedComponent) return;
        const { id_str, textDesc, ops, category } = selectedComponent;
        if (!currentSchematic) currentSchematic = new Schematic();
        const comp = currentSchematic.addComponent(id_str, id_str.split(':').pop(), ops, category, textDesc);
        setActiveTab('viewSchematicBtn');
        enterSchematicView();
        updateComponentListUI();
        updateSchematicButtons();
    });

    // ── AI Agent ──────────────────────────────────────────────────────────────

    agentBtn.addEventListener('click', () => {
        const prompt = agentPrompt.value.trim();
        if (!prompt || agentBusy) return;
        agentBusy = true;
        updateAgentButton();
        clearConversation();
        agentConversation.innerHTML = '';
        const userMsg = document.createElement('div');
        userMsg.className = 'conv-user-msg';
        userMsg.textContent = prompt;
        agentConversation.appendChild(userMsg);
        showAgentStatus('Starting agent...', 'thinking');
        displayNetlist([]);
        if (!currentSchematic) currentSchematic = new Schematic();
        socket.emit('agent:generate', { prompt });
    });

    agentPrompt.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            agentBtn.click();
        }
    });
    agentPrompt.addEventListener('input', updateAgentButton);

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

    // ── Zoom & UI ─────────────────────────────────────────────────────────────

    document.getElementById('zoomInBtn').addEventListener('click', () => {
        if (isPCBMode()) {
            pcbZoomBy(1.18);
        }
        else if (isSymbolPreviewMode() && currentPreviewOps) { renderOps(currentPreviewOps); }
        else if (renderer) renderer.setZoom(renderer.zoom * 1.3);
    });
    document.getElementById('zoomOutBtn').addEventListener('click', () => {
        if (isPCBMode()) {
            pcbZoomBy(1 / 1.18);
        }
        else if (isSymbolPreviewMode() && currentPreviewOps) { zoomLevel = Math.max(zoomLevel / 1.3, 0.05); drawSymbol(); }
        else if (renderer) renderer.setZoom(renderer.zoom / 1.3);
    });
    document.getElementById('zoomResetBtn').addEventListener('click', () => {
        if (isPCBMode()) {
            pcbResetView();
        }
        else if (isSymbolPreviewMode() && currentPreviewOps) { renderOps(currentPreviewOps); }
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
        if (!res.ok) throw new Error(await res.text());
        return await res.text();
    }

    window.appContext = { fetchSExpr };
});
