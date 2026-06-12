let socket = null;

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
    const componentList = document.getElementById('componentList');
    const compCount = document.getElementById('compCount');
    const modeIndicator = document.getElementById('modeIndicator');
    const autoRouteBtn = document.getElementById('autoRouteBtn');
    const routePrompt = document.getElementById('routePrompt');
    const agentBtn = document.getElementById('agentBtn');
    const agentPrompt = document.getElementById('agentPrompt');
    const agentLog = document.getElementById('agentLog');

    let selectedComponent = null;
    let currentPreviewOps = null;
    let agentBusy = false;

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
            addLogEntry(data.message || 'Design complete.', 'success');
            updateComponentListUI();
            updateSchematicButtons();
            const exportBtn = document.getElementById('exportSchBtn');
            if (exportBtn) exportBtn.disabled = false;
        });
        socket.on('agent:error', (data) => {
            agentBusy = false;
            updateAgentButton();
            showAgentStatus('');
            addLogEntry('Error: ' + (data.message || 'Unknown error'), 'error');
        });
    }

    function addLogEntry(text, type) {
        const empty = agentLog.querySelector('.agent-log-empty');
        if (empty) empty.remove();
        const entry = document.createElement('div');
        entry.className = 'agent-log-entry ' + (type || 'log');
        entry.textContent = text;
        agentLog.appendChild(entry);
        agentLog.scrollTop = agentLog.scrollHeight;
    }

    function showAgentStatus(text) {
        const existing = agentLog.querySelector('.agent-status');
        if (existing) existing.remove();
        if (!text) return;
        const entry = document.createElement('div');
        entry.className = 'agent-log-entry agent-status';
        entry.innerHTML = '<span class="spinner"></span> ' + text;
        agentLog.appendChild(entry);
        agentLog.scrollTop = agentLog.scrollHeight;
    }

    function updateAgentButton() {
        agentBtn.disabled = agentBusy || !agentPrompt.value.trim();
        agentBtn.textContent = agentBusy ? 'Building...' : 'Build';
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
        const applyBackendLayout = () => {
            placements.forEach(p => {
                const comp = currentSchematic.components.find(c => c.refDesignator === p.ref_des);
                if (comp) { comp.x = p.x; comp.y = p.y; }
            });
            currentSchematic.wirePaths = traces;
            currentSchematic.powerLabels = powerLabels;
            enterSchematicMode();
            addLogEntry(`Laid out ${placements.length} components (fallback router).`, 'success');
        };

        // Preferred: ELK (Eclipse Layout Kernel) — proven auto-layout +
        // orthogonal edge routing. LLM decides connectivity only.
        if (typeof ELK !== 'undefined') {
            addLogEntry('Running ELK auto-layout & orthogonal routing...', 'log');
            runElkLayout(netlist, powerPins)
                .then(() => {
                    enterSchematicMode();
                    addLogEntry(
                        `ELK: placed ${currentSchematic.components.length} components, ` +
                        `routed ${(currentSchematic.wirePaths || []).length} wires, ` +
                        `${(currentSchematic.powerLabels || []).length} power symbols.`, 'success');
                    saveLayoutToServer();
                })
                .catch(err => {
                    console.error('ELK layout failed:', err);
                    addLogEntry('ELK failed (' + err.message + '), using fallback router.', 'error');
                    applyBackendLayout();
                });
        } else {
            applyBackendLayout();
        }
    }

    // ── ELK auto-layout + orthogonal routing ────────────────────────────────

    function _getAttr(node, name) {
        if (!Array.isArray(node)) return null;
        for (let i = 1; i < node.length; i++) {
            if (Array.isArray(node[i]) && node[i][0] === name) return node[i];
        }
        return null;
    }

    // Extract pin endpoints (local symbol coords, y-up) keyed by pin number.
    // Also records which SIDE of the symbol body each pin is on, so we can
    // give ELK fixed port sides (lets it flip components to face neighbours).
    function extractPinEndpoints(ops, geom) {
        const pins = {};
        const cx = geom.x + geom.w / 2;
        const cy = geom.y + geom.h / 2;
        for (const op of ops) {
            if (op[0] !== 'pin') continue;
            const at = _getAttr(op, 'at');
            const len = _getAttr(op, 'length');
            const num = _getAttr(op, 'number');
            if (!at || !len || !num) continue;
            const x = parseFloat(at[1]), y = parseFloat(at[2]);
            const ang = parseFloat(at[3] || 0) * Math.PI / 180;
            const l = parseFloat(len[1]);
            const ex = x + Math.cos(ang) * l;
            const ey = y + Math.sin(ang) * l;
            const key = String(num[1]).replace(/"/g, '');
            if (pins[key]) continue;
            // Side relative to body center (endpoint is outside the body)
            const dx = ex - cx, dy = ey - cy;
            let side;
            if (Math.abs(dx) >= Math.abs(dy)) side = dx >= 0 ? 'EAST' : 'WEST';
            else side = dy >= 0 ? 'NORTH' : 'SOUTH';
            pins[key] = { x: ex, y: ey, side };
        }
        return pins;
    }

    async function runElkLayout(netlist, powerPins) {
        const elk = new ELK();
        const comps = currentSchematic.components;
        const localPins = {}; // refDes -> {pinNum: {x, y, side}}

        // Single global Y-flip reference so every component shares the SAME
        // coordinate origin (fixes the perimeter "ghost wire" artifact).
        const FLIP = 1000;

        const children = comps.map(c => {
            const g = c.geomBBox;
            const pins = extractPinEndpoints(c.ops, g);
            localPins[c.refDesignator] = pins;
            // Port position in node-local space (ELK y-down, origin = node TL)
            const ports = Object.entries(pins).map(([numKey, p]) => ({
                id: `${c.refDesignator}:${numKey}`,
                x: p.x - g.x,
                y: g.h - (p.y - g.y),       // flip y-up -> y-down within node
                width: 0.01,
                height: 0.01,
                layoutOptions: {
                    'elk.port.side': p.side,
                    // order ports along their side by the cross-axis position
                    'elk.port.index': String(Math.round(
                        (p.side === 'EAST' || p.side === 'WEST') ? -p.y : p.x)),
                },
            }));
            return {
                id: c.refDesignator,
                width: g.w,
                height: g.h,
                // FIXED_SIDE keeps each pin on its real side but lets ELK
                // mirror/rotate the node so connected pins face neighbours.
                layoutOptions: { 'elk.portConstraints': 'FIXED_SIDE' },
                ports,
            };
        });

        const validPorts = new Set();
        children.forEach(n => n.ports.forEach(p => validPorts.add(p.id)));

        const edges = [];
        const connectedNodes = new Set();
        (netlist || []).forEach((conn, i) => {
            if (validPorts.has(conn.source) && validPorts.has(conn.target)) {
                edges.push({ id: 'e' + i, sources: [conn.source], targets: [conn.target] });
                connectedNodes.add(conn.source.split(':')[0]);
                connectedNodes.add(conn.target.split(':')[0]);
            }
        });

        const graph = {
            id: 'root',
            layoutOptions: {
                'elk.algorithm': 'layered',
                'elk.direction': 'RIGHT',
                'elk.edgeRouting': 'ORTHOGONAL',
                // group connected sub-circuits; keep disconnected parts tidy
                'elk.layered.components.compaction': 'true',
                'elk.separateConnectedComponents': 'true',
                'elk.spacing.componentComponent': '15',
                'elk.spacing.nodeNode': '12',
                'elk.layered.spacing.nodeNodeBetweenLayers': '20',
                'elk.spacing.edgeNode': '8',
                'elk.spacing.edgeEdge': '4',
                'elk.layered.spacing.edgeNodeBetweenLayers': '8',
                'elk.layered.considerModelOrder.strategy': 'NODES_AND_EDGES',
                'elk.layered.nodePlacement.strategy': 'NETWORK_SIMPLEX',
                'elk.layered.crossingMinimization.semiInteractive': 'true',
            },
            children,
            edges,
        };

        const res = await elk.layout(graph);

        // Apply node positions. ELK gives node TOP-LEFT in y-down space.
        // Convert to canvas y-up using ONE shared flip origin.
        const nodeById = {};
        (res.children || []).forEach(n => { nodeById[n.id] = n; });
        comps.forEach(c => {
            const n = nodeById[c.refDesignator];
            if (!n) return;
            const g = c.geomBBox;
            // node TL (n.x,n.y) corresponds to symbol-local (g.x, g.y+g.h)
            c.x = snapToGrid(n.x - g.x);
            c.y = snapToGrid(FLIP - (n.y + g.h) - g.y);
        });

        const worldPin = (key) => {
            const sep = key.indexOf(':');
            const ref = key.slice(0, sep), numKey = key.slice(sep + 1);
            const c = comps.find(cc => cc.refDesignator === ref);
            const p = (localPins[ref] || {})[numKey];
            if (!c || !p) return null;
            return { x: c.x + p.x, y: c.y + p.y };
        };

        // Build wire paths from ELK edge sections. ELK routes in y-down space
        // with the SAME origin as nodes, so flip with the shared FLIP origin.
        const wirePaths = [];
        (res.edges || []).forEach(e => {
            const sec = (e.sections || [])[0];
            if (!sec) return;
            let pts = [sec.startPoint, ...(sec.bendPoints || []), sec.endPoint]
                .map(p => ({ x: snapToGrid(p.x), y: snapToGrid(FLIP - p.y) }));

            // Snap the two ends exactly onto the real pin endpoints
            const srcPin = worldPin(e.sources[0]);
            const tgtPin = worldPin(e.targets[0]);
            if (srcPin) attachEndpoint(pts, srcPin, true);
            if (tgtPin) attachEndpoint(pts, tgtPin, false);

            wirePaths.push({ source: e.sources[0], target: e.targets[0], path: pts });
        });
        currentSchematic.wirePaths = wirePaths;

        // Power symbols at power/GND pins (no routed wires for power nets)
        const labels = [];
        (powerPins || []).forEach(pp => {
            const pos = worldPin(pp.pin);
            if (!pos) return;
            const ref = pp.pin.split(':')[0];
            const c = comps.find(cc => cc.refDesignator === ref);
            if (!c) return;
            const p = (localPins[ref] || {})[pp.pin.slice(pp.pin.indexOf(':') + 1)];
            // Use the pin's real side for the symbol direction
            let dir = 'right';
            if (p && p.side) {
                dir = { EAST: 'right', WEST: 'left', NORTH: 'up', SOUTH: 'down' }[p.side];
            }
            labels.push({ pin: pp.pin, net: pp.net, x: pos.x, y: pos.y, dir });
        });
        currentSchematic.powerLabels = labels;
    }

    // Move a path endpoint exactly onto the pin while keeping orthogonality
    function attachEndpoint(pts, pinPos, isStart) {
        if (!pts.length) return;
        const idx = isStart ? 0 : pts.length - 1;
        const adjIdx = isStart ? 1 : pts.length - 2;
        if (pts.length >= 2) {
            const p = pts[idx], a = pts[adjIdx];
            if (Math.abs(a.x - p.x) < 0.01) a.x = pinPos.x;       // vertical segment
            else if (Math.abs(a.y - p.y) < 0.01) a.y = pinPos.y;  // horizontal segment
        }
        pts[idx] = { x: pinPos.x, y: pinPos.y };
    }

    // Send the ELK-computed geometry to the server so .kicad_sch export matches
    function saveLayoutToServer() {
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
            renderOps(ops);
            componentInfo.textContent = `ID: ${id_str}\nDesc: ${textDesc || 'Inherited'}\n\nReady to add to schematic. (${ops.length} ops)`;
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
        compCount.textContent = `(${currentSchematic ? currentSchematic.components.length : 0})`;
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
        componentInfo.textContent = `Added: ${comp.refDesignator} (${comp.name})\nColumn: ${COLUMN_DEFS[comp.column].label}\nPosition: (${comp.x.toFixed(2)}, ${comp.y.toFixed(2)})\n\nTotal components: ${currentSchematic.components.length}`;
        updateComponentListUI();
        updateSchematicButtons();
        if (currentSchematic.components.length >= 2) {
            enterSchematicMode();
        }
    });

    // ── Auto Route ────────────────────────────────────────────────────────────

    autoRouteBtn.addEventListener('click', async () => {
        if (!currentSchematic || currentSchematic.components.length < 2) return;
        const pinMatrix = currentSchematic.resolveAbsolutePins();
        const prompt = routePrompt.value.trim();
        componentInfo.textContent = 'Generating netlist...';
        try {
            const res = await fetch('/api/generate_netlist', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ pinMatrix, prompt }),
            });
            const netlist = await res.json();
            if (!netlist || netlist.length === 0) {
                componentInfo.textContent = 'No connections generated.';
                return;
            }
            currentSchematic.autoRoute(netlist);
            if (currentSchematic.mode === 'schematic') {
                drawSchematic();
            } else {
                enterSchematicMode();
            }
            componentInfo.textContent = `Auto-routed ${netlist.length} connections.`;
        } catch (err) {
            componentInfo.textContent = `Route error: ${err.message}`;
        }
    });

    // ── Auto Layout ───────────────────────────────────────────────────────────

    autoLayoutBtn.addEventListener('click', () => {
        if (!currentSchematic || currentSchematic.components.length === 0) return;
        currentSchematic.autoLayout();
        if (currentSchematic.mode === 'schematic') enterSchematicMode();
        componentInfo.textContent = `Auto-layout applied to ${currentSchematic.components.length} components.\nColumns: ${COLUMN_DEFS.map((c, i) => `${c.label}: ${currentSchematic.components.filter(comp => comp.column === i).length}`).join(', ')}`;
    });

    // ── View Schematic ───────────────────────────────────────────────────────

    viewSchematicBtn.addEventListener('click', () => {
        if (!currentSchematic || currentSchematic.components.length === 0) return;
        enterSchematicMode();
        modeIndicator.classList.remove('hidden');
        componentInfo.textContent = `Schematic View: ${currentSchematic.components.length} components\nScroll to zoom, drag to pan.\n\nColumns:\n${COLUMN_DEFS.map((c, i) => `  ${c.label}: ${currentSchematic.components.filter(comp => comp.column === i).length} components`).join('\n')}`;
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
        componentInfo.textContent = 'Schematic cleared.';
    });

    // ── AI Agent ──────────────────────────────────────────────────────────────

    agentBtn.addEventListener('click', () => {
        const prompt = agentPrompt.value.trim();
        if (!prompt || agentBusy) return;
        agentBusy = true;
        updateAgentButton();
        agentLog.innerHTML = '';
        addLogEntry(`Request: "${prompt}"`, 'system');
        showAgentStatus('Starting agent...');
        if (!currentSchematic) currentSchematic = new Schematic();
        socket.emit('agent:generate', { prompt });
    });

    agentPrompt.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') agentBtn.click();
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

    document.getElementById('zoomInBtn').addEventListener('click', zoomIn);
    document.getElementById('zoomOutBtn').addEventListener('click', zoomOut);
    document.getElementById('zoomResetBtn').addEventListener('click', resetZoom);

    let resizeTimer;
    window.addEventListener('resize', () => {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(() => {
            setupCanvasSize();
            if (currentSchematic && currentSchematic.mode === 'schematic' && currentSchematic.components.length > 0) {
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
