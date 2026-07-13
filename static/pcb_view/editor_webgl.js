const { mat3, vec2 } = glMatrix;

class PcbEditorWebGL {
    constructor(canvasId) {
        this._canvasId = canvasId;
        this._canvas = null;
        this._overlayCanvas = null;
        this._overlayCtx = null;
        this._gl = null;
        this._isWebGL2 = false;
        this._instancing = null;
        
        this._resizeHandler = () => {
            if (this._resizeFrame) cancelAnimationFrame(this._resizeFrame);
            this._resizeFrame = requestAnimationFrame(() => this._resize());
        };
        this._refreshFrame = null;
        this._resizeFrame = null;
        this._settleRefreshTimer = null;
        this._history = [];
        
        // Matrices
        this._projectionMatrix = mat3.create();
        this._viewMatrix = mat3.create();
        this._viewProjectionMatrix = mat3.create();
        // KiCanvas Camera2 for coordinate transforms
        this._camera = new KiCMath.Camera2();
        // KiCanvas WebGL renderer
        this._kcRenderer = null;
        this._kcReady = false;

        // Shaders and programs
        this._gridProgram = null;
        this._solidProgram = null;
        this._circleInstancedProgram = null;
        
        // Buffers
        this._traceVBO = null;
        this._traceVertexCount = 0;
        
        this._padVBO = null;
        this._padVertexCount = 0;
        
        this._silkVBO = null;
        this._silkVertexCount = 0;

        this._quadVBO = null; 
        this._viaInstanceBuffer = null;
        this._viaCount = 0;
    }

    ensure() {
        if (this._gl) return;
        this._canvas = document.getElementById(this._canvasId);
        if (!this._canvas) return;
        
        this._gl = this._canvas.getContext('webgl2');
        this._isWebGL2 = !!this._gl;
        if (!this._gl) this._gl = this._canvas.getContext('webgl');
        if (!this._gl) {
            console.error("WebGL not supported!");
            return;
        }
        this._instancing = this._resolveInstancingApi();
        if (!this._instancing) {
            console.warn("WebGL instancing support is unavailable; vias will not render.");
        }

        // Create overlay canvas for text and interactive UI (cursor, selection)
        this._overlayCanvas = document.createElement('canvas');
        this._overlayCanvas.id = 'pcbOverlayCanvas';
        this._overlayCanvas.className = 'pcb-overlay-canvas';
        this._overlayCanvas.style.position = 'absolute';
        this._overlayCanvas.style.top = '0';
        this._overlayCanvas.style.left = '0';
        this._overlayCanvas.style.right = '0';
        this._overlayCanvas.style.bottom = '0';
        this._overlayCanvas.style.zIndex = '3';
        this._overlayCanvas.style.pointerEvents = 'none'; // Let clicks pass to webgl canvas
        this._canvas.parentElement.style.position = 'relative';
        this._canvas.parentElement.appendChild(this._overlayCanvas);
        this._overlayCtx = this._overlayCanvas.getContext('2d');
        this._overlayCtx.imageSmoothingEnabled = true;
        this._syncOverlayVisibility();

        window.addEventListener('resize', this._resizeHandler);
        this._canvas.addEventListener('webglcontextlost', (e) => {
            e.preventDefault();
            console.warn('WebGL context lost');
            this._gl = null;
            if (this._refreshFrame) { cancelAnimationFrame(this._refreshFrame); this._refreshFrame = null; }
        });
        this._canvas.addEventListener('webglcontextrestored', () => {
            console.log('WebGL context restored');
            this._gl = null;
            this.ensure();
            this.refresh();
        });
        
        this._initShaders();
        this._resize();
        this._initKiCanvasRenderer().catch(e => {
            if (!this._suppressKcWarn) {
                console.warn('KiCanvas WebGL renderer init deferred or unavailable:', e.message);
            }
        });
    }

    destroy() {
        if (!this._gl) return;
        window.removeEventListener('resize', this._resizeHandler);
        if (this._refreshFrame) {
            cancelAnimationFrame(this._refreshFrame);
            this._refreshFrame = null;
        }
        if (this._resizeFrame) {
            cancelAnimationFrame(this._resizeFrame);
            this._resizeFrame = null;
        }
        if (this._settleRefreshTimer) {
            clearTimeout(this._settleRefreshTimer);
            this._settleRefreshTimer = null;
        }
        if (this._overlayCanvas && this._overlayCanvas.parentNode) {
            this._overlayCanvas.parentNode.removeChild(this._overlayCanvas);
        }
        this._gl = null;
        this._overlayCanvas = null;
        this._overlayCtx = null;
        this._canvas = null;
    }

    _compileShader(type, source) {
        const gl = this._gl;
        const shader = gl.createShader(type);
        gl.shaderSource(shader, source);
        gl.compileShader(shader);
        if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
            console.error("Shader compile error: ", gl.getShaderInfoLog(shader));
            gl.deleteShader(shader);
            return null;
        }
        return shader;
    }

    _createProgram(vsSource, fsSource) {
        const gl = this._gl;
        const vertexShader = this._compileShader(gl.VERTEX_SHADER, vsSource);
        const fragmentShader = this._compileShader(gl.FRAGMENT_SHADER, fsSource);
        if (!vertexShader || !fragmentShader) {
            return null;
        }
        const shaderProgram = gl.createProgram();
        gl.attachShader(shaderProgram, vertexShader);
        gl.attachShader(shaderProgram, fragmentShader);
        gl.linkProgram(shaderProgram);
        if (!gl.getProgramParameter(shaderProgram, gl.LINK_STATUS)) {
            console.error("Program link error: ", gl.getProgramInfoLog(shaderProgram));
            gl.deleteProgram(shaderProgram);
            return null;
        }
        gl.deleteShader(vertexShader);
        gl.deleteShader(fragmentShader);
        return shaderProgram;
    }

    _resolveInstancingApi() {
        const gl = this._gl;
        if (!gl) return null;
        if (this._isWebGL2) {
            return {
                vertexAttribDivisor(index, divisor) {
                    gl.vertexAttribDivisor(index, divisor);
                },
                drawArraysInstanced(mode, first, count, instanceCount) {
                    gl.drawArraysInstanced(mode, first, count, instanceCount);
                },
            };
        }
        const ext = gl.getExtension('ANGLE_instanced_arrays');
        if (!ext) return null;
        return {
            vertexAttribDivisor(index, divisor) {
                ext.vertexAttribDivisorANGLE(index, divisor);
            },
            drawArraysInstanced(mode, first, count, instanceCount) {
                ext.drawArraysInstancedANGLE(mode, first, count, instanceCount);
            },
        };
    }

    getViewportSize() {
        return {
            width: this._canvas ? this._canvas.width : 0,
            height: this._canvas ? this._canvas.height : 0,
        };
    }

    _initShaders() {
        const gl = this._gl;

        // 1. Grid Shader
        const gridVs = `
            attribute vec2 aVertexPosition;
            varying vec2 vPos;
            void main() {
                vPos = aVertexPosition;
                gl_Position = vec4(aVertexPosition, 0.0, 1.0);
            }
        `;
        const gridFs = `
            precision highp float;
            varying vec2 vPos;
            uniform mat3 uInverseMatrix;
            void main() {
                vec3 worldPos = uInverseMatrix * vec3(vPos.x, vPos.y, 1.0);

                // Background
                vec3 bgColor = vec3(0.0, 0.0, 0.0);

                // --- Minor grid lines (1.27mm spacing) ---
                // Distance from the nearest minor grid line in world units.
                float minorSpacing = 1.27;
                float mx = abs(fract(worldPos.x / minorSpacing + 0.5) - 0.5) * minorSpacing;
                float my = abs(fract(worldPos.y / minorSpacing + 0.5) - 0.5) * minorSpacing;
                float minorDist = min(mx, my);
                float minorIntensity = smoothstep(0.035, 0.0, minorDist);

                // --- Major grid lines (6.35mm = 5 * 1.27mm) ---
                float majorSpacing = 6.35;
                float Mx = abs(fract(worldPos.x / majorSpacing + 0.5) - 0.5) * majorSpacing;
                float My = abs(fract(worldPos.y / majorSpacing + 0.5) - 0.5) * majorSpacing;
                float majorDist = min(Mx, My);
                float majorIntensity = smoothstep(0.05, 0.0, majorDist);

                // --- Origin axes (at world 0,0) — brighter ---
                float originX = abs(worldPos.x);
                float originY = abs(worldPos.y);
                float originIntensityX = smoothstep(0.06, 0.0, originX);
                float originIntensityY = smoothstep(0.06, 0.0, originY);

                vec3 minorColor = vec3(0.09, 0.13, 0.17);
                vec3 majorColor = vec3(0.15, 0.21, 0.29);
                vec3 originColorX = vec3(0.35, 0.15, 0.15); // red X axis
                vec3 originColorY = vec3(0.15, 0.35, 0.15); // green Y axis

                vec3 color = bgColor;
                color = mix(color, minorColor, minorIntensity * 0.55);
                color = mix(color, majorColor, majorIntensity * 0.85);
                color = mix(color, originColorY, originIntensityX * 0.9);
                color = mix(color, originColorX, originIntensityY * 0.9);

                gl_FragColor = vec4(color, 1.0);
            }
        `;
        this._gridProgram = this._createProgram(gridVs, gridFs);

        // 2. Solid Polygon Shader (Traces, Pads, Silkscreen)
        const solidVs = `
            attribute vec2 aVertexPosition;
            uniform mat3 uMatrix;
            void main() {
                vec3 pos = uMatrix * vec3(aVertexPosition, 1.0);
                gl_Position = vec4(pos.xy, 0.0, 1.0);
            }
        `;
        const solidFs = `
            precision highp float;
            uniform vec4 uColor;
            void main() {
                gl_FragColor = uColor;
            }
        `;
        this._solidProgram = this._createProgram(solidVs, solidFs);
        
        // 3. Instanced Circle Shader (Vias & Drill holes)
        const circleVs = `
            attribute vec2 aVertexPosition;
            attribute vec3 aInstanceData; // x, y, radius
            uniform mat3 uMatrix;
            varying vec2 vUv;
            void main() {
                vUv = aVertexPosition;
                vec2 worldPos = aInstanceData.xy + (aVertexPosition * aInstanceData.z);
                vec3 pos = uMatrix * vec3(worldPos, 1.0);
                gl_Position = vec4(pos.xy, 0.0, 1.0);
            }
        `;
        const circleFs = `
            precision highp float;
            uniform vec4 uColor;
            varying vec2 vUv;
            void main() {
                float dist = length(vUv);
                if (dist > 1.0) discard;
                float alpha = smoothstep(1.0, 0.90, dist);
                gl_FragColor = vec4(uColor.rgb, uColor.a * alpha);
            }
        `;
        this._circleInstancedProgram = this._createProgram(circleVs, circleFs);

        // Base geometry quad (-1 to 1)
        this._quadVBO = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, this._quadVBO);
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
            -1, -1,  1, -1,  -1,  1,
            -1,  1,  1, -1,   1,  1
        ]), gl.STATIC_DRAW);
    }

    load(boardModel) {
        this.ensure();
        pcbState.boardModel = normalizeBoardModel(boardModel || { components: [], traces: [], vias: [], nets: [] });
        const liveRatsnest = this._computeClientRatsnest(pcbState.boardModel);
        pcbState.ratsnest = Object.keys(liveRatsnest).length
            ? liveRatsnest
            : (pcbState.boardModel.ratsnest || {});
        ensurePcbLayerVisibility(pcbState.boardModel);
        pcbState.activeTool = PCB_TOOL.PAN;
        pcbState.selectedComponentRef = null;
        pcbState.hoveredPadKey = null;
        pcbState.hoveredComponentRef = null;
        pcbState.hoveredViaIndex = null;
        pcbState.dragComponentRef = null;
        pcbState.dragViaIndex = null;
        pcbState.routeStartAnchor = null;
        pcbState.routeNetName = '';
        pcbState.routePoints = [];
        pcbState.routeVias = [];
        pcbState.routeCursor = null;
        pcbState.pointerDownScreen = null;
        pcbState.pointerDownWorld = null;
        pcbState.pointerDragMoved = false;
        this._computeView();
        this._buildBuffers();
        this.refresh();
        dispatchPcbInteractionUpdated();
        this._debugDumpBoardModel(pcbState.boardModel);
    }

    /**
     * Diagnostic dump — prints to the browser DevTools console (F12) exactly what
     * the frontend received in the board model. Use this to figure out why the
     * PCB view is missing silkscreen / traces / vias.
     *
     * Look for:
     *   - "components: 0"            → backend didn't send any components
     *   - "graphics: 0"              → KiCad footprint file failed to load on the
     *                                  Python side (the silent `except: return None`
     *                                  in _load_footprint_component swallowed the error)
     *   - "graphics kinds: {property: 2}" only  → footprint loaded but only text
     *                                  properties were parsed, no fp_line/fp_rect shapes
     *   - "pads: 0"                  → footprint loaded but pad parsing failed
     *   - "traces: 0"                → no traces in the board (expected for ratsnest-only mode)
     *   - "vias: 0"                  → no vias (expected for ratsnest-only mode)
     */
    _debugDumpBoardModel(model) {
        if (!model) {
            console.warn('[PCB DIAG] boardModel is null/undefined');
            return;
        }
        const components = model.components || [];
        const traces = model.traces || [];
        const vias = model.vias || [];
        const nets = model.nets || [];
        const outlineSegs = model.outline_segments || [];
        console.groupCollapsed(`[PCB DIAG] boardModel: ${components.length} components, ${traces.length} traces, ${vias.length} vias, ${nets.length} nets, ${outlineSegs.length} outline segments`);
        for (const comp of components) {
            const graphics = comp.graphics || [];
            const pads = comp.pads || [];
            const kinds = {};
            for (const g of graphics) {
                const k = g.kind || 'unknown';
                kinds[k] = (kinds[k] || 0) + 1;
            }
            const silkCount = graphics.filter(g => g.layer === 'F.SilkS' || g.layer === 'B.SilkS').length;
            const hasNonPropertyGraphics = graphics.some(g => g.kind !== 'property' && g.kind !== 'fp_text');
            console.log(
                `[PCB DIAG]   comp "${comp.ref}" footprint="${comp.footprint}"`,
                `pads=${pads.length}`,
                `graphics=${graphics.length}`,
                `silkItems=${silkCount}`,
                `hasShapeGraphics=${hasNonPropertyGraphics}`,
                `kinds=`, kinds,
                `pos=(${comp.x?.toFixed?.(2)}, ${comp.y?.toFixed?.(2)}) rot=${comp.rotation}`,
            );
            if (pads.length && !hasNonPropertyGraphics) {
                console.warn(`[PCB DIAG]     ⚠️  "${comp.ref}" has pads but NO shape graphics (fp_line/fp_rect/fp_circle). ` +
                             `The Python backend's _load_footprint_component likely failed silently to parse the .kicad_mod file. ` +
                             `Check the backend logs for the swallowed exception.`);
            }
        }
        if (!components.length) console.warn('[PCB DIAG] ⚠️  No components in boardModel — backend sent an empty board.');
        if (!traces.length)    console.log('[PCB DIAG] No traces (expected if you are in ratsnest-only / manual-routing mode).');
        if (!vias.length)      console.log('[PCB DIAG] No vias (expected for a fresh board).');
        if (!outlineSegs.length) console.warn('[PCB DIAG] ⚠️  No board outline segments — _drawBoardOutline will draw nothing.');
        console.log('[PCB DIAG] Full boardModel object:', model);
        console.groupEnd();
    }
    
    _addThickLine(verts, p1, p2, width) {
        const dx = p2.x - p1.x;
        const dy = p2.y - p1.y;
        const len = Math.hypot(dx, dy);
        if (len < 0.0001) return;
        const hw = width / 2.0;
        const nx = (-dy / len) * hw;
        const ny = (dx / len) * hw;
        const v1x = p1.x + nx, v1y = p1.y + ny;
        const v2x = p1.x - nx, v2y = p1.y - ny;
        const v3x = p2.x + nx, v3y = p2.y + ny;
        const v4x = p2.x - nx, v4y = p2.y - ny;
        verts.push(v1x, v1y, v2x, v2y, v3x, v3y);
        verts.push(v2x, v2y, v4x, v4y, v3x, v3y);
    }

    _addRotatedRect(verts, cx, cy, w, h, rotDeg) {
        const hw = w / 2;
        const hh = h / 2;
        const rad = rotDeg * Math.PI / 180;
        const cosR = Math.cos(rad);
        const sinR = Math.sin(rad);
        
        const transform = (x, y) => ({
            x: cx + (x * cosR - y * sinR),
            y: cy + (x * sinR + y * cosR)
        });

        const p1 = transform(-hw, -hh);
        const p2 = transform(hw, -hh);
        const p3 = transform(hw, hh);
        const p4 = transform(-hw, hh);

        verts.push(p1.x, p1.y, p2.x, p2.y, p3.x, p3.y);
        verts.push(p1.x, p1.y, p3.x, p3.y, p4.x, p4.y);
    }

    _buildBuffers() {
        if (!this._gl) return;
        const gl = this._gl;
        const empty = new Float32Array(0);
        if (!this._traceVBO) this._traceVBO = gl.createBuffer();
        if (!this._padVBO) this._padVBO = gl.createBuffer();
        if (!this._silkVBO) this._silkVBO = gl.createBuffer();
        if (!this._viaInstanceBuffer) this._viaInstanceBuffer = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, this._traceVBO);
        gl.bufferData(gl.ARRAY_BUFFER, empty, gl.STATIC_DRAW);
        gl.bindBuffer(gl.ARRAY_BUFFER, this._padVBO);
        gl.bufferData(gl.ARRAY_BUFFER, empty, gl.STATIC_DRAW);
        gl.bindBuffer(gl.ARRAY_BUFFER, this._silkVBO);
        gl.bufferData(gl.ARRAY_BUFFER, empty, gl.STATIC_DRAW);
        gl.bindBuffer(gl.ARRAY_BUFFER, this._viaInstanceBuffer);
        gl.bufferData(gl.ARRAY_BUFFER, empty, gl.STATIC_DRAW);
        this._traceVertexCount = 0;
        this._padVertexCount = 0;
        this._silkVertexCount = 0;
        this._viaCount = 0;
    }

    _resize() {
        if (!this._gl || !this._canvas) return;
        const parent = this._canvas.parentElement;
        const w = Math.max(parent ? parent.clientWidth : 1200, 100);
        const h = Math.max(parent ? parent.clientHeight : 700, 100);
        
        this._canvas.width = w;
        this._canvas.height = h;
        this._gl.viewport(0, 0, w, h);
        
        this._overlayCanvas.width = w;
        this._overlayCanvas.height = h;
        this._syncOverlayVisibility();

        pcbState.cx = w / 2;
        pcbState.cy = h / 2;
        this._applyCamera();
        this.refresh();
    }

    _computeView() {
        const bounds = modelBounds(pcbState.boardModel || { components: [], traces: [], vias: [], outline_segments: [] });
        const width = Math.max(bounds.maxX - bounds.minX, 10);
        const height = Math.max(bounds.maxY - bounds.minY, 10);
        const viewport = this.getViewportSize();
        pcbState.midX = (bounds.minX + bounds.maxX) / 2;
        pcbState.midY = (bounds.minY + bounds.maxY) / 2;
        pcbState.baseScale = Math.min(
            (viewport.width || 1200) / width,
            (viewport.height || 700) / height
        ) * 0.92;
        pcbState.zoom = 1;
        pcbState.panX = 0;
        pcbState.panY = 0;
        this._applyCamera();
    }

    _applyCamera() {
        if (!this._gl) return;
        
        const w = this._canvas.width;
        const h = this._canvas.height;
        
        this._setProjectionMatrix(this._projectionMatrix, w, h);
        
        const scale = pcbState.baseScale * pcbState.zoom;
        mat3.identity(this._viewMatrix);
        mat3.translate(this._viewMatrix, this._viewMatrix, [
            pcbState.cx + pcbState.panX, 
            pcbState.cy + pcbState.panY
        ]);
        mat3.scale(this._viewMatrix, this._viewMatrix, [scale, -scale]);
        mat3.translate(this._viewMatrix, this._viewMatrix, [-pcbState.midX, -pcbState.midY]);
        
        mat3.multiply(this._viewProjectionMatrix, this._projectionMatrix, this._viewMatrix);
        
        // Sync KiCanvas Camera2 for coordinate transforms
        this._syncCameraToState();
    }

    _syncCameraToState() {
        const w = this._canvas ? this._canvas.width : 0;
        const h = this._canvas ? this._canvas.height : 0;
        this._camera.viewport_size = new KiCMath.Vec2(w, h);
        this._camera.center = new KiCMath.Vec2(pcbState.midX, pcbState.midY);
        this._camera.zoom = pcbState.baseScale * pcbState.zoom;
    }

    _setProjectionMatrix(out, width, height) {
        // Convert pixel-space coordinates into WebGL clip space.
        out[0] = 2 / width;
        out[1] = 0;
        out[2] = 0;
        out[3] = 0;
        out[4] = -2 / height;
        out[5] = 0;
        out[6] = -1;
        out[7] = 1;
        out[8] = 1;
        return out;
    }

    async _initKiCanvasRenderer() {
        if (this._kcReady || !this._isWebGL2 || !this._gl) return;
        try {
            const { WebGL2Renderer } = KiCRender;
            this._kcRenderer = new WebGL2Renderer(this._canvas);
            await this._kcRenderer.setup(this._gl);
            this._kcReady = true;
            this.refresh();
    } catch (e) {
        console.error('KiCanvas WebGL renderer init failed:', e);
        this._kcReady = false;
    }
    }
    
    screenToWorld(sx, sy) {
        if (!this._gl) return {x:0, y:0};
        const rect = this._canvas.getBoundingClientRect();
        const localX = (sx - rect.left) * (this._canvas.width / rect.width);
        const localY = (sy - rect.top) * (this._canvas.height / rect.height);
        const inv = mat3.create();
        mat3.invert(inv, this._viewMatrix);
        const out = vec2.create();
        vec2.transformMat3(out, [localX, localY], inv);
        return { x: out[0], y: out[1] };
    }
    
    worldToScreen(wx, wy) {
        const out = vec2.create();
        vec2.transformMat3(out, [wx, wy], this._viewMatrix);
        return { x: out[0], y: out[1] };
    }
    
    requestOverlayRefresh() { this.requestRefresh(); }
    requestSettledRefresh(delay = 90) {
        if (!this._gl) return;
        if (this._settleRefreshTimer) clearTimeout(this._settleRefreshTimer);
        this._settleRefreshTimer = setTimeout(() => {
            this._settleRefreshTimer = null;
            this.requestRefresh();
        }, delay);
    }
    markDirty() {}
    
    requestRefresh() {
        if (!this._gl || this._refreshFrame) return;
        this._refreshFrame = requestAnimationFrame(() => {
            this._refreshFrame = null;
            this.refresh();
        });
    }

    refresh() {
        if (!this._gl) return;
        const gl = this._gl;
        
        gl.disable(gl.DEPTH_TEST);
        gl.enable(gl.BLEND);
        gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
        
        gl.clearColor(0.0, 0.0, 0.0, 1.0);
        gl.clear(gl.COLOR_BUFFER_BIT);

        // 1. Render Grid
        if (this._gridProgram) {
            gl.useProgram(this._gridProgram);
            const invViewProj = mat3.create();
            mat3.invert(invViewProj, this._viewProjectionMatrix);
            const uInvMat = gl.getUniformLocation(this._gridProgram, "uInverseMatrix");
            gl.uniformMatrix3fv(uInvMat, false, invViewProj);
            
            const aPos = gl.getAttribLocation(this._gridProgram, "aVertexPosition");
            gl.bindBuffer(gl.ARRAY_BUFFER, this._quadVBO);
            gl.enableVertexAttribArray(aPos);
            gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);
            
            gl.drawArrays(gl.TRIANGLES, 0, 6);
            gl.disableVertexAttribArray(aPos);
        }
        // Reset GL state after legacy grid shader so KiCanvas doesn't inherit
        // stale program or VAO bindings.
        gl.useProgram(null);
        gl.bindVertexArray(null);

        // 2. Render KiCanvas board content (if available)
        // Track whether KiCanvas succeeded so the overlay knows whether to
        // fall back to Canvas2D for board content (traces, pads, etc.).
        this._kcRenderSucceeded = false;
        if (this._kcReady && this._kcRenderer && pcbState.boardModel) {
            try {
                this._renderKiCanvasContent();
                this._kcRenderSucceeded = true;
            } catch (err) {
                console.warn('[PCB] KiCanvas render failed, falling back to Canvas2D:', err);
                this._kcRenderSucceeded = false;
                // Disable KiCanvas permanently so subsequent frames don't keep failing
                this._kcReady = false;
            }
        }
        
        // 3. Render overlay (text, airwires, selection, ghost)
        this._renderOverlay();
    }

    _renderKiCanvasContent() {
        const r = this._kcRenderer;
        if (!r || !r.gl) throw new Error('KiCanvas renderer or GL context unavailable');
        const gl = r.gl;

        // Do NOT use DEPTH_TEST here. All board content is a single flat 2D layer;
        // the shared depth buffer with GREATER would reject every primitive type
        // after the first (all drawn at the same depth), hiding traces, pads, etc.
        gl.disable(gl.DEPTH_TEST);

        // Dispose any stale layers from previous frames to prevent GPU memory
        // leak and stale accumulation (e.g. if rendering threw between startLayer
        // and removeLayer in a prior frame).
        for (const layer of r.layers.slice()) {
            layer.dispose();
        }
        r.layers.length = 0;

        // Build view * projection matrix (world → clip space)
        const w = this._canvas.width;
        const h = this._canvas.height;
        const proj = KiCMath.Mat3.orthographic(w, h);
        r.projectionMatrix = proj;

        const scale = pcbState.baseScale * pcbState.zoom;
        // multiply_self does LEFT-multiply (this = b * this), so chain is built
        // in reverse order vs glMatrix: center-translate first, scale, screen-translate last.
        const viewMat = KiCMath.Mat3.identity()
            .translate_self(-pcbState.midX, -pcbState.midY)
            .scale_self(scale, -scale)
            .translate_self(pcbState.cx + pcbState.panX, pcbState.cy + pcbState.panY);

        // multiply_self(b) computes this = b * this (left-multiply).
        // To get proj * viewMat, we need: viewMat.copy() then multiply_self(proj).
        const camera = viewMat.copy().multiply_self(proj);

        // Paint board content into a layer
        const boardLayer = r.startLayer('board');
        // state.matrix is a LOCAL/MODEL transform applied during primitive creation.
        // Camera matrix (proj * view) is applied in the shader during render().
        // If we set state.matrix=viewMat here, the view transform would be applied
        // TWICE (once during primitive creation, once in the shader via camera).
        r.state.matrix = KiCMath.Mat3.identity();
        const painter = new KiCBoard.BoardPainter(
            r, pcbState.boardModel, pcbState.visibleLayers || {}
        );
        painter.paint();
        r.endLayer();

        // Render the layer — only render the boardLayer we just created,
        // not ALL accumulated layers (which could include stale layers).
        if (boardLayer) {
            boardLayer.render(camera, 0.5, 1.0);
        }

        // Clean up GPU resources and remove from layer stack
        boardLayer.dispose();
        r.removeLayer(boardLayer);

        // Reset GL state so legacy shaders (grid) don't inherit VAO bindings
        gl.bindVertexArray(null);
        gl.useProgram(null);
        gl.disable(gl.DEPTH_TEST);
    }

    _renderOverlay() {
        const ctx = this._overlayCtx;
        const w = this._overlayCanvas.width;
        const h = this._overlayCanvas.height;
        ctx.clearRect(0, 0, w, h);

        const model = pcbState.boardModel;
        if (!model) return;
        // When KiCanvas WebGL successfully rendered board content, only draw UI
        // overlays here (text, airwires, selection, ghost). When KiCanvas is
        // unavailable or failed, fall back to Canvas2D for ALL board content.
        // Use _kcRenderSucceeded (not _kcReady) so silent failures (no throw
        // but no visible output) also trigger the fallback.
        if (!this._kcRenderSucceeded) {
            this._drawBoardCanvas(ctx, model);
        }
        this._drawAirwiresCanvas(ctx, model);
        this._drawBoardTexts(ctx, model);

        // Draw Active Route Cursor / Airwires
        if (pcbState.routePoints && pcbState.routePoints.length > 0) {
            const points = pcbState.routeCursor ? appendRoutePoint(pcbState.routePoints, pcbState.routeCursor) : pcbState.routePoints;
            this._strokeWorldPath(ctx, points, Math.max(pcbState.routeWidth || 0.25, 0.2), '#fff1a8', 0.95);
            // Snap-to-grid crosshair indicator at cursor
            if (pcbState.routeCursor) {
                const cp = this.worldToScreen(pcbState.routeCursor.x, pcbState.routeCursor.y);
                ctx.save();
                ctx.strokeStyle = 'rgba(255, 241, 168, 0.6)';
                ctx.lineWidth = 1;
                ctx.beginPath();
                ctx.moveTo(cp.x - 8, cp.y); ctx.lineTo(cp.x + 8, cp.y);
                ctx.moveTo(cp.x, cp.y - 8); ctx.lineTo(cp.x, cp.y + 8);
                ctx.stroke();
                // Route info tooltip
                const allPts = appendRoutePoint(pcbState.routePoints, pcbState.routeCursor);
                let routeLen = 0;
                for (let i = 1; i < allPts.length; i++) {
                    routeLen += Math.hypot(allPts[i].x - allPts[i-1].x, allPts[i].y - allPts[i-1].y);
                }
                const netName = pcbState.routeNetName || '';
                const info = netName ? `${netName}  ${routeLen.toFixed(2)}mm` : `${routeLen.toFixed(2)}mm`;
                ctx.fillStyle = 'rgba(0,0,0,0.7)';
                ctx.font = '11px "JetBrains Mono", monospace';
                ctx.textAlign = 'left';
                const tw = ctx.measureText(info).width;
                ctx.fillRect(cp.x + 12, cp.y - 16, tw + 8, 18);
                ctx.fillStyle = '#fff1a8';
                ctx.fillText(info, cp.x + 16, cp.y - 3);
                ctx.restore();
            }
        }

        // Draw outline preview during drawing
        if (pcbState.outlinePoints && pcbState.outlinePoints.length > 0) {
            ctx.save();
            ctx.setLineDash([7, 6]);
            const allPts = pcbState.outlineDraft
                ? [...pcbState.outlinePoints, pcbState.outlineDraft]
                : pcbState.outlinePoints;
            if (allPts.length >= 2) {
                this._strokeWorldPath(ctx, allPts, 0.15, '#19d7b0', 0.85);
            }
            // Draw placed vertices
            for (const pt of pcbState.outlinePoints) {
                const sp = this.worldToScreen(pt.x, pt.y);
                ctx.fillStyle = '#19d7b0';
                ctx.beginPath();
                ctx.arc(sp.x, sp.y, 4, 0, Math.PI * 2);
                ctx.fill();
            }
            ctx.restore();
        }

        // Highlight Selected Component
        if (pcbState.selectedComponentRef) {
            const comp = model.components.find(c => c.ref === pcbState.selectedComponentRef);
            if (comp) {
                const bounds = getComponentBounds(comp);
                const tl = this.worldToScreen(bounds.minX, bounds.minY);
                const br = this.worldToScreen(bounds.maxX, bounds.maxY);
                const rx = Math.min(tl.x, br.x);
                const ry = Math.min(tl.y, br.y);
                const rw = Math.abs(br.x - tl.x);
                const rh = Math.abs(br.y - tl.y);
                ctx.strokeStyle = '#4df1c2';
                ctx.fillStyle = 'rgba(77, 241, 194, 0.08)';
                ctx.lineWidth = 2;
                this._roundRectPath(ctx, rx, ry, rw, rh, 10);
                ctx.fill();
                ctx.stroke();
            }
        }

        // Highlight Hovered Component (subtle glow, only when not selected)
        if (pcbState.hoveredComponentRef && pcbState.hoveredComponentRef !== pcbState.selectedComponentRef) {
            const comp = model.components.find(c => c.ref === pcbState.hoveredComponentRef);
            if (comp) {
                const bounds = getComponentBounds(comp);
                const tl = this.worldToScreen(bounds.minX, bounds.minY);
                const br = this.worldToScreen(bounds.maxX, bounds.maxY);
                const rx = Math.min(tl.x, br.x);
                const ry = Math.min(tl.y, br.y);
                const rw = Math.abs(br.x - tl.x);
                const rh = Math.abs(br.y - tl.y);
                ctx.strokeStyle = 'rgba(77, 241, 194, 0.4)';
                ctx.lineWidth = 1;
                this._roundRectPath(ctx, rx, ry, rw, rh, 8);
                ctx.stroke();
            }
        }

        // Highlight hovered pad during routing — KiCad-style attraction glow
        if (pcbState.hoveredPadKey && pcbState.activeTool === PCB_TOOL.ROUTE) {
            const [ref, padNum] = pcbState.hoveredPadKey.split(':');
            const comp = (model.components || []).find(c => c.ref === ref);
            if (comp) {
                const pad = (comp.pads || []).find(p => String(p.number) === String(padNum));
                if (pad) {
                    const center = getComponentPadPosition(comp, pad);
                    const sp = this.worldToScreen(center.x, center.y);
                    const r = this._worldRadiusToPixels(Math.max(pad.width, pad.height) / 2) + 6;
                    // Outer glow ring
                    ctx.save();
                    ctx.beginPath();
                    ctx.arc(sp.x, sp.y, r, 0, Math.PI * 2);
                    ctx.fillStyle = 'rgba(255, 241, 168, 0.15)';
                    ctx.fill();
                    ctx.strokeStyle = '#fff1a8';
                    ctx.lineWidth = 2;
                    ctx.stroke();
                    // Inner highlight dot
                    ctx.beginPath();
                    ctx.arc(sp.x, sp.y, 3, 0, Math.PI * 2);
                    ctx.fillStyle = '#fff1a8';
                    ctx.fill();
                    ctx.restore();
                }
            }
        }

        // Highlight Hovered Trace (for delete feedback)
        if (pcbState.hoveredTraceIndex != null && model.traces) {
            const trace = model.traces[pcbState.hoveredTraceIndex];
            if (trace && trace.path && trace.path.length >= 2) {
                ctx.save();
                this._strokeWorldPath(ctx, trace.path, (trace.width || 0.254) + 0.15, '#ff5555', 0.7);
                // Highlight the specific hovered segment
                if (pcbState.hoveredSegmentIndex != null) {
                    const si = pcbState.hoveredSegmentIndex;
                    if (si < trace.path.length - 1) {
                        this._strokeWorldPath(ctx, [trace.path[si], trace.path[si + 1]], (trace.width || 0.254) + 0.2, '#ff5555', 0.95);
                    }
                }
                ctx.restore();
            }
        }

        // Highlight selected traces (multi-select)
        if (pcbState.selectedTraceIndices && pcbState.selectedTraceIndices.length > 0 && model.traces) {
            ctx.save();
            for (const idx of pcbState.selectedTraceIndices) {
                const trace = model.traces[idx];
                if (trace && trace.path && trace.path.length >= 2) {
                    this._strokeWorldPath(ctx, trace.path, (trace.width || 0.254) + 0.2, '#00ff88', 0.6);
                }
            }
            ctx.restore();
        }

        // Net highlighting — glow pads and traces belonging to the highlighted net
        if (pcbState.highlightedNet && model) {
            const hNet = pcbState.highlightedNet;
            ctx.save();
            // Highlight pads
            for (const comp of model.components || []) {
                for (const pad of comp.pads || []) {
                    const padNet = pad.net || getNetNameForPad(model, comp.ref, pad.number);
                    if (padNet === hNet) {
                        const center = getComponentPadPosition(comp, pad);
                        const sp = this.worldToScreen(center.x, center.y);
                        const r = this._worldRadiusToPixels(Math.max(pad.width, pad.height) / 2) + 4;
                        ctx.beginPath();
                        ctx.arc(sp.x, sp.y, r, 0, Math.PI * 2);
                        ctx.fillStyle = 'rgba(77, 241, 194, 0.25)';
                        ctx.fill();
                        ctx.strokeStyle = '#4df1c2';
                        ctx.lineWidth = 2;
                        ctx.stroke();
                    }
                }
            }
            // Highlight traces
            for (const trace of model.traces || []) {
                if (trace.net === hNet) {
                    this._strokeWorldPath(ctx, trace.path, (trace.width || 0.254) + 0.2, '#4df1c2', 0.4);
                }
            }
            // Highlight airwires
            const hEdges = (pcbState.ratsnest || {})[hNet] || [];
            for (const edge of hEdges) {
                const start = this._resolveAirwireEndpoint(model, edge, 'from', 'x1', 'y1');
                const end = this._resolveAirwireEndpoint(model, edge, 'to', 'x2', 'y2');
                if (start && end) this._strokeWorldPath(ctx, [start, end], 0.3, '#4df1c2', 0.8);
            }
            // Net name label
            ctx.font = '11px "JetBrains Mono", monospace';
            ctx.fillStyle = '#4df1c2';
            ctx.textAlign = 'left';
            ctx.fillText('Net: ' + hNet, 10, 30);
            ctx.restore();
        }

        // Draw Ghost Component for AI Proposals
        if (pcbState.mode === PCB_MODE.GHOST_PLACEMENT && pcbState.ghostProposal && pcbState.lastPointerWorld) {
            const { x, y } = pcbState.lastPointerWorld;
            const screenPos = this.worldToScreen(x, y);
            const ghostComponent = pcbState.ghostProposal.component || {};
            const body = ghostComponent.body || {};
            const halfWidth = Math.max(Number(body.width) || 1, 0.6) / 2;
            const halfHeight = Math.max(Number(body.height) || 0.6, 0.4) / 2;
            const topLeft = this.worldToScreen(x - halfWidth, y - halfHeight);
            const bottomRight = this.worldToScreen(x + halfWidth, y + halfHeight);

            ctx.save();
            ctx.globalAlpha = 0.5;
            ctx.setLineDash([5, 5]);
            ctx.strokeStyle = '#4fc3f7';
            ctx.lineWidth = 2;

            const rw = bottomRight.x - topLeft.x;
            const rh = bottomRight.y - topLeft.y;
            ctx.strokeRect(Math.min(topLeft.x, bottomRight.x), Math.min(topLeft.y, bottomRight.y), Math.abs(rw), Math.abs(rh));
            ctx.fillStyle = '#4fc3f7';
            ctx.font = '12px "JetBrains Mono", monospace';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(ghostComponent.name || 'Component', screenPos.x, screenPos.y);
            ctx.restore();

            if (Array.isArray(ghostComponent.pins) && ghostComponent.pins.length) {
                ctx.save();
                ctx.strokeStyle = 'rgba(255, 255, 255, 0.4)';
                ctx.lineWidth = 1.5;
                ctx.beginPath();

                for (const pin of ghostComponent.pins) {
                    const targetNet = pin && typeof pin === 'object' ? pin.targetNet : null;
                    if (targetNet) {
                        const targetPad = this._findNearestPadForNet(targetNet, x, y);
                        if (targetPad) {
                            const targetScreen = this.worldToScreen(targetPad.x, targetPad.y);
                            ctx.moveTo(screenPos.x, screenPos.y);
                            ctx.lineTo(targetScreen.x, targetScreen.y);
                        }
                    }
                }
                ctx.stroke();
                ctx.restore();
            }
        }
    }

    _findNearestPadForNet(netName, fromX, fromY) {
        if (!netName || !pcbState.boardModel) return null;
        let best = null;
        let minDist = Infinity;
        for (const comp of pcbState.boardModel.components || []) {
            for (const pad of comp.pads || []) {
                if (pad.net === netName) {
                    const padPos = getComponentPadPosition(comp, pad);
                    const dist = Math.hypot(padPos.x - fromX, padPos.y - fromY);
                    if (dist < minDist) {
                        minDist = dist;
                        best = padPos;
                    }
                }
            }
        }
        return best;
    }

    _syncOverlayVisibility() {
        if (!this._overlayCanvas || !this._canvas) return;
        const visible = this._canvas.style.display !== 'none' && this._canvas.style.visibility !== 'hidden';
        this._overlayCanvas.style.display = visible ? 'block' : 'none';
        this._overlayCanvas.style.visibility = visible ? 'visible' : 'hidden';
    }

    _drawAirwiresCanvas(ctx, model) {
        const ratsnest = pcbState.ratsnest || {};
        const netNames = Object.keys(ratsnest);
        if (!netNames.length) return;

        // When actively routing, redirect airwires for the routed net
        // so the suggestion line follows the cursor (not the original pad).
        const isRouting = pcbState.mode === PCB_MODE.ROUTE && pcbState.routeStartAnchor;
        const routeNet = isRouting ? pcbState.routeNetName : null;
        const routeSourceKey = isRouting ? pcbState.routeStartAnchor.key : null;
        // Use the live cursor position so the airwire follows the mouse, not just the last placed point
        const routeEnd = isRouting && pcbState.routeCursor
            ? pcbState.routeCursor
            : isRouting && pcbState.routePoints && pcbState.routePoints.length > 0
                ? pcbState.routePoints[pcbState.routePoints.length - 1]
                : null;

        ctx.save();
        ctx.setLineDash([4, 4]); // KiCad-style thin dashed line
        for (const netName of netNames) {
            const edges = ratsnest[netName] || [];
            const isCurrentNet = netName === routeNet;
            for (const edge of edges) {
                let start = this._resolveAirwireEndpoint(model, edge, 'from', 'x1', 'y1');
                let end = this._resolveAirwireEndpoint(model, edge, 'to', 'x2', 'y2');
                if (!start || !end) continue;

                // Redirect airwire endpoints from the route source pad to the last placed route point
                if (isRouting && netName === routeNet && routeEnd) {
                    if (edge.from === routeSourceKey) {
                        start = { x: routeEnd.x, y: routeEnd.y };
                    }
                    if (edge.to === routeSourceKey) {
                        end = { x: routeEnd.x, y: routeEnd.y };
                    }
                }

                // Bright airwire for current net, dim for others (only when routing)
                if (isRouting && isCurrentNet) {
                    this._strokeWorldPath(ctx, [start, end], 0.25, '#00ffcc', 0.9);
                } else if (isRouting && !isCurrentNet) {
                    this._strokeWorldPath(ctx, [start, end], 0.15, '#333333', 0.3);
                } else {
                    // Not routing - show all airwires bright
                    this._strokeWorldPath(ctx, [start, end], 0.15, '#aab8c8', 0.7);
                }
            }
        }
        ctx.restore();
    }

    _resolveAirwireEndpoint(model, edge, pinField, xField, yField) {
        if (!edge) return null;
        if (edge[pinField]) {
            const pos = getPadPositionByPinKey(model, edge[pinField]);
            if (pos) return pos;
        }
        const x = Number(edge[xField]);
        const y = Number(edge[yField]);
        if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
        return { x, y };
    }

    _drawBoardCanvas(ctx, model) {
        this._drawBoardOutline(ctx, model);

        const components = model.components || [];

        // Pass 1: all body fills for every component (bottom — behind everything)
        for (const component of components) {
            this._drawComponentBodyFill(ctx, component);
        }

        // Pass 2: all copper traces — drawn AFTER body fill so component shadows
        // correctly obscure traces underneath the component body.
        this._drawTracesCanvas(ctx, model.traces || []);

        // Pass 3: all vias
        this._drawViasCanvas(ctx, model.vias || []);

        // Pass 4: all solder mask expansions for every component
        for (const component of components) {
            this._drawComponentMasksCanvas(ctx, component);
        }

        // Pass 5: all copper pads for every component (on top of traces)
        for (const component of components) {
            this._drawComponentCopperCanvas(ctx, component);
        }

        // Pass 6: all drill holes for every component (punches through copper)
        for (const component of components) {
            this._drawComponentDrillsCanvas(ctx, component);
        }

        // Pass 7: Graphics (silkscreen, fab, courtyard) drawn on top
        for (const component of components) {
            this._drawComponentGraphicsCanvas(ctx, component, true);
        }
    }

    _drawBoardOutline(ctx, model) {
        if (!isPcbLayerVisible('Edge.Cuts')) return;
        const segments = outlineSegments(model);

        // --- Board substrate fill ---
        // Draw a filled polygon (or fallback rectangle) so the board area is
        // clearly distinguishable from the dark background. KiCad shows the
        // PCB substrate as a dark green/black area.
        ctx.save();
        ctx.fillStyle = '#0d1f1a'; // dark green PCB substrate
        ctx.globalAlpha = 0.92;

        if (segments.length) {
            // Try to stitch outline segments into a closed polygon for the fill.
            const allPoints = [];
            for (const seg of segments) {
                const pts = this._outlineSegmentPoints(seg);
                if (pts.length >= 2) {
                    // Drop last point if it equals first (closing duplicate)
                    if (pts.length > 2 &&
                        Math.abs(pts[0].x - pts[pts.length - 1].x) < 0.01 &&
                        Math.abs(pts[0].y - pts[pts.length - 1].y) < 0.01) {
                        allPoints.push(...pts.slice(0, -1));
                    } else {
                        allPoints.push(...pts.slice(0, -1));
                    }
                }
            }
            if (allPoints.length >= 3) {
                ctx.beginPath();
                const first = this.worldToScreen(allPoints[0].x, allPoints[0].y);
                ctx.moveTo(first.x, first.y);
                for (let i = 1; i < allPoints.length; i++) {
                    const pt = this.worldToScreen(allPoints[i].x, allPoints[i].y);
                    ctx.lineTo(pt.x, pt.y);
                }
                ctx.closePath();
                ctx.fill();
            } else {
                // Fallback: fill the model bounds
                const bounds = modelBounds(model);
                const tl = this.worldToScreen(bounds.minX - 5, bounds.minY - 5);
                const br = this.worldToScreen(bounds.maxX + 5, bounds.maxY + 5);
                const fw1 = br.x - tl.x;
                const fh1 = br.y - tl.y;
                ctx.fillRect(Math.min(tl.x, br.x), Math.min(tl.y, br.y), Math.abs(fw1), Math.abs(fh1));
            }
        } else {
            // No outline segments at all — fill the model bounds
            const bounds = modelBounds(model);
            const tl = this.worldToScreen(bounds.minX - 5, bounds.minY - 5);
            const br = this.worldToScreen(bounds.maxX + 5, bounds.maxY + 5);
            const fw2 = br.x - tl.x;
            const fh2 = br.y - tl.y;
            ctx.fillRect(Math.min(tl.x, br.x), Math.min(tl.y, br.y), Math.abs(fw2), Math.abs(fh2));
        }
        ctx.restore();

        // --- Board edge outline (bright line on top of the fill) ---
        if (!segments.length) return;
        ctx.save();
        ctx.strokeStyle = '#19d7b0';
        ctx.lineWidth = Math.max(this._worldRadiusToPixels(0.1), 0.75);
        ctx.shadowColor = 'rgba(6, 59, 50, 0.85)';
        ctx.shadowBlur = 10;
        ctx.lineJoin = 'round';
        ctx.lineCap = 'round';
        for (const segment of segments) {
            const points = this._outlineSegmentPoints(segment);
            if (points.length < 2) continue;
            ctx.beginPath();
            const start = this.worldToScreen(points[0].x, points[0].y);
            ctx.moveTo(start.x, start.y);
            for (let i = 1; i < points.length; i++) {
                const pt = this.worldToScreen(points[i].x, points[i].y);
                ctx.lineTo(pt.x, pt.y);
            }
            ctx.stroke();
        }
        ctx.restore();
    }

    _outlineSegmentPoints(segment) {
        if (!segment) return [];
        if (Array.isArray(segment.points) && segment.points.length > 1) {
            return segment.points;
        }
        if (segment.kind === 'gr_rect' && segment.start && segment.end) {
            return [
                segment.start,
                { x: segment.end.x, y: segment.start.y },
                segment.end,
                { x: segment.start.x, y: segment.end.y },
                segment.start
            ];
        }
        if (segment.kind === 'gr_arc' && segment.start && segment.mid && segment.end) {
            return arcPoints(segment.start, segment.mid, segment.end, 28);
        }
        const points = [];
        if (segment.start) points.push(segment.start);
        if (segment.end) points.push(segment.end);
        return points;
    }

    _drawTracesCanvas(ctx, traces) {
        // Bottom copper first, then front copper, so bottom traces sit behind.
        const group = (layer) => isBottomCopperLayer(layer) ? 0 : 1;
        const sorted = (traces || []).slice().sort((a, b) => group(a.layer) - group(b.layer));
        for (const trace of sorted) {
            if (!isPcbLayerVisible(trace.layer || 'F.Cu')) continue;
            const path = trace.path || [];
            if (path.length < 2) continue;
            const rawColor = (typeof copperColorForLayer === 'function') ? copperColorForLayer(trace.layer) : null;
            const color = this._copperColorForLayer(trace.layer, rawColor);
            const traceWidth = Math.max(trace.width || 0.254, 0.14);
            this._strokeWorldPath(ctx, path, traceWidth, color, 1.0);
        }
    }

    /**
     * Resolve a KiCad-like copper color for a layer.
     * Falls back to a sensible copper palette if copperColorForLayer() is missing or returns junk.
     */
    _copperColorForLayer(layer, rawColor) {
        const isBack = (layer === 'B.Cu' || layer === 'back_c' || layer === 'back_copper' || layer === 'bottom');
        const fallback = isBack ? '#356cff' : '#ff563d';
        if (rawColor == null) return fallback;
        if (typeof rawColor === 'string') {
            const trimmed = rawColor.trim();
            if (/^#[0-9a-fA-F]{6}$/.test(trimmed)) return trimmed;
            if (/^[0-9a-fA-F]{6}$/.test(trimmed)) return `#${trimmed}`;
            if (trimmed.startsWith('#') || trimmed.startsWith('rgb')) return trimmed;
            return fallback;
        }
        if (typeof rawColor === 'number' && Number.isFinite(rawColor)) {
            return `#${(rawColor >>> 0).toString(16).padStart(6, '0').slice(-6)}`;
        }
        return fallback;
    }

    _drawViasCanvas(ctx, vias) {
        for (const via of vias) {
            const viaLayers = via.layers || ['F.Cu', 'B.Cu'];
            if (!viaLayers.some((layer) => isPcbLayerVisible(layer))) continue;
            const center = this.worldToScreen(via.x, via.y);
            const outerR = this._worldRadiusToPixels(Math.max(via.diameter || 0.6, 0.6) / 2);
            const drillR = this._worldRadiusToPixels(Math.max(via.drill || Math.max((via.diameter || 0.6) * 0.45, 0.2), 0.18) / 2);

            // Determine via copper color based on layers.
            const hasTop = viaLayers.some((l) => isFrontCopperLayer(l));
            const hasBot = viaLayers.some((l) => isBottomCopperLayer(l));
            const viaColor = hasBot && !hasTop ? '#7f9bff' : hasTop && !hasBot ? '#b87333' : '#caa15f';

            // 1. Copper annular ring (KiCad-like copper pad around the hole).
            ctx.beginPath();
            ctx.fillStyle = viaColor;
            ctx.arc(center.x, center.y, outerR, 0, Math.PI * 2);
            ctx.fill();

            // 2. Subtle highlight on the ring for depth (top-left light).
            ctx.beginPath();
            ctx.strokeStyle = 'rgba(255, 215, 170, 0.35)';
            ctx.lineWidth = Math.max(1, outerR * 0.18);
            ctx.arc(center.x, center.y, Math.max(outerR - ctx.lineWidth / 2, drillR + 0.5), Math.PI * 1.15, Math.PI * 1.85);
            ctx.stroke();

            // 3. Dark drill hole punched through the copper.
            ctx.beginPath();
            ctx.fillStyle = '#0b1116';
            ctx.arc(center.x, center.y, drillR, 0, Math.PI * 2);
            ctx.fill();

            // 4. Thin shadow inside the hole for depth.
            if (drillR > 2) {
                ctx.beginPath();
                ctx.strokeStyle = 'rgba(0, 0, 0, 0.6)';
                ctx.lineWidth = 1;
                ctx.arc(center.x, center.y, Math.max(drillR - 0.5, 0.5), 0, Math.PI * 2);
                ctx.stroke();
            }
        }
    }

    _drawComponentCanvas(ctx, component) {
        this._drawComponentMasksCanvas(ctx, component);
        this._drawComponentCopperCanvas(ctx, component);
        this._drawComponentDrillsCanvas(ctx, component);
        this._drawComponentGraphicsCanvas(ctx, component);
    }

    _drawComponentMasksCanvas(ctx, component) {
        const pads = (component.pads || []).slice().sort((a, b) => {
            // Bottom masks behind top masks; within same layer, largest first.
            const aBot = (a.layers || []).some((l) => l === 'B.Mask') ? 0 : 1;
            const bBot = (b.layers || []).some((l) => l === 'B.Mask') ? 0 : 1;
            if (aBot !== bBot) return aBot - bBot;
            return ((b.width || 0) * (b.height || 0)) - ((a.width || 0) * (a.height || 0));
        });
        for (const pad of pads) {
            const padLayers = pad.layers || ['F.Cu'];
            const maskLayers = padLayers.filter((layer) => layer === 'F.Mask' || layer === 'B.Mask');
            if (maskLayers.length && !maskLayers.some((layer) => isPcbLayerVisible(layer))) continue;
            if (!maskLayers.length && !padLayers.some((layer) => isPcbLayerVisible(layer))) continue;
            const center = getComponentPadPosition(component, pad);
            const rotation = (component.rotation || 0) + (pad.rotation || 0);
            const width = pad.width || 1;
            const height = pad.height || 1;
            this._fillPadShape(ctx, center, width + 0.1, height + 0.1, pad.shape || 'rect', rotation, '#0f3b32', 0.95, pad.roundrect_rratio, null, pad.rect_delta_x, pad.rect_delta_y);
        }
    }

    _drawComponentCopperCanvas(ctx, component) {
        const pads = (component.pads || []).slice().sort((a, b) => {
            // Bottom copper behind front copper; through-hole in middle.
            const aGroup = (a.layers || []).some(isBottomCopperLayer) ? 0 : ((a.type === 'thru_hole' || a.type === 'np_thru_hole' || a.type === 'tht') ? 1 : 2);
            const bGroup = (b.layers || []).some(isBottomCopperLayer) ? 0 : ((b.type === 'thru_hole' || b.type === 'np_thru_hole' || b.type === 'tht') ? 1 : 2);
            if (aGroup !== bGroup) return aGroup - bGroup;
            return ((b.width || 0) * (b.height || 0)) - ((a.width || 0) * (a.height || 0));
        });
        const dimAlpha = this._getRoutingDimAlpha(component);
        for (const pad of pads) {
            if (!(pad.layers || ['F.Cu']).some((layer) => isPcbLayerVisible(layer))) continue;
            const center = getComponentPadPosition(component, pad);
            const rotation = (component.rotation || 0) + (pad.rotation || 0);
            const width = pad.width || 1;
            const height = pad.height || 1;
            const isBottom = (pad.layers || []).some((layer) => isBottomCopperLayer(layer));
            const isThrough = pad.type === 'thru_hole' || pad.type === 'np_thru_hole' || pad.type === 'tht';
            const copperColor = isThrough ? '#ffcc88' : (isBottom ? '#5599ff' : '#ff6655');
            const strokeColor = isThrough ? '#ffdd99' : (isBottom ? '#66aaff' : '#ff8877');
            this._fillPadShape(ctx, center, width, height, pad.shape || 'rect', rotation, copperColor, dimAlpha, pad.roundrect_rratio, strokeColor, pad.rect_delta_x, pad.rect_delta_y);
        }
    }

    _drawComponentDrillsCanvas(ctx, component) {
        for (const pad of component.pads || []) {
            const isThrough = pad.type === 'thru_hole' || pad.type === 'np_thru_hole' || pad.type === 'tht';
            if (!isThrough) continue;
            if (!(pad.layers || ['F.Cu']).some((layer) => isPcbLayerVisible(layer))) continue;
            const center = getComponentPadPosition(component, pad);
            const rotation = (component.rotation || 0) + (pad.rotation || 0);
            const width = pad.width || 1;
            const height = pad.height || 1;
            // Cap the drill diameter to 65% of the pad's smaller dimension so the
            // copper annular ring is always visible. If the data specifies a drill
            // larger than the pad, clamp it down.
            const maxDrill = Math.min(width, height) * 0.65;
            const rawDrill = pad.drill || Math.min(width, height) * 0.45;
            const drill = Math.max(Math.min(rawDrill, maxDrill), 0.2);
            const drillWidth = Math.min(pad.drill_width || drill, maxDrill);
            const offset = rotatePoint(pad.drill_offset_x || 0, pad.drill_offset_y || 0, -(component.rotation || 0));
            this._fillPadDrill(ctx, { x: center.x + offset.x, y: center.y + offset.y }, drill, drillWidth, rotation);
        }
    }

    _drawComponentPadsCanvas(ctx, component) {
        // Legacy single-pass method (kept for compatibility)
        this._drawComponentMasksCanvas(ctx, component);
        this._drawComponentCopperCanvas(ctx, component);
        this._drawComponentDrillsCanvas(ctx, component);
    }

    _drawComponentGraphicsCanvas(ctx, component, skipBodyFill = false) {
        const grouped = {
            'F.CrtYd': [],
            'B.CrtYd': [],
            'F.Fab': [],
            'B.Fab': [],
            'F.SilkS': [],
            'B.SilkS': [],
        };
        for (const item of component.graphics || []) {
            if (item.kind === 'property') continue;
            if (!isPcbLayerVisible(item.layer)) continue;
            if (grouped[item.layer]) grouped[item.layer].push(item);
        }

        // ── Component body fill ──────────────────────────────────────────
        // Always draw a subtle dark body behind the pads so the component
        // looks like a physical part, not just floating pads. This runs
        // regardless of whether silkscreen graphics exist.
        if (!skipBodyFill) this._drawComponentBodyFill(ctx, component);

        const graphicsDimAlpha = this._getRoutingDimAlpha(component);
        for (const item of grouped['F.CrtYd']) this._drawGraphicItem(ctx, component, item, '#3d7570', 0.7 * graphicsDimAlpha, true);
        for (const item of grouped['B.CrtYd']) this._drawGraphicItem(ctx, component, item, '#3d7570', 0.45 * graphicsDimAlpha, true);
        for (const item of grouped['F.Fab']) this._drawGraphicItem(ctx, component, item, '#8eb0aa', 0.9 * graphicsDimAlpha, false, 'rgba(58, 104, 96, 0.18)');
        for (const item of grouped['B.Fab']) this._drawGraphicItem(ctx, component, item, '#8eb0aa', 0.55 * graphicsDimAlpha, false, 'rgba(58, 104, 96, 0.12)');
        for (const item of grouped['F.SilkS']) this._drawGraphicItem(ctx, component, item, '#ffffff', graphicsDimAlpha, false, 'rgba(255, 255, 255, 0.15)');
        for (const item of grouped['B.SilkS']) this._drawGraphicItem(ctx, component, item, '#dddddd', 0.55 * graphicsDimAlpha, false, 'rgba(220, 220, 220, 0.10)');

        // ── Fallback body outline ─────────────────────────────────────────
        // If the backend failed to load the KiCad footprint graphics (the Python
        // `_load_footprint_component` swallows all exceptions and returns None),
        // the user would otherwise see only bare pads floating on the board.
        // Draw a KiCad-style silkscreen bounding-box outline around the pad extents
        // so the component body is always visible. This is the key fix for the
        // "only pads visible, no silkscreen" bug shown in the screenshots.
        const hasSilk = (grouped['F.SilkS'].length + grouped['B.SilkS'].length) > 0;
        if (!hasSilk && (isPcbLayerVisible('F.SilkS') || isPcbLayerVisible('B.SilkS'))) {
            this._drawFallbackComponentOutline(ctx, component);
        }
    }

    /**
     * Draw a subtle dark body fill behind the pads of every component.
     * This makes the component look like a physical part (IC package, connector
     * body, etc.) instead of just floating pads on the bare PCB.
     * Runs for ALL components, even those with silkscreen graphics, because
     * the silkscreen outline alone doesn't give a "body" feel.
     */
    _drawComponentBodyFill(ctx, component) {
        const pads = component.pads || [];
        if (!pads.length) return;
        // Compute the bounding box of all pads (in world coords)
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        for (const pad of pads) {
            const c = getComponentPadPosition(component, pad);
            const hw = (pad.width || 1) / 2;
            const hh = (pad.height || 1) / 2;
            if (c.x - hw < minX) minX = c.x - hw;
            if (c.x + hw > maxX) maxX = c.x + hw;
            if (c.y - hh < minY) minY = c.y - hh;
            if (c.y + hh > maxY) maxY = c.y + hh;
        }
        if (!Number.isFinite(minX)) return;
        const a = this.worldToScreen(minX, minY);
        const b = this.worldToScreen(maxX, maxY);
        ctx.save();
        const dimAlpha = this._getRoutingDimAlpha(component);
        ctx.fillStyle = `rgba(20, 25, 35, ${0.50 * dimAlpha})`;
        const fx = Math.min(a.x, b.x);
        const fy = Math.min(a.y, b.y);
        const fw = Math.abs(b.x - a.x);
        const fh = Math.abs(b.y - a.y);
        ctx.fillRect(fx, fy, fw, fh);
        ctx.restore();

        // Pin 1 marker — always draw so orientation is visible
        this._drawPin1Marker(ctx, component);
    }

    /**
     * Draw a pin-1 marker (small filled circle) near pad 1.
     * KiCad convention: a dot or stripe near pin 1 indicates orientation.
     */
    _drawPin1Marker(ctx, component) {
        const pads = component.pads || [];
        if (!pads.length) return;
        const pin1 = pads.find(p => String(p.number) === '1') || pads[0];
        if (!pin1) return;
        const center = getComponentPadPosition(component, pin1);
        const screen = this.worldToScreen(center.x, center.y);
        const markerR = Math.max(this._worldRadiusToPixels(0.18), 2.5);
        ctx.save();
        ctx.globalAlpha = 0.85;
        ctx.fillStyle = '#4df1c2';
        ctx.beginPath();
        ctx.arc(screen.x, screen.y, markerR, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
    }

    _isComponentConnectedToNet(component, netName) {
        if (!netName) return true;
        for (const pad of component.pads || []) {
            if (pad.net === netName) return true;
        }
        return false;
    }

    _getRoutingDimAlpha(component) {
        const isRouting = pcbState.mode === PCB_MODE.ROUTE && pcbState.routeNetName;
        if (!isRouting) return 1.0;
        return this._isComponentConnectedToNet(component, pcbState.routeNetName) ? 1.0 : 0.2;
    }

    /**
     * Draw a KiCad-style fallback silkscreen outline around the component's pad extents.
     * Used when component.graphics is missing or has no silkscreen items, so the user
     * never sees "just pads floating with no body".
     */
    _drawFallbackComponentOutline(ctx, component) {
        const pads = component.pads || [];
        if (!pads.length) return;
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        for (const pad of pads) {
            const c = getComponentPadPosition(component, pad);
            const hw = (pad.width || 1) / 2 + 0.25; // 0.25mm silkscreen expansion
            const hh = (pad.height || 1) / 2 + 0.25;
            if (c.x - hw < minX) minX = c.x - hw;
            if (c.x + hw > maxX) maxX = c.x + hw;
            if (c.y - hh < minY) minY = c.y - hh;
            if (c.y + hh > maxY) maxY = c.y + hh;
        }
        if (!Number.isFinite(minX)) return;
        const a = this.worldToScreen(minX, minY);
        const b = this.worldToScreen(maxX, maxY);
        const rectW = b.x - a.x;
        const rectH = b.y - a.y;

        ctx.save();

        const sx = Math.min(a.x, b.x);
        const sy = Math.min(a.y, b.y);
        const sw = Math.abs(rectW);
        const sh = Math.abs(rectH);

        // 1. Filled component body (dark, semi-transparent — like an IC package)
        ctx.fillStyle = 'rgba(18, 22, 30, 0.55)';
        ctx.fillRect(sx, sy, sw, sh);

        // 2. Solid silkscreen outline (not dashed — dashed looks like "unplaced")
        ctx.strokeStyle = '#e0f0ed';
        ctx.lineWidth = Math.max(this._worldRadiusToPixels(0.15), 1.5);
        ctx.lineJoin = 'round';
        ctx.lineCap = 'round';
        ctx.globalAlpha = 0.9;
        ctx.strokeRect(sx, sy, sw, sh);

        // 3. Pin 1 marker — small filled circle near pad 1
        const pin1Pad = pads.find(p => String(p.number) === '1') || pads[0];
        if (pin1Pad) {
            const pin1Center = getComponentPadPosition(component, pin1Pad);
            const pin1Screen = this.worldToScreen(pin1Center.x, pin1Center.y);
            const markerR = Math.max(this._worldRadiusToPixels(0.22), 3);
            ctx.globalAlpha = 0.9;
            ctx.fillStyle = '#4df1c2';
            ctx.beginPath();
            ctx.arc(pin1Screen.x, pin1Screen.y, markerR, 0, Math.PI * 2);
            ctx.fill();
        }

        ctx.setLineDash([]);
        ctx.restore();
    }

    _lineDashForStyle(lineStyle, lineWidthPx) {
        if (!lineStyle || lineStyle === 'solid' || lineStyle === 'default') return [];
        const dashLen = Math.max(11 * lineWidthPx, 2);
        const gapLen = Math.max(4 * lineWidthPx, 2);
        const dotLen = Math.max(0.2 * lineWidthPx, 1);
        switch (lineStyle) {
            case 'dash': return [dashLen, gapLen];
            case 'dot': return [dotLen, gapLen];
            case 'dash_dot': return [dashLen, gapLen, dotLen, gapLen];
            case 'dash_dot_dot': return [dashLen, gapLen, dotLen, gapLen, dotLen, gapLen];
            default: return [];
        }
    }

    _drawGraphicItem(ctx, component, item, strokeStyle, alpha, dashed = false, fillStyle = null) {
        ctx.save();
        ctx.globalAlpha = alpha;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        const strokeWidth = item.width || 0.12;
        ctx.lineWidth = this._worldRadiusToPixels(strokeWidth);
        const dash = this._lineDashForStyle(item.line_style || (dashed ? 'dash' : 'solid'), ctx.lineWidth);
        if (dash.length) ctx.setLineDash(dash);
        ctx.strokeStyle = strokeStyle;
        
        // KiCad fill can be boolean ('yes'/'no'), a color string, or 'none'.
        const fill = item.fill;
        const isFilled = fill === true || fill === 'yes' || fill === 'outline' ||
            (typeof fill === 'string' && fill !== 'none' && fill !== 'no');
        const effectiveFillStyle = fillStyle || strokeStyle;

        if (item.kind === 'fp_circle') {
            const center = this._transformGraphicPoints(component, [item.center])[0];
            const screenCenter = this.worldToScreen(center.x, center.y);
            const radiusWorld = Math.hypot(item.end.x - item.center.x, item.end.y - item.center.y);
            const radiusScreen = this._worldRadiusToPixels(radiusWorld);
            ctx.beginPath();
            if (isFilled) {
                ctx.arc(screenCenter.x, screenCenter.y, radiusScreen + (ctx.lineWidth / 2), 0, 2 * Math.PI);
                ctx.fillStyle = effectiveFillStyle;
                ctx.fill();
            }
            ctx.arc(screenCenter.x, screenCenter.y, radiusScreen, 0, 2 * Math.PI);
            ctx.stroke();
            ctx.restore();
            return;
        }

        const points = this._componentGraphicPoints(component, item);
        if (points.length < 2) {
            ctx.restore();
            return;
        }
        
        if (isFilled && (item.kind === 'fp_rect' || item.kind === 'fp_poly')) {
            ctx.beginPath();
            this._screenPathFromWorldPoints(ctx, points);
            ctx.closePath();
            ctx.fillStyle = effectiveFillStyle;
            ctx.fill();
        }
        
        if (!isFilled || item.kind !== 'fp_rect' || strokeStyle !== fillStyle) {
            ctx.beginPath();
            this._screenPathFromWorldPoints(ctx, points);
            ctx.stroke();
        }
        ctx.restore();
    }

    _componentGraphicPoints(component, item) {
        if (!item) return [];
        if (item.kind === 'fp_rect' && item.start && item.end) {
            return this._transformGraphicPoints(component, [
                item.start,
                { x: item.end.x, y: item.start.y },
                item.end,
                { x: item.start.x, y: item.end.y },
                item.start,
            ]);
        }
        if (item.kind === 'fp_poly') {
            const points = (item.points || []).slice();
            if (points.length) points.push(points[0]);
            return this._transformGraphicPoints(component, points);
        }
        if (item.kind === 'fp_circle' && item.center && item.end) {
            // Drawn using arc directly in _drawGraphicItem
            return [];
        }
        if (item.kind === 'fp_arc' && item.start && item.mid && item.end) {
            return this._transformGraphicPoints(component, arcPoints(item.start, item.mid, item.end, 32));
        }
        const points = [];
        if (item.start) points.push(item.start);
        if (item.end) points.push(item.end);
        return this._transformGraphicPoints(component, points);
    }

    _transformGraphicPoints(component, points) {
        return (points || []).map((point) => {
            const rotated = rotatePoint(point.x || 0, point.y || 0, -(component.rotation || 0));
            return {
                x: component.x + rotated.x,
                y: component.y + rotated.y,
            };
        });
    }

    _drawBoardTexts(ctx, model) {
        const scale = pcbState.baseScale * pcbState.zoom;
        ctx.save();
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        for (const component of model.components || []) {
            this._drawComponentPadLabelsCanvas(ctx, component, scale);
            this._drawComponentTextsCanvas(ctx, component, scale);
        }
        ctx.restore();
    }

    _drawComponentPadLabelsCanvas(ctx, component, scale) {
        for (const pad of component.pads || []) {
            const padWidth = pad.width || 1;
            const padHeight = pad.height || 1;
            const padMaxDim = Math.max(padWidth, padHeight);
            const minZoom = padMaxDim >= 2.5 ? 2.1 : (padMaxDim >= 1.2 ? 2.6 : 3.4);
            const padScreenSize = this._worldRadiusToPixels(padMaxDim);
            const showNetName = padScreenSize >= 42 && pcbState.zoom >= minZoom + 0.35;
            if (pad.number == null || padMaxDim < 0.4 || pcbState.zoom < minZoom) continue;
            const center = getComponentPadPosition(component, pad);
            const padRotation = (component.rotation || 0) + (pad.rotation || 0);
            let maxWidth = padWidth;
            let textRotated = false;
            let maxFontSize = padHeight;
            if (padWidth < padHeight * 0.95) {
                textRotated = true;
                maxWidth = padHeight;
                maxFontSize = padWidth;
            }
            maxFontSize = Math.min(maxFontSize, 1.8);
            const netName = getNetNameForPad(pcbState.boardModel, component.ref, pad.number);
            const hasNet = showNetName && netName && netName !== '_manual' && netName !== '';
            const padNumText = String(pad.number);
            let yOffsetPadNet = 0;
            let yOffsetPadNum = 0;
            let fontPx = this._worldRadiusToPixels(maxFontSize * 0.55);
            if (hasNet && padNumText !== '') {
                fontPx = this._worldRadiusToPixels((maxFontSize / 2.8) * 0.9);
                yOffsetPadNet = maxFontSize / 3.4;
                yOffsetPadNum = -maxFontSize / 3.4;
            }
            const maxTextLength = Math.max(padNumText.length, hasNet ? netName.length : 0, 3);
            const widthScale = maxWidth / (maxTextLength * 0.45);
            const textScale = Math.min(1, widthScale * 1.1);
            let textRot = padRotation + (textRotated ? 90 : 0);
            while (textRot > 90) textRot -= 180;
            while (textRot <= -90) textRot += 180;
            this._drawWorldText(ctx, padNumText, center.x, center.y, {
                rotation: textRot,
                fontPx: Math.max(fontPx * textScale, 9),
                fill: '#ffffff',
                shadow: 'rgba(0, 0, 0, 0.85)',
                offsetWorldY: yOffsetPadNum,
            });
            if (hasNet) {
                this._drawWorldText(ctx, netName, center.x, center.y, {
                    rotation: textRot,
                    fontPx: Math.max(fontPx * textScale, 7),
                    fill: '#ffffff',
                    shadow: 'rgba(0, 0, 0, 0.85)',
                    offsetWorldY: yOffsetPadNet,
                });
            }
        }
    }

    _drawComponentTextsCanvas(ctx, component, scale) {
        const graphics = component.graphics || [];
        for (const item of graphics) {
            if ((item.kind !== 'property' && item.kind !== 'fp_text') || item.hidden) continue;
            if (!isPcbLayerVisible(item.layer)) continue;
            const point = this._transformGraphicPoints(component, [{ x: item.x || 0, y: item.y || 0 }])[0];
            const rotation = (component.rotation || 0) + (item.rotation || 0);
            
            // Stroke font (Hershey vector) rendering for KiCad-style text
            const sizeWorld = Math.max(item.size || 1.0, 0.4);
            const fontPx = Math.max(this._worldRadiusToPixels(sizeWorld), 3);
            if (fontPx < 5) continue; // Too small to render nicely
            
            this._drawWorldText(ctx, item.text || '', point.x, point.y, {
                rotation,
                fontPx: fontPx,
                fill: item.layer === 'F.Fab' || item.layer === 'B.Fab' ? '#8eb0aa' : '#e0f0ed',
                shadow: 'rgba(0, 0, 0, 0.75)',
            });
        }
        const hasRefText = graphics.some((g) => g.kind === 'property' && g.name === 'Reference' && !g.hidden);
        const hasValueText = graphics.some((g) => g.kind === 'property' && g.name === 'Value' && !g.hidden);
        const bounds = getComponentBounds(component);
        const height = Math.max(bounds.maxY - bounds.minY, 2.5);
        const compScreenHeight = this._worldRadiusToPixels(height);
        let textRot = component.rotation || 0;
        while (textRot > 90) textRot -= 180;
        while (textRot <= -90) textRot += 180;
        // Show ref designators at lower zoom thresholds so the user always sees labels
        // (the original 2.2x / 28px threshold hid refs on dense boards, matching the
        // "orphan pads with no label" symptom in the screenshots).
        const showFallbackRef = isPcbLayerVisible('F.SilkS') || isPcbLayerVisible('B.SilkS') || isPcbLayerVisible('F.Fab') || isPcbLayerVisible('B.Fab') || isPcbLayerVisible('F.Cu') || isPcbLayerVisible('B.Cu');
        if (!hasRefText && showFallbackRef && (pcbState.zoom >= 0.5) && compScreenHeight >= 10) {
            const refOffset = rotatePoint(0, -Math.max(height / 2, 2.0), component.rotation || 0);
            this._drawWorldText(ctx, component.ref || '', component.x + refOffset.x, component.y + refOffset.y, {
                rotation: textRot,
                fontPx: Math.max(this._worldRadiusToPixels(0.9), 10),
                fill: '#f2f5f4',
                shadow: 'rgba(0, 0, 0, 0.85)',
                fontWeight: '700',
            });
        }
        if (!hasValueText && showFallbackRef && pcbState.zoom >= 1.0 && compScreenHeight >= 18) {
            const valueText = component.value || compactFootprintName(component.footprint);
            if (valueText && valueText !== component.ref) {
                const valueOffset = rotatePoint(0, Math.max(height / 2, 2.4), component.rotation || 0);
                this._drawWorldText(ctx, valueText, component.x + valueOffset.x, component.y + valueOffset.y, {
                    rotation: textRot,
                    fontPx: Math.max(this._worldRadiusToPixels(0.65), 10),
                    fill: '#91aaa4',
                    shadow: 'rgba(0, 0, 0, 0.75)',
                });
            }
        }
    }

    _drawWorldText(ctx, text, worldX, worldY, options = {}) {
        if (!text) return;
        const offsetRot = rotatePoint(0, options.offsetWorldY || 0, options.rotation || 0);
        const pt = this.worldToScreen(worldX + offsetRot.x, worldY + offsetRot.y);
        
        let rotation = options.rotation || 0;
        while (rotation > 90) rotation -= 180;
        while (rotation <= -90) rotation += 180;
        
        const fontPx = Math.max(options.fontPx || 10, 6);
        const sf = getStrokeFont();
        sf.renderText(ctx, text, pt.x, pt.y, fontPx, {
            rotation,
            color: options.fill || '#e9f7f4',
            strokeWidth: Math.max(fontPx / 10, 0.8),
            halign: 'center',
            valign: 'middle',
        });
    }

    _strokeWorldPath(ctx, points, widthWorld, strokeStyle, alpha = 1) {
        if (!points || points.length < 2) return;
        ctx.save();
        ctx.strokeStyle = strokeStyle;
        ctx.globalAlpha = alpha;
        ctx.lineWidth = this._worldRadiusToPixels(widthWorld);
        ctx.lineCap = 'round';
        ctx.lineJoin = 'miter';
        ctx.beginPath();
        this._screenPathFromWorldPoints(ctx, points);
        ctx.stroke();
        ctx.restore();
    }

    _screenSmoothPath(ctx, points) {
        if (!points.length) return;
        const start = this.worldToScreen(points[0].x, points[0].y);
        ctx.moveTo(start.x, start.y);
        if (points.length === 2) {
            const end = this.worldToScreen(points[1].x, points[1].y);
            ctx.lineTo(end.x, end.y);
            return;
        }
        for (let i = 1; i < points.length - 1; i++) {
            const curr = this.worldToScreen(points[i].x, points[i].y);
            const next = this.worldToScreen(points[i + 1].x, points[i + 1].y);
            const midX = (curr.x + next.x) / 2;
            const midY = (curr.y + next.y) / 2;
            ctx.quadraticCurveTo(curr.x, curr.y, midX, midY);
        }
        const last = this.worldToScreen(points[points.length - 1].x, points[points.length - 1].y);
        ctx.lineTo(last.x, last.y);
    }

    _screenPathFromWorldPoints(ctx, points) {
        if (!points.length) return;
        const start = this.worldToScreen(points[0].x, points[0].y);
        ctx.moveTo(start.x, start.y);
        for (let i = 1; i < points.length; i++) {
            const pt = this.worldToScreen(points[i].x, points[i].y);
            ctx.lineTo(pt.x, pt.y);
        }
    }

    _fillPadShape(ctx, center, width, height, shape, rotation, fillStyle, alpha = 1, roundrectRratio = 0.25, strokeStyle = null, rectDeltaX = 0, rectDeltaY = 0) {
        ctx.save();
        ctx.globalAlpha = alpha;
        
        // Transform ctx for the pad
        const pt = this.worldToScreen(center.x, center.y);
        ctx.translate(pt.x, pt.y);
        ctx.rotate(-((rotation || 0) * Math.PI / 180));
        
        const wWorld = Math.max(width, 0.05);
        const hWorld = Math.max(height, 0.05);
        const wScreen = this._worldRadiusToPixels(wWorld) * 2; // diameter
        const hScreen = this._worldRadiusToPixels(hWorld) * 2;

        // Border width scales with pad size so it remains proportional at any zoom.
        const borderPx = Math.max(0.5, Math.min(wScreen, hScreen) * 0.08);
        ctx.fillStyle = fillStyle;
        if (strokeStyle) {
            ctx.strokeStyle = strokeStyle;
            ctx.lineWidth = borderPx;
        }

        ctx.beginPath();
        if (shape === 'circle') {
            const r = Math.max(wScreen, hScreen) / 2;
            ctx.arc(0, 0, r, 0, Math.PI * 2);
            ctx.fill();
            if (strokeStyle) ctx.stroke();
        } else if (shape === 'oval') {
            // KiCanvas draws oval as a thick line with round caps (KiCad convention)
            const halfSizeX = wScreen / 2;
            const halfSizeY = hScreen / 2;
            const halfWidth = Math.min(halfSizeX, halfSizeY);
            const halfLenX = halfSizeX - halfWidth;
            const halfLenY = halfSizeY - halfWidth;

            if (Math.abs(halfLenX) < 0.5 && Math.abs(halfLenY) < 0.5) {
                ctx.arc(0, 0, halfWidth, 0, Math.PI * 2);
                ctx.fill();
                if (strokeStyle) ctx.stroke();
            } else {
                ctx.lineCap = 'round';
                ctx.lineJoin = 'round';
                ctx.lineWidth = halfWidth * 2;
                ctx.beginPath();
                ctx.moveTo(-halfLenX, -halfLenY);
                ctx.lineTo(halfLenX, halfLenY);
                ctx.stroke();
                if (strokeStyle) {
                    ctx.lineWidth = borderPx;
                    ctx.strokeStyle = strokeStyle;
                    ctx.stroke();
                }
            }
        } else if (shape === 'roundrect' || shape === 'trapezoid') {
            // KiCanvas roundrect/trapezoid exact port
            const rounding = Math.min(wScreen, hScreen) * (roundrectRratio != null ? roundrectRratio : 0.25);
            const halfSizeX = (wScreen / 2) - rounding;
            const halfSizeY = (hScreen / 2) - rounding;

            // KiCanvas: trap_delta = rect_delta * 0.5 (world units)
            // In screen pixels: trap_delta_world * baseScale * zoom * 2 = rect_delta * baseScale * zoom
            // worldRadiusToPixels(value) = value * baseScale * zoom (for typical values)
            const td_x = this._worldRadiusToPixels(rectDeltaX || 0);  // affects Y
            const td_y = this._worldRadiusToPixels(rectDeltaY || 0);  // affects X

            // KiCanvas corner order with swapped X/Y delta:
            // Corner 0: (-hs.x - td.y,  hs.y + td.x)
            // Corner 1: ( hs.x + td.y,  hs.y - td.x)
            // Corner 2: ( hs.x - td.y, -hs.y + td.x)
            // Corner 3: (-hs.x + td.y, -hs.y - td.x)
            const rectPoints = [
                { x: -halfSizeX - td_y, y:  halfSizeY + td_x },
                { x:  halfSizeX + td_y, y:  halfSizeY - td_x },
                { x:  halfSizeX - td_y, y: -halfSizeY + td_x },
                { x: -halfSizeX + td_y, y: -halfSizeY - td_x },
            ];

            ctx.beginPath();
            ctx.moveTo(rectPoints[0].x, rectPoints[0].y);
            for (let i = 1; i < 4; i++) {
                ctx.lineTo(rectPoints[i].x, rectPoints[i].y);
            }
            ctx.closePath();
            ctx.fill();

            // Roundrect rounded corners via thick stroke
            if (rounding > 0) {
                ctx.lineCap = 'round';
                ctx.lineJoin = 'round';
                ctx.lineWidth = rounding * 2;
                ctx.strokeStyle = fillStyle;
                ctx.stroke();
            }

            if (strokeStyle) {
                ctx.lineWidth = borderPx;
                ctx.strokeStyle = strokeStyle;
                ctx.stroke();
            }
        } else {
            // KiCanvas draws rect as filled polygon (4 corners), no extra stroke
            const hw = wScreen / 2;
            const hh = hScreen / 2;
            ctx.beginPath();
            ctx.moveTo(-hw, -hh);
            ctx.lineTo( hw, -hh);
            ctx.lineTo( hw,  hh);
            ctx.lineTo(-hw,  hh);
            ctx.closePath();
            ctx.fill();
            if (strokeStyle) {
                ctx.lineWidth = borderPx;
                ctx.strokeStyle = strokeStyle;
                ctx.stroke();
            }
        }
        
        ctx.restore();
    }

    _fillPadDrill(ctx, center, drillDiameter, drillWidth = null, rotation = 0) {
        ctx.save();
        const wWorld = Math.max(drillDiameter || 0, 0.05);
        const hWorld = Math.max(drillWidth || drillDiameter || 0, 0.05);
        const pt = this.worldToScreen(center.x, center.y);
        
        if (Math.abs(wWorld - hWorld) < 0.001) {
            const r = Math.max(this._worldRadiusToPixels(wWorld / 2), 1.5);
            // 1. Subtle copper annular ring around the hole (KiCad-like).
            ctx.beginPath();
            ctx.strokeStyle = 'rgba(255, 215, 170, 0.45)';
            ctx.lineWidth = Math.max(1, r * 0.18);
            ctx.arc(pt.x, pt.y, r + ctx.lineWidth / 2, 0, Math.PI * 2);
            ctx.stroke();
            // 2. Dark drill hole punched through the copper.
            ctx.beginPath();
            ctx.fillStyle = '#0b1116';
            ctx.arc(pt.x, pt.y, r, 0, Math.PI * 2);
            ctx.fill();
            // 3. Inner shadow for depth.
            if (r > 2) {
                ctx.beginPath();
                ctx.strokeStyle = 'rgba(0, 0, 0, 0.7)';
                ctx.lineWidth = 1;
                ctx.arc(pt.x, pt.y, Math.max(r - 0.5, 0.5), 0, Math.PI * 2);
                ctx.stroke();
            }
        } else {
            ctx.translate(pt.x, pt.y);
            ctx.rotate(-((rotation || 0) * Math.PI / 180));
            
            const wScreen = this._worldRadiusToPixels(wWorld) * 2;
            const hScreen = this._worldRadiusToPixels(hWorld) * 2;
            const halfSizeX = wScreen / 2;
            const halfSizeY = hScreen / 2;
            const halfWidth = Math.min(halfSizeX, halfSizeY);
            const halfLenX = halfSizeX - halfWidth;
            const halfLenY = halfSizeY - halfWidth;

            // Copper annular ring (slot version) — slightly outside the hole.
            ctx.lineCap = 'round';
            ctx.lineWidth = halfWidth * 2 + 2;
            ctx.strokeStyle = 'rgba(255, 215, 170, 0.35)';
            ctx.beginPath();
            ctx.moveTo(-halfLenX, -halfLenY);
            ctx.lineTo(halfLenX, halfLenY);
            ctx.stroke();
            // Dark drill slot.
            ctx.lineWidth = halfWidth * 2;
            ctx.strokeStyle = '#0b1116';
            ctx.beginPath();
            ctx.moveTo(-halfLenX, -halfLenY);
            ctx.lineTo(halfLenX, halfLenY);
            ctx.stroke();
        }
        ctx.restore();
    }

    _padShapePath(center, width, height, shape, rotation, roundrectRratio = 0.25, rectDeltaX = 0, rectDeltaY = 0) {
        // No longer used, replaced by direct drawing in _fillPadShape
        return [];
    }

    _worldRadiusToPixels(value) {
        return Math.max(Math.abs(value) * pcbState.baseScale * pcbState.zoom, 0.75);
    }

    _roundRectPath(ctx, x, y, width, height, radius) {
        const r = Math.max(Math.min(radius, Math.abs(width) / 2, Math.abs(height) / 2), 0);
        ctx.beginPath();
        ctx.moveTo(x + r, y);
        ctx.arcTo(x + width, y, x + width, y + height, r);
        ctx.arcTo(x + width, y + height, x, y + height, r);
        ctx.arcTo(x, y + height, x, y, r);
        ctx.arcTo(x, y, x + width, y, r);
        ctx.closePath();
    }

    _hexToCss(color) {
        // Robust to numeric (0xRRGGBB), string ('RRGGBB'), or already-CSS ('#RRGGBB') inputs.
        // The old implementation did Number(color).toString(16) which returned 'NaN' for strings,
        // producing invalid '#NaN' colors and silently breaking trace rendering.
        if (color == null) return '#c87533'; // sensible copper fallback
        if (typeof color === 'number' && Number.isFinite(color)) {
            return `#${(color >>> 0).toString(16).padStart(6, '0').slice(-6)}`;
        }
        if (typeof color === 'string') {
            const s = color.trim();
            if (/^#[0-9a-fA-F]{6}$/.test(s)) return s;
            if (/^[0-9a-fA-F]{6}$/.test(s)) return `#${s}`;
            if (/^#[0-9a-fA-F]{3}$/.test(s)) return `#${s[1]}${s[1]}${s[2]}${s[2]}${s[3]}${s[3]}`;
            return s; // already CSS (rgb(), named, etc.)
        }
        return '#c87533';
    }

    // --- Hit Testing ---
    hitTestPad(screenX, screenY) {
        const world = this.screenToWorld(screenX, screenY);
        const model = pcbState.boardModel;
        if (!model) return null;
        
        for (const comp of model.components || []) {
            for (const pad of comp.pads || []) {
                const center = getComponentPadPosition(comp, pad);
                const w = pad.width || 1;
                const h = pad.height || 1;
                const tolerance = Math.max(0.9 / (pcbState.baseScale * pcbState.zoom), 0.01);
                if (Math.abs(world.x - center.x) <= w/2 + tolerance && Math.abs(world.y - center.y) <= h/2 + tolerance) {
                    const padId = pad.number != null ? pad.number : (pad.name != null ? pad.name : '');
                    return { pad, component: comp, key: `${comp.ref}:${padId}`, x: center.x, y: center.y };
                }
            }
        }
        return null;
    }

    hitTestTrace(screenX, screenY) {
        const world = this.screenToWorld(screenX, screenY);
        const model = pcbState.boardModel;
        if (!model) return null;

        for (let ti = (model.traces || []).length - 1; ti >= 0; ti--) {
            const trace = model.traces[ti];
            const path = trace.path || [];
            const w = Math.max(trace.width || 0.254, 0.2);
            for (let i = 0; i < path.length - 1; i++) {
                const p1 = path[i];
                const p2 = path[i+1];

                const l2 = (p2.x-p1.x)**2 + (p2.y-p1.y)**2;
                let t = 0;
                if (l2 > 0) {
                    t = Math.max(0, Math.min(1, ((world.x - p1.x)*(p2.x - p1.x) + (world.y - p1.y)*(p2.y - p1.y)) / l2));
                }
                const projX = p1.x + t * (p2.x - p1.x);
                const projY = p1.y + t * (p2.y - p1.y);
                const dist = Math.hypot(world.x - projX, world.y - projY);

                if (dist <= w/2 + 0.1) {
                    return { trace, traceIndex: ti, segmentIndex: i, x: projX, y: projY };
                }
            }
        }
        return null;
    }

    hitTestVia(screenX, screenY) {
        const world = this.screenToWorld(screenX, screenY);
        const model = pcbState.boardModel;
        if (!model) return null;
        
        const vias = model.vias || [];
        for (let i = vias.length - 1; i >= 0; i--) {
            const via = vias[i];
            const dist = Math.hypot(world.x - via.x, world.y - via.y);
            const radius = Math.max(via.diameter || 0.6, 0.6) / 2.0;
            if (dist <= radius + 0.1) {
                return { via, index: i };
            }
        }
        return null;
    }

    hitTestComponent(screenX, screenY) {
        const world = this.screenToWorld(screenX, screenY);
        const model = pcbState.boardModel;
        if (!model) return null;
        
        for (const comp of model.components || []) {
            const bounds = getComponentBounds(comp);
            if (world.x >= bounds.minX && world.x <= bounds.maxX &&
                world.y >= bounds.minY && world.y <= bounds.maxY) {
                return {
                    ref: comp.ref,
                    x: comp.x,
                    y: comp.y,
                    component: comp,
                };
            }
        }
        return null;
    }

    _sessionUrl(path) {
        const sid = (window.circuitbotChatSessionId || '').trim();
        return sid ? path + '?session_id=' + encodeURIComponent(sid) : path;
    }

    async saveBoardModel() {
        if (!pcbState.boardModel) return false;
        const res = await fetch(this._sessionUrl('/api/save_board_model'), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ board_model: pcbState.boardModel }),
        });
        if (!res.ok) {
            throw new Error(`save_board_model failed (${res.status})`);
        }
        return true;
    }

    pushHistory(name, before, after) {
        this._history.push({ name, before, after });
        this._redoStack = [];
    }
    
    async undo() {
        if (this._history.length > 0) {
            const entry = this._history.pop();
            if (!this._redoStack) this._redoStack = [];
            this._redoStack.push(entry);
            pcbState.boardModel = entry.before;
            pcbState.ratsnest = this._computeClientRatsnest(pcbState.boardModel);
            this._buildBuffers();
            this.refresh();
            await this.saveBoardModel();
        }
    }
    async redo() {
        if (this._redoStack && this._redoStack.length > 0) {
            const entry = this._redoStack.pop();
            this._history.push(entry);
            pcbState.boardModel = entry.after;
            pcbState.ratsnest = this._computeClientRatsnest(pcbState.boardModel);
            this._buildBuffers();
            this.refresh();
            await this.saveBoardModel();
        }
    }

    refreshAirwires() {
        if (!pcbState.boardModel) {
            pcbState.ratsnest = {};
            this.requestOverlayRefresh();
            return {};
        }
        pcbState.ratsnest = this._computeClientRatsnest(pcbState.boardModel);
        this.requestOverlayRefresh();
        return pcbState.ratsnest;
    }

    _computeClientRatsnest(boardModel) {
        const model = normalizeBoardModel(boardModel || { components: [], traces: [], vias: [], nets: [] });
        const result = {};
        const nets = Array.isArray(model.nets) ? model.nets : [];
        const TOLERANCE = 0.2; // 0.2mm tolerance for matching trace endpoints to pad positions (must exceed half the grid step)

        for (const netEntry of nets) {
            const netName = netEntry.name || netEntry.net || '';
            const pinKeys = Array.isArray(netEntry.pins) ? netEntry.pins : [];
            if (!netName || pinKeys.length < 2) continue;
            const positions = [];
            for (const pinKey of pinKeys) {
                const pos = getPadPositionByPinKey(model, pinKey);
                if (pos) positions.push({ pinKey, pos });
            }
            if (positions.length < 2) continue;

            // Build adjacency from traces with tolerance-based matching
            const adjacency = new Map();
            const posKeys = positions.map((entry, idx) => ({
                idx,
                key: `${Number(entry.pos.x).toFixed(2)},${Number(entry.pos.y).toFixed(2)}`,
                x: entry.pos.x,
                y: entry.pos.y
            }));

            // Helper to find which pad position a point is close to
            const findClosestPadKey = (point) => {
                const px = Number(point.x);
                const py = Number(point.y);
                for (const pk of posKeys) {
                    if (Math.abs(pk.x - px) < TOLERANCE && Math.abs(pk.y - py) < TOLERANCE) {
                        return pk.key;
                    }
                }
                return null;
            };

            for (const trace of model.traces || []) {
                if (String(trace.net || '').toUpperCase() !== String(netName).toUpperCase()) continue;
                const path = Array.isArray(trace.path) ? trace.path : [];
                if (path.length < 2) continue;
                const start = path[0];
                const end = path[path.length - 1];

                // Use tolerance-based matching to find closest pad positions
                const startKey = findClosestPadKey(start);
                const endKey = findClosestPadKey(end);

                if (startKey && endKey && startKey !== endKey) {
                    if (!adjacency.has(startKey)) adjacency.set(startKey, []);
                    if (!adjacency.has(endKey)) adjacency.set(endKey, []);
                    adjacency.get(startKey).push(endKey);
                    adjacency.get(endKey).push(startKey);
                }
            }

            const pointToIndices = new Map();
            positions.forEach((entry, index) => {
                const key = `${Number(entry.pos.x).toFixed(2)},${Number(entry.pos.y).toFixed(2)}`;
                if (!pointToIndices.has(key)) pointToIndices.set(key, []);
                pointToIndices.get(key).push(index);
            });
            const groups = new Array(positions.length).fill(-1);
            let groupId = 0;
            for (let index = 0; index < positions.length; index += 1) {
                if (groups[index] !== -1) continue;
                const seed = positions[index];
                const seedKey = `${Number(seed.pos.x).toFixed(2)},${Number(seed.pos.y).toFixed(2)}`;
                const stack = [seedKey];
                const visited = new Set();
                while (stack.length) {
                    const point = stack.pop();
                    if (visited.has(point)) continue;
                    visited.add(point);
                    for (const padIndex of pointToIndices.get(point) || []) {
                        groups[padIndex] = groupId;
                    }
                    for (const neighbor of adjacency.get(point) || []) {
                        if (!visited.has(neighbor)) stack.push(neighbor);
                    }
                }
                if (groups[index] === -1) groups[index] = groupId;
                groupId += 1;
            }
            const uniqueGroups = Array.from(new Set(groups));
            if (uniqueGroups.length < 2) continue;
            const representatives = [];
            for (const group of uniqueGroups) {
                const repIndex = groups.findIndex((value) => value === group);
                if (repIndex >= 0) representatives.push(repIndex);
            }
            const edges = [];
            for (let a = 0; a < representatives.length; a += 1) {
                const pa = positions[representatives[a]];
                for (let b = a + 1; b < representatives.length; b += 1) {
                    const pb = positions[representatives[b]];
                    edges.push({
                        a,
                        b,
                        dist: Math.hypot(pa.pos.x - pb.pos.x, pa.pos.y - pb.pos.y),
                    });
                }
            }
            edges.sort((left, right) => left.dist - right.dist);
            const parent = representatives.map((_, index) => index);
            const rank = representatives.map(() => 0);
            const find = (value) => {
                let current = value;
                while (parent[current] !== current) {
                    parent[current] = parent[parent[current]];
                    current = parent[current];
                }
                return current;
            };
            const unite = (left, right) => {
                const rootLeft = find(left);
                const rootRight = find(right);
                if (rootLeft === rootRight) return false;
                if (rank[rootLeft] < rank[rootRight]) parent[rootLeft] = rootRight;
                else if (rank[rootLeft] > rank[rootRight]) parent[rootRight] = rootLeft;
                else {
                    parent[rootRight] = rootLeft;
                    rank[rootLeft] += 1;
                }
                return true;
            };
            const netEdges = [];
            for (const edge of edges) {
                if (!unite(edge.a, edge.b)) continue;
                const from = positions[representatives[edge.a]];
                const to = positions[representatives[edge.b]];
                netEdges.push({
                    from: from.pinKey,
                    to: to.pinKey,
                    x1: from.pos.x,
                    y1: from.pos.y,
                    x2: to.pos.x,
                    y2: to.pos.y,
                });
            }
            if (netEdges.length) result[netName] = netEdges;
        }
        return result;
    }

    async fetchRatsnest() {
        if (!pcbState.boardModel) {
            pcbState.ratsnest = {};
            return {};
        }
        let serverRatsnest = null;
        try {
            const response = await fetch('/api/ratsnest', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(pcbState.boardModel),
            });
            if (!response.ok) {
                throw new Error(`ratsnest failed (${response.status})`);
            }
            serverRatsnest = await response.json();
        } catch (_) {
            serverRatsnest = null;
        }
        const clientRatsnest = this._computeClientRatsnest(pcbState.boardModel);
        pcbState.ratsnest = Object.keys(clientRatsnest).length ? clientRatsnest : (serverRatsnest || {});
        this.requestOverlayRefresh();
        return pcbState.ratsnest;
    }
}

window.PcbEditorWebGL = PcbEditorWebGL;