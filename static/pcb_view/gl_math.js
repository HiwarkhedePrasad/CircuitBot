// Minimal local subset of gl-matrix used by the PCB WebGL renderer.
(function initGlMatrixFallback(globalThis) {
    if (globalThis.glMatrix && globalThis.glMatrix.mat3 && globalThis.glMatrix.vec2) {
        return;
    }

    const mat3 = {
        create() {
            return new Float32Array([
                1, 0, 0,
                0, 1, 0,
                0, 0, 1,
            ]);
        },

        identity(out) {
            out[0] = 1; out[1] = 0; out[2] = 0;
            out[3] = 0; out[4] = 1; out[5] = 0;
            out[6] = 0; out[7] = 0; out[8] = 1;
            return out;
        },

        translate(out, a, v) {
            const x = v[0];
            const y = v[1];

            if (out !== a) {
                out[0] = a[0]; out[1] = a[1]; out[2] = a[2];
                out[3] = a[3]; out[4] = a[4]; out[5] = a[5];
            }

            out[6] = a[0] * x + a[3] * y + a[6];
            out[7] = a[1] * x + a[4] * y + a[7];
            out[8] = a[2] * x + a[5] * y + a[8];
            return out;
        },

        scale(out, a, v) {
            const x = v[0];
            const y = v[1];

            out[0] = a[0] * x;
            out[1] = a[1] * x;
            out[2] = a[2] * x;
            out[3] = a[3] * y;
            out[4] = a[4] * y;
            out[5] = a[5] * y;
            out[6] = a[6];
            out[7] = a[7];
            out[8] = a[8];
            return out;
        },

        multiply(out, a, b) {
            const a00 = a[0], a01 = a[1], a02 = a[2];
            const a10 = a[3], a11 = a[4], a12 = a[5];
            const a20 = a[6], a21 = a[7], a22 = a[8];

            const b00 = b[0], b01 = b[1], b02 = b[2];
            const b10 = b[3], b11 = b[4], b12 = b[5];
            const b20 = b[6], b21 = b[7], b22 = b[8];

            out[0] = a00 * b00 + a10 * b01 + a20 * b02;
            out[1] = a01 * b00 + a11 * b01 + a21 * b02;
            out[2] = a02 * b00 + a12 * b01 + a22 * b02;
            out[3] = a00 * b10 + a10 * b11 + a20 * b12;
            out[4] = a01 * b10 + a11 * b11 + a21 * b12;
            out[5] = a02 * b10 + a12 * b11 + a22 * b12;
            out[6] = a00 * b20 + a10 * b21 + a20 * b22;
            out[7] = a01 * b20 + a11 * b21 + a21 * b22;
            out[8] = a02 * b20 + a12 * b21 + a22 * b22;
            return out;
        },

        invert(out, a) {
            const a00 = a[0], a01 = a[1], a02 = a[2];
            const a10 = a[3], a11 = a[4], a12 = a[5];
            const a20 = a[6], a21 = a[7], a22 = a[8];

            const b01 = a22 * a11 - a12 * a21;
            const b11 = -a22 * a10 + a12 * a20;
            const b21 = a21 * a10 - a11 * a20;

            let det = a00 * b01 + a01 * b11 + a02 * b21;
            if (!det) {
                return null;
            }
            det = 1 / det;

            out[0] = b01 * det;
            out[1] = (-a22 * a01 + a02 * a21) * det;
            out[2] = (a12 * a01 - a02 * a11) * det;
            out[3] = b11 * det;
            out[4] = (a22 * a00 - a02 * a20) * det;
            out[5] = (-a12 * a00 + a02 * a10) * det;
            out[6] = b21 * det;
            out[7] = (-a21 * a00 + a01 * a20) * det;
            out[8] = (a11 * a00 - a01 * a10) * det;
            return out;
        },
    };

    const vec2 = {
        create() {
            return new Float32Array(2);
        },

        transformMat3(out, a, m) {
            const x = a[0];
            const y = a[1];
            out[0] = m[0] * x + m[3] * y + m[6];
            out[1] = m[1] * x + m[4] * y + m[7];
            return out;
        },
    };

    globalThis.glMatrix = { mat3, vec2 };
})(window);