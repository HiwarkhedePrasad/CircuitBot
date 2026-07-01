// --- S-Expression Parser ---
function parseSExpr(str) {
    const tokens = [];
    let current = '';
    let inString = false;
    
    for (let i = 0; i < str.length; i++) {
        let char = str[i];
        if (char === '"' && (i === 0 || str[i-1] !== '\\')) {
            inString = !inString;
            current += char;
        } else if (/\s/.test(char) && !inString) {
            if (current) { tokens.push(current); current = ''; }
        } else if ((char === '(' || char === ')') && !inString) {
            if (current) { tokens.push(current); current = ''; }
            tokens.push(char);
        } else {
            current += char;
        }
    }
    if (current) tokens.push(current);

    const root = [];
    const stack = [root];
    
    for (const token of tokens) {
        if (token === '(') {
            const newList = [];
            stack[stack.length - 1].push(newList);
            stack.push(newList);
        } else if (token === ')') {
            if (stack.length > 1) stack.pop();
        } else {
            let val = token;
            if (val.startsWith('"') && val.endsWith('"')) {
                val = val.slice(1, -1);
            }
            stack[stack.length - 1].push(val);
        }
    }
    return root[0] || null;
}

// Helper to get nested value e.g., (at 10 20) -> [10, 20]
function getAttr(node, name) {
    if (!Array.isArray(node)) return null;
    for (let i = 1; i < node.length; i++) {
        if (Array.isArray(node[i]) && node[i][0] === name) return node[i];
    }
    return null;
}

async function resolveAndParse(sexprStr, category, accOps = []) {
    const ast = parseSExpr(sexprStr);
    if (!ast) return accOps;
    
    let extendsName = null;
    
    function extractNodes(node) {
        if (!Array.isArray(node)) return;
        const type = node[0];
        
        switch (type) {
            case 'extends':
                extendsName = node[1];
                break;
            case 'symbol':
                for (let i = 1; i < node.length; i++) {
                    extractNodes(node[i]);
                }
                break;
            case 'rectangle':
            case 'polyline':
            case 'circle':
            case 'arc':
            case 'pin':
            case 'property':
            case 'text':
                accOps.push(node);
                break;
        }
    }
    
    if (ast[0] === 'kicad_symbol_lib') {
        for (let i = 1; i < ast.length; i++) {
            if (Array.isArray(ast[i]) && ast[i][0] === 'symbol') {
                extractNodes(ast[i]);
            }
        }
    } else if (ast[0] === 'symbol') {
        extractNodes(ast);
    }
    
    if (extendsName && window.appContext) {
        try {
            const parentId = `${category}:${extendsName}`;
            const parentSExpr = await window.appContext.fetchSExpr(parentId);
            await resolveAndParse(parentSExpr, category, accOps);
        } catch (e) {
            console.error("Failed to fetch parent symbol:", extendsName, e);
        }
    }
    
    return accOps;
}

// --- Symbol Preview Renderer (Canvas2D) ---
const COLORS = {
    symbolLine: '#E34E32',
    symbolFill: 'rgba(255, 240, 220, 0.08)',
    pinLine: '#E34E32',
    pinName: '#00A8A8',
    pinNum: '#E34E32',
    propertyRef: '#00A8A8',
    propertyVal: '#00A8A8',
    text: '#888888',
};

let currentOps = [];
let currentTransform = null;
let zoomLevel = 1;
let panX = 0, panY = 0;

function getCanvasAndCtx() {
    const canvas = document.getElementById('symbolCanvas');
    return { canvas, ctx: canvas ? canvas.getContext('2d') : null };
}

function renderOps(ops) {
    currentOps = ops;
    
    const { canvas, ctx } = getCanvasAndCtx();
    
    setupCanvasSize();
    
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    function updateBounds(x, y) {
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
    }
    
    // Pre-calculate bounds
    ops.forEach(op => {
        const type = op[0];
        if (type === 'rectangle') {
            const start = getAttr(op, 'start');
            const end = getAttr(op, 'end');
            if (start) { updateBounds(parseFloat(start[1]), parseFloat(start[2])); }
            if (end) { updateBounds(parseFloat(end[1]), parseFloat(end[2])); }
        } else if (type === 'polyline') {
            const pts = getAttr(op, 'pts');
            if (pts) {
                for (let i = 1; i < pts.length; i++) {
                    if (pts[i][0] === 'xy') {
                        updateBounds(parseFloat(pts[i][1]), parseFloat(pts[i][2]));
                    }
                }
            }
        } else if (type === 'circle') {
            const center = getAttr(op, 'center');
            const rad = getAttr(op, 'radius');
            if (center && rad) {
                const cx = parseFloat(center[1]), cy = parseFloat(center[2]);
                const r = parseFloat(rad[1]);
                updateBounds(cx - r, cy - r);
                updateBounds(cx + r, cy + r);
            }
        } else if (type === 'pin') {
            const at = getAttr(op, 'at');
            const lenNode = getAttr(op, 'length');
            if (at && lenNode) {
                let x = parseFloat(at[1]), y = parseFloat(at[2]);
                let len = parseFloat(lenNode[1]);
                let ang = parseFloat(at[3] || 0);
                updateBounds(x, y);
                updateBounds(x + Math.cos(ang * Math.PI/180)*len, y + Math.sin(ang * Math.PI/180)*len);
            }
        } else if (type === 'property' || type === 'text') {
            const at = getAttr(op, 'at');
            const hide = getAttr(op, 'hide');
            if (at && (!hide || hide[1] !== 'yes')) {
                updateBounds(parseFloat(at[1]), parseFloat(at[2]));
            }
        }
    });
    
    if (minX === Infinity) { minX = -10; maxX = 10; minY = -10; maxY = 10; }
    
    const margin = 5;
    minX -= margin; maxX += margin; minY -= margin; maxY += margin;
    const w = maxX - minX;
    const h = maxY - minY;
    
    const scale = Math.min(canvas.width / w, canvas.height / h) * 0.85;
    const cx = canvas.width / 2;
    const cy = canvas.height / 2;
    const midX = (minX + maxX) / 2;
    const midY = (minY + maxY) / 2;
    
    currentTransform = {
        baseScale: scale,
        cx, cy, midX, midY
    };
    
    zoomLevel = 1;
    panX = 0;
    panY = 0;
    
    drawSymbol();
}

function setupCanvasSize() {
    const { canvas } = getCanvasAndCtx();
    const container = canvas.parentElement;
    const dpr = window.devicePixelRatio || 1;
    const rect = container.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
}

// Generic label collision solver
function resolveLabelCollisions(ops, ctx) {
    const labels = [];

    function fontSizeOf(op) {
        let size = 1.27;
        const effects = getAttr(op, 'effects');
        if (effects) {
            const font = getAttr(effects, 'font');
            if (font) {
                const s = getAttr(font, 'size');
                if (s) size = parseFloat(s[2]);
            }
        }
        return size;
    }

    ops.forEach(op => {
        const type = op[0];
        if (type === 'property' || type === 'text') {
            const at = getAttr(op, 'at');
            const hide = getAttr(op, 'hide');
            if (!at || (hide && hide[1] === 'yes')) return;
            const txt = type === 'property' ? op[2] : op[1];
            if (!txt || txt === '"~"') return;
            const size = fontSizeOf(op);
            ctx.font = `${size}px "JetBrains Mono", "Fira Code", monospace`;
            const w = ctx.measureText(txt).width;
            labels.push({
                op, x: parseFloat(at[1]), y: parseFloat(at[2]),
                w, h: size * 1.4, dx: 0, dy: 0, axis: 'y',
                anchorX: null, anchorY: null, maxLead: Infinity,
            });
        } else if (type === 'pin') {
            const at = getAttr(op, 'at');
            const lenNode = getAttr(op, 'length');
            const numNode = getAttr(op, 'number');
            if (!at || !lenNode || !numNode || numNode[1] === '"~"') return;
            const x = parseFloat(at[1]), y = parseFloat(at[2]);
            const len = parseFloat(lenNode[1]);
            const angDeg = parseFloat(at[3] || 0);
            const size = fontSizeOf(getAttr(op, 'name') || op);
            ctx.font = `${size}px "JetBrains Mono", "Fira Code", monospace`;
            const w = ctx.measureText(numNode[1]).width;
            let numx, numy, axis;
            if (angDeg === 0) { numx = x + len / 2; numy = y + 0.3 + size * 0.7; axis = 'y'; }
            else if (angDeg === 180) { numx = x - len / 2; numy = y + 0.3 + size * 0.7; axis = 'y'; }
            else if (angDeg === 90) { numx = x - 0.3 - w / 2; numy = y + len / 2; axis = 'x'; }
            else if (angDeg === 270) { numx = x - 0.3 - w / 2; numy = y - len / 2; axis = 'x'; }
            else return;
            labels.push({
                op, x: numx, y: numy, w, h: size * 1.4, dx: 0, dy: 0, axis,
                anchorX: x + Math.cos(angDeg * Math.PI / 180) * len / 2,
                anchorY: y + Math.sin(angDeg * Math.PI / 180) * len / 2,
                maxLead: Math.max(len, 1.27),
            });
        }
    });

    const PUSH = 0.45;
    for (let pass = 0; pass < 10; pass++) {
        let moved = false;
        for (let i = 0; i < labels.length; i++) {
            for (let j = i + 1; j < labels.length; j++) {
                const A = labels[i], B = labels[j];
                const ax = A.x + A.dx, ay = A.y + A.dy;
                const bx = B.x + B.dx, by = B.y + B.dy;
                const ovX = (A.w + B.w) / 2 - Math.abs(ax - bx);
                const ovY = (A.h + B.h) / 2 - Math.abs(ay - by);
                if (ovX <= 0.01 || ovY <= 0.01) continue;
                if (A.axis === 'y' || B.axis === 'y') {
                    const dir = ay >= by ? 1 : -1;
                    if (A.axis === 'y') A.dy += PUSH * dir; else A.dx += PUSH * dir;
                    if (B.axis === 'y') B.dy -= PUSH * dir; else B.dx -= PUSH * dir;
                } else {
                    const dir = ax >= bx ? 1 : -1;
                    A.dx += PUSH * dir;
                    B.dx -= PUSH * dir;
                }
                moved = true;
            }
        }
        if (!moved) break;
    }

    const offsets = new Map();
    const leaders = [];
    labels.forEach(l => {
        if (l.dx || l.dy) offsets.set(l.op, { dx: l.dx, dy: l.dy });
        if (l.anchorX !== null && Math.hypot(l.dx, l.dy) > l.maxLead) {
            leaders.push({ x1: l.anchorX, y1: l.anchorY, x2: l.x + l.dx, y2: l.y + l.dy });
        }
    });
    return { offsets, leaders };
}

function drawSymbol() {
    const ops = currentOps;
    if (!currentTransform || ops.length === 0) return;
    
    const { canvas, ctx } = getCanvasAndCtx();
    const t = currentTransform;
    
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    
    ctx.save();
    ctx.translate(t.cx + panX, t.cy + panY);
    ctx.scale(t.baseScale * zoomLevel, -t.baseScale * zoomLevel);
    ctx.translate(-t.midX, -t.midY);
    
    const order = { 'rectangle': 1, 'circle': 1, 'arc': 1, 'polyline': 1, 'pin': 2, 'property': 3, 'text': 3 };
    ops.sort((a, b) => (order[a[0]] || 0) - (order[b[0]] || 0));

    function applyStyles(op) {
        const stroke = getAttr(op, 'stroke');
        const fill = getAttr(op, 'fill');
        
        ctx.strokeStyle = COLORS.symbolLine;
        ctx.fillStyle = 'transparent';
        
        let width = 0.254;
        if (stroke) {
            const wAttr = getAttr(stroke, 'width');
            if (wAttr) width = parseFloat(wAttr[1]);
        }
        ctx.lineWidth = width;
        
        if (fill && fill[1] === '(type background)') {
            ctx.fillStyle = COLORS.symbolFill;
        } else if (fill && fill[1] === '(type solid)') {
            ctx.fillStyle = COLORS.symbolLine;
        }
    }

    function getFontSize(op) {
        let size = 1.27;
        const effects = getAttr(op, 'effects');
        if (effects) {
            const font = getAttr(effects, 'font');
            if (font) {
                const s = getAttr(font, 'size');
                if (s) size = parseFloat(s[2]);
            }
        }
        return size;
    }

    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    const { offsets: labelOffsets, leaders: labelLeaders } = resolveLabelCollisions(ops, ctx);

    const renderedPinNames = [];

    ops.forEach(op => {
        const type = op[0];
        ctx.save();
        
        if (type === 'rectangle') {
            const start = getAttr(op, 'start');
            const end = getAttr(op, 'end');
            if (start && end) {
                applyStyles(op);
                const x1 = parseFloat(start[1]), y1 = parseFloat(start[2]);
                const x2 = parseFloat(end[1]), y2 = parseFloat(end[2]);
                const rx = Math.min(x1, x2), ry = Math.min(y1, y2);
                const rw = Math.abs(x2 - x1), rh = Math.abs(y2 - y1);
                
                if (ctx.fillStyle !== 'transparent') ctx.fillRect(rx, ry, rw, rh);
                ctx.strokeRect(rx, ry, rw, rh);
            }
        } else if (type === 'polyline') {
            const pts = getAttr(op, 'pts');
            if (pts) {
                applyStyles(op);
                ctx.beginPath();
                let first = true;
                for (let i = 1; i < pts.length; i++) {
                    if (pts[i][0] === 'xy') {
                        const x = parseFloat(pts[i][1]), y = parseFloat(pts[i][2]);
                        if (first) { ctx.moveTo(x, y); first = false; }
                        else { ctx.lineTo(x, y); }
                    }
                }
                if (ctx.fillStyle !== 'transparent') ctx.fill();
                ctx.stroke();
            }
        } else if (type === 'circle') {
            const center = getAttr(op, 'center');
            const rad = getAttr(op, 'radius');
            if (center && rad) {
                applyStyles(op);
                ctx.beginPath();
                ctx.arc(parseFloat(center[1]), parseFloat(center[2]), parseFloat(rad[1]), 0, Math.PI * 2);
                if (ctx.fillStyle !== 'transparent') ctx.fill();
                ctx.stroke();
            }
        } else if (type === 'arc') {
            const start = getAttr(op, 'start');
            const end = getAttr(op, 'end');
            const mid = getAttr(op, 'mid');
            if (start && end && mid) {
                applyStyles(op);
                ctx.beginPath();
                ctx.moveTo(parseFloat(start[1]), parseFloat(start[2]));
                ctx.quadraticCurveTo(parseFloat(mid[1]), parseFloat(mid[2]), parseFloat(end[1]), parseFloat(end[2]));
                ctx.stroke();
            }
        } else if (type === 'pin') {
            const at = getAttr(op, 'at');
            const lenNode = getAttr(op, 'length');
            if (at && lenNode) {
                const x = parseFloat(at[1]), y = parseFloat(at[2]);
                const len = parseFloat(lenNode[1]);
                const angDeg = parseFloat(at[3] || 0);
                const ang = angDeg * Math.PI / 180;
                
                ctx.strokeStyle = COLORS.pinLine;
                ctx.lineWidth = 0.254;
                ctx.beginPath();
                ctx.moveTo(x, y);
                const ex = x + Math.cos(ang) * len;
                const ey = y + Math.sin(ang) * len;
                ctx.lineTo(ex, ey);
                ctx.stroke();
                
                const nameNode = getAttr(op, 'name');
                const numNode = getAttr(op, 'number');
                
                ctx.save();
                const size = getFontSize(nameNode || op);
                ctx.font = `${size}px "JetBrains Mono", "Fira Code", monospace`;
                
                if (nameNode && nameNode[1] !== '"~"') {
                    ctx.fillStyle = COLORS.pinName;
                    let nx = ex, ny = ey;
                    const nameText = nameNode[1];

                    let shouldRender = true;
                    for (const prev of renderedPinNames) {
                        if (prev.name === nameText && Math.abs(ex - prev.x) < 3.0 && Math.abs(ey - prev.y) < 3.0) {
                            shouldRender = false;
                            break;
                        }
                    }

                    if (shouldRender) {
                        if (angDeg === 0) {
                            nx += 0.5;
                            ctx.textAlign = 'left';
                            ctx.textBaseline = 'middle';
                            ctx.save();
                            ctx.translate(nx, ny);
                            ctx.scale(1, -1);
                            ctx.fillText(nameText, 0, 0);
                            ctx.restore();
                        } else if (angDeg === 180) {
                            nx -= 0.5;
                            ctx.textAlign = 'right';
                            ctx.textBaseline = 'middle';
                            ctx.save();
                            ctx.translate(nx, ny);
                            ctx.scale(1, -1);
                            ctx.fillText(nameText, 0, 0);
                            ctx.restore();
                        } else if (angDeg === 90 || angDeg === 270) {
                            nx -= 0.8;
                            ctx.textAlign = 'center';
                            ctx.textBaseline = 'middle';
                            ctx.save();
                            ctx.translate(nx, ny);
                            ctx.scale(1, -1);
                            ctx.rotate(-Math.PI / 2);
                            ctx.fillText(nameText, 0, 0);
                            ctx.restore();
                        }
                        renderedPinNames.push({ name: nameText, x: ex, y: ey });
                    }
                }
                
                if (numNode && numNode[1] !== '"~"') {
                    ctx.fillStyle = COLORS.pinNum;
                    let numx = x, numy = y;
                    if (angDeg === 0) { numx = x + len/2; numy = y + 0.3; ctx.textAlign = 'center'; ctx.textBaseline = 'bottom'; }
                    else if (angDeg === 180) { numx = x - len/2; numy = y + 0.3; ctx.textAlign = 'center'; ctx.textBaseline = 'bottom'; }
                    else if (angDeg === 90) { numx = x - 0.3; numy = y + len/2; ctx.textAlign = 'right'; ctx.textBaseline = 'middle'; }
                    else if (angDeg === 270) { numx = x - 0.3; numy = y - len/2; ctx.textAlign = 'right'; ctx.textBaseline = 'middle'; }

                    const no = labelOffsets.get(op) || { dx: 0, dy: 0 };
                    numx += no.dx;
                    numy += no.dy;

                    ctx.save();
                    ctx.translate(numx, numy);
                    ctx.scale(1, -1);
                    ctx.fillText(numNode[1], 0, 0);
                    ctx.restore();
                }
                ctx.restore();
            }
        } else if (type === 'property' || type === 'text') {
            const at = getAttr(op, 'at');
            const hide = getAttr(op, 'hide');
            
            if (at && (!hide || hide[1] !== 'yes')) {
                const txt = type === 'property' ? op[2] : op[1];
                if (txt !== '"~"') {
                    ctx.save();
                    const x = parseFloat(at[1]), y = parseFloat(at[2]);
                    const ang = parseFloat(at[3] || 0);
                    
                    const lo = labelOffsets.get(op) || { dx: 0, dy: 0 };
                    const ox = lo.dx;
                    const oy = lo.dy;
                    
                    ctx.translate(x + ox, y + oy);
                    ctx.scale(1, -1);
                    
                    if (ang !== 0) ctx.rotate(-ang * Math.PI / 180);
                    
                    ctx.fillStyle = (op[1] === '"Reference"') ? COLORS.propertyRef : COLORS.propertyVal;
                    if (type === 'text') ctx.fillStyle = COLORS.text;
                    
                    const size = getFontSize(op);
                    ctx.font = `${size}px "JetBrains Mono", "Fira Code", monospace`;
                    ctx.textAlign = 'center';
                    ctx.textBaseline = 'middle';
                    
                    ctx.fillText(txt, 0, 0);
                    ctx.restore();
                }
            }
        }
        ctx.restore();
    });
    
    if (labelLeaders.length) {
        ctx.save();
        ctx.strokeStyle = COLORS.pinNum;
        ctx.lineWidth = 0.1;
        ctx.setLineDash([0.4, 0.4]);
        labelLeaders.forEach(l => {
            ctx.beginPath();
            ctx.moveTo(l.x1, l.y1);
            ctx.lineTo(l.x2, l.y2);
            ctx.stroke();
        });
        ctx.restore();
    }

    ctx.restore();
}
