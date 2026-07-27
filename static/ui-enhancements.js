// CircuitBot UI Enhancements
// Activity bar panel switching, right panel switching, route input toggle, status bar updates, co-pilot status banner

document.addEventListener('DOMContentLoaded', () => {
    // ── Left Activity Bar Panel Switching ────────────────────────────────────
    function switchPanel(panelName) {
        document.querySelectorAll('.activity-bar-btn[data-panel]').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.panel === panelName);
        });
        document.querySelectorAll('.panel-view[data-view]').forEach(view => {
            const isActive = view.dataset.view === panelName;
            view.classList.toggle('active', isActive);
            view.classList.toggle('hidden', !isActive);
        });
    }

    document.querySelectorAll('.activity-bar-btn[data-panel]').forEach(btn => {
        btn.addEventListener('click', () => switchPanel(btn.dataset.panel));
    });

    window._activityBarSwitchPanel = switchPanel;

    // ── Right Activity Bar Panel Switching ───────────────────────────────────
    function switchRightPanel(panelName) {
        document.querySelectorAll('.activity-bar-btn[data-rpanel]').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.rpanel === panelName);
        });
        document.querySelectorAll('.panel-view[data-rview]').forEach(view => {
            const isActive = view.dataset.rview === panelName;
            view.classList.toggle('active', isActive);
            view.classList.toggle('hidden', !isActive);
        });
    }

    document.querySelectorAll('.activity-bar-btn[data-rpanel]').forEach(btn => {
        btn.addEventListener('click', () => switchRightPanel(btn.dataset.rpanel));
    });

    window._switchRightPanel = switchRightPanel;

    // ── Sidebar collapse/expand is handled by app.js setupCollapse() ────────
    // No duplicate handlers here — app.js manages leftToggle, leftPanel,
    // leftExpand, leftResize, rightToggle, rightPanel, rightExpand, rightResize

    // ── Chat Panel Collapse/Expand ──────────────────────────────────────────
    const chatColumn = document.getElementById('chatColumn');
    const chatExpand = document.getElementById('chatExpand');
    const canvasDivider = document.getElementById('canvasDivider');
    let savedChatWidth = null;

    function collapseChat() {
        if (!chatColumn) return;
        savedChatWidth = chatColumn.offsetWidth;
        chatColumn.classList.add('collapsed');
        chatColumn.style.flexBasis = '0px';
        chatColumn.style.minWidth = '0px';
        chatColumn.style.overflow = 'hidden';
        if (chatExpand) chatExpand.style.display = 'flex';
        if (canvasDivider) canvasDivider.style.display = 'none';
    }

    function expandChat() {
        if (!chatColumn) return;
        chatColumn.classList.remove('collapsed');
        chatColumn.style.flexBasis = (savedChatWidth || 40) + '%';
        chatColumn.style.minWidth = '320px';
        chatColumn.style.overflow = '';
        if (chatExpand) chatExpand.style.display = 'none';
        if (canvasDivider) canvasDivider.style.display = '';
    }

    // Expand strip opens the chat
    if (chatExpand) {
        chatExpand.addEventListener('click', expandChat);
    }

    // Double-click the canvas divider toggles chat
    if (canvasDivider) {
        canvasDivider.addEventListener('dblclick', () => {
            if (chatColumn && chatColumn.classList.contains('collapsed')) {
                expandChat();
            } else {
                collapseChat();
            }
        });
    }

    // ── Canvas Divider Resize ────────────────────────────────────────────────
    if (canvasDivider && chatColumn) {
        let isDragging = false;
        let startX, startWidth;

        canvasDivider.addEventListener('mousedown', (e) => {
            isDragging = true;
            startX = e.clientX;
            startWidth = chatColumn.offsetWidth;
            document.body.style.cursor = 'col-resize';
            document.body.style.userSelect = 'none';
        });

        document.addEventListener('mousemove', (e) => {
            if (!isDragging) return;
            const diff = e.clientX - startX;
            const newWidth = Math.max(280, Math.min(600, startWidth + diff));
            chatColumn.style.flexBasis = newWidth + 'px';
            chatColumn.style.flexGrow = '0';
            chatColumn.style.flexShrink = '0';
        });

        document.addEventListener('mouseup', () => {
            if (isDragging) {
                isDragging = false;
                document.body.style.cursor = '';
                document.body.style.userSelect = '';
            }
        });
    }

    // ── Route Input Toggle ───────────────────────────────────────────────────
    const routeInput = document.getElementById('floatingRouteInput');
    const routeToggle = document.getElementById('routeInputToggle');
    if (routeInput && routeToggle) {
        routeToggle.addEventListener('click', () => {
            const isCollapsed = routeInput.style.opacity === '0.15';
            routeInput.style.opacity = isCollapsed ? '' : '0.15';
            routeToggle.innerHTML = isCollapsed ? '&#x2212;' : '&#x002B;';
        });
    }

    // ── Status Bar Updates ───────────────────────────────────────────────────
    const zoomStatus = document.getElementById('zoomStatus');
    const componentCountStatus = document.getElementById('componentCountStatus');
    const viewModeStatus = document.getElementById('viewModeStatus');
    const zoomLevel = document.getElementById('zoomLevel');
    const compCount = document.getElementById('compCount');
    const statusDrc = document.getElementById('statusDrc');
    const statusErc = document.getElementById('statusErc');
    const statusNets = document.getElementById('statusNets');

    // Sync zoom display to status bar
    if (zoomLevel && zoomStatus) {
        const observer = new MutationObserver(() => {
            zoomStatus.textContent = 'Zoom: ' + zoomLevel.textContent;
        });
        observer.observe(zoomLevel, { childList: true, characterData: true, subtree: true });
    }

    // Sync component count to status bar
    if (compCount && componentCountStatus) {
        const observer = new MutationObserver(() => {
            componentCountStatus.textContent = 'Components: ' + compCount.textContent;
        });
        observer.observe(compCount, { childList: true, characterData: true, subtree: true });
    }

    // Update view mode in status bar
    function updateViewModeStatus() {
        if (!viewModeStatus) return;
        const schematicBtn = document.getElementById('viewSchematicBtn');
        const pcbBtn = document.getElementById('viewPCBBtn');
        const symbolBtn = document.getElementById('viewSymbolBtn');
        const btn3d = document.getElementById('view3DBtn');
        if (schematicBtn && schematicBtn.classList.contains('active')) {
            viewModeStatus.textContent = 'Schematic';
        } else if (pcbBtn && pcbBtn.classList.contains('active')) {
            viewModeStatus.textContent = 'PCB';
        } else if (btn3d && btn3d.classList.contains('active')) {
            viewModeStatus.textContent = '3D';
        } else if (symbolBtn && symbolBtn.classList.contains('active')) {
            viewModeStatus.textContent = 'Symbol';
        }
    }

    // Listen for tab clicks
    document.querySelectorAll('.tab').forEach(tab => {
        tab.addEventListener('click', () => {
            setTimeout(updateViewModeStatus, 50);
        });
    });

    // Update DRC/ERC status indicators
    window.updateDrcStatus = function(count) {
        if (statusDrc) {
            statusDrc.textContent = 'DRC: ' + count;
            statusDrc.classList.toggle('has-errors', count > 0);
        }
    };

    window.updateErcStatus = function(count) {
        if (statusErc) {
            statusErc.textContent = 'ERC: ' + count;
            statusErc.classList.toggle('has-warnings', count > 0);
        }
    };

    window.updateNetCount = function(count) {
        if (statusNets) {
            statusNets.textContent = 'Nets: ' + count;
        }
    };

    // ── Co-Pilot Status Banner ───────────────────────────────────────────────
    window.showAgentStatusBanner = function(text, type) {
        const banner = document.getElementById('agentStatusBanner');
        const bannerText = document.getElementById('agentStatusText');
        if (!banner || !bannerText) return;
        bannerText.textContent = text;
        banner.className = 'agent-status-banner ' + (type || 'success');
        banner.style.display = 'flex';
    };

    window.hideAgentStatusBanner = function() {
        const banner = document.getElementById('agentStatusBanner');
        if (banner) banner.style.display = 'none';
    };
});
