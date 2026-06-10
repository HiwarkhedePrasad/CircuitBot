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

        placements.forEach(p => {
            const comp = currentSchematic.components.find(c => c.refDesignator === p.ref_des);
            if (comp) {
                comp.x = p.x;
                comp.y = p.y;
            }
        });

        currentSchematic.wirePaths = traces;

        enterSchematicMode();
        addLogEntry(`Laid out ${placements.length} components, routed ${traces.length} wires.`, 'success');
    }

    connectSocket();

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
