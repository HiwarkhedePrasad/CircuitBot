let socket = null;
const CIRCUITBOT_LAYOUT_VERSION = 'v8-elk-fixed-side';
console.log('%c[CircuitBot] Layout engine ' + CIRCUITBOT_LAYOUT_VERSION + ' loaded', 'color:#a371f7;font-weight:bold');

// Validate a wire path: must be orthogonal and under length cap.
// Defined at module level so both handleAgentLayoutReady and runWireBenderLayout can use it.
const isValidWirePath = (pts, maxLen) => {
    if (!pts || pts.length < 2) return false;
    let total = 0;
    for (let i = 0; i < pts.length - 1; i++) {
        const dx = Math.abs(pts[i].x - pts[i+1].x);
        const dy = Math.abs(pts[i].y - pts[i+1].y);
        if (dx > 0.001 && dy > 0.001) return false; // diagonal
        total += dx + dy;
        if (total > maxLen) return false;
    }
    return true;
};

// Repair a wire path by converting diagonal segments into L-shaped bends.
// Mirrors Python repair_wire_path() in pcb_design/coord_validator.py.
const repairWirePath = (pts) => {
    if (!pts || pts.length < 2) return null;
    // Step 1: orthogonalize — split diagonals into L-shapes
    const out = [{ x: pts[0].x, y: pts[0].y }];
    for (let i = 1; i < pts.length; i++) {
        const a = out[out.length - 1];
        const b = pts[i];
        if (Math.abs(a.x - b.x) < 0.001 || Math.abs(a.y - b.y) < 0.001) {
            out.push({ x: b.x, y: b.y });
            continue;
        }
        // Diagonal — insert corner (horizontal first, then vertical)
        out.push({ x: b.x, y: a.y });
        out.push({ x: b.x, y: b.y });
    }
    // Step 2: drop consecutive duplicates
    const cleaned = [{ x: out[0].x, y: out[0].y }];
    for (let i = 1; i < out.length; i++) {
        const last = cleaned[cleaned.length - 1];
        if (Math.abs(last.x - out[i].x) > 0.001 || Math.abs(last.y - out[i].y) > 0.001) {
            cleaned.push({ x: out[i].x, y: out[i].y });
        }
    }
    // Step 3: verify no diagonal remains
    for (let i = 0; i < cleaned.length - 1; i++) {
        const dx = Math.abs(cleaned[i].x - cleaned[i+1].x);
        const dy = Math.abs(cleaned[i].y - cleaned[i+1].y);
        if (dx > 0.001 && dy > 0.001) return null; // still diagonal — unrepairable
    }
    return cleaned;
};

document.addEventListener('DOMContentLoaded', () => {
    const searchInput = document.getElementById('searchInput');
    const searchBtn = document.getElementById('searchBtn');
    const searchResults = document.getElementById('searchResults');
    const loading = document.getElementById('loading');
    const componentInfo = document.getElementById('componentInfo');
    const addBtn = document.getElementById('addBtn');
    const autoLayoutBtn = document.getElementById('autoLayoutBtn');
    const clearBtn = document.getElementById('clearBtn');
    const viewSchematicBtn = document.getElementById('viewSchematicBtn');
    const viewSymbolBtn = document.getElementById('viewSymbolBtn');
    const componentList = document.getElementById('componentList');
    const compCount = document.getElementById('compCount');
    const modeIndicator = document.getElementById('modeIndicator');
    const autoRouteBtn = document.getElementById('autoRouteBtn');
    const routePrompt = document.getElementById('routePrompt');
    const agentBtn = document.getElementById('agentBtn');
    const agentPrompt = document.getElementById('agentPrompt');
    const agentConversation = document.getElementById('agentConversation');
    const agentThinking = document.getElementById('agentThinking');
    const coordDisplay = document.getElementById('coordDisplay');
    const zoomLevelDisplay = document.getElementById('zoomLevel');

    let selectedComponent = null;
    let currentPreviewOps = null;
    let agentBusy = false;

    // ── Tab Management ────────────────────────────────────────────────────────

    function setActiveTab(tabId) {
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        const tab = document.getElementById(tabId);
        if (tab) tab.classList.add('active');
    }

    if (viewSymbolBtn) {
        viewSymbolBtn.addEventListener('click', () => {
            if (currentPreviewOps) {
                setActiveTab('viewSymbolBtn');
                renderOps(currentPreviewOps);
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
                enterSchematicMode();
                modeIndicator.classList.remove('hidden');
                pcbUploadArea.classList.add('hidden');
                document.getElementById('routePrompt').classList.remove('hidden');
            }
        });
    }

    if (viewPCBBtn) {
        viewPCBBtn.addEventListener('click', () => {
            setActiveTab('viewPCBBtn');
            modeIndicator.classList.add('hidden');
            document.getElementById('routePrompt').classList.add('hidden');
            if (pcbState.boardModel) {
                pcbUploadArea.classList.add('hidden');
                pcbSetupCanvas();
                pcbDraw();
            } else {
                pcbUploadArea.classList.remove('hidden');
                const { canvas, ctx } = pcbGetCanvas();
                if (canvas) {
                    pcbSetupCanvas();
                    ctx.fillStyle = '#0A0A14';
                    ctx.fillRect(0, 0, canvas.width, canvas.height);
                }
            }
        });
    }

    // ── Canvas Coordinates ────────────────────────────────────────────────────

    const canvas = document.getElementById('compCanvas');

    function isPCBMode() {
        return document.getElementById('viewPCBBtn').classList.contains('active');
    }

    canvas.addEventListener('mousemove', (e) => {
        if (isPCBMode()) {
            pcbHandleMouseMove(e);
            return;
        }
        if (!currentTransform) return;
        const rect = canvas.getBoundingClientRect();
        const mouseX = (e.clientX - rect.left) * (canvas.width / rect.width);
        const mouseY = (e.clientY - rect.top) * (canvas.height / rect.height);
        
        // Inverse transform to get mm coords
        const t = currentTransform;
        const s = t.baseScale * zoomLevel;
        const mmX = (mouseX - t.cx - panX) / s + t.midX;
        const mmY = -((mouseY - t.cy - panY) / s) + t.midY;
        
        if (coordDisplay) {
            coordDisplay.textContent = `X: ${mmX.toFixed(2)} Y: ${mmY.toFixed(2)}`;
        }
    });

    canvas.addEventListener('mousedown', (e) => {
        if (isPCBMode()) {
            pcbHandleMouseDown(e);
            return;
        }
        handleMouseDown(e);
    });

    canvas.addEventListener('mouseup', (e) => {
        if (isPCBMode()) {
            pcbHandleMouseUp(e);
            return;
        }
        handleMouseUp(e);
    });

    canvas.addEventListener('wheel', (e) => {
        if (isPCBMode()) {
            pcbHandleWheel(e);
            return;
        }
        handleWheel(e);
    }, { passive: false });

    canvas.addEventListener('mouseleave', (e) => {
        if (isPCBMode()) pcbHandleMouseUp(e);
        else handleMouseUp(e);
    });

    // ── SocketIO ──────────────────────────────────────────────────────────────

    function connectSocket() {
        socket = io();
        socket.on('connect', () => {
            addLogEntry('Connected to agent backend.', 'system');
        });
        socket.on('disconnect', () => {
            addLogEntry('Disconnected from agent backend.', 'system');
        });
        socket.on('agent:thinking', (data) => {
            showAgentStatus(data.message || 'Thinking...');
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
            showAgentStatus('');
            addConversationMessage('system', data.message || 'Design complete.');
            updateComponentListUI();
            updateSchematicButtons();
            const exportBtn = document.getElementById('exportSchBtn');
            if (exportBtn) exportBtn.disabled = false;
        });
        socket.on('agent:pcb_ready', (data) => {
            if (data.board_model) {
                pcbLoadBoard(data.board_model);
                addLogEntry('PCB model loaded for board view.', 'success');
                exportPCBBtn.disabled = false;
                importPCBBtn.disabled = false;
            }
        });
        socket.on('agent:error', (data) => {
            agentBusy = false;
            updateAgentButton();
            showAgentStatus('');
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
        entry.className = 'conv-message conv-' + (type || 'log');
        entry.textContent = text;
        agentConversation.appendChild(entry);
        agentConversation.scrollTop = agentConversation.scrollHeight;
    }

    function showAgentStatus(text) {
        agentThinking.textContent = text || '';
        agentThinking.classList.toggle('active', !!text);
    }

    function updateAgentButton() {
        agentBtn.disabled = agentBusy || !agentPrompt.value.trim();
        agentBtn.textContent = agentBusy ? 'Building...' : 'Build';
    }

    const conversation = []; // append-only conversation log

    function handleConversationEvent(data) {
        if (!data || !data.type) return;
        conversation.push(data);
        const empty = agentConversation.querySelector('.conv-empty');
        if (empty) empty.remove();

        if (data.type === 'assistant') {
            const bubble = document.createElement('div');
            bubble.className = 'conv-assistant';
            bubble.textContent = data.content || '';
            agentConversation.appendChild(bubble);
        } else if (data.type === 'tool_card') {
            const card = document.createElement('div');
            card.className = `conv-tool-card ${data.status || 'running'}`;

            let detailsHtml = '';
            if (data.details) {
                const detailStr = typeof data.details === 'object'
                    ? Object.entries(data.details).map(([k, v]) => `${k}: ${v}`).join('\n')
                    : data.details;
                detailsHtml = `<div class="tc-details" hidden>${escapeHtml(detailStr)}</div>`;
            }

            card.innerHTML = `
                <div class="tc-header">
                    <span class="tc-dot ${data.status || 'running'}"></span>
                    <span class="tc-title">${escapeHtml(data.title)}</span>
                    <span class="tc-status">${data.status || 'running'}</span>
                </div>
                ${data.summary ? `<div class="tc-summary">${escapeHtml(data.summary)}</div>` : ''}
                ${detailsHtml}
            `;

            const header = card.querySelector('.tc-header');
            const detailsDiv = card.querySelector('.tc-details');
            if (detailsDiv) {
                header.style.cursor = 'pointer';
                header.addEventListener('click', () => {
                    detailsDiv.hidden = !detailsDiv.hidden;
                    card.classList.toggle('expanded', !detailsDiv.hidden);
                });
            }

            agentConversation.appendChild(card);
        }

        agentConversation.scrollTop = agentConversation.scrollHeight;
    }

    function clearConversation() {
        conversation.length = 0;
        agentConversation.innerHTML = '';
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

    function handleAgentLayoutReady(data) {
        if (!currentSchematic) return;
        const placements = data.placements || [];
        const traces = data.traces || [];
        const powerLabels = data.power_labels || [];
        const netlist = data.netlist || [];
        const powerPins = data.power_pins || [];

        // Fallback: backend grid-router layout
        const applyBackendLayout = (label = 'fallback router') => {
            placements.forEach(p => {
                const comp = currentSchematic.components.find(c => c.refDesignator === p.ref_des);
                if (comp) { comp.x = p.x; comp.y = p.y; }
            });
            currentSchematic.wirePaths = traces;
            currentSchematic.powerLabels = powerLabels;
            enterSchematicMode();
            addLogEntry(`Laid out ${placements.length} components (${label}).`, 'success');
        };

        // Skip WireBender for large designs — WASM router reliability degrades.
        if (traces.length > 20) {
            addLogEntry(`Skipping WireBender (${traces.length} wires > 20 threshold), using backend layout.`, 'log');
            applyBackendLayout();
            return;
        }

        // Preferred: WireBender WASM — advanced placement + orthogonal routing.
        addLogEntry('Running WireBender layout...', 'log');
        runWireBenderLayout(netlist, powerPins)
            .then(() => {
                setActiveTab('viewSchematicBtn');
                enterSchematicMode();
                addLogEntry(
                    `WireBender: placed ${currentSchematic.components.length} components, ` +
                    `routed ${(currentSchematic.wirePaths || []).length} wires, ` +
                    `${(currentSchematic.powerLabels || []).length} power symbols.`, 'success');
                saveLayoutToServer();
            })
            .catch(err => {
                console.error('WireBender failed:', err);
                addLogEntry('WireBender failed (' + err.message + '), using fallback router.', 'error');
                applyBackendLayout();
            });
    }

    // ── WireBender (WASM) layout + routing ──────────────────────────────────

    let _WB = null;     // WASM Module
    let _wb = null;     // WireBender Instance
    let _modulePromise = null;

    async function initWireBender() {
        if (_modulePromise) return _modulePromise;
        console.log('[WireBender] Loading WASM module...');
        _modulePromise = import('https://dev-lab.github.io/WireBender/latest/WireBender.js')
            .then(m => m.default({
                locateFile: f => f === 'WireBender.wasm' ? 'https://dev-lab.github.io/WireBender/latest/WireBender.wasm' : f,
            }))
            .then(module => {
                _WB = module;
                console.log('[WireBender] WASM module loaded');
            })
            .catch(err => {
                console.error('[WireBender] Failed to load WASM module:', err);
                throw err;
            });
        return _modulePromise;
    }

    async function runWireBenderLayout(netlist, powerPins) {
        await initWireBender();
        if (!_WB) throw new Error('WireBender module not loaded');

        // Cleanup old instance
        if (_wb) {
            try { _wb.delete(); } catch(e) {}
        }
        _wb = new _WB.WireBender();

        const comps = currentSchematic.components;

        // 1. Register Components
        comps.forEach(c => {
            const g = c.geomBBox;
            const pinsVec = new _WB.VectorPinDescriptor();
            
            // Extract pins for WireBender
            for (const op of c.ops) {
                if (op[0] !== 'pin') continue;
                const at = _getAttr(op, 'at');
                const len = _getAttr(op, 'length');
                const num = _getAttr(op, 'number');
                if (!at || !len || !num) continue;
                
                const x = parseFloat(at[1]), y = parseFloat(at[2]);
                const angDeg = parseFloat(at[3] || 0);
                const l = parseFloat(len[1]);
                
                // Endpoint relative to symbol origin
                const ex = x + Math.cos(angDeg * Math.PI / 180) * l;
                const ey = y + Math.sin(angDeg * Math.PI / 180) * l;
                const key = String(num[1]).replace(/"/g, '');
                
                // Direction flags (libavoid style: Right=1, Up=2, Left=4, Down=8)
                let df = 0;
                const deg = (Math.round(angDeg) + 360) % 360;
                if (deg === 0) df = 1;       // Right (East)
                else if (deg === 90) df = 2;  // Up (North)
                else if (deg === 180) df = 4; // Left (West)
                else if (deg === 270) df = 8; // Down (South)
                
                pinsVec.push_back({
                    number: parseInt(key) || 0,
                    name: key,
                    x: ex - g.x, // Local coords within the component bbox
                    y: ey - g.y,
                    directionFlags: df
                });
            }

            _wb.addComponent({
                id: c.refDesignator,
                width: g.w,
                height: g.h,
                padding: 10.16, // Increase padding for cleaner routing
                pins: pinsVec
            });
            pinsVec.delete();
        });

        // 2. Register Nets
        const netGroups = {};
        (netlist || []).forEach(conn => {
            const netName = conn.net || `n_${conn.source.replace(/:/g,'_')}_${conn.target.replace(/:/g,'_')}`;
            if (!netGroups[netName]) netGroups[netName] = new Set();
            netGroups[netName].add(conn.source);
            netGroups[netName].add(conn.target);
        });

        for (const netName in netGroups) {
            const pinsVec = new _WB.VectorPinRef();
            netGroups[netName].forEach(pinKey => {
                const [ref, num] = pinKey.split(':');
                pinsVec.push_back({ componentId: ref, pinNumber: parseInt(num) || 0 });
            });
            _wb.addNet({ name: netName, pins: pinsVec });
            pinsVec.delete();
        }

        // 3. Compute Auto-Placement
        const cls = _wb.classify();
        _wb.applyClassification(cls);
        cls.delete();

        const placementResult = _wb.computePlacements();
        const placements = placementResult.toObject();
        placementResult.delete();
        
        // 4. Compute Orthogonal Routing
        const routeResult = _wb.routeAll();
        
        // 5. Apply results to Schematic
        for (const id in placements) {
            const p = placements[id];
            const comp = currentSchematic.components.find(c => c.refDesignator === id);
            if (comp) {
                const g = comp.geomBBox;
                // WireBender gives center-based positions. 
                // We need to set comp.x/y such that symbol (0,0) is at the right spot.
                comp.x = snapToGrid(p.position.x - (g.w / 2 + g.x));
                comp.y = snapToGrid(p.position.y - (g.h / 2 + g.y));
            }
        }

        // Apply wires (with validation — drop bad ones)
        const MAX_WIRE_LEN = 150.0;  // mm, matches backend hard cap
        const rawWires = [];
        for (let i = 0; i < routeResult.wires.size(); i++) {
            const wire = routeResult.wires.get(i);
            const pts = [];
            for (let j = 0; j < wire.points.size(); j++) {
                const p = wire.points.get(j);
                pts.push({ x: p.x, y: p.y });
            }
            if (pts.length >= 2) {
                rawWires.push({ source: wire.net, target: '', path: pts });
            }
        }
        // Validate every wire; drop bad ones, count them.
        const validWires = [];
        let nDropped = 0;
        for (const w of rawWires) {
            if (isValidWirePath(w.path, MAX_WIRE_LEN)) {
                validWires.push(w);
            } else {
                // Try to repair diagonal segments before giving up
                const repaired = repairWirePath(w.path);
                if (repaired && isValidWirePath(repaired, MAX_WIRE_LEN)) {
                    validWires.push({ ...w, path: repaired });
                } else {
                    nDropped++;
                }
            }
        }
        // If too many wires are bad (> 50%), abort WireBender and use backend traces.
        if (rawWires.length > 0 && nDropped / rawWires.length > 0.5) {
            throw new Error(
                `WireBender produced ${nDropped}/${rawWires.length} bad wires — falling back to backend router.`
            );
        }
        currentSchematic.wirePaths = validWires;
        if (nDropped > 0) {
            console.log(`[WireBender] Dropped ${nDropped} bad wire(s), kept ${validWires.length}.`);
        }
        
        // Apply junctions
        currentSchematic.junctionPoints = [];
        for (let i = 0; i < routeResult.junctions.size(); i++) {
            const j = routeResult.junctions.get(i);
            currentSchematic.junctionPoints.push({ x: j.position.x, y: j.position.y, net: j.net });
        }

        // Power symbols
        const worldPin = (key) => {
            const [ref, num] = key.split(':');
            const comp = currentSchematic.components.find(cc => cc.refDesignator === ref);
            if (!comp) return null;
            const g = comp.geomBBox;
            for (const op of comp.ops) {
                if (op[0] === 'pin' && String(_getAttr(op, 'number')[1]).replace(/"/g,'') === num) {
                    const at = _getAttr(op, 'at');
                    const len = _getAttr(op, 'length');
                    const x = parseFloat(at[1]), y = parseFloat(at[2]);
                    const ang = parseFloat(at[3] || 0) * Math.PI / 180;
                    const l = parseFloat(len[1]);
                    return { 
                        x: comp.x + x + Math.cos(ang) * l, 
                        y: comp.y + y + Math.sin(ang) * l,
                        side: (Math.abs(Math.cos(ang)) > 0.8) ? (Math.cos(ang) > 0 ? 'EAST' : 'WEST') : (Math.sin(ang) > 0 ? 'SOUTH' : 'NORTH')
                    };
                }
            }
            return null;
        };

        const labels = [];
        (powerPins || []).forEach(pp => {
            const pos = worldPin(pp.pin);
            if (!pos) return;
            let dir = 'right';
            if (pos.side) {
                dir = { EAST: 'right', WEST: 'left', NORTH: 'up', SOUTH: 'down' }[pos.side];
            }
            labels.push({ pin: pp.pin, net: pp.net, x: pos.x, y: pos.y, dir });
        });
        currentSchematic.powerLabels = labels;
        
        try { routeResult.delete(); } catch(e) {}
    }

    function _getAttr(node, name) {
        if (!Array.isArray(node)) return null;
        for (let i = 1; i < node.length; i++) {
            if (Array.isArray(node[i]) && node[i][0] === name) return node[i];
        }
        return null;
    }

    // Send the ELK-computed geometry to the server so .kicad_sch export matches
    function saveLayoutToServer(boardModel) {
        const placements = currentSchematic.components.map(c => ({
            ref_des: c.refDesignator, x: c.x, y: c.y,
        }));
        fetch('/api/save_layout', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                placements,
                wire_paths: currentSchematic.wirePaths || [],
                power_labels: currentSchematic.powerLabels || [],
                board_model: boardModel || null,
            }),
        }).catch(() => {});
    }

    connectSocket();

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
                pcbLoadBoard(data.board_model);
                addLogEntry(`Imported ${file.name}: ${data.board_model.components.length} components, ${data.board_model.traces.length} traces.`, 'success');
                setActiveTab('viewPCBBtn');
                viewPCBBtn.click();
            } catch (err) {
                addLogEntry(`Import error: ${err.message}`, 'error');
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

    // ── Search ────────────────────────────────────────────────────────────────

    searchBtn.addEventListener('click', performSearch);
    searchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') performSearch();
    });

    async function performSearch() {
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
                    li.innerHTML = `<div class="result-id">${item.id_str}</div>
                                    <div class="result-text">${item.text}</div>`;
                    li.addEventListener('click', () => {
                        document.querySelectorAll('#searchResults li').forEach(el => el.classList.remove('selected'));
                        li.classList.add('selected');
                        previewComponent(item.id_str, item.text);
                    });
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
        componentInfo.textContent = `Loading ${id_str}...`;
        try {
            const sexpr = await fetchSExpr(id_str);
            const category = id_str.split(':')[0];
            const ops = await resolveAndParse(sexpr, category);
            selectedComponent = { id_str, textDesc, ops, category };
            currentPreviewOps = ops;
            if (currentSchematic) currentSchematic.mode = 'single';
            setActiveTab('viewSymbolBtn');
            renderOps(ops);
            componentInfo.innerHTML = `<div class="prop-group">
                <div class="prop-row"><span class="prop-key">ID</span><span class="prop-val">${id_str}</span></div>
                <div class="prop-row"><span class="prop-key">Description</span><span class="prop-val">${textDesc || 'Inherited'}</span></div>
                <div class="prop-row"><span class="prop-key">Ops</span><span class="prop-val">${ops.length}</span></div>
            </div>`;
            updateAddButton();
        } catch (err) {
            componentInfo.textContent = `Error loading ${id_str}:\n${err.message}`;
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
        autoLayoutBtn.disabled = !hasComponents;
        autoRouteBtn.disabled = !hasComponents;
        clearBtn.disabled = !hasComponents;
        viewSchematicBtn.disabled = !hasComponents;
        compCount.textContent = (currentSchematic ? currentSchematic.components.length : 0);
    }

    function updateComponentListUI() {
        componentList.innerHTML = '';
        if (!currentSchematic) return;
        currentSchematic.components.forEach(comp => {
            const li = document.createElement('li');
            li.className = `col-${comp.column}`;
            li.innerHTML = `
                <div class="comp-label">
                    <div class="comp-name">${comp.refDesignator} - ${comp.name.split(':').pop()}</div>
                    <div class="comp-id">${comp.id}</div>
                </div>
                <button class="comp-remove" title="Remove">&times;</button>
            `;
            li.querySelector('.comp-remove').addEventListener('click', (e) => {
                e.stopPropagation();
                currentSchematic.removeComponent(comp.id);
                updateComponentListUI();
                updateSchematicButtons();
                if (currentSchematic.mode === 'schematic') enterSchematicMode();
            });
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
        enterSchematicMode();
        updateComponentListUI();
        updateSchematicButtons();
    });

    // ── Auto Route ────────────────────────────────────────────────────────────

    autoRouteBtn.addEventListener('click', async () => {
        if (!currentSchematic || currentSchematic.components.length < 2) return;
        const pinMatrix = currentSchematic.resolveAbsolutePins();
        const prompt = routePrompt.value.trim();
        addLogEntry('Generating netlist via LLM...', 'log');
        try {
            const res = await fetch('/api/generate_netlist', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pinMatrix, prompt }),
            });
            const netlist = await res.json();
            if (!netlist || netlist.length === 0) {
                addLogEntry('No connections generated.', 'error');
                return;
            }
            currentSchematic.autoRoute(netlist);
            if (currentSchematic.mode === 'schematic') {
                drawSchematic();
            } else {
                setActiveTab('viewSchematicBtn');
                enterSchematicMode();
            }
            addLogEntry(`Auto-routed ${netlist.length} connections.`, 'success');
        } catch (err) {
            addLogEntry(`Route error: ${err.message}`, 'error');
        }
    });

    // ── Auto Layout ───────────────────────────────────────────────────────────

    autoLayoutBtn.addEventListener('click', () => {
        if (!currentSchematic || currentSchematic.components.length === 0) return;
        currentSchematic.autoLayout();
        if (currentSchematic.mode === 'schematic') enterSchematicMode();
        addLogEntry(`Auto-layout applied to ${currentSchematic.components.length} components.`, 'success');
    });

    // ── Clear All ─────────────────────────────────────────────────────────────

    clearBtn.addEventListener('click', () => {
        if (!currentSchematic) return;
        currentSchematic.clear();
        currentTransform = null;
        zoomLevel = 1;
        panX = 0;
        panY = 0;
        modeIndicator.classList.add('hidden');
        updateComponentListUI();
        updateSchematicButtons();
        const { canvas, ctx } = getCanvasAndCtx();
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        componentInfo.innerHTML = '<div class="empty-state">Schematic cleared.</div>';
    });

    // ── AI Agent ──────────────────────────────────────────────────────────────

    agentBtn.addEventListener('click', () => {
        const prompt = agentPrompt.value.trim();
        if (!prompt || agentBusy) return;
        agentBusy = true;
        updateAgentButton();
        clearConversation();
        agentConversation.innerHTML = '';
        showAgentStatus('Starting agent...');
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
        if (isPCBMode()) { pcbState.zoom = Math.min(pcbState.zoom * 1.3, 50); pcbDraw(); }
        else zoomIn();
    });
    document.getElementById('zoomOutBtn').addEventListener('click', () => {
        if (isPCBMode()) { pcbState.zoom = Math.max(pcbState.zoom / 1.3, 0.05); pcbDraw(); }
        else zoomOut();
    });
    document.getElementById('zoomResetBtn').addEventListener('click', () => {
        if (isPCBMode()) { pcbComputeTransform(); pcbDraw(); }
        else resetZoom();
    });

    let resizeTimer;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => {
            setupCanvasSize();
            if (isPCBMode()) {
                pcbSetupCanvas();
                if (pcbState.boardModel) pcbDraw();
            } else if (currentSchematic && currentSchematic.mode === 'schematic' && currentSchematic.components.length > 0) {
                enterSchematicMode();
            } else if (currentPreviewOps) {
                renderOps(currentPreviewOps);
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
