(function initKiCWebGLRenderer(globalThis) {
    if (globalThis.KiCRender) return;

    const { Mat3, Color, Vec2, BBox, RenderState, RenderStateStack } = KiCMath;
    const { PrimitiveSet } = KiCVec;

    class WebGL2RenderLayer {
        constructor(renderer, name) {
            this.renderer = renderer;
            this.name = name;
            this.compositeOperation = 'source-over';
            this._prim = new PrimitiveSet(renderer.gl);
        }

        dispose() { this._prim.dispose(); }
        clear() { this._prim.clear(); }

        render(camera, depth, globalAlpha) {
            this._prim.render(camera, depth, globalAlpha);
        }

        get prim() { return this._prim; }
    }

    class WebGL2Renderer {
        constructor(canvas) {
            this.canvas = canvas;
            this.canvasSize = new Vec2(0, 0);
            this.state = new RenderStateStack();
            this._backgroundColor = Color.black.copy();
            this._layers = [];
            this._activeLayer = null;
            this.projectionMatrix = Mat3.identity();
            this.gl = null;
            this._currentBBox = null;
        }

        get backgroundColor() { return this._backgroundColor; }
        set backgroundColor(color) {
            this._backgroundColor = color;
            this.canvas.style.backgroundColor = this._backgroundColor.to_css();
        }

        async setup(existingGl) {
            const gl = existingGl || this.canvas.getContext('webgl2', { alpha: false });
            if (!gl) throw new Error('Unable to create WebGL2 context');
            this.gl = gl;
            gl.enable(gl.BLEND);
            gl.blendEquation(gl.FUNC_ADD);
            gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
            // Do NOT enable DEPTH_TEST here. All board content is flat 2D;
            // depth testing with GREATER would reject primitives drawn at the
            // same depth, hiding traces, pads, etc.
            gl.clearColor(...this._backgroundColor.to_array());
            gl.clearDepth(0);
            gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
            this._updateCanvasSize();
            await PrimitiveSet.loadShaders(gl);
        }

        dispose() {
            for (const layer of this._layers) layer.dispose();
            this.gl = null;
        }

        _updateCanvasSize() {
            if (!this.gl) return;
            const dpr = window.devicePixelRatio || 1;
            const rect = this.canvas.getBoundingClientRect();
            const logicalW = rect.width;
            const logicalH = rect.height;
            if (this.canvas.width !== Math.round(logicalW * dpr) || this.canvas.height !== Math.round(logicalH * dpr)) {
                this.canvas.width = Math.round(logicalW * dpr);
                this.canvas.height = Math.round(logicalH * dpr);
            }
            this.canvasSize.set(logicalW, logicalH);
            this.gl.viewport(0, 0, this.canvas.width, this.canvas.height);
            this.projectionMatrix = Mat3.orthographic(this.canvas.width, this.canvas.height);
        }

        clearCanvas() {
            if (!this.gl) return;
            this._updateCanvasSize();
            this.gl.clearColor(...this._backgroundColor.to_array());
            this.gl.clear(this.gl.COLOR_BUFFER_BIT | this.gl.DEPTH_BUFFER_BIT);
        }

        startBBox() { this._currentBBox = new BBox(0, 0, 0, 0); }
        addBBox(bb) {
            if (!this._currentBBox) return;
            this._currentBBox = BBox.combine([this._currentBBox, bb], bb.context);
        }
        endBBox(context) {
            const bb = this._currentBBox;
            if (!bb) throw new Error('No current bbox');
            bb.context = context;
            this._currentBBox = null;
            return bb;
        }

        startLayer(name) {
            const layer = new WebGL2RenderLayer(this, name);
            this._layers.push(layer);
            this._activeLayer = layer;
            return layer;
        }

        endLayer() {
            if (this._activeLayer) {
                this._activeLayer.prim.commit();
            }
            const layer = this._activeLayer;
            this._activeLayer = null;
            return layer;
        }

        get layers() { return this._layers; }

        removeLayer(layer) {
            const idx = this._layers.indexOf(layer);
            if (idx >= 0) this._layers.splice(idx, 1);
        }

        circle(circleOrCenter, radius, color) {
            if (!this._activeLayer) return;
            let circle;
            if (circleOrCenter instanceof KiCVec.Circle) {
                circle = circleOrCenter;
            } else {
                circle = new KiCVec.Circle(
                    new Vec2(circleOrCenter),
                    radius,
                    color ?? this.state.fill,
                );
            }
            if (!circle.color || circle.color.is_transparent_black) {
                circle.color = this.state.fill ?? Color.transparent_black;
            }
            circle.center = this.state.matrix.transform(circle.center);
            const radial = new Vec2(circle.radius, circle.radius);
            this.addBBox(BBox.from_points([
                circle.center.add(radial),
                circle.center.sub(radial),
            ]));
            this._activeLayer.prim.addCircle(circle);
        }

        line(lineOrPoints, width, color) {
            if (!this._activeLayer) return;
            let line;
            if (lineOrPoints instanceof KiCVec.Polyline) {
                line = lineOrPoints;
            } else {
                line = new KiCVec.Polyline(
                    lineOrPoints,
                    width ?? this.state.stroke_width,
                    color ?? this.state.stroke,
                );
            }
            if (!line.color || line.color.is_transparent_black) {
                line.color = this.state.stroke ?? Color.transparent_black;
            }
            line.points = Array.from(this.state.matrix.transform_all(line.points));
            let bbox = BBox.from_points(line.points);
            bbox = bbox.grow(line.width);
            this.addBBox(bbox);
            this._activeLayer.prim.addLine(line);
        }

        polygon(polygonOrPoints, color) {
            if (!this._activeLayer) return;
            let polygon;
            if (polygonOrPoints instanceof KiCVec.Polygon) {
                polygon = polygonOrPoints;
            } else {
                polygon = new KiCVec.Polygon(
                    polygonOrPoints,
                    color ?? this.state.fill,
                );
            }
            if (!polygon.color || polygon.color.is_transparent_black) {
                polygon.color = this.state.fill ?? Color.transparent_black;
            }
            polygon.points = Array.from(this.state.matrix.transform_all(polygon.points));
            this.addBBox(BBox.from_points(polygon.points));
            this._activeLayer.prim.addPolygon(polygon);
        }
    }

    globalThis.KiCRender = { WebGL2Renderer, WebGL2RenderLayer };
})(window);
