// CircuitBot Panel Logic
// Handles: ERC, DRC, BOM, Memory, MCP Tools, Version History, Requirements, Command Palette

document.addEventListener('DOMContentLoaded', () => {
    const socket = window.io ? window.io() : null;

    // ── ERC Panel ────────────────────────────────────────────────────────────
    const ercRunBtn = document.getElementById('ercRunBtn');
    const ercResults = document.getElementById('ercResults');
    const ercCount = document.getElementById('ercCount');

    if (ercRunBtn && socket) {
        ercRunBtn.addEventListener('click', () => {
            ercRunBtn.textContent = '⏳';
            ercRunBtn.disabled = true;
            socket.emit('erc:run', { session_id: getSessionId() });
        });

        socket.on('erc:results', (data) => {
            ercRunBtn.textContent = '▶';
            ercRunBtn.disabled = false;
            renderErcResults(data.violations || []);
        });
    }

    function renderErcResults(violations) {
        if (!ercResults) return;
        ercResults.innerHTML = '';

        if (violations.length === 0) {
            ercResults.innerHTML = '<div class="erc-placeholder">✅ No electrical rule violations</div>';
            if (ercCount) ercCount.textContent = '0';
            if (window.updateErcStatus) window.updateErcStatus(0);
            return;
        }

        if (ercCount) ercCount.textContent = violations.length;
        if (window.updateErcStatus) window.updateErcStatus(violations.length);

        violations.forEach(v => {
            const div = document.createElement('div');
            div.className = `erc-issue ${v.type}`;
            div.innerHTML = `
                <span class="issue-icon">${v.type === 'error' ? '✕' : v.type === 'warning' ? '⚠' : '✓'}</span>
                <span class="issue-text">${escapeHtml(v.message)}</span>
            `;
            if (v.ref) {
                div.addEventListener('click', () => highlightComponent(v.ref));
            }
            ercResults.appendChild(div);
        });
    }

    // ── DRC Panel ────────────────────────────────────────────────────────────
    const drcRunBtn = document.getElementById('drcRunBtn');
    const drcResults = document.getElementById('drcResults');
    const drcCount = document.getElementById('drcCount');

    if (drcRunBtn && socket) {
        drcRunBtn.addEventListener('click', () => {
            drcRunBtn.textContent = '⏳';
            drcRunBtn.disabled = true;
            socket.emit('drc:run', { session_id: getSessionId() });
        });

        socket.on('drc:results', (data) => {
            drcRunBtn.textContent = '▶';
            drcRunBtn.disabled = false;
            renderDrcResults(data.violations || []);
        });
    }

    function renderDrcResults(violations) {
        if (!drcResults) return;
        drcResults.innerHTML = '';

        if (violations.length === 0) {
            drcResults.innerHTML = '<div class="drc-placeholder">✅ No design rule violations</div>';
            if (drcCount) drcCount.textContent = '0';
            if (window.updateDrcStatus) window.updateDrcStatus(0);
            return;
        }

        if (drcCount) drcCount.textContent = violations.length;
        if (window.updateDrcStatus) window.updateDrcStatus(violations.length);

        violations.forEach(v => {
            const div = document.createElement('div');
            div.className = `drc-violation ${v.type}`;
            div.innerHTML = `
                <span class="violation-icon">${v.type === 'error' ? '✕' : '⚠'}</span>
                <span class="violation-text">${escapeHtml(v.message)}</span>
            `;
            div.addEventListener('click', () => {
                if (v.ref_a) highlightComponent(v.ref_a);
            });
            drcResults.appendChild(div);
        });
    }

    // ── BOM Panel ────────────────────────────────────────────────────────────
    const bomTable = document.getElementById('bomTable');
    const bomSummary = document.getElementById('bomSummary');
    const bomExportBtn = document.getElementById('bomExportBtn');

    window.updateBomPanel = function(components) {
        if (!bomTable) return;
        bomTable.innerHTML = '';

        if (!components || components.length === 0) {
            bomTable.innerHTML = '<div class="bom-placeholder">No components placed yet</div>';
            if (bomSummary) bomSummary.classList.add('hidden');
            return;
        }

        // Group by component type
        const groups = {};
        components.forEach(comp => {
            const key = comp.id_str || comp.name || 'Unknown';
            if (!groups[key]) {
                groups[key] = {
                    ref: comp.ref_des || comp.ref || '?',
                    value: comp.name || comp.description || key,
                    package: comp.footprint || '—',
                    qty: 0,
                };
            }
            groups[key].qty++;
        });

        const entries = Object.values(groups);
        entries.forEach((entry, i) => {
            const row = document.createElement('div');
            row.className = 'bom-row';
            row.innerHTML = `
                <span class="bom-num">${i + 1}</span>
                <span class="bom-ref">${escapeHtml(entry.ref)}</span>
                <span class="bom-value">${escapeHtml(entry.value)}</span>
                <span class="bom-pkg">${escapeHtml(entry.package)}</span>
                <span class="bom-qty">${entry.qty}</span>
            `;
            bomTable.appendChild(row);
        });

        if (bomSummary) {
            bomSummary.classList.remove('hidden');
            document.getElementById('bomTotalParts').textContent = entries.length + ' unique parts';
        }
    };

    if (bomExportBtn) {
        bomExportBtn.addEventListener('click', exportBomCsv);
    }

    function exportBomCsv() {
        const rows = bomTable.querySelectorAll('.bom-row');
        if (rows.length === 0) return;
        let csv = '#,Ref,Value,Package,Qty\n';
        rows.forEach(row => {
            const cells = row.querySelectorAll('span');
            csv += Array.from(cells).map(c => c.textContent).join(',') + '\n';
        });
        downloadFile('bom.csv', csv, 'text/csv');
    }

    // ── Memory Panel ─────────────────────────────────────────────────────────
    const memoryNotes = document.getElementById('memoryNotes');

    window.updateMemoryPanel = function(memory) {
        if (!memory) return;
        const pinned = document.getElementById('memoryPinned');
        const decisions = document.getElementById('memoryDecisions');

        if (pinned && memory.pinned) {
            pinned.innerHTML = memory.pinned.length === 0
                ? '<div class="memory-empty">No pinned items yet</div>'
                : memory.pinned.map(p => `<div class="memory-entry">📌 ${escapeHtml(p)}</div>`).join('');
        }

        if (decisions && memory.decisions) {
            decisions.innerHTML = memory.decisions.length === 0
                ? '<div class="memory-empty">No decisions recorded</div>'
                : memory.decisions.map(d => `<div class="memory-entry">📋 ${escapeHtml(d)}</div>`).join('');
        }

        if (memoryNotes && memory.notes) {
            memoryNotes.value = memory.notes;
        }
    };

    if (memoryNotes && socket) {
        let saveTimeout;
        memoryNotes.addEventListener('input', () => {
            clearTimeout(saveTimeout);
            saveTimeout = setTimeout(() => {
                socket.emit('memory:save', {
                    session_id: getSessionId(),
                    notes: memoryNotes.value,
                });
            }, 1000);
        });

        socket.on('memory:state', (data) => {
            window.updateMemoryPanel(data);
        });

        socket.on('memory:saved', () => {
            // Memory saved successfully
        });
    }

    // ── Version History Panel ────────────────────────────────────────────────
    const versionList = document.getElementById('versionList');
    let versionEntries = [];

    window.addVersionEntry = function(message) {
        versionEntries.unshift({
            time: new Date(),
            message: message || 'Agent action completed',
        });
        // Keep only last 50 entries
        if (versionEntries.length > 50) versionEntries.length = 50;
        renderVersionList();
    };

    function renderVersionList() {
        if (!versionList) return;
        if (versionEntries.length === 0) {
            versionList.innerHTML = '<div class="version-empty">No versions yet. Versions are created automatically after each agent action.</div>';
            return;
        }

        versionList.innerHTML = '';
        versionEntries.forEach((entry, i) => {
            const div = document.createElement('div');
            div.className = 'version-entry';
            const timeStr = formatTimeAgo(entry.time);
            div.innerHTML = `
                <span class="version-dot"></span>
                <div class="version-info">
                    <div class="version-time">${timeStr}</div>
                    <div class="version-msg">${escapeHtml(entry.message)}</div>
                </div>
            `;
            versionList.appendChild(div);
        });
    }

    // Auto-add version on agent completion
    if (socket) {
        socket.on('agent:done', (data) => {
            window.addVersionEntry(data.message || 'Design completed');
        });
    }

    // ── Requirements Panel ───────────────────────────────────────────────────
    const requirementsList = document.getElementById('requirementsList');
    const reqEditBtn = document.getElementById('reqEditBtn');
    let designRequirements = [];

    window.updateRequirementsPanel = function(requirements) {
        designRequirements = requirements || [];
        renderRequirements();
    };

    function renderRequirements() {
        if (!requirementsList) return;
        if (designRequirements.length === 0) {
            requirementsList.innerHTML = '<div class="requirements-empty">No requirements defined. Click Edit to add design requirements.</div>';
            return;
        }

        requirementsList.innerHTML = '';
        designRequirements.forEach(req => {
            const div = document.createElement('div');
            div.className = `req-item ${req.met ? 'met' : 'unmet'}`;
            div.innerHTML = `
                <span class="req-status">${req.met ? '✓' : '✕'}</span>
                <span class="req-text">${escapeHtml(req.text)}</span>
            `;
            requirementsList.appendChild(div);
        });
    }

    if (reqEditBtn) {
        reqEditBtn.addEventListener('click', () => {
            const input = prompt('Enter requirements (one per line, format: "requirement | met/unmet"):');
            if (input) {
                designRequirements = input.split('\n').filter(Boolean).map(line => {
                    const [text, status] = line.split('|').map(s => s.trim());
                    return { text, met: status === 'met' };
                });
                renderRequirements();
            }
        });
    }

    // ── Command Palette (Global Cmd+K) ──────────────────────────────────────
    const globalPalette = document.createElement('div');
    globalPalette.id = 'globalCommandPalette';
    globalPalette.className = 'global-command-palette hidden';
    globalPalette.innerHTML = `
        <div class="global-palette-backdrop"></div>
        <div class="global-palette-content">
            <div class="global-palette-input-wrap">
                <span class="global-palette-icon">🔍</span>
                <input type="text" id="globalPaletteInput" placeholder="Type a command..." autocomplete="off">
            </div>
            <div class="global-palette-results" id="globalPaletteResults"></div>
        </div>
    `;
    document.body.appendChild(globalPalette);

    const globalPaletteInput = document.getElementById('globalPaletteInput');
    const globalPaletteResults = document.getElementById('globalPaletteResults');
    const globalPaletteBackdrop = globalPalette.querySelector('.global-palette-backdrop');

    const commands = [
        { icon: '📦', name: 'Add Component', category: 'Components', action: () => { document.getElementById('manualSearchInput')?.focus(); } },
        { icon: '🔍', name: 'Find Net', category: 'Navigation', action: () => switchLeftPanel('netlist') },
        { icon: '🧠', name: 'Ask AI', category: 'Agent', action: () => document.getElementById('agentPrompt')?.focus() },
        { icon: '📐', name: 'Run ERC', category: 'Analysis', action: () => { switchLeftPanel('erc'); document.getElementById('ercRunBtn')?.click(); } },
        { icon: '🛡', name: 'Run DRC', category: 'Analysis', action: () => { switchLeftPanel('drc'); document.getElementById('drcRunBtn')?.click(); } },
        { icon: '⚡', name: 'Simulate', category: 'Analysis', action: () => {} },
        { icon: '📋', name: 'Generate BOM', category: 'Export', action: () => window._switchRightPanel?.('bom') },
        { icon: '💾', name: 'Save All', category: 'File', action: () => {} },
        { icon: '⬇', name: 'Export Schematic', category: 'Export', action: () => document.getElementById('exportSchBtn')?.click() },
        { icon: '⬇', name: 'Export PCB', category: 'Export', action: () => document.getElementById('exportPCBBtn')?.click() },
        { icon: '🏷', name: 'Net Labels Mode', category: 'Tools', action: () => document.getElementById('schematicModeBtn')?.click() },
        { icon: '⚙', name: 'Settings', category: 'View', action: () => switchLeftPanel('settings') },
        { icon: '📊', name: 'View BOM', category: 'View', action: () => window._switchRightPanel?.('bom') },
        { icon: '🧠', name: 'View Memory', category: 'View', action: () => window._switchRightPanel?.('memory') },
        { icon: '🔌', name: 'View MCP Tools', category: 'View', action: () => window._switchRightPanel?.('mcp') },
        { icon: '⏱', name: 'View Version History', category: 'View', action: () => window._switchRightPanel?.('versions') },
        { icon: '📝', name: 'View Requirements', category: 'View', action: () => window._switchRightPanel?.('requirements') },
        { icon: '⋮', name: 'View Pipeline Activity', category: 'Agent', action: () => window.pipelinePanel?.open() },
    ];

    let selectedPaletteIndex = 0;

    function openGlobalPalette() {
        globalPalette.classList.remove('hidden');
        globalPaletteInput.value = '';
        globalPaletteInput.focus();
        selectedPaletteIndex = 0;
        renderPaletteResults(commands);
    }

    function closeGlobalPalette() {
        globalPalette.classList.add('hidden');
    }

    function renderPaletteResults(filtered) {
        globalPaletteResults.innerHTML = '';
        if (filtered.length === 0) {
            globalPaletteResults.innerHTML = '<div class="palette-empty">No matching commands</div>';
            return;
        }

        filtered.forEach((cmd, i) => {
            const div = document.createElement('div');
            div.className = `palette-item ${i === selectedPaletteIndex ? 'selected' : ''}`;
            div.innerHTML = `
                <span class="palette-item-icon">${cmd.icon}</span>
                <span class="palette-item-name">${escapeHtml(cmd.name)}</span>
                <span class="palette-item-category">${escapeHtml(cmd.category)}</span>
            `;
            div.addEventListener('click', () => {
                closeGlobalPalette();
                cmd.action();
            });
            div.addEventListener('mouseenter', () => {
                selectedPaletteIndex = i;
                updatePaletteSelection();
            });
            globalPaletteResults.appendChild(div);
        });
    }

    function updatePaletteSelection() {
        globalPaletteResults.querySelectorAll('.palette-item').forEach((item, i) => {
            item.classList.toggle('selected', i === selectedPaletteIndex);
        });
    }

    if (globalPaletteInput) {
        globalPaletteInput.addEventListener('input', () => {
            const query = globalPaletteInput.value.toLowerCase();
            const filtered = commands.filter(cmd =>
                cmd.name.toLowerCase().includes(query) ||
                cmd.category.toLowerCase().includes(query)
            );
            selectedPaletteIndex = 0;
            renderPaletteResults(filtered);
        });

        globalPaletteInput.addEventListener('keydown', (e) => {
            const items = globalPaletteResults.querySelectorAll('.palette-item');
            if (e.key === 'ArrowDown') {
                e.preventDefault();
                selectedPaletteIndex = Math.min(selectedPaletteIndex + 1, items.length - 1);
                updatePaletteSelection();
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                selectedPaletteIndex = Math.max(selectedPaletteIndex - 1, 0);
                updatePaletteSelection();
            } else if (e.key === 'Enter') {
                e.preventDefault();
                if (items[selectedPaletteIndex]) {
                    items[selectedPaletteIndex].click();
                }
            } else if (e.key === 'Escape') {
                closeGlobalPalette();
            }
        });
    }

    if (globalPaletteBackdrop) {
        globalPaletteBackdrop.addEventListener('click', closeGlobalPalette);
    }

    // Global keyboard shortcut: Cmd+K / Ctrl+K
    document.addEventListener('keydown', (e) => {
        if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
            e.preventDefault();
            if (globalPalette.classList.contains('hidden')) {
                openGlobalPalette();
            } else {
                closeGlobalPalette();
            }
        }
    });

    // ── Helper Functions ─────────────────────────────────────────────────────

    function getSessionId() {
        return localStorage.getItem('circuitbot_session_id') || 'default';
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function formatTimeAgo(date) {
        const seconds = Math.floor((new Date() - date) / 1000);
        if (seconds < 60) return 'just now';
        if (seconds < 3600) return Math.floor(seconds / 60) + 'min ago';
        if (seconds < 86400) return Math.floor(seconds / 3600) + 'h ago';
        return Math.floor(seconds / 86400) + 'd ago';
    }

    function switchLeftPanel(name) {
        if (window._activityBarSwitchPanel) window._activityBarSwitchPanel(name);
    }

    function highlightComponent(ref) {
        // Dispatch custom event for canvas to handle
        document.dispatchEvent(new CustomEvent('circuitbot:highlight', { detail: { ref } }));
    }

    function downloadFile(filename, content, mimeType) {
        const blob = new Blob([content], { type: mimeType });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        a.click();
        URL.revokeObjectURL(url);
    }

    // ── Schematic Tool Switching ─────────────────────────────────────────────
    let currentTool = 'select';
    const schTools = document.querySelectorAll('.sch-tool');

    schTools.forEach(btn => {
        btn.addEventListener('click', () => {
            setSchematicTool(btn.dataset.tool);
        });
    });

    function setSchematicTool(tool) {
        currentTool = tool;
        schTools.forEach(btn => {
            btn.classList.toggle('active', btn.dataset.tool === tool);
        });
        const viewport = document.getElementById('canvasViewport');
        if (viewport) {
            const cursors = {
                select: 'default', wire: 'crosshair', bus: 'crosshair',
                component: 'crosshair', label: 'crosshair', junction: 'crosshair',
                noconnect: 'crosshair', text: 'text', delete: 'crosshair',
            };
            viewport.style.cursor = cursors[tool] || 'default';
        }
        document.dispatchEvent(new CustomEvent('circuitbot:tool-change', { detail: { tool } }));
    }

    document.addEventListener('keydown', (e) => {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
        const keyMap = { 'v': 'select', 'w': 'wire', 'b': 'bus', 'c': 'component', 'l': 'label', 'j': 'junction', 'x': 'noconnect', 't': 'text' };
        if (keyMap[e.key.toLowerCase()]) {
            e.preventDefault();
            setSchematicTool(keyMap[e.key.toLowerCase()]);
        }
    });

    window.setSchematicTool = setSchematicTool;

    // ── Global Keyboard Shortcuts ────────────────────────────────────────────
    document.addEventListener('keydown', (e) => {
        const isCmd = e.metaKey || e.ctrlKey;

        // Cmd+Enter: Send message (when agent prompt is focused)
        if (isCmd && e.key === 'Enter') {
            const agentPrompt = document.getElementById('agentPrompt');
            if (document.activeElement === agentPrompt) {
                e.preventDefault();
                document.getElementById('agentBtn')?.click();
            }
        }

        // Cmd+Shift+F: Focus file search (components panel)
        if (isCmd && e.shiftKey && e.key === 'F') {
            e.preventDefault();
            switchLeftPanel('components');
            document.getElementById('manualSearchInput')?.focus();
        }

        // Escape: Close popups, deselect
        if (e.key === 'Escape') {
            // Close global command palette if open
            const gp = document.getElementById('globalCommandPalette');
            if (gp && !gp.classList.contains('hidden')) {
                gp.classList.add('hidden');
                return;
            }
            // Close any other open popups
            document.querySelectorAll('.hidden').forEach(el => {
                if (el.classList.contains('command-palette')) {
                    el.classList.add('hidden');
                }
            });
        }
    });
});
