(function(root, factory) {
    const api = factory(root);
    if (typeof module === 'object' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis, function(root) {
    'use strict';

    const TERMINAL = new Set(['completed', 'warning', 'failed', 'skipped', 'cancelled']);
    const LOG_LIMIT_PER_ATTEMPT = 100;
    let attachedSocket = null;
    let socketHandlers = null;
    let elapsedTimer = null;
    let compact = false;
    let state = createInitialState();

    function createInitialState() {
        return {
            runId: null,
            graphVersion: null,
            runStatus: 'idle',
            sequence: 0,
            startedAtMs: null,
            completedAtMs: null,
            durationMs: null,
            currentStage: null,
            currentAttempt: null,
            phaseCatalog: [],
            stageCatalog: [],
            stages: {},
            logs: {},
            expandedStages: new Set(),
            expandedPhases: new Set(),
        };
    }

    function cloneState(current) {
        return {
            ...current,
            stages: { ...current.stages },
            logs: { ...current.logs },
            expandedStages: new Set(current.expandedStages),
            expandedPhases: new Set(current.expandedPhases),
        };
    }

    function normalizeCatalog(value) {
        return Array.isArray(value) ? value.map(item => ({ ...item })) : [];
    }

    function reducePipelineEvent(current, event) {
        if (!event || event.schema_version !== 1 || !event.action) return current;

        if (event.action === 'run_started') {
            const next = createInitialState();
            next.runId = event.run_id;
            next.graphVersion = event.graph_version || null;
            next.runStatus = 'running';
            next.sequence = event.sequence || 0;
            next.startedAtMs = event.started_at_ms || event.ts || Date.now();
            next.phaseCatalog = normalizeCatalog(event.phase_catalog);
            next.stageCatalog = normalizeCatalog(event.stage_catalog);
            if (next.phaseCatalog[0]) next.expandedPhases.add(next.phaseCatalog[0].key);
            return next;
        }

        if (!current.runId || event.run_id !== current.runId) return current;
        if ((event.sequence || 0) <= current.sequence) return current;

        const next = cloneState(current);
        next.sequence = event.sequence;

        if (event.action === 'stage_started' || event.action === 'stage_updated' || event.action === 'stage_finished') {
            const stageKey = event.stage_key;
            if (!stageKey) return current;
            const attempts = Array.isArray(next.stages[stageKey]) ? next.stages[stageKey].slice() : [];
            const attemptIndex = attempts.findIndex(item => item.attempt === event.attempt);
            const attempt = {
                attempt: event.attempt,
                status: event.status,
                started_at_ms: event.started_at_ms,
                completed_at_ms: event.completed_at_ms || null,
                duration_ms: event.duration_ms ?? null,
                summary: event.summary || '',
                metrics: event.metrics || {},
            };
            if (attemptIndex >= 0) attempts[attemptIndex] = { ...attempts[attemptIndex], ...attempt };
            else attempts.push(attempt);
            next.stages[stageKey] = attempts;

            if (!next.stageCatalog.some(item => item.key === stageKey)) {
                next.stageCatalog.push({
                    key: stageKey,
                    label: event.stage_label || stageKey,
                    phase: event.phase || 'understand',
                    order: event.order || 999,
                    optional: false,
                });
            }

            if (event.action === 'stage_finished' || TERMINAL.has(event.status)) {
                if (next.currentStage === stageKey && next.currentAttempt === event.attempt) {
                    next.currentStage = null;
                    next.currentAttempt = null;
                }
            } else {
                next.currentStage = stageKey;
                next.currentAttempt = event.attempt;
                if (event.phase) next.expandedPhases.add(event.phase);
            }
            return next;
        }

        if (event.action === 'run_finished') {
            next.runStatus = event.status || 'completed';
            next.completedAtMs = event.completed_at_ms || event.ts || Date.now();
            next.durationMs = event.duration_ms ?? Math.max(0, next.completedAtMs - next.startedAtMs);
            next.currentStage = null;
            next.currentAttempt = null;
        }
        return next;
    }

    function hydratePipeline(snapshot) {
        if (!snapshot || snapshot.schema_version !== 1 || !snapshot.run_id) {
            state = createInitialState();
            render();
            return;
        }

        const next = createInitialState();
        next.runId = snapshot.run_id;
        next.graphVersion = snapshot.graph_version || null;
        next.runStatus = snapshot.status || 'idle';
        next.sequence = snapshot.sequence || 0;
        next.startedAtMs = snapshot.started_at_ms || null;
        next.completedAtMs = snapshot.completed_at_ms || null;
        next.durationMs = snapshot.duration_ms ?? null;
        next.currentStage = snapshot.current_stage || null;
        next.currentAttempt = snapshot.current_attempt || null;
        next.phaseCatalog = normalizeCatalog(snapshot.phase_catalog);
        next.stageCatalog = normalizeCatalog(snapshot.stage_catalog);
        next.stages = {};
        Object.entries(snapshot.stages || {}).forEach(([key, attempts]) => {
            next.stages[key] = Array.isArray(attempts) ? attempts.map(item => ({ ...item })) : [];
        });

        const activeDefinition = next.stageCatalog.find(item => item.key === next.currentStage);
        if (activeDefinition) next.expandedPhases.add(activeDefinition.phase);
        else if (next.phaseCatalog[0]) next.expandedPhases.add(next.phaseCatalog[0].key);
        state = next;
        render();
    }

    function appendCorrelatedLog(data) {
        if (!data || !data.stage_key || !data.attempt) return;
        if (data.run_id && state.runId && data.run_id !== state.runId) return;
        const key = `${data.stage_key}:${data.attempt}`;
        const entries = Array.isArray(state.logs[key]) ? state.logs[key].slice() : [];
        entries.push({
            message: String(data.message || data.content || ''),
            ts: data.ts || Date.now(),
        });
        state.logs = { ...state.logs, [key]: entries.slice(-LOG_LIMIT_PER_ATTEMPT) };
        if (state.expandedStages.has(key)) render();
    }

    function detachSocket() {
        if (!attachedSocket || !socketHandlers) return;
        Object.entries(socketHandlers).forEach(([event, handler]) => attachedSocket.off(event, handler));
        attachedSocket = null;
        socketHandlers = null;
    }

    function attachSocket(socket) {
        if (!socket || socket === attachedSocket) return;
        detachSocket();
        socketHandlers = {
            'agent:pipeline': event => {
                const previousStage = state.currentStage;
                state = reducePipelineEvent(state, event);
                render();
                if (state.currentStage && state.currentStage !== previousStage) scrollActiveStageIntoView();
            },
            'agent:log': appendCorrelatedLog,
            'agent:thought_stream': appendCorrelatedLog,
            'chat:state': data => hydratePipeline(data && data.pipeline),
        };
        Object.entries(socketHandlers).forEach(([event, handler]) => socket.on(event, handler));
        attachedSocket = socket;
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function formatDuration(durationMs) {
        if (durationMs == null || Number.isNaN(Number(durationMs))) return '';
        const seconds = Math.max(0, Number(durationMs)) / 1000;
        if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
        if (seconds < 60) return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)}s`;
        const minutes = Math.floor(seconds / 60);
        const remainder = Math.floor(seconds % 60);
        return `${minutes}m ${String(remainder).padStart(2, '0')}s`;
    }

    function currentDuration(attempt) {
        if (!attempt) return null;
        if (attempt.duration_ms != null) return attempt.duration_ms;
        if (attempt.started_at_ms) return Date.now() - attempt.started_at_ms;
        return null;
    }

    function latestAttempt(stageKey) {
        const attempts = state.stages[stageKey] || [];
        return attempts.length ? attempts[attempts.length - 1] : null;
    }

    function stageStatus(stageKey) {
        const attempt = latestAttempt(stageKey);
        return attempt ? attempt.status : 'pending';
    }

    function phaseStatus(phaseKey) {
        const definitions = state.stageCatalog.filter(item => item.phase === phaseKey);
        const statuses = definitions.map(item => stageStatus(item.key));
        if (statuses.includes('failed')) return 'failed';
        if (statuses.includes('waiting')) return 'waiting';
        if (statuses.includes('running')) return 'running';
        if (statuses.some(status => status === 'completed' || status === 'warning' || status === 'skipped')) {
            const unfinishedRequired = definitions.some(item => !item.optional && stageStatus(item.key) === 'pending');
            return unfinishedRequired && state.runStatus === 'running' ? 'running' : 'completed';
        }
        return 'pending';
    }

    function statusMark(status) {
        const marks = {
            pending: '',
            running: '',
            waiting: 'II',
            completed: '✓',
            warning: '!',
            failed: '×',
            skipped: '–',
            cancelled: '×',
        };
        return marks[status] || '';
    }

    function renderProgressRail(container) {
        const phases = state.phaseCatalog.slice().sort((a, b) => a.order - b.order);
        if (!phases.length) {
            container.innerHTML = '';
            return;
        }
        container.innerHTML = phases.map(phase => {
            const status = phaseStatus(phase.key);
            return `<span class="pipeline-progress-segment pipeline-progress-segment--${status}" title="${escapeHtml(phase.label)}: ${status}"></span>`;
        }).join('');
    }

    function renderMetrics(metrics) {
        const entries = Object.entries(metrics || {});
        if (!entries.length) return '';
        return `<dl class="pipeline-stage-metrics">${entries.map(([key, value]) =>
            `<div><dt>${escapeHtml(key.replace(/_/g, ' '))}</dt><dd>${escapeHtml(value)}</dd></div>`
        ).join('')}</dl>`;
    }

    function renderLogs(stageKey, attemptNumber) {
        const entries = state.logs[`${stageKey}:${attemptNumber}`] || [];
        if (!entries.length) return '';
        return `<div class="pipeline-stage-events" role="log">${entries.map(entry =>
            `<div class="pipeline-stage-event">${escapeHtml(entry.message)}</div>`
        ).join('')}</div>`;
    }

    function renderStage(definition) {
        const attempts = state.stages[definition.key] || [];
        const attempt = attempts.length ? attempts[attempts.length - 1] : null;
        const status = attempt ? attempt.status : 'pending';
        const attemptNumber = attempt ? attempt.attempt : 1;
        const expansionKey = `${definition.key}:${attemptNumber}`;
        const expanded = state.expandedStages.has(expansionKey);
        const hasDetails = Boolean(attempt && (attempt.summary || Object.keys(attempt.metrics || {}).length || state.logs[expansionKey]));
        const retry = attempts.length > 1 ? `<span class="pipeline-retry-badge">Attempt ${attempts.length}</span>` : '';
        const duration = attempt ? formatDuration(currentDuration(attempt)) : '';
        const detail = expanded && attempt ? `
            <div class="pipeline-stage-detail" id="pipeline-detail-${escapeHtml(definition.key)}-${attemptNumber}">
                ${attempt.summary ? `<p>${escapeHtml(attempt.summary)}</p>` : ''}
                ${renderMetrics(attempt.metrics)}
                ${renderLogs(definition.key, attemptNumber)}
            </div>` : '';

        return `
            <div class="pipeline-stage pipeline-stage--${status}${expanded ? ' pipeline-stage--expanded' : ''}" data-stage-key="${escapeHtml(definition.key)}">
                <button class="pipeline-stage-row" type="button"
                    data-pipeline-stage="${escapeHtml(expansionKey)}"
                    aria-expanded="${expanded}"
                    aria-controls="pipeline-detail-${escapeHtml(definition.key)}-${attemptNumber}"
                    ${hasDetails ? '' : 'disabled'}>
                    <span class="pipeline-stage-icon" aria-hidden="true">${statusMark(status)}</span>
                    <span class="pipeline-stage-name">${escapeHtml(definition.label)}</span>
                    ${retry}
                    <span class="pipeline-stage-duration">${escapeHtml(duration)}</span>
                    <span class="pipeline-stage-chevron" aria-hidden="true">${hasDetails ? (expanded ? '⌄' : '›') : ''}</span>
                    <span class="sr-only">${escapeHtml(status)}</span>
                </button>
                ${detail}
            </div>`;
    }

    function renderPhase(phase) {
        const definitions = state.stageCatalog
            .filter(item => item.phase === phase.key)
            .sort((a, b) => a.order - b.order);
        const expanded = state.expandedPhases.has(phase.key);
        const completeCount = definitions.filter(item => TERMINAL.has(stageStatus(item.key))).length;
        const status = phaseStatus(phase.key);
        return `
            <section class="pipeline-phase pipeline-phase--${status}">
                <button class="pipeline-phase-header" type="button" data-pipeline-phase="${escapeHtml(phase.key)}"
                    aria-expanded="${expanded}" aria-controls="pipeline-phase-${escapeHtml(phase.key)}">
                    <span class="pipeline-phase-status" aria-hidden="true"></span>
                    <span class="pipeline-phase-name">${escapeHtml(phase.label)}</span>
                    <span class="pipeline-phase-count">${completeCount}/${definitions.length}</span>
                    <span class="pipeline-phase-chevron" aria-hidden="true">${expanded ? '⌄' : '›'}</span>
                </button>
                <div class="pipeline-phase-stages${expanded ? '' : ' hidden'}" id="pipeline-phase-${escapeHtml(phase.key)}">
                    ${definitions.map(renderStage).join('')}
                </div>
            </section>`;
    }

    function render() {
        if (typeof document === 'undefined') return;
        const phasesEl = document.getElementById('pipelinePhases');
        const statusEl = document.getElementById('pipelineRunStatus');
        const phaseLabelEl = document.getElementById('pipelinePhaseLabel');
        const elapsedEl = document.getElementById('pipelineElapsed');
        const railEl = document.getElementById('pipelineProgressRail');
        const dotEl = document.getElementById('pipelineStatusDot');
        const statusTextEl = document.getElementById('pipelineStatusText');
        const panelEl = document.getElementById('pipelinePanel');
        if (!phasesEl || !statusEl || !railEl) return;

        panelEl && panelEl.classList.toggle('pipeline-compact', compact);
        statusEl.textContent = state.runStatus === 'idle' ? 'Idle' : state.runStatus;
        statusEl.className = `pipeline-run-status pipeline-run-status--${state.runStatus}`;

        const activeDefinition = state.stageCatalog.find(item => item.key === state.currentStage);
        const activePhase = state.phaseCatalog.find(item => activeDefinition && item.key === activeDefinition.phase);
        phaseLabelEl.textContent = activeDefinition
            ? `${activePhase ? activePhase.label : 'Pipeline'} / ${activeDefinition.label}`
            : state.runStatus === 'idle'
                ? 'No active run'
                : state.runStatus === 'completed'
                    ? 'All requested phases complete'
                    : state.runStatus === 'failed'
                        ? 'Pipeline stopped'
                        : 'Preparing next stage';

        const elapsed = state.startedAtMs
            ? (state.durationMs != null ? state.durationMs : Date.now() - state.startedAtMs)
            : null;
        elapsedEl.textContent = elapsed == null ? '' : formatDuration(elapsed);

        renderProgressRail(railEl);
        if (!state.runId || !state.stageCatalog.length) {
            phasesEl.innerHTML = `
                <div class="pipeline-empty">
                    <span class="pipeline-empty-mark" aria-hidden="true"></span>
                    <span>No pipeline run for this session.</span>
                </div>`;
        } else {
            phasesEl.innerHTML = state.phaseCatalog
                .slice()
                .sort((a, b) => a.order - b.order)
                .map(renderPhase)
                .join('');
        }

        const dotStatus = state.currentStage && stageStatus(state.currentStage) === 'waiting'
            ? 'waiting'
            : state.runStatus;
        if (dotEl) dotEl.className = `pipeline-status-dot pipeline-status-dot--${dotStatus}`;
        if (statusTextEl) statusTextEl.textContent = `Pipeline ${dotStatus}`;
        bindRenderedControls();
        updateTimer();
    }

    function bindRenderedControls() {
        document.querySelectorAll('[data-pipeline-phase]').forEach(button => {
            button.addEventListener('click', () => {
                const phase = button.dataset.pipelinePhase;
                if (state.expandedPhases.has(phase)) state.expandedPhases.delete(phase);
                else state.expandedPhases.add(phase);
                render();
            });
        });
        document.querySelectorAll('[data-pipeline-stage]').forEach(button => {
            button.addEventListener('click', () => {
                const key = button.dataset.pipelineStage;
                if (state.expandedStages.has(key)) state.expandedStages.delete(key);
                else state.expandedStages.add(key);
                render();
            });
        });
    }

    function updateTimer() {
        const shouldRun = state.runStatus === 'running';
        if (shouldRun && !elapsedTimer) elapsedTimer = root.setInterval(render, 1000);
        if (!shouldRun && elapsedTimer) {
            root.clearInterval(elapsedTimer);
            elapsedTimer = null;
        }
    }

    function scrollActiveStageIntoView() {
        if (typeof document === 'undefined' || !state.currentStage) return;
        root.setTimeout(() => {
            const container = document.getElementById('pipelinePhases');
            const active = container && container.querySelector(`[data-stage-key="${state.currentStage}"]`);
            if (!container || !active) return;
            const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 96;
            if (nearBottom || container.scrollTop === 0) active.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        }, 0);
    }

    function openPanel() {
        if (typeof document === 'undefined') return;
        const rightPanel = document.getElementById('rightPanel');
        if (rightPanel && rightPanel.classList.contains('collapsed')) {
            document.getElementById('rightToggle')?.click();
        }
        if (root._switchRightPanel) root._switchRightPanel('pipeline');
    }

    function init() {
        if (typeof document === 'undefined') return;
        document.addEventListener('circuitbot:socket-ready', event => attachSocket(event.detail && event.detail.socket));
        if (root.socket) attachSocket(root.socket);
        document.getElementById('pipelineCompactToggle')?.addEventListener('click', event => {
            compact = !compact;
            event.currentTarget.setAttribute('aria-pressed', String(compact));
            render();
        });
        render();
    }

    if (typeof document !== 'undefined') {
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
        else init();
    }

    const api = {
        attachSocket,
        createInitialState,
        getState: () => state,
        hydratePipeline,
        open: openPanel,
        reducePipelineEvent,
    };
    root.pipelinePanel = api;
    return api;
});
