let socket = null;
const CIRCUITBOT_LAYOUT_VERSION = 'v8-backend-routing';
console.log('%c[CircuitBot] Layout engine ' + CIRCUITBOT_LAYOUT_VERSION + ' loaded', 'color:#a371f7;font-weight:bold');

document.addEventListener('DOMContentLoaded', () => {
    const searchInput = document.getElementById('searchInput');
    const searchBtn = document.getElementById('searchBtn');
    const searchResults = document.getElementById('searchResults');
    const loading = document.getElementById('loading');
    const componentInfo = document.getElementById('componentInfo');
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
    const agentThinking = document.getElementById('agentThinking');
    const coordDisplay = document.getElementById('coordDisplay');
    const zoomLevelDisplay = document.getElementById('zoomLevel');

    let selectedComponent = null;
    let currentPreviewOps = null;
    let agentBusy = false;
    let currentSchematic = null;

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
                showViewport('symbol');
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
                enterSchematicView();
                pcbUploadArea.classList.add('hidden');
                document.getElementById('routePrompt').classList.remove('hidden');
            }
        });
    }

    if (viewPCBBtn) {
        viewPCBBtn.addEventListener('click', () => {
            setActiveTab('viewPCBBtn');
            showViewport('pcb');
            document.getElementById('routePrompt').classList.add('hidden');
            pcbUploadArea.classList.add('hidden');
            if (_useLegacyPcbViewer) {
                if (pcbState.boardModel) {
                    pcbSetupCanvas();
                    pcbDraw();
                } else {
                    showPcbUploadOverlay();
                }
            } else {
                if (pcbState.boardModel) {
                    if (window.TscircuitViewer) {
                        window.TscircuitViewer.mount('tscircuit-container');
                    }
                } else {
                    showPcbUploadOverlay();
                }
            }
        });
    }

    function showPcbUploadOverlay() {
        pcbUploadArea.classList.remove('hidden');
        if (!_useLegacyPcbViewer) {
            const tsc = document.getElementById('tscircuit-container');
            if (tsc) tsc.style.display = 'none';
        }
    }

    // ── Renderer (PixiJS) ────────────────────────────────────────────────────

    let renderer = null;
    let _initialRenderZoom = 1;

    function getRenderer() {
        if (!renderer) {
            renderer = new SchematicRenderer('canvasContainer', {
                onSelect: (comp) => {
                    if (comp) {
                        componentInfo.innerHTML = `<div class="prop-group">
                            <div class="prop-row"><span class="prop-key">Ref</span><span class="prop-val">${comp.refDesignator}</span></div>
                            <div class="prop-row"><span class="prop-key">Name</span><span class="prop-val">${comp.name.split(':').pop()}</span></div>
                            <div class="prop-row"><span class="prop-key">Category</span><span class="prop-val">${comp.category}</span></div>
                            <div class="prop-row"><span class="prop-key">Position</span><span class="prop-val">${comp.x.toFixed(2)}, ${comp.y.toFixed(2)} mm</span></div>
                        </div>`;
                    } else {
                        componentInfo.innerHTML = '<div class="empty-state">No component selected</div>';
                    }
                },
                onCoordChange: (wx, wy) => {
                    if (coordDisplay) coordDisplay.textContent = `X: ${wx.toFixed(2)} Y: ${wy.toFixed(2)}`;
                },
                onZoomChange: (zoom) => {
                    const pct = Math.round(zoom / (_initialRenderZoom || zoom) * 100);
                    if (zoomLevelDisplay) zoomLevelDisplay.textContent = `${pct}%`;
                },
            });
            _initialRenderZoom = renderer.zoom;
        }
        return renderer;
    }

    function isPCBMode() {
        return document.getElementById('viewPCBBtn').classList.contains('active');
    }

    function isSymbolPreviewMode() {
        return document.getElementById('viewSymbolBtn').classList.contains('active');
    }

    let _useLegacyPcbViewer = false;

    function showViewport(active) {
        const container = document.getElementById('canvasContainer');
        const pcbCanvas = document.getElementById('pcbCanvas');
        const symbolCanvas = document.getElementById('symbolCanvas');
        const tscircuitContainer = document.getElementById('tscircuit-container');
        container.style.display = active === 'schematic' ? '' : 'none';
        symbolCanvas.style.display = active === 'symbol' ? '' : 'none';
        if (active === 'pcb') {
            if (_useLegacyPcbViewer) {
                pcbCanvas.style.display = '';
                tscircuitContainer.style.display = 'none';
            } else {
                pcbCanvas.style.display = 'none';
                tscircuitContainer.style.display = '';
            }
        } else {
            pcbCanvas.style.display = 'none';
            tscircuitContainer.style.display = 'none';
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
        if (isPCBMode() && _useLegacyPcbViewer) pcbHandleKeyDown(e);
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
            // Ensure schematic is visible after agent completes
            if (currentSchematic && currentSchematic.components.length > 0) {
                setActiveTab('viewSchematicBtn');
                enterSchematicView();
            }
        });
        socket.on('agent:pcb_approval', (data) => {
            addConversationMessage('assistant', data.message || 'Schematic complete. Proceed to PCB layout?');
            const btnDiv = document.createElement('div');
            btnDiv.className = 'conv-approval-buttons';
            btnDiv.innerHTML = `
                <button class="btn-approve" onclick="socket.emit('agent:pcb_approve', {approved: true})">Proceed to PCB</button>
                <button class="btn-skip" onclick="socket.emit('agent:pcb_approve', {approved: false})">Skip PCB</button>
            `;
            agentConversation.appendChild(btnDiv);
            agentConversation.scrollTop = agentConversation.scrollHeight;
        });
        socket.on('agent:pcb_ready', (data) => {
            if (data.board_model) {
                pcbLoadBoard(data.board_model);
                addLogEntry('PCB model loaded for board view.', 'success');
                exportPCBBtn.disabled = false;
                importPCBBtn.disabled = false;
                // Drive the tscircuit viewer: mount if it has never been
                // mounted yet, otherwise refresh into the existing container.
                if (window.TscircuitViewer && !_useLegacyPcbViewer) {
                    if (window.TscircuitViewer.isMounted && window.TscircuitViewer.isMounted()) {
                        window.TscircuitViewer.refresh();
                    } else {
                        window.TscircuitViewer.mount('tscircuit-container');
                    }
                }
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

    function pruneDisconnectedNetIslands(traces, netlist) {
        const safeTraces = Array.isArray(traces) ? traces : [];
        const safeNetlist = Array.isArray(netlist) ? netlist : [];
        if (safeTraces.length === 0) return [];

        const expectedRefsByNet = {};
        for (const conn of safeNetlist) {
            const net = conn.net || '';
            if (!net) continue;
            if (!expectedRefsByNet[net]) expectedRefsByNet[net] = new Set();
            const s = (conn.source || '').split(':')[0];
            const t = (conn.target || '').split(':')[0];
            if (s) expectedRefsByNet[net].add(s);
            if (t) expectedRefsByNet[net].add(t);
        }

        const tracesByNet = {};
        for (const tr of safeTraces) {
            const net = tr.net || '';
            if (!net) continue;
            if (!tracesByNet[net]) tracesByNet[net] = [];
            tracesByNet[net].push(tr);
        }

        const keep = new Set();
        for (const [net, netTraces] of Object.entries(tracesByNet)) {
            const expected = expectedRefsByNet[net] || new Set();
            if (expected.size <= 2) {
                for (const tr of netTraces) keep.add(tr);
                continue;
            }

            const adj = {};
            for (const ref of expected) adj[ref] = new Set();

            for (const tr of netTraces) {
                const s = (tr.source || '').split(':')[0];
                const t = (tr.target || '').split(':')[0];
                if (!s || !t) continue;
                if (adj[s] && adj[t]) {
                    adj[s].add(t);
                    adj[t].add(s);
                }
            }

            const visited = new Set();
            const groups = [];
            for (const ref of expected) {
                if (visited.has(ref) || !adj[ref] || adj[ref].size === 0) continue;
                const stack = [ref];
                const group = new Set();
                while (stack.length) {
                    const cur = stack.pop();
                    if (visited.has(cur)) continue;
                    visited.add(cur);
                    group.add(cur);
                    for (const nxt of adj[cur]) {
                        if (!visited.has(nxt)) stack.push(nxt);
                    }
                }
                if (group.size > 0) groups.push(group);
            }

            if (groups.length <= 1) {
                for (const tr of netTraces) keep.add(tr);
                continue;
            }

            let mainGroup = groups[0];
            for (let i = 1; i < groups.length; i++) {
                if (groups[i].size > mainGroup.size) mainGroup = groups[i];
            }
            for (const tr of netTraces) {
                const s = (tr.source || '').split(':')[0];
                const t = (tr.target || '').split(':')[0];
                if (mainGroup.has(s) && mainGroup.has(t)) {
                    keep.add(tr);
                }
            }
        }

        return safeTraces.filter(tr => {
            const net = tr.net || '';
            return !net || keep.has(tr);
        });
    }

    function handleAgentLayoutReady(data) {
        if (!currentSchematic) return;
        const placements = data.placements || [];
        const traces = pruneDisconnectedNetIslands(data.traces || [], data.netlist || []);
        const powerLabels = data.power_labels || [];

        // Apply backend placements directly
        placements.forEach(p => {
            const comp = currentSchematic.components.find(c => c.refDesignator === p.ref_des);
            if (comp) { comp.x = p.x; comp.y = p.y; }
        });
        currentSchematic.wirePaths = traces;
        currentSchematic.powerLabels = powerLabels;
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
            showViewport('symbol');
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
                if (currentSchematic.mode === 'schematic') enterSchematicView();
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
        showAgentStatus('Starting agent...');
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
        if (isPCBMode() && _useLegacyPcbViewer) { pcbState.zoom = Math.min(pcbState.zoom * 1.3, 50); pcbDraw(); }
        else if (isSymbolPreviewMode() && currentPreviewOps) { renderOps(currentPreviewOps); }
        else if (renderer) renderer.setZoom(renderer.zoom * 1.3);
    });
    document.getElementById('zoomOutBtn').addEventListener('click', () => {
        if (isPCBMode() && _useLegacyPcbViewer) { pcbState.zoom = Math.max(pcbState.zoom / 1.3, 0.05); pcbDraw(); }
        else if (isSymbolPreviewMode() && currentPreviewOps) { zoomLevel = Math.max(zoomLevel / 1.3, 0.05); drawSymbol(); }
        else if (renderer) renderer.setZoom(renderer.zoom / 1.3);
    });
    document.getElementById('zoomResetBtn').addEventListener('click', () => {
        if (isPCBMode() && _useLegacyPcbViewer) { pcbComputeTransform(); pcbDraw(); }
        else if (isSymbolPreviewMode() && currentPreviewOps) { renderOps(currentPreviewOps); }
        else if (currentSchematic && currentSchematic.components.length > 0) { enterSchematicView(); }
    });

    let resizeTimer;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => {
            if (isPCBMode()) {
                if (_useLegacyPcbViewer) {
                    pcbSetupCanvas();
                    if (pcbState.boardModel) pcbDraw();
                }
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
