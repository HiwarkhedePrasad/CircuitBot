(function initKiCVector(globalThis) {
    if (globalThis.KiCVec) return;

    const { Mat3, Vec2, Color } = KiCMath;
    const { ShaderProgram, VertexArray, Buffer } = KiCGL;

    // Inline GLSL shaders
    const POLYGON_VERT_SRC = `#version 300 es
uniform mat3 u_matrix;
in vec2 a_position;
in vec4 a_color;
out vec4 v_color;
void main() {
    v_color = a_color;
    gl_Position = vec4((u_matrix * vec3(a_position, 1)).xy, 0, 1);
}`;

    const POLYGON_FRAG_SRC = `#version 300 es
precision highp float;
uniform float u_depth;
uniform float u_alpha;
in vec4 v_color;
out vec4 o_color;
void main() {
    vec4 i_color = v_color;
    i_color.a *= u_alpha;
    o_color = i_color;
    gl_FragDepth = u_depth;
}`;

    const POLYLINE_VERT_SRC = `#version 300 es
uniform mat3 u_matrix;
in vec2 a_position;
in vec4 a_color;
in float a_cap_region;
out vec2 v_linespace;
out float v_cap_region;
out vec4 v_color;
vec2 c_linespace[6] = vec2[](
    vec2(-1, -1), vec2(1, -1), vec2(-1, 1),
    vec2(-1, 1), vec2(1, -1), vec2(1, 1)
);
void main() {
    int triangle_vertex_num = int(gl_VertexID % 6);
    v_linespace = c_linespace[triangle_vertex_num];
    v_cap_region = a_cap_region;
    gl_Position = vec4((u_matrix * vec3(a_position, 1)).xy, 0, 1);
    v_color = a_color;
}`;

    const POLYLINE_FRAG_SRC = `#version 300 es
precision highp float;
uniform float u_depth;
uniform float u_alpha;
in vec2 v_linespace;
in float v_cap_region;
in vec4 v_color;
out vec4 outColor;
void main() {
    vec4 i_color = v_color;
    i_color.a *= u_alpha;
    float x = v_linespace.x;
    float y = v_linespace.y;
    if(x < (-1.0 + v_cap_region)) {
        float a = (1.0 + x) / v_cap_region;
        x = mix(-1.0, 0.0, a);
        if(x * x + y * y < 1.0) { outColor = i_color; } else { discard; }
    } else if (x > (1.0 - v_cap_region)) {
        float a = (x - (1.0 - v_cap_region)) / v_cap_region;
        x = mix(0.0, 1.0, a);
        if(x * x + y * y < 1.0) { outColor = i_color; } else { discard; }
    } else {
        outColor = i_color;
    }
    gl_FragDepth = u_depth;
}`;

    // Minimal earcut polygon triangulation
    function earcut(data) {
        const n = data.length >> 1;
        if (n === 0) return [];
        const vertices = new Float64Array(data);
        const holes = [];
        const dim = 2;
        const result = earcutInternal(vertices, holes, dim, 0, n);
        return result;
    }

    function earcutInternal(data, holeIndices, dim, start, end) {
        const n = end - start;
        if (n < 3) return [];
        const coords = [];
        for (let i = start * dim; i < end * dim; i++) coords.push(data[i]);
        const verts = [];
        for (let i = 0; i < n; i++) verts.push(i);
        const result = [];
        const indices = verts.slice();
        const count = n;
        if (isConvex(coords, count, dim)) {
            for (let i = 1; i < count - 1; i++) {
                result.push(indices[0] + start, indices[i] + start, indices[i + 1] + start);
            }
            return result;
        }
        triangulate(coords, verts, result, dim);
        return result.map(i => i + start);
    }

    function isConvex(coords, n, dim) {
        if (n < 3) return false;
        let prev = 0, curr = 1, next = 2;
        const ax = coords[prev * dim], ay = coords[prev * dim + 1];
        const bx = coords[curr * dim], by = coords[curr * dim + 1];
        const cx = coords[next * dim], cy = coords[next * dim + 1];
        const area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
        if (area >= 0) return false;
        for (let i = 3; i < n; i++) {
            prev = i - 1; curr = i;
            const pax = coords[(prev - 1) * dim], pay = coords[(prev - 1) * dim + 1];
            const pbx = coords[prev * dim], pby = coords[prev * dim + 1];
            const pcx = coords[curr * dim], pcy = coords[curr * dim + 1];
            if ((pbx - pax) * (pcy - pay) - (pby - pay) * (pcx - pax) >= 0) return false;
        }
        return true;
    }

    function triangulate(coords, verts, result, dim) {
        const n = verts.length;
        if (n === 3) {
            result.push(verts[0], verts[1], verts[2]);
            return;
        }
        for (let i = 0; i < n; i++) {
            const a = i;
            const b = (i + 1) % n;
            const c = (i + 2) % n;
            const ax = coords[verts[a] * dim], ay = coords[verts[a] * dim + 1];
            const bx = coords[verts[b] * dim], by = coords[verts[b] * dim + 1];
            const cx = coords[verts[c] * dim], cy = coords[verts[c] * dim + 1];
            const area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
            if (area <= 0) continue;
            let ear = true;
            for (let j = 0; j < n; j++) {
                if (j === a || j === b || j === c) continue;
                const px = coords[verts[j] * dim], py = coords[verts[j] * dim + 1];
                if (pointInTriangle(ax, ay, bx, by, cx, cy, px, py)) {
                    ear = false; break;
                }
            }
            if (ear) {
                result.push(verts[a], verts[b], verts[c]);
                verts.splice(b, 1);
                triangulate(coords, verts, result, dim);
                return;
            }
        }
        result.push(verts[0], verts[1], verts[2]);
    }

    function pointInTriangle(ax, ay, bx, by, cx, cy, px, py) {
        const d1 = sign(px, py, ax, ay, bx, by);
        const d2 = sign(px, py, bx, by, cx, cy);
        const d3 = sign(px, py, cx, cy, ax, ay);
        const hasNeg = (d1 < 0) || (d2 < 0) || (d3 < 0);
        const hasPos = (d1 > 0) || (d2 > 0) || (d3 > 0);
        return !(hasNeg && hasPos);
    }

    function sign(px, py, x1, y1, x2, y2) {
        return (px - x2) * (y1 - y2) - (x1 - x2) * (py - y2);
    }

    // ────────────────────────────────────────────
    // Shapes
    // ────────────────────────────────────────────
    class Circle {
        constructor(center, radius, color) {
            this.center = center;
            this.radius = radius;
            this.color = color;
        }
    }

    class Polyline {
        constructor(points, width, color) {
            this.points = points;
            this.width = width;
            this.color = color;
        }
    }

    class Polygon {
        constructor(points, color) {
            this.points = points;
            this.color = color;
            this.vertices = null;
        }
    }

    // ────────────────────────────────────────────
    // Tesselator
    // ────────────────────────────────────────────
    const VERTICES_PER_QUAD = 6;

    class Tesselator {
        static quadToTriangles(quad) {
            return [
                quad[0].x, quad[0].y,
                quad[2].x, quad[2].y,
                quad[1].x, quad[1].y,
                quad[1].x, quad[1].y,
                quad[2].x, quad[2].y,
                quad[3].x, quad[3].y,
            ];
        }

        static populateColorData(dest, color, offset, length) {
            if (!color) color = new Color(1, 0, 0, 1);
            const cd = color.to_array();
            for (let i = 0; i < length; i++) {
                dest[offset + i] = cd[i % cd.length];
            }
        }

        static tesselateSegment(p1, p2, width) {
            const line = new Vec2(p2).sub(new Vec2(p1));
            const norm = line.normal.normalize();
            const n = norm.multiply(width / 2);
            const n2 = n.normal;
            const a = new Vec2(p1).add(n).add(n2);
            const b = new Vec2(p1).sub(n).add(n2);
            const c = new Vec2(p2).add(n).sub(n2);
            const d = new Vec2(p2).sub(n).sub(n2);
            return [a, b, c, d];
        }

        static tesselatePolyline(polyline) {
            const width = polyline.width || 0;
            const points = polyline.points;
            const color = polyline.color;
            const segmentCount = points.length - 1;
            const vertexCount = segmentCount * VERTICES_PER_QUAD;
            const positionData = new Float32Array(vertexCount * 2);
            const colorData = new Float32Array(vertexCount * 4);
            const capData = new Float32Array(vertexCount);
            let vi = 0;
            for (let s = 1; s < points.length; s++) {
                const p1 = points[s - 1];
                const p2 = points[s];
                const length = new Vec2(p2).sub(new Vec2(p1)).magnitude;
                if (length === 0) continue;
                const quad = this.tesselateSegment(p1, p2, width);
                const capRegion = width / (length + width);
                const triData = this.quadToTriangles(quad);
                positionData.set(triData, vi * 2);
                for (let j = 0; j < VERTICES_PER_QUAD; j++) capData[vi + j] = capRegion;
                this.populateColorData(colorData, color, vi * 4, VERTICES_PER_QUAD * 4);
                vi += VERTICES_PER_QUAD;
            }
            return {
                positionArray: positionData.slice(0, vi * 2),
                capArray: capData.slice(0, vi),
                colorArray: colorData.slice(0, vi * 4),
            };
        }

        static tesselateCircle(circle) {
            const n = new Vec2(circle.radius, 0);
            const n2 = n.normal;
            const c = circle.center;
            const a = new Vec2(c).add(n).add(n2);
            const b = new Vec2(c).sub(n).add(n2);
            const d = new Vec2(c).add(n).sub(n2);
            const e = new Vec2(c).sub(n).sub(n2);
            return [a, b, d, e];
        }

        static tesselateCircles(circles) {
            const vertexCount = circles.length * VERTICES_PER_QUAD;
            const positionData = new Float32Array(vertexCount * 2);
            const capData = new Float32Array(vertexCount);
            const colorData = new Float32Array(vertexCount * 4);
            let vi = 0;
            for (const c of circles) {
                const capRegion = 1.0;
                const quad = this.tesselateCircle(c);
                positionData.set(this.quadToTriangles(quad), vi * 2);
                for (let j = 0; j < VERTICES_PER_QUAD; j++) capData[vi + j] = capRegion;
                this.populateColorData(colorData, c.color, vi * 4, VERTICES_PER_QUAD * 4);
                vi += VERTICES_PER_QUAD;
            }
            return {
                positionArray: positionData.slice(0, vi * 2),
                capArray: capData.slice(0, vi),
                colorArray: colorData.slice(0, vi * 4),
            };
        }

        static triangulatePolygon(polygon) {
            if (polygon.vertices) return polygon;
            const points = polygon.points;
            const flat = new Float64Array(points.length * 2);
            for (let i = 0; i < points.length; i++) {
                flat[i * 2] = points[i].x;
                flat[i * 2 + 1] = points[i].y;
            }
            if (points.length === 3) {
                polygon.vertices = new Float32Array(flat);
                polygon.points = [];
                return polygon;
            }
            const tris = earcut(flat);
            const verts = new Float32Array(tris.length * 2);
            for (let i = 0; i < tris.length; i++) {
                verts[i * 2] = flat[tris[i] * 2];
                verts[i * 2 + 1] = flat[tris[i] * 2 + 1];
            }
            polygon.vertices = verts;
            polygon.points = [];
            return polygon;
        }
    }

    // ────────────────────────────────────────────
    // Primitive Sets
    // ────────────────────────────────────────────

    let sharedPolygonShader = null;
    let sharedPolylineShader = null;

    class PolygonSet {
        constructor(gl) {
            this.gl = gl;
            this.shader = sharedPolygonShader;
            this.vao = new VertexArray(gl);
            this.positionBuf = this.vao.buffer(this.shader['a_position'], 2);
            this.colorBuf = this.vao.buffer(this.shader['a_color'], 4);
            this.vertexCount = 0;
        }

        dispose() {
            this.vao.dispose();
            this.positionBuf.dispose();
            this.colorBuf.dispose();
        }

        set(polygons) {
            let totalVerts = 0;
            for (const poly of polygons) {
                Tesselator.triangulatePolygon(poly);
                totalVerts += poly.vertices ? poly.vertices.length : 0;
            }
            const totalVertices = totalVerts / 2;
            const vertexData = new Float32Array(totalVerts);
            const colorData = new Float32Array(totalVertices * 4);
            let vi = 0, ci = 0;
            for (const poly of polygons) {
                if (!poly.vertices) continue;
                const cnt = poly.vertices.length / 2;
                vertexData.set(poly.vertices, vi);
                vi += poly.vertices.length;
                Tesselator.populateColorData(colorData, poly.color, ci, cnt * 4);
                ci += cnt * 4;
            }
            this.positionBuf.set(vertexData);
            this.colorBuf.set(colorData);
            this.vertexCount = vi / 2;
        }

        render() {
            if (!this.vertexCount) return;
            this.vao.bind();
            this.gl.drawArrays(this.gl.TRIANGLES, 0, this.vertexCount);
        }
    }

    class PolylineSet {
        constructor(gl) {
            this.gl = gl;
            this.shader = sharedPolylineShader;
            this.vao = new VertexArray(gl);
            this.positionBuf = this.vao.buffer(this.shader['a_position'], 2);
            this.capRegionBuf = this.vao.buffer(this.shader['a_cap_region'], 1);
            this.colorBuf = this.vao.buffer(this.shader['a_color'], 4);
            this.vertexCount = 0;
        }

        dispose() {
            this.vao.dispose();
            this.positionBuf.dispose();
            this.capRegionBuf.dispose();
            this.colorBuf.dispose();
        }

        set(lines) {
            if (!lines.length) return;
            let totalVerts = 0;
            for (const line of lines) {
                totalVerts += (line.points.length - 1) * VERTICES_PER_QUAD;
            }
            const positionData = new Float32Array(totalVerts * 2);
            const capData = new Float32Array(totalVerts);
            const colorData = new Float32Array(totalVerts * 4);
            let pi = 0, ci = 0, cai = 0;
            for (const line of lines) {
                const { positionArray, capArray, colorArray } = Tesselator.tesselatePolyline(line);
                positionData.set(positionArray, pi); pi += positionArray.length;
                capData.set(capArray, cai); cai += capArray.length;
                colorData.set(colorArray, ci); ci += colorArray.length;
            }
            this.positionBuf.set(positionData);
            this.capRegionBuf.set(capData);
            this.colorBuf.set(colorData);
            this.vertexCount = pi / 2;
        }

        render() {
            if (!this.vertexCount) return;
            this.vao.bind();
            this.gl.drawArrays(this.gl.TRIANGLES, 0, this.vertexCount);
        }
    }

    class CircleSet {
        constructor(gl) {
            this.gl = gl;
            this.shader = sharedPolylineShader;
            this.vao = new VertexArray(gl);
            this.positionBuf = this.vao.buffer(this.shader['a_position'], 2);
            this.capRegionBuf = this.vao.buffer(this.shader['a_cap_region'], 1);
            this.colorBuf = this.vao.buffer(this.shader['a_color'], 4);
            this.vertexCount = 0;
        }

        dispose() {
            this.vao.dispose();
            this.positionBuf.dispose();
            this.capRegionBuf.dispose();
            this.colorBuf.dispose();
        }

        set(circles) {
            const { positionArray, capArray, colorArray } = Tesselator.tesselateCircles(circles);
            this.positionBuf.set(positionArray);
            this.capRegionBuf.set(capArray);
            this.colorBuf.set(colorArray);
            this.vertexCount = positionArray.length / 2;
        }

        render() {
            if (!this.vertexCount) return;
            this.vao.bind();
            this.gl.drawArrays(this.gl.TRIANGLES, 0, this.vertexCount);
        }
    }

    class PrimitiveSet {
        constructor(gl) {
            this.gl = gl;
            this._polygons = [];
            this._circles = [];
            this._lines = [];
            this._polygonSet = null;
            this._circleSet = null;
            this._polylineSet = null;
        }

        static async loadShaders(gl) {
            sharedPolygonShader = await ShaderProgram.load(gl, 'polygon', POLYGON_VERT_SRC, POLYGON_FRAG_SRC);
            sharedPolylineShader = await ShaderProgram.load(gl, 'polyline', POLYLINE_VERT_SRC, POLYLINE_FRAG_SRC);
        }

        dispose() {
            if (this._polygonSet) this._polygonSet.dispose();
            if (this._circleSet) this._circleSet.dispose();
            if (this._polylineSet) this._polylineSet.dispose();
        }

        clear() {
            this.dispose();
            this._polygonSet = null;
            this._circleSet = null;
            this._polylineSet = null;
            this._polygons = [];
            this._circles = [];
            this._lines = [];
        }

        addCircle(circle) { this._circles.push(circle); }
        addPolygon(polygon) { this._polygons.push(polygon); }
        addLine(line) { this._lines.push(line); }

        commit() {
            if (this._polygons.length) {
                this._polygonSet = new PolygonSet(this.gl);
                this._polygonSet.set(this._polygons);
                this._polygons = null;
            }
            if (this._lines.length) {
                this._polylineSet = new PolylineSet(this.gl);
                this._polylineSet.set(this._lines);
                this._lines = null;
            }
            if (this._circles.length) {
                this._circleSet = new CircleSet(this.gl);
                this._circleSet.set(this._circles);
                this._circles = null;
            }
        }

        render(matrix, depth, alpha) {
            depth = depth ?? 0;
            alpha = alpha ?? 1;
            if (this._polygonSet) {
                this._polygonSet.shader.bind();
                this._polygonSet.shader['u_matrix'].mat3f(false, matrix.elements);
                this._polygonSet.shader['u_depth'].f1(depth);
                this._polygonSet.shader['u_alpha'].f1(alpha);
                this._polygonSet.render();
            }
            if (this._circleSet) {
                this._circleSet.shader.bind();
                this._circleSet.shader['u_matrix'].mat3f(false, matrix.elements);
                this._circleSet.shader['u_depth'].f1(depth);
                this._circleSet.shader['u_alpha'].f1(alpha);
                this._circleSet.render();
            }
            if (this._polylineSet) {
                this._polylineSet.shader.bind();
                this._polylineSet.shader['u_matrix'].mat3f(false, matrix.elements);
                this._polylineSet.shader['u_depth'].f1(depth);
                this._polylineSet.shader['u_alpha'].f1(alpha);
                this._polylineSet.render();
            }
        }
    }

    globalThis.KiCVec = {
        Circle, Polyline, Polygon,
        Tesselator,
        PolygonSet, PolylineSet, CircleSet, PrimitiveSet,
    };
})(window);
