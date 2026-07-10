// Stroke font renderer — Hershey vector fonts from KiCad
// Based on KiCanvas StrokeFont implementation

class StrokeFontRenderer {
    static FONT_SCALE = 1 / 21;
    static FONT_OFFSET = -10;
    static ITALIC_TILT = 1 / 8;
    static SPACE_WIDTH = 0.6;
    static INTER_CHAR = 0.2;

    constructor() {
        this._glyphs = new Map();
        this._sharedGlyphs = [];

        // Pre-decode shared glyphs
        for (const data of SHARED_GLYPHS) {
            this._sharedGlyphs.push(this._decodeGlyph(data));
        }

        // Load first 256 glyphs (ASCII + some extended)
        const count = Math.min(GLYPH_DATA.length, 256);
        for (let i = 0; i < count; i++) {
            this._loadGlyph(i);
        }
    }

    _loadGlyph(idx) {
        const data = GLYPH_DATA[idx];
        if (typeof data === 'string') {
            this._glyphs.set(idx, this._decodeGlyph(data));
        } else if (typeof data === 'number') {
            this._glyphs.set(idx, this._sharedGlyphs[data]);
        }
    }

    _getGlyph(c) {
        const idx = c.charCodeAt(0) - 32;
        if (idx < 0 || idx >= GLYPH_DATA.length) {
            return this._getGlyph('?');
        }
        if (!this._glyphs.has(idx)) {
            this._loadGlyph(idx);
        }
        return this._glyphs.get(idx);
    }

    _decodeGlyph(glyphData) {
        let startX = 0;
        let endX = 0;
        let width = 0;
        let minY = 0;
        let maxY = 0;
        const strokes = [];
        let points = null;

        for (let i = 0; i < glyphData.length; i += 2) {
            const c1 = glyphData[i];
            const c2 = glyphData[i + 1];
            const x = c1.charCodeAt(0) - 82; // ord('R') = 82
            const y = c2.charCodeAt(0) - 82;

            if (i < 2) {
                startX = x * StrokeFontRenderer.FONT_SCALE;
                endX = y * StrokeFontRenderer.FONT_SCALE;
                width = endX - startX;
            } else if (c1 === ' ' && c2 === 'R') {
                points = null;
            } else {
                const px = x * StrokeFontRenderer.FONT_SCALE - startX;
                const py = (y + StrokeFontRenderer.FONT_OFFSET) * StrokeFontRenderer.FONT_SCALE;
                if (points === null) {
                    points = [];
                    strokes.push(points);
                }
                minY = Math.min(minY, py);
                maxY = Math.max(maxY, py);
                points.push({ x: px, y: py });
            }
        }

        return { strokes, width, minY, maxY };
    }

    renderText(ctx, text, x, y, size, options = {}) {
        const {
            rotation = 0,
            mirror = false,
            italic = false,
            halign = 'center',
            valign = 'center',
            strokeWidth = 0.15,
            color = '#e9f7f4',
        } = options;

        const lines = text.split('\n');
        const glyphSize = size;
        const lineHeight = glyphSize * StrokeFontRenderer.INTER_CHAR * 6;

        // Calculate total text extents for alignment
        const { totalWidth, totalHeight } = this._measureLines(lines, glyphSize, italic);

        ctx.save();
        ctx.globalAlpha = 1;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.strokeStyle = color;
        ctx.fillStyle = color;

        // Position and rotate at anchor point
        ctx.translate(x, y);
        ctx.rotate(-(rotation * Math.PI / 180));

        let startY;
        if (valign === 'top') startY = -totalHeight / 2;
        else if (valign === 'bottom') startY = totalHeight / 2;
        else startY = 0;

        for (let li = 0; li < lines.length; li++) {
            const line = lines[li];
            const lineWidth = this._measureLine(line, glyphSize, italic);
            const lineY = startY + (li - (lines.length - 1) / 2) * lineHeight;

            let startX;
            if (halign === 'left') startX = -lineWidth / 2;
            else if (halign === 'right') startX = lineWidth / 2;
            else startX = 0;

            let cursorX = startX - lineWidth / 2;
            const cursorY = lineY;

            for (const c of line) {
                if (c === ' ') {
                    cursorX += Math.round(glyphSize * StrokeFontRenderer.SPACE_WIDTH);
                    continue;
                }
                if (c === '\t') {
                    cursorX += Math.round(glyphSize * 4 * 0.82);
                    continue;
                }

                const glyph = this._getGlyph(c);
                if (!glyph) continue;

                const extents = glyph.width * glyphSize;

                // Draw each stroke
                for (const stroke of glyph.strokes) {
                    if (stroke.length < 2) continue;
                    ctx.beginPath();
                    let first = true;
                    for (const pt of stroke) {
                        let sx = pt.x * glyphSize + cursorX;
                        let sy = pt.y * glyphSize + cursorY;
                        if (italic) {
                            sx -= sy * StrokeFontRenderer.ITALIC_TILT;
                        }
                        if (mirror) {
                            sx = -sx;
                        }
                        if (first) {
                            ctx.moveTo(sx, sy);
                            first = false;
                        } else {
                            ctx.lineTo(sx, sy);
                        }
                    }
                    ctx.stroke();
                }

                if (italic) {
                    cursorX += Math.round(extents - extents * StrokeFontRenderer.ITALIC_TILT * 0); // simplified
                }
                cursorX += Math.round(extents);
            }
        }

        ctx.restore();
    }

    _measureLine(text, glyphSize, italic) {
        let width = 0;
        for (const c of text) {
            if (c === ' ') {
                width += Math.round(glyphSize * StrokeFontRenderer.SPACE_WIDTH);
                continue;
            }
            if (c === '\t') {
                width += Math.round(glyphSize * 4 * 0.82);
                continue;
            }
            const glyph = this._getGlyph(c);
            if (!glyph) continue;
            width += Math.round(glyph.width * glyphSize);
        }
        return width;
    }

    _measureLines(lines, glyphSize, italic) {
        let totalWidth = 0;
        for (const line of lines) {
            totalWidth = Math.max(totalWidth, this._measureLine(line, glyphSize, italic));
        }
        const totalHeight = lines.length * glyphSize * StrokeFontRenderer.INTER_CHAR * 6;
        return { totalWidth, totalHeight };
    }
}

// Singleton
let _instance = null;
function getStrokeFont() {
    if (!_instance) _instance = new StrokeFontRenderer();
    return _instance;
}
