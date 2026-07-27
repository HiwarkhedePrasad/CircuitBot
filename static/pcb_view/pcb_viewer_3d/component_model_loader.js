/**
 * component_model_loader.js — Fetches and parses STEP/VRML 3D models.
 */
class ComponentModelLoader {
    constructor(cache) {
        this._cache = cache || new ModelCache();
        this._occt = null;
        this._occtLoading = false;
        this._occtReady = false;
        this._pendingLoads = new Map();
    }

    async initialize() {
        if (this._occtReady || this._occtLoading) return;
        this._occtLoading = true;
        try {
            // Lazy-load occt-import-js WASM
            const script = document.createElement('script');
            script.src = 'https://cdn.jsdelivr.net/npm/occt-import-js@0.0.23/dist/occt-import-js.js';
            document.head.appendChild(script);
            await new Promise((resolve, reject) => {
                script.onload = resolve;
                script.onerror = reject;
            });
            this._occt = await occtimportjs();
            this._occtReady = true;
        } catch (e) {
            console.warn('[3D] occt-import-js failed to load, STEP parsing disabled:', e.message);
            this._occtReady = false;
        }
        this._occtLoading = false;
    }

    /**
     * Load a 3D model by path. Returns THREE.Group or null.
     */
    async load(modelPath, modelOffset, modelScale, modelRotate) {
        if (!modelPath) return null;

        // Check cache
        const cached = this._cache.get(modelPath);
        if (cached) {
            return this._instantiateFromCache(cached, modelOffset, modelScale, modelRotate);
        }

        // Deduplicate concurrent requests for the same model
        if (this._pendingLoads.has(modelPath)) {
            const group = await this._pendingLoads.get(modelPath);
            return this._instantiateFromCache(group, modelOffset, modelScale, modelRotate);
        }

        const promise = this._fetchAndParse(modelPath);
        this._pendingLoads.set(modelPath, promise);

        try {
            const geometries = await promise;
            if (geometries && geometries.length > 0) {
                this._cache.set(modelPath, geometries);
                return this._instantiateFromCache(geometries, modelOffset, modelScale, modelRotate);
            }
        } catch (e) {
            console.warn(`[3D] Failed to load model ${modelPath}:`, e.message);
        } finally {
            this._pendingLoads.delete(modelPath);
        }
        return null;
    }

    async _fetchAndParse(modelPath) {
        const url = `/api/3d_models/${modelPath}`;
        const resp = await fetch(url);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

        const isStep = modelPath.endsWith('.step') || modelPath.endsWith('.stp');
        const isVrml = modelPath.endsWith('.wrl');

        if (isStep) {
            const buffer = await resp.arrayBuffer();
            return this._parseStep(new Uint8Array(buffer));
        } else if (isVrml) {
            const text = await resp.text();
            return this._parseVrml(text);
        }
        return null;
    }

    async _parseStep(buffer) {
        if (!this._occtReady || !this._occt) {
            console.warn('[3D] STEP parser not available');
            return null;
        }
        try {
            const result = this._occt.ReadStepFile(buffer, null);
            if (!result || !result.meshes) return null;

            const geometries = [];
            for (const meshInfo of result.meshes) {
                const mesh = meshInfo.mesh;
                if (!mesh || !mesh.attributes || !mesh.attributes.position) continue;

                const geo = new THREE.BufferGeometry();
                geo.setAttribute('position',
                    new THREE.Float32BufferAttribute(mesh.attributes.position.array, 3));
                if (mesh.attributes.normal) {
                    geo.setAttribute('normal',
                        new THREE.Float32BufferAttribute(mesh.attributes.normal.array, 3));
                }
                if (mesh.index) {
                    geo.setIndex(new THREE.BufferAttribute(
                        new Uint16Array(mesh.index.array), 1));
                }
                geometries.push(geo);
            }
            return geometries;
        } catch (e) {
            console.warn('[3D] STEP parse error:', e.message);
            return null;
        }
    }

    async _parseVrml(text) {
        try {
            const loader = new THREE.VRMLLoader();
            const scene = loader.parse(text);
            const geometries = [];
            scene.traverse(child => {
                if (child.isMesh && child.geometry) {
                    child.geometry.computeVertexNormals();
                    geometries.push(child.geometry.clone());
                }
            });
            return geometries.length > 0 ? geometries : null;
        } catch (e) {
            console.warn('[3D] VRML parse error:', e.message);
            return null;
        }
    }

    _instantiateFromCache(geometries, offset, scale, rotate) {
        const group = new THREE.Group();
        const mat = new THREE.MeshStandardMaterial({
            color: 0xaaaaaa,
            roughness: 0.5,
            metalness: 0.3,
        });

        for (const geo of geometries) {
            const mesh = new THREE.Mesh(geo, mat.clone());
            group.add(mesh);
        }

        // Apply transforms
        if (offset) group.position.set(offset[0] || 0, offset[2] || 0, -(offset[1] || 0));
        if (scale) group.scale.set(scale[0] || 1, scale[2] || 1, scale[1] || 1);
        if (rotate) {
            group.rotation.set(
                (rotate[0] || 0) * Math.PI / 180,
                -(rotate[2] || 0) * Math.PI / 180,
                (rotate[1] || 0) * Math.PI / 180,
            );
        }

        return group;
    }
}
