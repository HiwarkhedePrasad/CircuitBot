(function initKiCWebGLHelpers(globalThis) {
    if (globalThis.KiCGL) return;

    class Uniform {
        constructor(gl, name, location, type) {
            this.gl = gl;
            this.name = name;
            this.location = location;
            this.type = type;
        }
        f1(x) { this.gl.uniform1f(this.location, x); }
        f1v(data, srcOffset, srcLength) { this.gl.uniform1fv(this.location, data, srcOffset, srcLength); }
        f2(x, y) { this.gl.uniform2f(this.location, x, y); }
        f2v(data, srcOffset, srcLength) { this.gl.uniform2fv(this.location, data, srcOffset, srcLength); }
        f3(x, y, z) { this.gl.uniform3f(this.location, x, y, z); }
        f3v(data, srcOffset, srcLength) { this.gl.uniform3fv(this.location, data, srcOffset, srcLength); }
        f4(x, y, z, w) { this.gl.uniform4f(this.location, x, y, z, w); }
        f4v(data, srcOffset, srcLength) { this.gl.uniform4fv(this.location, data, srcOffset, srcLength); }
        mat3f(transpose, data, srcOffset, srcLength) { this.gl.uniformMatrix3fv(this.location, transpose, data, srcOffset, srcLength); }
        mat3fv(transpose, data, srcOffset, srcLength) { this.gl.uniformMatrix3fv(this.location, transpose, data, srcOffset, srcLength); }
    }

    class ShaderProgram {
        static _cache = new WeakMap();

        constructor(gl, name, vertexSrc, fragmentSrc) {
            this.gl = gl;
            this.name = name;
            if (typeof vertexSrc === 'string') {
                vertexSrc = ShaderProgram._compile(gl, gl.VERTEX_SHADER, vertexSrc);
            }
            if (typeof fragmentSrc === 'string') {
                fragmentSrc = ShaderProgram._compile(gl, gl.FRAGMENT_SHADER, fragmentSrc);
            }
            this.vertex = vertexSrc;
            this.fragment = fragmentSrc;
            this.program = ShaderProgram._link(gl, vertexSrc, fragmentSrc);
            this.uniforms = {};
            this.attribs = {};
            this._discoverUniforms();
            this._discoverAttribs();
        }

        static async load(gl, name, vertSrc, fragSrc) {
            let cache = ShaderProgram._cache.get(gl);
            if (!cache) { cache = new Map(); ShaderProgram._cache.set(gl, cache); }
            if (!cache.has(name)) {
                if (vertSrc instanceof URL) { vertSrc = await (await fetch(vertSrc)).text(); }
                if (fragSrc instanceof URL) { fragSrc = await (await fetch(fragSrc)).text(); }
                cache.set(name, new ShaderProgram(gl, name, vertSrc, fragSrc));
            }
            return cache.get(name);
        }

        static _compile(gl, type, source) {
            const shader = gl.createShader(type);
            if (!shader) throw new Error('Could not create shader');
            gl.shaderSource(shader, source);
            gl.compileShader(shader);
            if (gl.getShaderParameter(shader, gl.COMPILE_STATUS)) return shader;
            const info = gl.getShaderInfoLog(shader);
            gl.deleteShader(shader);
            throw new Error(`Error compiling ${type} shader: ${info}`);
        }

        static _link(gl, vertex, fragment) {
            const program = gl.createProgram();
            if (!program) throw new Error('Could not create shader program');
            gl.attachShader(program, vertex);
            gl.attachShader(program, fragment);
            gl.linkProgram(program);
            if (gl.getProgramParameter(program, gl.LINK_STATUS)) return program;
            const info = gl.getProgramInfoLog(program);
            gl.deleteProgram(program);
            throw new Error(`Error linking shader program: ${info}`);
        }

        _discoverUniforms() {
            const gl = this.gl;
            const n = gl.getProgramParameter(this.program, gl.ACTIVE_UNIFORMS);
            for (let i = 0; i < n; i++) {
                const info = gl.getActiveUniform(this.program, i);
                if (!info) continue;
                const loc = gl.getUniformLocation(this.program, info.name);
                if (!loc) continue;
                const u = new Uniform(gl, info.name, loc, info.type);
                this[info.name] = u;
                this.uniforms[info.name] = u;
            }
        }

        _discoverAttribs() {
            const gl = this.gl;
            const n = gl.getProgramParameter(this.program, gl.ACTIVE_ATTRIBUTES);
            for (let i = 0; i < n; i++) {
                const info = gl.getActiveAttrib(this.program, i);
                if (!info) continue;
                this.attribs[info.name] = info;
                this[info.name] = gl.getAttribLocation(this.program, info.name);
            }
        }

        bind() { this.gl.useProgram(this.program); }
    }

    class VertexArray {
        constructor(gl) {
            this.gl = gl;
            this.buffers = [];
            const vao = gl.createVertexArray();
            if (!vao) throw new Error('Could not create VertexArray');
            this.vao = vao;
            this.bind();
        }

        dispose(includeBuffers = true) {
            this.gl.deleteVertexArray(this.vao);
            this.vao = undefined;
            if (includeBuffers) {
                for (const buf of this.buffers) buf.dispose();
            }
        }

        bind() { this.gl.bindVertexArray(this.vao); }

        buffer(attrib, size, type, normalized, stride, offset, target) {
            const gl = this.gl;
            type = type ?? gl.FLOAT;
            const b = new Buffer(gl, target);
            b.bind();
            gl.vertexAttribPointer(attrib, size, type, normalized ?? false, stride ?? 0, offset ?? 0);
            gl.enableVertexAttribArray(attrib);
            this.buffers.push(b);
            return b;
        }
    }

    class Buffer {
        constructor(gl, target) {
            this.gl = gl;
            this.target = target ?? gl.ARRAY_BUFFER;
            const buf = gl.createBuffer();
            if (!buf) throw new Error('Unable to create Buffer');
            this._buf = buf;
        }

        dispose() {
            if (this._buf) { this.gl.deleteBuffer(this._buf); this._buf = undefined; }
        }

        bind() { this.gl.bindBuffer(this.target, this._buf); }

        set(data, usage) {
            this.bind();
            this.gl.bufferData(this.target, data, usage ?? this.gl.STATIC_DRAW);
        }

        get length() {
            this.bind();
            return this.gl.getBufferParameter(this.target, this.gl.BUFFER_SIZE);
        }
    }

    globalThis.KiCGL = { Uniform, ShaderProgram, VertexArray, Buffer };
})(window);
