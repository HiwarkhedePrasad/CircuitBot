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
        
        this._resizeHandler = () => this._resize();
        this._refreshFrame = null;
        this._settleRefreshTimer = null;
        this._history = [];
        
        // Matrices
        this._projectionMatrix = mat3.create();
        this._viewMatrix = mat3.create();
        this._viewProjectionMatrix = mat3.create();

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
        this._syncOverlayVisibility();

        window.addEventListener('resize', this._resizeHandler);
        
        this._initShaders();
        this._resize();
    }

    destroy() {
        if (!this._gl) return;
        window.removeEventListener('resize', this._resizeHandler);
        if (this._refreshFrame) {
            cancelAnimationFrame(this._refreshFrame);
            this._refreshFrame = null;
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
                float gridX = fract(worldPos.x / 1.27);
                float gridY = fract(worldPos.y / 1.27);
                if (gridX < 0.04 && gridY < 0.04) {
                    gl_FragColor = vec4(0.3, 0.4, 0.5, 0.6);
                } else {
                    gl_FragColor = vec4(0.043, 0.066, 0.086, 1.0);
                }
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
        
        gl.enable(gl.BLEND);
        gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
        
        gl.clearColor(0.043, 0.066, 0.086, 1.0);
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
        
        this._renderOverlay();
    }

    _renderOverlay() {
        const ctx = this._overlayCtx;
        const w = this._overlayCanvas.width;
        const h = this._overlayCanvas.height;
        ctx.clearRect(0, 0, w, h);

        const model = pcbState.boardModel;
        if (!model) return;
        this._drawBoardCanvas(ctx, model);
        this._drawAirwiresCanvas(ctx, model);
        this._drawBoardTexts(ctx, model);

        // Draw Active Route Cursor / Airwires
        if (pcbState.routePoints && pcbState.routePoints.length > 0) {
            const points = pcbState.routeCursor ? appendRoutePoint(pcbState.routePoints, pcbState.routeCursor) : pcbState.routePoints;
            this._strokeWorldPath(ctx, points, Math.max(pcbState.routeWidth || 0.25, 0.2), '#fff1a8', 0.95);
        }

        // Highlight Selected Component
        if (pcbState.selectedComponentRef) {
            const comp = model.components.find(c => c.ref === pcbState.selectedComponentRef);
            if (comp) {
                const bounds = getComponentBounds(comp);
                const tl = this.worldToScreen(bounds.minX, bounds.minY);
                const br = this.worldToScreen(bounds.maxX, bounds.maxY);
                ctx.strokeStyle = '#4df1c2';
                ctx.fillStyle = 'rgba(77, 241, 194, 0.08)';
                ctx.lineWidth = 2;
                this._roundRectPath(ctx, tl.x, tl.y, br.x - tl.x, br.y - tl.y, 10);
                ctx.fill();
                ctx.stroke();
            }
        }
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
        ctx.save();
        ctx.setLineDash([7, 6]);
        for (const netName of netNames) {
            const edges = ratsnest[netName] || [];
            for (const edge of edges) {
                const start = this._resolveAirwireEndpoint(model, edge, 'from', 'x1', 'y1');
                const end = this._resolveAirwireEndpoint(model, edge, 'to', 'x2', 'y2');
                if (!start || !end) continue;
                this._strokeWorldPath(ctx, [start, end], 0.24, '#ffffff', 0.52);
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
        this._drawTracesCanvas(ctx, model.traces || []);
        this._drawViasCanvas(ctx, model.vias || []);

        const components = model.components || [];

        // Pass 1: all solder mask expansions for every component (bottom layer)
        for (const component of components) {
            this._drawComponentMasksCanvas(ctx, component);
        }

        // Pass 2: all copper pads for every component (middle layer)
        for (const component of components) {
            this._drawComponentCopperCanvas(ctx, component);
        }

        // Pass 3: all drill holes for every component (top — punches through copper)
        for (const component of components) {
            this._drawComponentDrillsCanvas(ctx, component);
        }

        // Graphics (silkscreen, fab, courtyard) drawn last
        for (const component of components) {
            this._drawComponentGraphicsCanvas(ctx, component);
        }
    }

    _drawBoardOutline(ctx, model) {
        if (!isPcbLayerVisible('Edge.Cuts')) return;
        const segments = outlineSegments(model);
        if (!segments.length) return;
        ctx.save();
        ctx.strokeStyle = '#19d7b0';
        ctx.lineWidth = 1.5;
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
        if (segment.kind === 'gr_arc' && segment.start && segment.mid && segment.end) {
            return arcPoints(segment.start, segment.mid, segment.end, 28);
        }
        const points = [];
        if (segment.start) points.push(segment.start);
        if (segment.end) points.push(segment.end);
        return points;
    }

    _drawTracesCanvas(ctx, traces) {
        for (const trace of traces) {
            if (!isPcbLayerVisible(trace.layer || 'F.Cu')) continue;
            const path = trace.path || [];
            if (path.length < 2) continue;
            const color = this._hexToCss(copperColorForLayer(trace.layer));
            this._strokeWorldPath(ctx, path, Math.max(trace.width || 0.254, 0.14), color, 0.96);
        }
    }

    _drawViasCanvas(ctx, vias) {
        for (const via of vias) {
            const viaLayers = via.layers || ['F.Cu', 'B.Cu'];
            if (!viaLayers.some((layer) => isPcbLayerVisible(layer))) continue;
            const center = this.worldToScreen(via.x, via.y);
            const radius = this._worldRadiusToPixels(Math.max(via.diameter || 0.6, 0.6) / 2);
            const drill = this._worldRadiusToPixels(Math.max(via.drill || Math.max((via.diameter || 0.6) * 0.45, 0.2), 0.18) / 2);
            ctx.beginPath();
            ctx.fillStyle = '#dce8ef';
            ctx.arc(center.x, center.y, radius, 0, Math.PI * 2);
            ctx.fill();
            ctx.beginPath();
            ctx.fillStyle = '#071019';
            ctx.arc(center.x, center.y, drill, 0, Math.PI * 2);
            ctx.fill();
        }
    }

    _drawComponentCanvas(ctx, component) {
        this._drawComponentMasksCanvas(ctx, component);
        this._drawComponentCopperCanvas(ctx, component);
        this._drawComponentDrillsCanvas(ctx, component);
        this._drawComponentGraphicsCanvas(ctx, component);
    }

    _drawComponentMasksCanvas(ctx, component) {
        const pads = (component.pads || []).slice().sort((a, b) => ((b.width || 0) * (b.height || 0)) - ((a.width || 0) * (a.height || 0)));
        for (const pad of pads) {
            if (!(pad.layers || ['F.Cu']).some((layer) => isPcbLayerVisible(layer))) continue;
            const center = getComponentPadPosition(component, pad);
            const rotation = (component.rotation || 0) + (pad.rotation || 0);
            const width = pad.width || 1;
            const height = pad.height || 1;
            this._fillPadShape(ctx, center, width + 0.34, height + 0.34, pad.shape || 'rect', rotation, '#12392f', 0.92, pad.roundrect_rratio);
        }
    }

    _drawComponentCopperCanvas(ctx, component) {
        const pads = (component.pads || []).slice().sort((a, b) => ((b.width || 0) * (b.height || 0)) - ((a.width || 0) * (a.height || 0)));
        for (const pad of pads) {
            if (!(pad.layers || ['F.Cu']).some((layer) => isPcbLayerVisible(layer))) continue;
            const center = getComponentPadPosition(component, pad);
            const rotation = (component.rotation || 0) + (pad.rotation || 0);
            const width = pad.width || 1;
            const height = pad.height || 1;
            const isBottom = (pad.layers || []).some((layer) => isBottomCopperLayer(layer));
            const isThrough = pad.type === 'thru_hole' || pad.type === 'np_thru_hole' || pad.type === 'tht' || pad.drill;
            const copperColor = isThrough ? '#f2c8b8' : (isBottom ? '#5b93ff' : '#f27a6a');
            this._fillPadShape(ctx, center, width, height, pad.shape || 'rect', rotation, copperColor, 1, pad.roundrect_rratio, '#ffb199');
        }
    }

    _drawComponentDrillsCanvas(ctx, component) {
        for (const pad of component.pads || []) {
            const isThrough = pad.type === 'thru_hole' || pad.type === 'np_thru_hole' || pad.type === 'tht' || pad.drill;
            if (!isThrough) continue;
            if (!(pad.layers || ['F.Cu']).some((layer) => isPcbLayerVisible(layer))) continue;
            const center = getComponentPadPosition(component, pad);
            const width = pad.width || 1;
            const height = pad.height || 1;
            const drill = Math.max(pad.drill || Math.min(width, height) * 0.5, 0.2);
            this._fillPadDrill(ctx, center, drill);
        }
    }

    _drawComponentPadsCanvas(ctx, component) {
        // Legacy single-pass method (kept for compatibility)
        this._drawComponentMasksCanvas(ctx, component);
        this._drawComponentCopperCanvas(ctx, component);
        this._drawComponentDrillsCanvas(ctx, component);
    }

    _drawComponentGraphicsCanvas(ctx, component) {
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
        for (const item of grouped['F.CrtYd']) this._drawGraphicItem(ctx, component, item, '#3d7570', 0.7, true);
        for (const item of grouped['B.CrtYd']) this._drawGraphicItem(ctx, component, item, '#3d7570', 0.45, true);
        for (const item of grouped['F.Fab']) this._drawGraphicItem(ctx, component, item, '#8eb0aa', 0.9, false, 'rgba(58, 104, 96, 0.18)');
        for (const item of grouped['B.Fab']) this._drawGraphicItem(ctx, component, item, '#8eb0aa', 0.55, false, 'rgba(58, 104, 96, 0.12)');
        for (const item of grouped['F.SilkS']) this._drawGraphicItem(ctx, component, item, '#e0f0ed', 1, false, 'rgba(200, 230, 224, 0.12)');
        for (const item of grouped['B.SilkS']) this._drawGraphicItem(ctx, component, item, '#e0f0ed', 0.55, false, 'rgba(200, 230, 224, 0.08)');
    }

    _drawGraphicItem(ctx, component, item, strokeStyle, alpha, dashed = false, fillStyle = null) {
        const points = this._componentGraphicPoints(component, item);
        if (points.length < 2) return;
        ctx.save();
        ctx.globalAlpha = alpha;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.lineWidth = Math.max(this._worldRadiusToPixels(item.width || 0.15), dashed ? 1 : 1.2);
        ctx.strokeStyle = strokeStyle;
        ctx.setLineDash(dashed ? [7, 5] : []);
        if (fillStyle && (item.fill === 'solid' || item.fill === 'yes' || item.kind === 'fp_rect' || item.kind === 'fp_circle' || item.kind === 'fp_poly')) {
            ctx.beginPath();
            this._screenPathFromWorldPoints(ctx, points);
            ctx.closePath();
            ctx.fillStyle = fillStyle;
            ctx.fill();
        }
        ctx.beginPath();
        this._screenPathFromWorldPoints(ctx, points);
        ctx.stroke();
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
            const radius = Math.hypot(item.end.x - item.center.x, item.end.y - item.center.y);
            const points = [];
            for (let step = 0; step <= 40; step += 1) {
                const angle = (Math.PI * 2 * step) / 40;
                points.push({
                    x: item.center.x + Math.cos(angle) * radius,
                    y: item.center.y + Math.sin(angle) * radius,
                });
            }
            return this._transformGraphicPoints(component, points);
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
            const rotated = rotatePoint(point.x || 0, point.y || 0, component.rotation || 0);
            return {
                x: component.x + rotated.x,
                y: component.y + rotated.y,
            };
        });
    }

    _drawBoardTexts(ctx, model) {
        const scale = pcbState.baseScale * pcbState.zoom;
        if (pcbState.zoom < 1.6 || scale < 20) return;
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
        const showPropertyText = pcbState.zoom >= 3.2 && scale >= 34;
        for (const item of graphics) {
            if (item.kind !== 'property' || item.hidden) continue;
            if (!isPcbLayerVisible(item.layer)) continue;
            if (!showPropertyText) continue;
            const point = this._transformGraphicPoints(component, [{ x: item.x || 0, y: item.y || 0 }])[0];
            const rotation = (component.rotation || 0) + (item.rotation || 0);
            this._drawWorldText(ctx, item.text || '', point.x, point.y, {
                rotation,
                fontPx: Math.max(this._worldRadiusToPixels(Math.max(item.size || 1, 0.6) * 0.8), 10),
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
        const showFallbackRef = isPcbLayerVisible('F.SilkS') || isPcbLayerVisible('B.SilkS') || isPcbLayerVisible('F.Fab') || isPcbLayerVisible('B.Fab');
        if (!hasRefText && showFallbackRef && pcbState.zoom >= 2.2 && compScreenHeight >= 28) {
            const refOffset = rotatePoint(0, -Math.max(height / 2, 2.4), component.rotation || 0);
            this._drawWorldText(ctx, component.ref || '', component.x + refOffset.x, component.y + refOffset.y, {
                rotation: textRot,
                fontPx: Math.max(this._worldRadiusToPixels(0.9), 12),
                fill: '#e0f0ed',
                shadow: 'rgba(0, 0, 0, 0.8)',
                fontWeight: '700',
            });
        }
        if (!hasValueText && showFallbackRef && pcbState.zoom >= 3 && compScreenHeight >= 40) {
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
        ctx.save();
        ctx.translate(pt.x, pt.y);
        ctx.rotate(-((options.rotation || 0) * Math.PI / 180));
        ctx.font = `${options.fontWeight || '600'} ${Math.max(options.fontPx || 10, 8)}px "JetBrains Mono", monospace`;
        ctx.fillStyle = options.fill || '#e9f7f4';
        if (options.shadow) {
            ctx.shadowColor = options.shadow;
            ctx.shadowBlur = 4;
        }
        ctx.fillText(text, 0, 0);
        ctx.restore();
    }

    _strokeWorldPath(ctx, points, widthWorld, strokeStyle, alpha = 1) {
        if (!points || points.length < 2) return;
        ctx.save();
        ctx.strokeStyle = strokeStyle;
        ctx.globalAlpha = alpha;
        ctx.lineWidth = Math.max(this._worldRadiusToPixels(widthWorld), 1.2);
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.shadowColor = strokeStyle;
        ctx.shadowBlur = Math.max(ctx.lineWidth * 0.4, 1);
        ctx.beginPath();
        this._screenSmoothPath(ctx, points);
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

    _fillPadShape(ctx, center, width, height, shape, rotation, fillStyle, alpha = 1, roundrectRratio = 0.25, strokeStyle = null) {
        const path = this._padShapePath(center, width, height, shape, rotation, roundrectRratio);
        if (!path.length) return;
        ctx.save();
        ctx.globalAlpha = alpha;
        ctx.beginPath();
        this._screenPathFromWorldPoints(ctx, path);
        ctx.closePath();
        ctx.fillStyle = fillStyle;
        ctx.fill();
        if (strokeStyle) {
            ctx.strokeStyle = strokeStyle;
            ctx.lineWidth = 1;
            ctx.stroke();
        }
        ctx.restore();
    }

    _fillPadDrill(ctx, center, drillDiameter) {
        const pt = this.worldToScreen(center.x, center.y);
        const r = Math.max(this._worldRadiusToPixels(drillDiameter / 2), 1.5);
        // KiCanvas approach: draw hole using background color as solid opaque fill.
        // The ':Pad:Holes' layer in KiCanvas uses theme['background'] color (#0b1116).
        // This works because holes are drawn in a single global pass AFTER all copper
        // from all components, so background-color circles always sit on top.
        ctx.beginPath();
        ctx.fillStyle = '#0b1116';
        ctx.arc(pt.x, pt.y, r, 0, Math.PI * 2);
        ctx.fill();
    }

    _padShapePath(center, width, height, shape, rotation, roundrectRratio = 0.25) {
        const w = Math.max(width, 0.2);
        const h = Math.max(height, 0.2);
        let points;
        if (shape === 'circle') {
            points = [];
            const radius = Math.max(w, h) / 2;
            for (let step = 0; step <= 40; step += 1) {
                const angle = (Math.PI * 2 * step) / 40;
                points.push({ x: Math.cos(angle) * radius, y: Math.sin(angle) * radius });
            }
        } else if (shape === 'oval') {
            points = getRoundRectPoints(w, h, Math.min(w, h) / 2, 10);
        } else if (shape === 'roundrect') {
            points = getRoundRectPoints(w, h, Math.min(w, h) * (roundrectRratio != null ? roundrectRratio : 0.25), 10);
        } else {
            points = [
                { x: -w / 2, y: -h / 2 },
                { x: w / 2, y: -h / 2 },
                { x: w / 2, y: h / 2 },
                { x: -w / 2, y: h / 2 },
            ];
        }
        return points.map((pt) => {
            const rot = rotatePoint(pt.x, pt.y, rotation || 0);
            return { x: center.x + rot.x, y: center.y + rot.y };
        });
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
        return `#${Number(color).toString(16).padStart(6, '0')}`;
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
                if (Math.abs(world.x - center.x) <= w/2 && Math.abs(world.y - center.y) <= h/2) {
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
        
        for (const trace of model.traces || []) {
            const path = trace.path || [];
            const w = Math.max(trace.width || 0.254, 0.2);
            for (let i = 0; i < path.length - 1; i++) {
                const p1 = path[i];
                const p2 = path[i+1];
                
                // Line segment distance logic
                const l2 = (p2.x-p1.x)**2 + (p2.y-p1.y)**2;
                let t = 0;
                if (l2 > 0) {
                    t = Math.max(0, Math.min(1, ((world.x - p1.x)*(p2.x - p1.x) + (world.y - p1.y)*(p2.y - p1.y)) / l2));
                }
                const projX = p1.x + t * (p2.x - p1.x);
                const projY = p1.y + t * (p2.y - p1.y);
                const dist = Math.hypot(world.x - projX, world.y - projY);
                
                if (dist <= w/2 + 0.1) {
                    return { trace, x: projX, y: projY };
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

    async saveBoardModel() {
        if (!pcbState.boardModel) return false;
        const res = await fetch('/api/save_board_model', {
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
    }
    
    async undo() {
        if (this._history.length > 0) {
            const entry = this._history.pop();
            pcbState.boardModel = entry.before;
            pcbState.ratsnest = this._computeClientRatsnest(pcbState.boardModel);
            this._buildBuffers();
            this.refresh();
            await this.saveBoardModel();
        }
    }
    async redo() {}

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
            const adjacency = new Map();
            for (const trace of model.traces || []) {
                if (String(trace.net || '').toUpperCase() !== String(netName).toUpperCase()) continue;
                const path = Array.isArray(trace.path) ? trace.path : [];
                if (path.length < 2) continue;
                const start = path[0];
                const end = path[path.length - 1];
                const startKey = `${Number(start.x).toFixed(2)},${Number(start.y).toFixed(2)}`;
                const endKey = `${Number(end.x).toFixed(2)},${Number(end.y).toFixed(2)}`;
                if (!adjacency.has(startKey)) adjacency.set(startKey, []);
                if (!adjacency.has(endKey)) adjacency.set(endKey, []);
                adjacency.get(startKey).push(endKey);
                adjacency.get(endKey).push(startKey);
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
