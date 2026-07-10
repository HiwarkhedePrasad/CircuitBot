(function initKiCanvasMath(globalThis) {
    if (globalThis.KiCMath) return;

    // ────────────────────────────────────────────
    // Color
    // ────────────────────────────────────────────
    class Color {
        constructor(r, g, b, a = 1) {
            this.r = r; this.g = g; this.b = b; this.a = a;
        }
        copy() { return new Color(this.r, this.g, this.b, this.a); }
        static get transparent_black() { return new Color(0, 0, 0, 0); }
        static get black() { return new Color(0, 0, 0, 1); }
        static get white() { return new Color(1, 1, 1, 1); }
        get is_transparent_black() { return this.r === 0 && this.g === 0 && this.b === 0 && this.a === 0; }

        static from_css(str) {
            let r, g, b, a;
            if (str[0] === '#') {
                str = str.slice(1);
                if (str.length === 3) str = `${str[0]}${str[0]}${str[1]}${str[1]}${str[2]}${str[2]}`;
                if (str.length === 6) str = `${str}FF`;
                r = parseInt(str.slice(0, 2), 16) / 255;
                g = parseInt(str.slice(2, 4), 16) / 255;
                b = parseInt(str.slice(4, 6), 16) / 255;
                a = parseInt(str.slice(6, 8), 16) / 255;
            } else if (str.startsWith('rgb')) {
                if (!str.startsWith('rgba')) str = `rgba(${str.slice(4, -1)}, 1)`;
                const parts = str.trim().slice(5, -1).split(',').map(s => parseFloat(s.trim()));
                r = parts[0] / 255; g = parts[1] / 255; b = parts[2] / 255; a = parts[3] ?? 1;
            } else {
                r = 0; g = 0; b = 0; a = 1;
            }
            return new Color(r, g, b, a);
        }

        to_array() { return [this.r, this.g, this.b, this.a]; }
        to_css() {
            const to255 = (v) => Math.round(Math.max(0, Math.min(1, v)) * 255);
            return `rgba(${to255(this.r)}, ${to255(this.g)}, ${to255(this.b)}, ${this.a.toFixed(3)})`;
        }
    }

    // ────────────────────────────────────────────
    // Angle
    // ────────────────────────────────────────────
    class Angle {
        constructor(radians) {
            if (radians instanceof Angle) return radians;
            this.radians = radians;
        }
        static rad_to_deg(r) { return (r / Math.PI) * 180; }
        static deg_to_rad(d) { return (d / 180) * Math.PI; }
        static round(degrees) { return Math.round((degrees + Number.EPSILON) * 100) / 100; }

        get radians() { return this._rad; }
        set radians(v) { this._rad = v; this._deg = Angle.round(Angle.rad_to_deg(v)); }
        get degrees() { return this._deg; }
        set degrees(v) { this._deg = v; this._rad = Angle.deg_to_rad(v); }

        static from_degrees(v) { return new Angle(Angle.deg_to_rad(v)); }

        copy() { return new Angle(this._rad); }
        add(other) { return new Angle(this._rad + new Angle(other)._rad); }
        sub(other) { return new Angle(this._rad - new Angle(other)._rad); }

        normalize() {
            let deg = Angle.round(this._deg);
            while (deg < 0) deg += 360;
            while (deg >= 360) deg -= 360;
            return Angle.from_degrees(deg);
        }

        normalize180() {
            let deg = Angle.round(this._deg);
            while (deg <= -180) deg += 360;
            while (deg > 180) deg -= 360;
            return Angle.from_degrees(deg);
        }

        normalize720() {
            let deg = Angle.round(this._deg);
            while (deg < -360) deg += 360;
            while (deg >= 360) deg -= 360;
            return Angle.from_degrees(deg);
        }

        negative() { return new Angle(-this._rad); }

        get is_vertical() { return this._deg === 90 || this._deg === 270; }
        get is_horizontal() { return this._deg === 0 || this._deg === 180; }

        rotate_point(point, origin) {
            origin = origin || { x: 0, y: 0 };
            let x = point.x - origin.x;
            let y = point.y - origin.y;
            const angle = this.normalize();
            const d = angle.degrees;
            if (d === 0) { /* noop */ }
            else if (d === 90) { const t = x; x = -y; y = t; }
            else if (d === 180) { x = -x; y = -y; }
            else if (d === 270) { const t = x; x = y; y = -t; }
            else {
                const sina = Math.sin(angle.radians);
                const cosa = Math.cos(angle.radians);
                const x0 = x, y0 = y;
                x = x0 * cosa - y0 * sina;
                y = x0 * sina + y0 * cosa;
            }
            return { x: x + origin.x, y: y + origin.y };
        }
    }

    // ────────────────────────────────────────────
    // Vec2
    // ────────────────────────────────────────────
    class Vec2 {
        constructor(x, y) {
            if (x instanceof Vec2) { this.x = x.x; this.y = x.y; }
            else if (Array.isArray(x)) { this.x = x[0]; this.y = x[1]; }
            else if (x && typeof x === 'object' && 'x' in x) { this.x = x.x; this.y = x.y; }
            else { this.x = x ?? 0; this.y = y ?? 0; }
        }
        set(x, y) { this.x = x; this.y = y; return this; }
        copy() { return new Vec2(this.x, this.y); }
        get magnitude() { return Math.sqrt(this.x * this.x + this.y * this.y); }
        get squared_magnitude() { return this.x * this.x + this.y * this.y; }
        get normal() { return new Vec2(-this.y, this.x); }

        get angle() { return new Angle(Math.atan2(this.y, this.x)); }

        get kicad_angle() {
            if (this.x === 0 && this.y === 0) return new Angle(0);
            if (this.y === 0) return this.x >= 0 ? new Angle(0) : Angle.from_degrees(-180);
            if (this.x === 0) return this.y >= 0 ? Angle.from_degrees(90) : Angle.from_degrees(-90);
            if (this.x === this.y) return this.x >= 0 ? Angle.from_degrees(45) : Angle.from_degrees(-135);
            if (this.x === -this.y) return this.x >= 0 ? Angle.from_degrees(-45) : Angle.from_degrees(135);
            return this.angle;
        }

        normalize() {
            if (this.x === 0 && this.y === 0) return new Vec2(0, 0);
            const l = this.magnitude;
            return new Vec2(this.x / l, this.y / l);
        }

        equals(b) { return this.x === (b && b.x) && this.y === (b && b.y); }
        add(b) { return new Vec2(this.x + b.x, this.y + b.y); }
        sub(b) { return new Vec2(this.x - b.x, this.y - b.y); }
        scale(b) { return (typeof b === 'number') ? new Vec2(this.x * b, this.y * b) : new Vec2(this.x * b.x, this.y * b.y); }
        multiply(s) { return this.scale(s); }

        rotate(angle) {
            const m = Mat3.rotation(angle);
            return m.transform(this);
        }

        resize(len) { return this.normalize().multiply(len); }
        cross(b) { return this.x * b.y - this.y * b.x; }

        static segment_intersect(a1, b1, a2, b2) {
            const ray1 = new Vec2(b1).sub(new Vec2(a1));
            const ray2 = new Vec2(b2).sub(new Vec2(a2));
            const delta = new Vec2(a2).sub(new Vec2(a1));
            const d = ray2.cross(ray1);
            const t1 = ray2.cross(delta);
            const t2 = ray1.cross(delta);
            if (d === 0) return null;
            if (d > 0 && (t2 < 0 || t2 > d || t1 < 0 || t1 > d)) return null;
            if (d < 0 && (t2 < d || t1 < d || t1 > 0 || t2 > 0)) return null;
            return new Vec2(a2.x + (t2 / d) * ray2.x, a2.y + (t2 / d) * ray2.y);
        }
    }

    // ────────────────────────────────────────────
    // Mat3 (expanded from original)
    // ────────────────────────────────────────────
    class Mat3 {
        constructor(elements) {
            this.elements = new Float32Array(elements || [
                1, 0, 0,
                0, 1, 0,
                0, 0, 1,
            ]);
        }

        copy() { return new Mat3(this.elements); }

        transform(point) {
            const p = new Vec2(point);
            const e = this.elements;
            return new Vec2(
                p.x * e[0] + p.y * e[3] + e[6],
                p.x * e[1] + p.y * e[4] + e[7],
            );
        }

        transform_all(points) {
            return Array.from(points).map(p => this.transform(p));
        }

        multiply_self(b) {
            const a = this.elements;
            const a0 = a[0], a1 = a[1], a2 = a[2];
            const a3 = a[3], a4 = a[4], a5 = a[5];
            const a6 = a[6], a7 = a[7], a8 = a[8];
            const be = b.elements;
            const b0 = be[0], b1 = be[1], b2 = be[2];
            const b3 = be[3], b4 = be[4], b5 = be[5];
            const b6 = be[6], b7 = be[7], b8 = be[8];
            // Column-major C = B * A  (matches transform() convention)
            a[0] = b0 * a0 + b3 * a1 + b6 * a2;
            a[1] = b1 * a0 + b4 * a1 + b7 * a2;
            a[2] = b2 * a0 + b5 * a1 + b8 * a2;
            a[3] = b0 * a3 + b3 * a4 + b6 * a5;
            a[4] = b1 * a3 + b4 * a4 + b7 * a5;
            a[5] = b2 * a3 + b5 * a4 + b8 * a5;
            a[6] = b0 * a6 + b3 * a7 + b6 * a8;
            a[7] = b1 * a6 + b4 * a7 + b7 * a8;
            a[8] = b2 * a6 + b5 * a7 + b8 * a8;
            return this;
        }

        multiply(b) { return this.copy().multiply_self(b); }

        translate_self(x, y) { return this.multiply_self(Mat3.translation(x, y)); }
        rotate_self(angle) { return this.multiply_self(Mat3.rotation(angle)); }
        scale_self(x, y) { return this.multiply_self(Mat3.scaling(x, y)); }

        inverse() {
            const a = this.elements;
            const a00 = a[0], a01 = a[1], a02 = a[2];
            const a10 = a[3], a11 = a[4], a12 = a[5];
            const a20 = a[6], a21 = a[7], a22 = a[8];
            const b01 = a22 * a11 - a12 * a21;
            const b11 = -a22 * a10 + a12 * a20;
            const b21 = a21 * a10 - a11 * a20;
            const det = a00 * b01 + a01 * b11 + a02 * b21;
            const invDet = 1.0 / det;
            return new Mat3([
                b01 * invDet,
                (-a22 * a01 + a02 * a21) * invDet,
                (a12 * a01 - a02 * a11) * invDet,
                b11 * invDet,
                (a22 * a00 - a02 * a20) * invDet,
                (-a12 * a00 + a02 * a10) * invDet,
                b21 * invDet,
                (-a21 * a00 + a01 * a20) * invDet,
                (a11 * a00 - a01 * a10) * invDet,
            ]);
        }

        get absolute_translation() { return this.transform({ x: 0, y: 0 }); }

        get absolute_rotation() {
            const p0 = this.transform({ x: 0, y: 0 });
            const p1 = this.transform({ x: 1, y: 0 });
            return new Vec2(p1).sub(new Vec2(p0)).angle.normalize();
        }

        static identity() { return new Mat3(); }

        static translation(x, y) {
            return new Mat3([1, 0, 0, 0, 1, 0, x, y, 1]);
        }

        static scaling(x, y) {
            return new Mat3([x, 0, 0, 0, y, 0, 0, 0, 1]);
        }

        static rotation(angle) {
            const theta = new Angle(angle).radians;
            const cos = Math.cos(theta);
            const sin = Math.sin(theta);
            return new Mat3([cos, -sin, 0, sin, cos, 0, 0, 0, 1]);
        }

        static orthographic(width, height) {
            return new Mat3([2 / width, 0, 0, 0, -2 / height, 0, -1, 1, 1]);
        }
    }

    // ────────────────────────────────────────────
    // Arc (math arc — arc center from 3 points)
    // ────────────────────────────────────────────
    class MathArc {
        constructor(center, radius, start_angle, end_angle, width, direction) {
            this.center = new Vec2(center);
            this.radius = radius;
            this.start_angle = new Angle(start_angle);
            this.end_angle = new Angle(end_angle);
            this.width = width ?? 1;
            this.direction = direction ?? 'clockwise';
        }

        static from_three_points(start, mid, end, width) {
            const u = 1000000;
            const center = arc_center_from_three_points(
                new Vec2(start.x * u, start.y * u),
                new Vec2(mid.x * u, mid.y * u),
                new Vec2(end.x * u, end.y * u),
            );
            center.x /= u;
            center.y /= u;
            const radius = new Vec2(center).sub(new Vec2(mid)).magnitude;
            const start_angle = new Vec2(start).sub(center).angle;
            const mid_angle = new Vec2(mid).sub(center).angle;
            const end_angle = new Vec2(end).sub(center).angle;
            let arc_angle;
            const start_to_mid = mid_angle.sub(start_angle).normalize();
            const start_to_end = end_angle.sub(start_angle).normalize();
            if (start_to_mid.degrees < start_to_end.degrees) {
                arc_angle = start_to_end;
            } else {
                arc_angle = Angle.from_degrees(360).sub(start_to_end);
            }
            let arc_start;
            let direction;
            const mid_to_start = new Vec2(mid).sub(new Vec2(start));
            const end_to_mid = new Vec2(end).sub(new Vec2(mid));
            if (mid_to_start.cross(end_to_mid) < 0) {
                arc_start = end_angle.normalize();
                direction = 'counter-clockwise';
            } else {
                arc_start = start_angle.normalize();
                direction = 'clockwise';
            }
            const arc_end = arc_start.add(arc_angle);
            return new MathArc(center, radius, arc_start, arc_end, width, direction);
        }

        static from_center_start_end(center, start, end, width) {
            const radius = new Vec2(start).sub(new Vec2(center)).magnitude;
            const start_radial = new Vec2(start).sub(center);
            const end_radial = new Vec2(end).sub(center);
            let start_angle = start_radial.kicad_angle;
            let end_angle = end_radial.kicad_angle;
            if (end_angle.degrees === start_angle.degrees) {
                end_angle.degrees = start_angle.degrees + 360;
            }
            if (start_angle.degrees > end_angle.degrees) {
                if (end_angle.degrees < 0) {
                    end_angle = end_angle.normalize();
                } else {
                    start_angle = start_angle.normalize().sub(Angle.from_degrees(-360));
                }
            }
            return new MathArc(center, radius, start_angle, end_angle, width);
        }

        get start_radial() { return this.start_angle.rotate_point({ x: this.radius, y: 0 }); }
        get start_point() { return new Vec2(this.center).add(new Vec2(this.start_radial)); }
        get end_radial() { return this.end_angle.rotate_point({ x: this.radius, y: 0 }); }
        get end_point() { return new Vec2(this.center).add(new Vec2(this.end_radial)); }

        get mid_angle() { return new Angle((this.start_angle.radians + this.end_angle.radians) / 2); }
        get mid_radial() { return this.mid_angle.rotate_point({ x: this.radius, y: 0 }); }
        get mid_point() { return new Vec2(this.center).add(new Vec2(this.mid_radial)); }
        get arc_angle() { return this.end_angle.sub(this.start_angle); }

        to_polyline(segments) {
            segments = segments || 32;
            const points = [];
            let start = this.start_angle.radians;
            let end = this.end_angle.radians;
            if (start > end) {
                const tmp = start; start = end; end = tmp;
            }
            for (let theta = start; theta < end; theta += Math.PI / segments) {
                points.push({
                    x: this.center.x + Math.cos(theta) * this.radius,
                    y: this.center.y + Math.sin(theta) * this.radius,
                });
            }
            let last_angle;
            if (this.direction === 'counter-clockwise') {
                points.reverse();
                last_angle = start;
            } else {
                last_angle = end;
            }
            const last_pt = {
                x: this.center.x + Math.cos(last_angle) * this.radius,
                y: this.center.y + Math.sin(last_angle) * this.radius,
            };
            const last = points[points.length - 1];
            if (!last || Math.abs(last.x - last_pt.x) > 0.001 || Math.abs(last.y - last_pt.y) > 0.001) {
                points.push(last_pt);
            }
            return points;
        }

        get bbox() {
            const points = [this.start_point, this.mid_point, this.end_point];
            const r = this.radius;
            const c = this.center;
            if (this.start_angle.degrees < 0 && this.end_angle.degrees >= 0) points.push({ x: c.x + r, y: c.y });
            if (this.start_angle.degrees < 90 && this.end_angle.degrees >= 90) points.push({ x: c.x, y: c.y + r });
            if (this.start_angle.degrees < 180 && this.end_angle.degrees >= 180) points.push({ x: c.x - r, y: c.y });
            if (this.start_angle.degrees < 270 && this.end_angle.degrees >= 270) points.push({ x: c.x, y: c.y + r });
            if (this.start_angle.degrees < 360 && this.end_angle.degrees >= 360) points.push({ x: c.x, y: c.y + r });
            return BBox.from_points(points);
        }
    }

    function arc_center_from_three_points(start, mid, end) {
        const center = new Vec2(0, 0);
        const y_delta_21 = mid.y - start.y;
        let x_delta_21 = mid.x - start.x;
        const y_delta_32 = end.y - mid.y;
        let x_delta_32 = end.x - mid.x;
        if ((x_delta_21 === 0 && y_delta_32 === 0) || (y_delta_21 === 0 && x_delta_32 === 0)) {
            center.x = (start.x + end.x) / 2;
            center.y = (start.y + end.y) / 2;
            return center;
        }
        if (x_delta_21 === 0) x_delta_21 = Number.EPSILON;
        if (x_delta_32 === 0) x_delta_32 = -Number.EPSILON;
        const sqrt_1_2 = Math.SQRT1_2;
        let slope_a = y_delta_21 / x_delta_21;
        let slope_b = y_delta_32 / x_delta_32;
        const d_slope_a = slope_a * new Vec2(0.5 / y_delta_21, 0.5 / x_delta_21).magnitude;
        const d_slope_b = slope_b * new Vec2(0.5 / y_delta_32, 0.5 / x_delta_32).magnitude;
        if (slope_a === slope_b) {
            if (start.x === end.x && start.y === end.y) {
                center.x = (start.x + mid.x) / 2;
                center.y = (start.y + mid.y) / 2;
                return center;
            } else {
                slope_a += Number.EPSILON;
                slope_b -= Number.EPSILON;
            }
        }
        if (slope_a === 0) slope_a = Number.EPSILON;
        const slope_ab_start_end_y = slope_a * slope_b * (start.y - end.y);
        const d_slope_ab_start_end_y = slope_ab_start_end_y * Math.sqrt(
            ((d_slope_a / slope_a) * d_slope_a) / slope_a +
            ((d_slope_b / slope_b) * d_slope_b) / slope_b +
            (sqrt_1_2 / (start.y - end.y)) * (sqrt_1_2 / (start.y - end.y)),
        );
        const slope_b_start_mid_x = slope_b * (start.x + mid.x);
        const d_slope_b_start_mid_x = slope_b_start_mid_x * Math.sqrt(
            ((d_slope_b / slope_b) * d_slope_b) / slope_b +
            ((sqrt_1_2 / (start.x + mid.x)) * sqrt_1_2) / (start.x + mid.x),
        );
        const slope_a_mid_end_x = slope_a * (mid.x + end.x);
        const d_slope_a_mid_end_x = slope_a_mid_end_x * Math.sqrt(
            ((d_slope_a / slope_a) * d_slope_a) / slope_a +
            ((sqrt_1_2 / (mid.x + end.x)) * sqrt_1_2) / (mid.x + end.x),
        );
        const twice_b_a_slope_diff = 2 * (slope_b - slope_a);
        const d_twice_b_a_slope_diff = 2 * Math.sqrt(d_slope_b * d_slope_b + d_slope_a * d_slope_a);
        const center_numerator_x = slope_ab_start_end_y + slope_b_start_mid_x - slope_a_mid_end_x;
        const d_center_numerator_x = Math.sqrt(
            d_slope_ab_start_end_y * d_slope_ab_start_end_y +
            d_slope_b_start_mid_x * d_slope_b_start_mid_x +
            d_slope_a_mid_end_x * d_slope_a_mid_end_x,
        );
        const center_x = center_numerator_x / twice_b_a_slope_diff;
        const d_center_x = center_x * Math.sqrt(
            ((d_center_numerator_x / center_numerator_x) * d_center_numerator_x) / center_numerator_x +
            ((d_twice_b_a_slope_diff / twice_b_a_slope_diff) * d_twice_b_a_slope_diff) / twice_b_a_slope_diff,
        );
        const center_numerator_y = (start.x + mid.x) / 2 - center_x;
        const d_center_numerator_y = Math.sqrt(1 / 8 + d_center_x * d_center_x);
        const center_first_term = center_numerator_y / slope_a;
        const d_center_first_term_y = center_first_term * Math.sqrt(
            ((d_center_numerator_y / center_numerator_y) * d_center_numerator_y) / center_numerator_y +
            ((d_slope_a / slope_a) * d_slope_a) / slope_a,
        );
        const center_y = center_first_term + (start.y + mid.y) / 2;
        const d_center_y = Math.sqrt(d_center_first_term_y * d_center_first_term_y + 1 / 8);
        const rounded_100_cx = Math.floor((center_x + 50) / 100) * 100;
        const rounded_100_cy = Math.floor((center_y + 50) / 100) * 100;
        const rounded_10_cx = Math.floor((center_x + 5) / 10) * 10;
        const rounded_10_cy = Math.floor((center_y + 5) / 10) * 10;
        if (Math.abs(rounded_100_cx - center_x) < d_center_x && Math.abs(rounded_100_cy - center_y) < d_center_y) {
            center.x = rounded_100_cx; center.y = rounded_100_cy;
        } else if (Math.abs(rounded_10_cx - center_x) < d_center_x && Math.abs(rounded_10_cy - center_y) < d_center_y) {
            center.x = rounded_10_cx; center.y = rounded_10_cy;
        } else {
            center.x = center_x; center.y = center_y;
        }
        return center;
    }

    // ────────────────────────────────────────────
    // BBox
    // ────────────────────────────────────────────
    class BBox {
        constructor(x, y, w, h, context) {
            this.x = x ?? 0; this.y = y ?? 0; this.w = w ?? 0; this.h = h ?? 0; this.context = context;
            if (this.w < 0) { this.w *= -1; this.x -= this.w; }
            if (this.h < 0) { this.h *= -1; this.y -= this.h; }
        }
        copy() { return new BBox(this.x, this.y, this.w, this.h, this.context); }

        static from_corners(x1, y1, x2, y2, context) {
            if (x2 < x1) { const t = x1; x1 = x2; x2 = t; }
            if (y2 < y1) { const t = y1; y1 = y2; y2 = t; }
            return new BBox(x1, y1, x2 - x1, y2 - y1, context);
        }

        static from_points(points, context) {
            if (!points.length) return new BBox(0, 0, 0, 0);
            let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
            for (const p of points) {
                if (p.x < minX) minX = p.x;
                if (p.y < minY) minY = p.y;
                if (p.x > maxX) maxX = p.x;
                if (p.y > maxY) maxY = p.y;
            }
            return BBox.from_corners(minX, minY, maxX, maxY, context);
        }

        static combine(boxes, context) {
            let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
            for (const box of boxes) {
                if (box.w === 0 && box.h === 0) continue;
                if (box.x < minX) minX = box.x;
                if (box.y < minY) minY = box.y;
                if (box.x2 > maxX) maxX = box.x2;
                if (box.y2 > maxY) maxY = box.y2;
            }
            if (minX === Infinity) return new BBox(0, 0, 0, 0, context);
            return BBox.from_corners(minX, minY, maxX, maxY, context);
        }

        get valid() { return (this.w !== 0 || this.h !== 0); }
        get start() { return { x: this.x, y: this.y }; }
        get end() { return { x: this.x + this.w, y: this.y + this.h }; }
        get top_left() { return this.start; }
        get top_right() { return { x: this.x + this.w, y: this.y }; }
        get bottom_left() { return { x: this.x, y: this.y + this.h }; }
        get bottom_right() { return this.end; }
        get x2() { return this.x + this.w; }
        set x2(v) { this.w = v - this.x; if (this.w < 0) { this.w *= -1; this.x -= this.w; } }
        get y2() { return this.y + this.h; }
        set y2(v) { this.h = v - this.y; if (this.h < 0) { this.h *= -1; this.y -= this.h; } }
        get center() { return { x: this.x + this.w / 2, y: this.y + this.h / 2 }; }

        transform(mat) {
            const s = mat.transform(this.start);
            const e = mat.transform(this.end);
            return BBox.from_corners(s.x, s.y, e.x, e.y, this.context);
        }

        grow(dx, dy) {
            dy = dy ?? dx;
            return new BBox(this.x - dx, this.y - dy, this.w + dx * 2, this.h + dy * 2, this.context);
        }

        scale(s) {
            return BBox.from_points([
                { x: this.x * s, y: this.y * s },
                { x: this.x2 * s, y: this.y2 * s },
            ], this.context);
        }

        contains_point(v) {
            return v.x >= this.x && v.x <= this.x2 && v.y >= this.y && v.y <= this.y2;
        }

        constrain_point(v) {
            return { x: Math.min(Math.max(v.x, this.x), this.x2), y: Math.min(Math.max(v.y, this.y), this.y2) };
        }

        contains(other) {
            return this.contains_point(other.start) && this.contains_point(other.end);
        }
    }

    // ────────────────────────────────────────────
    // Camera2
    // ────────────────────────────────────────────
    class Camera2 {
        constructor(viewport_size, center, zoom, rotation, flipped) {
            this.viewport_size = new Vec2(viewport_size || { x: 0, y: 0 });
            this.center = new Vec2(center || { x: 0, y: 0 });
            this.zoom = zoom ?? 1;
            this.rotation = rotation instanceof Angle ? rotation : new Angle(rotation || 0);
            this.flipped = flipped ?? false;
        }

        translate(v, bound) {
            const new_pos = this.center.add(new Vec2(this.flipped ? -v.x : v.x, v.y));
            if (bound) {
                const c = bound.constrain_point(new_pos);
                new_pos.x = c.x; new_pos.y = c.y;
            }
            this.center = new_pos;
        }

        rotate(a) { this.rotation = this.rotation.add(a); }

        get matrix() {
            const mx = this.viewport_size.x / 2;
            const my = this.viewport_size.y / 2;
            const dx = this.center.x - this.center.x * this.zoom;
            const dy = this.center.y - this.center.y * this.zoom;
            const left = this.flipped
                ? -(this.center.x + mx) + dx
                : -(this.center.x - mx) + dx;
            const top = -(this.center.y - my) + dy;
            const scale = this.flipped ? -1 : 1;
            return Mat3.identity()
                .scale_self(scale, 1)
                .translate_self(left, top)
                .rotate_self(this.rotation)
                .scale_self(this.zoom, this.zoom);
        }

        get bbox() {
            const m = this.matrix.inverse();
            const start = m.transform({ x: 0, y: 0 });
            const end = m.transform({ x: this.viewport_size.x, y: this.viewport_size.y });
            return new BBox(start.x, start.y, end.x - start.x, end.y - start.y);
        }

        set bbox(bbox) {
            const zoom_w = this.viewport_size.x / bbox.w;
            const zoom_h = this.viewport_size.y / bbox.h;
            this.zoom = Math.min(zoom_w, zoom_h);
            this.center = new Vec2(bbox.x + bbox.w / 2, bbox.y + bbox.h / 2);
        }

        apply_to_canvas(ctx) {
            this.viewport_size.set(ctx.canvas.clientWidth, ctx.canvas.clientHeight);
            const m = this.matrix;
            ctx.setTransform(m.elements[0], m.elements[1], m.elements[3], m.elements[4], m.elements[6], m.elements[7]);
        }

        screen_to_world(v) {
            return this.matrix.inverse().transform(new Vec2(v));
        }

        world_to_screen(v) {
            return this.matrix.transform(new Vec2(v));
        }
    }

    // ────────────────────────────────────────────
    // RenderState & RenderStateStack
    // ────────────────────────────────────────────
    class RenderState {
        constructor() {
            this.matrix = Mat3.identity();
            this.fill = Color.black;
            this.stroke = Color.black;
            this.stroke_width = 0;
            this.flipped = false;
        }

        copy() {
            const s = new RenderState();
            s.matrix = this.matrix.copy();
            s.fill = this.fill.copy();
            s.stroke = this.stroke.copy();
            s.stroke_width = this.stroke_width;
            s.flipped = this.flipped;
            return s;
        }
    }

    class RenderStateStack {
        constructor() {
            this._stack = [new RenderState()];
        }

        get top() { return this._stack[this._stack.length - 1]; }
        get matrix() { return this.top.matrix; }
        set matrix(m) { this.top.matrix = m; }
        get fill() { return this.top.fill; }
        set fill(c) { this.top.fill = c; }
        get stroke() { return this.top.stroke; }
        set stroke(c) { this.top.stroke = c; }
        get stroke_width() { return this.top.stroke_width; }
        set stroke_width(n) { this.top.stroke_width = n; }
        get flipped() { return this.top.flipped; }
        set flipped(f) { this.top.flipped = f; }

        multiply(mat) { this.top.matrix.multiply_self(mat); }
        push() { this._stack.push(this.top.copy()); }
        pop() { this._stack.pop(); }
    }

    // ────────────────────────────────────────────
    // Export
    // ────────────────────────────────────────────
    globalThis.KiCMath = {
        Color,
        Angle,
        Vec2,
        Mat3,
        MathArc,
        BBox,
        Camera2,
        RenderState,
        RenderStateStack,
    };
})(window);
