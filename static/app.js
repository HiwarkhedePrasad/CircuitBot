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

    let selectedComponent = null; // { id_str, textDesc, ops, category }
    let currentPreviewOps = null;

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

            // Show in single-component preview mode
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

    // --- Add to Schematic ---
    addBtn.addEventListener('click', () => {
        if (!selectedComponent) return;
        const { id_str, textDesc, ops, category } = selectedComponent;

        if (!currentSchematic) currentSchematic = new Schematic();

        const comp = currentSchematic.addComponent(id_str, id_str.split(':').pop(), ops, category, textDesc);

        componentInfo.textContent = `Added: ${comp.refDesignator} (${comp.name})\nColumn: ${COLUMN_DEFS[comp.column].label}\nPosition: (${comp.x.toFixed(2)}, ${comp.y.toFixed(2)})\n\nTotal components: ${currentSchematic.components.length}`;

        updateComponentListUI();
        updateSchematicButtons();

        // Auto-switch to schematic view if 2+ components
        if (currentSchematic.components.length >= 2) {
            enterSchematicMode();
        }
    });

    // --- Auto Layout ---
    autoLayoutBtn.addEventListener('click', () => {
        if (!currentSchematic || currentSchematic.components.length === 0) return;
        currentSchematic.autoLayout();
        if (currentSchematic.mode === 'schematic') enterSchematicMode();
        componentInfo.textContent = `Auto-layout applied to ${currentSchematic.components.length} components.\nColumns: ${COLUMN_DEFS.map((c, i) => `${c.label}: ${currentSchematic.components.filter(comp => comp.column === i).length}`).join(', ')}`;
    });

    // --- View Schematic ---
    viewSchematicBtn.addEventListener('click', () => {
        if (!currentSchematic || currentSchematic.components.length === 0) return;
        enterSchematicMode();
        modeIndicator.classList.remove('hidden');
        componentInfo.textContent = `Schematic View: ${currentSchematic.components.length} components\nScroll to zoom, drag to pan.\n\nColumns:\n${COLUMN_DEFS.map((c, i) => `  ${c.label}: ${currentSchematic.components.filter(comp => comp.column === i).length} components`).join('\n')}`;
    });

    // --- Clear All ---
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

    // --- Zoom buttons ---
    document.getElementById('zoomInBtn').addEventListener('click', zoomIn);
    document.getElementById('zoomOutBtn').addEventListener('click', zoomOut);
    document.getElementById('zoomResetBtn').addEventListener('click', resetZoom);

    // --- Resize handler ---
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
