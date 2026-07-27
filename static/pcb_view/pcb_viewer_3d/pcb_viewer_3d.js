/**
 * pcb_viewer_3d.js — Main orchestrator for the 3D PCB viewer.
 *
 * Usage:
 *   const viewer = new PcbViewer3D(document.getElementById('view3DContainer'));
 *   viewer.loadBoard(boardModel);
 *   // In animation loop: viewer.update()
 */
class PcbViewer3D {
    constructor(container) {
        this.container = container;
        this.sceneSetup = null;
        this.camera = null;
        this.cameraCtrl = null;
        this.boardMeshBuilder = null;
        this.componentPlacer = null;
        this.layerPanel = null;
        this.modelLoader = null;
        this.cache = null;

        this._animFrame = null;
        this._running = false;
        this._boardModel = null;

        this._init();
    }

    _init() {
        // Scene + renderer
        this.sceneSetup = new SceneSetup(this.container);

        // Camera
        this.camera = new THREE.PerspectiveCamera(
            45,
            this.container.clientWidth / this.container.clientHeight,
            0.1,
            2000
        );

        // Camera controls
        this.cameraCtrl = new CameraController(this.camera, this.sceneSetup.renderer.domElement);

        // Board mesh builder
        this.boardMeshBuilder = new BoardMeshBuilder();
        this.sceneSetup.scene.add(this.boardMeshBuilder.group);

        // Component placer
        this.componentPlacer = new ComponentPlacer(this.boardMeshBuilder.group);

        // Model loader + cache
        this.cache = new ModelCache();
        this.modelLoader = new ComponentModelLoader(this.cache);

        // Layer panel
        this.layerPanel = new LayerPanel3D(this.boardMeshBuilder.group);

        // Handle resize
        this._resizeObserver = new ResizeObserver(() => this._onResize());
        this._resizeObserver.observe(this.container);

        // Start render loop
        this._start();
    }

    _start() {
        if (this._running) return;
        this._running = true;
        this._animate();
    }

    _animate() {
        if (!this._running) return;
        this._animFrame = requestAnimationFrame(() => this._animate());
        this.cameraCtrl.update();
        this.sceneSetup.render(this.camera);
    }

    _onResize() {
        if (!this.camera || !this.sceneSetup) return;
        const w = this.container.clientWidth;
        const h = this.container.clientHeight;
        this.camera.aspect = w / h;
        this.camera.updateProjectionMatrix();
        this.sceneSetup.resize();
    }

    /**
     * Load a board model and render it.
     */
    async loadBoard(boardModel) {
        this._boardModel = boardModel;
        if (!boardModel) return;

        // Clear existing components
        this.componentPlacer.clear();

        // Build board meshes
        this.boardMeshBuilder.build(boardModel);
        const centerOffset = this.boardMeshBuilder.centerOffset;

        // Fit camera to board
        const bbox = this.boardMeshBuilder._computeBBox(boardModel.outline_segments || [], boardModel);
        if (bbox) {
            const w = bbox.maxX - bbox.minX;
            const h = bbox.maxY - bbox.minY;
            this.cameraCtrl.fitToBoard(w, h);
        }

        // Initialize WASM for STEP parsing (non-blocking)
        this.modelLoader.initialize().catch(() => {});

        // Place components
        await this._placeComponents(boardModel.components || [], centerOffset);
    }

    async _placeComponents(components, centerOffset) {
        for (const comp of components) {
            if (comp.model_3d_path) {
                // Try to load real 3D model
                const model = await this.modelLoader.load(
                    comp.model_3d_path,
                    comp.model_3d_offset,
                    comp.model_3d_scale,
                    comp.model_3d_rotate,
                );
                if (model) {
                    this.componentPlacer.place(comp, model, centerOffset);
                    continue;
                }
            }
            // Fallback to placeholder
            this.componentPlacer.placePlaceholder(comp, centerOffset);
        }
    }

    // ── Public API ──────────────────────────────────────────────────────

    viewTop()    { this.cameraCtrl.viewTop(); }
    viewBottom() { this.cameraCtrl.viewBottom(); }
    viewFront()  { this.cameraCtrl.viewFront(); }
    viewBack()   { this.cameraCtrl.viewBack(); }
    viewLeft()   { this.cameraCtrl.viewLeft(); }
    viewRight()  { this.cameraCtrl.viewRight(); }
    fitToBoard() {
        if (this._boardModel) {
            const bbox = this.boardMeshBuilder._computeBBox(this._boardModel.outline_segments || []);
            if (bbox) this.cameraCtrl.fitToBoard(bbox.maxX - bbox.minX, bbox.maxY - bbox.minY);
        }
    }

    toggleWireframe() {
        const wireframe = !this._wireframe;
        this._wireframe = wireframe;
        this.sceneSetup.scene.traverse(child => {
            if (child.isMesh && child.material) {
                child.material.wireframe = wireframe;
            }
        });
    }

    toggleExplode() {
        if (this._exploded) {
            this.componentPlacer.collapse();
            this._exploded = false;
        } else {
            this.componentPlacer.explode(3.0);
            this._exploded = true;
        }
    }

    setBoardOpacity(value) {
        this.layerPanel.setLayerOpacity('Substrate', value);
    }

    screenshot() {
        this.sceneSetup.renderer.render(this.sceneSetup.scene, this.camera);
        return this.sceneSetup.renderer.domElement.toDataURL('image/png');
    }

    highlightComponent(ref) {
        this.componentPlacer.highlight(ref);
    }

    unhighlightComponent(ref) {
        this.componentPlacer.unhighlight(ref);
    }

    dispose() {
        this._running = false;
        if (this._animFrame) cancelAnimationFrame(this._animFrame);
        if (this._resizeObserver) this._resizeObserver.disconnect();
        this.componentPlacer.dispose();
        this.boardMeshBuilder.dispose();
        this.cache.clear();
        this.sceneSetup.dispose();
    }
}

// Expose globally
window.PcbViewer3D = PcbViewer3D;

window.init3DViewer = function(boardModel) {
    const container = document.getElementById('view3DContainer');
    if (!container) return null;

    if (!window.pcbViewer3DInstance) {
        window.pcbViewer3DInstance = new PcbViewer3D(container);
    }
    if (boardModel) {
        window.pcbViewer3DInstance.loadBoard(boardModel);
    }
    return window.pcbViewer3DInstance;
};
