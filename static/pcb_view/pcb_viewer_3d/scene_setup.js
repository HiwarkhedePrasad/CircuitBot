/**
 * scene_setup.js — Creates and configures the Three.js scene, renderer, and lighting.
 */
class SceneSetup {
    constructor(container) {
        this.container = container;
        this.scene = null;
        this.renderer = null;
        this._setupScene();
        this._setupRenderer();
        this._setupLights();
    }

    _setupScene() {
        this.scene = new THREE.Scene();
        this.scene.background = new THREE.Color(0x1a1a2e);
        this.scene.fog = new THREE.FogExp2(0x1a1a2e, 0.003);
    }

    _setupRenderer() {
        this.renderer = new THREE.WebGLRenderer({
            antialias: true,
            alpha: false,
            powerPreference: 'high-performance',
        });
        this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        this.renderer.setSize(this.container.clientWidth, this.container.clientHeight);
        this.renderer.shadowMap.enabled = false;
        this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
        this.renderer.toneMappingExposure = 1.2;
        const canvas = this.renderer.domElement;
        if (canvas && typeof canvas.addEventListener === 'function') {
            canvas.addEventListener('webglcontextlost', (e) => {
                e.preventDefault();
                console.warn('[3D] WebGL context lost');
            }, false);
            canvas.addEventListener('webglcontextrestored', () => {
                console.log('[3D] WebGL context restored');
                this.renderer.setSize(this.container.clientWidth, this.container.clientHeight);
            }, false);
        }
        this.container.appendChild(canvas);
    }

    _setupLights() {
        // Ambient — soft fill
        const ambient = new THREE.AmbientLight(0x404060, 0.6);
        this.scene.add(ambient);

        // Hemisphere — sky/ground color bleed
        const hemi = new THREE.HemisphereLight(0x88aacc, 0x443322, 0.5);
        this.scene.add(hemi);

        // Main directional light (from top-right-front)
        const dir = new THREE.DirectionalLight(0xffffff, 1.2);
        dir.position.set(50, 80, 60);
        dir.castShadow = false;
        this.scene.add(dir);

        // Fill light (from opposite side, dimmer)
        const fill = new THREE.DirectionalLight(0xaabbdd, 0.4);
        fill.position.set(-40, 30, -30);
        this.scene.add(fill);
    }

    resize() {
        if (!this.container) return;
        const w = this.container.clientWidth;
        const h = this.container.clientHeight;
        this.renderer.setSize(w, h);
    }

    render(camera) {
        this.renderer.render(this.scene, camera);
    }

    dispose() {
        this.renderer.dispose();
        if (this.renderer && this.renderer.domElement && this.renderer.domElement.parentNode && typeof this.renderer.domElement.parentNode.removeChild === 'function') {
            this.renderer.domElement.parentNode.removeChild(this.renderer.domElement);
        }
    }
}
