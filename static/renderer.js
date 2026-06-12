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

// --- Professional Renderer ---
const COLORS = {
    symbolLine: '#E34E32',   // KiCad deep red
    symbolFill: 'rgba(255, 240, 220, 0.08)', // Faint fill for shapes
    pinLine: '#E34E32',
    pinName: '#00A8A8',      // Cyan/Teal
    pinNum: '#E34E32',       // Same as line
    propertyRef: '#00A8A8',  // Ref string
    propertyVal: '#00A8A8',  // Value string
    text: '#888888',
};

// Zoom and pan state
let currentOps = [];
let currentTransform = null;
let zoomLevel = 1;
let panX = 0, panY = 0;
let zoomListenersAttached = false;
let currentSchematic = null; // Schematic instance for multi-component mode

function zoomIn() {
    zoomLevel = Math.min(zoomLevel * 1.3, 50);
    drawCurrentMode();
}

function zoomOut() {
    zoomLevel = Math.max(zoomLevel / 1.3, 0.05);
    drawCurrentMode();
}

function resetZoom() {
    zoomLevel = 1;
    panX = 0;
    panY = 0;
    drawCurrentMode();
}

function getCanvasAndCtx() {
    const canvas = document.getElementById('compCanvas');
    return { canvas, ctx: canvas.getContext('2d') };
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
    attachZoomHandlers();
}

let isDragging = false;
let dragStartX = 0;
let dragStartY = 0;

function attachZoomHandlers() {
    if (zoomListenersAttached) return;
    zoomListenersAttached = true;
    
    const canvas = document.getElementById('compCanvas');
    
    canvas.addEventListener('wheel', handleWheel, { passive: false });
    canvas.addEventListener('mousedown', handleMouseDown);
    canvas.addEventListener('mousemove', handleMouseMove);
    canvas.addEventListener('mouseup', handleMouseUp);
    canvas.addEventListener('mouseleave', handleMouseUp);
}

function handleMouseDown(e) {
    if (!currentTransform) return;
    isDragging = true;
    dragStartX = e.clientX;
    dragStartY = e.clientY;
    e.target.style.cursor = 'grabbing';
}

function handleMouseMove(e) {
    if (!isDragging || !currentTransform) return;
    const dx = e.clientX - dragStartX;
    const dy = e.clientY - dragStartY;
    dragStartX = e.clientX;
    dragStartY = e.clientY;
    panX += dx;
    panY += dy;
    drawCurrentMode();
}

function handleMouseUp(e) {
    isDragging = false;
    if (e && e.target) e.target.style.cursor = 'grab';
}

function handleWheel(e) {
    e.preventDefault();
    if (!currentTransform) return;
    
    const { canvas } = getCanvasAndCtx();
    const rect = canvas.getBoundingClientRect();
    const mouseX = (e.clientX - rect.left) * (canvas.width / rect.width);
    const mouseY = (e.clientY - rect.top) * (canvas.height / rect.height);
    
    const t = currentTransform;
    const oldZoom = zoomLevel;
    const delta = -e.deltaY;
    const factor = delta > 0 ? 1.1 : 1 / 1.1;
    const newZoom = Math.min(Math.max(oldZoom * factor, 0.05), 50);
    
    const r = newZoom / oldZoom;
    panX = r * panX + (1 - r) * (mouseX - t.cx);
    panY = r * panY + (1 - r) * (mouseY - t.cy);
    zoomLevel = newZoom;
    
    drawCurrentMode();
}

function setupCanvasSize() {
    const { canvas } = getCanvasAndCtx();
    const container = canvas.parentElement;
    const dpr = window.devicePixelRatio || 1;
    const rect = container.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
}

function drawSymbol() {
    const ops = currentOps;
    if (!currentTransform || ops.length === 0) return;
    
    const { canvas, ctx } = getCanvasAndCtx();
    const t = currentTransform;
    
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    
    // Apply combined transform with zoom and pan
    ctx.save();
    ctx.translate(t.cx + panX, t.cy + panY);
    ctx.scale(t.baseScale * zoomLevel, -t.baseScale * zoomLevel);
    ctx.translate(-t.midX, -t.midY);
    
    // Sort ops: fills/shapes first, then pins, then text
    const order = { 'rectangle': 1, 'circle': 1, 'arc': 1, 'polyline': 1, 'pin': 2, 'property': 3, 'text': 3 };
    ops.sort((a, b) => (order[a[0]] || 0) - (order[b[0]] || 0));

    function applyStyles(op) {
        const stroke = getAttr(op, 'stroke');
        const fill = getAttr(op, 'fill');
        
        ctx.strokeStyle = COLORS.symbolLine;
        ctx.fillStyle = 'transparent';
        
        let width = 0.254; // default KiCad width
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
        let size = 1.27; // default mm
        const effects = getAttr(op, 'effects');
        if (effects) {
            const font = getAttr(effects, 'font');
            if (font) {
                const s = getAttr(font, 'size');
                if (s) size = parseFloat(s[2]); // Y size
            }
        }
        return size;
    }

    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    // --- Overlap detection for properties ---
    // Group property/text ops by position to detect overlaps
    const propGroups = new Map();
    ops.forEach(op => {
        const type = op[0];
        if (type !== 'property' && type !== 'text') return;
        const at = getAttr(op, 'at');
        const hide = getAttr(op, 'hide');
        if (!at || (hide && hide[1] === 'yes')) return;
        const txt = type === 'property' ? op[2] : op[1];
        if (txt === '"~"') return;
        const key = `${parseFloat(at[1])},${parseFloat(at[2])}`;
        if (!propGroups.has(key)) propGroups.set(key, []);
        propGroups.get(key).push(op);
    });

    // Track property position offsets for overlaps
    const propOffsetX = new Map();
    const propOffsetY = new Map();

    propGroups.forEach((group, key) => {
        if (group.length > 1) {
            const lineSpacing = 1.8;
            const totalHeight = (group.length - 1) * lineSpacing;
            group.forEach((op, i) => {
                propOffsetY.set(op, -totalHeight / 2 + i * lineSpacing);
                propOffsetX.set(op, 0);
            });
        }
    });

    // --- Pin name deduplication ---
    // For pins with same name at close positions, only render once
    const renderedPinNames = [];

    // --- Overlap detection for pin numbers ---
    const pinNumPositions = [];
    ops.forEach(op => {
        if (op[0] !== 'pin') return;
        const at = getAttr(op, 'at');
        const lenNode = getAttr(op, 'length');
        const numNode = getAttr(op, 'number');
        if (!at || !lenNode || !numNode) return;
        if (numNode[1] === '"~"') return;

        const x = parseFloat(at[1]), y = parseFloat(at[2]);
        const len = parseFloat(lenNode[1]);
        const angDeg = parseFloat(at[3] || 0);
        const ang = angDeg * Math.PI / 180;

        let numx = x, numy = y;
        if (angDeg === 0) { numx = x + len / 2; numy = y + 0.3; }
        else if (angDeg === 180) { numx = x - len / 2; numy = y + 0.3; }
        else if (angDeg === 90) { numx = x - 0.3; numy = y + len / 2; }
        else if (angDeg === 270) { numx = x - 0.3; numy = y - len / 2; }

        pinNumPositions.push({ op, numx, numy });
    });

    const pinNumOffsets = new Map();
    const numThreshold = 2.5;
    const numLineSpacing = 1.5;

    pinNumPositions.sort((a, b) => a.numx - b.numx || a.numy - b.numy);

    const pinNumGroups = [];
    pinNumPositions.forEach(p => {
        let added = false;
        for (const g of pinNumGroups) {
            const last = g[g.length - 1];
            if (Math.abs(p.numx - last.numx) < numThreshold && Math.abs(p.numy - last.numy) < numThreshold) {
                g.push(p);
                added = true;
                break;
            }
        }
        if (!added) pinNumGroups.push([p]);
    });

    pinNumGroups.forEach(group => {
        if (group.length <= 1) return;
        const totalHeight = (group.length - 1) * numLineSpacing;
        group.forEach((p, i) => {
            pinNumOffsets.set(p.op, -totalHeight / 2 + i * numLineSpacing);
        });
    });

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
                ctx.font = `${size}px "Segoe UI", Arial, sans-serif`;
                
                if (nameNode && nameNode[1] !== '"~"') {
                    ctx.fillStyle = COLORS.pinName;
                    let nx = ex, ny = ey;
                    const nameText = nameNode[1];

                    // Deduplication: skip if same name already rendered at nearby position
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
                            // Top/bottom pins: write name vertically (rotated 90°)
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

                    const numOffset = pinNumOffsets.get(op) || 0;
                    numy += numOffset;

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
            
            // Do NOT render hidden properties!
            if (at && (!hide || hide[1] !== 'yes')) {
                const txt = type === 'property' ? op[2] : op[1];
                if (txt !== '"~"') {
                    ctx.save();
                    const x = parseFloat(at[1]), y = parseFloat(at[2]);
                    const ang = parseFloat(at[3] || 0);
                    
                    // Apply overlap offset
                    const ox = propOffsetX.get(op) || 0;
                    const oy = propOffsetY.get(op) || 0;
                    
                    ctx.translate(x + ox, y + oy);
                    ctx.scale(1, -1);
                    
                    if (ang !== 0) ctx.rotate(-ang * Math.PI / 180);
                    
                    ctx.fillStyle = (op[1] === '"Reference"') ? COLORS.propertyRef : COLORS.propertyVal;
                    if (type === 'text') ctx.fillStyle = COLORS.text;
                    
                    const size = getFontSize(op);
                    ctx.font = `${size}px "Segoe UI", Arial, sans-serif`;
                    ctx.textAlign = 'center';
                    ctx.textBaseline = 'middle';
                    
                    ctx.fillText(txt, 0, 0);
                    ctx.restore();
                }
            }
        }
        ctx.restore();
    });
    
    ctx.restore();
}

// --- Render a single component at a specific offset position ---
function renderComponentAt(ctx, ops, offsetX, offsetY, globalOpState) {
    const { renderedPinNames: gpn } = globalOpState;

    ops.forEach(op => {
        const type = op[0];
        ctx.save();

        if (type === 'rectangle') {
            const start = getAttr(op, 'start');
            const end = getAttr(op, 'end');
            if (start && end) {
                ctx.strokeStyle = COLORS.symbolLine;
                ctx.fillStyle = COLORS.symbolFill;
                let width = 0.254;
                const stroke = getAttr(op, 'stroke');
                if (stroke) { const wAttr = getAttr(stroke, 'width'); if (wAttr) width = parseFloat(wAttr[1]); }
                ctx.lineWidth = width;
                const fill = getAttr(op, 'fill');
                if (fill && fill[1] === '(type background)') ctx.fillStyle = COLORS.symbolFill;
                else if (fill && fill[1] === '(type solid)') ctx.fillStyle = COLORS.symbolLine;
                else ctx.fillStyle = 'transparent';

                const x1 = parseFloat(start[1]) + offsetX, y1 = parseFloat(start[2]) + offsetY;
                const x2 = parseFloat(end[1]) + offsetX, y2 = parseFloat(end[2]) + offsetY;
                const rx = Math.min(x1, x2), ry = Math.min(y1, y2);
                const rw = Math.abs(x2 - x1), rh = Math.abs(y2 - y1);
                if (ctx.fillStyle !== 'transparent') ctx.fillRect(rx, ry, rw, rh);
                ctx.strokeRect(rx, ry, rw, rh);
            }
        } else if (type === 'polyline') {
            const pts = getAttr(op, 'pts');
            if (pts) {
                ctx.strokeStyle = COLORS.symbolLine;
                ctx.fillStyle = 'transparent';
                let width = 0.254;
                const stroke = getAttr(op, 'stroke');
                if (stroke) { const wAttr = getAttr(stroke, 'width'); if (wAttr) width = parseFloat(wAttr[1]); }
                ctx.lineWidth = width;
                const fill = getAttr(op, 'fill');
                if (fill && fill[1] === '(type background)') ctx.fillStyle = COLORS.symbolFill;
                else if (fill && fill[1] === '(type solid)') ctx.fillStyle = COLORS.symbolLine;

                ctx.beginPath();
                let first = true;
                for (let i = 1; i < pts.length; i++) {
                    if (pts[i][0] === 'xy') {
                        const x = parseFloat(pts[i][1]) + offsetX, y = parseFloat(pts[i][2]) + offsetY;
                        if (first) { ctx.moveTo(x, y); first = false; } else { ctx.lineTo(x, y); }
                    }
                }
                if (ctx.fillStyle !== 'transparent') ctx.fill();
                ctx.stroke();
            }
        } else if (type === 'circle') {
            const center = getAttr(op, 'center');
            const rad = getAttr(op, 'radius');
            if (center && rad) {
                ctx.strokeStyle = COLORS.symbolLine;
                ctx.fillStyle = 'transparent';
                let width = 0.254;
                const stroke = getAttr(op, 'stroke');
                if (stroke) { const wAttr = getAttr(stroke, 'width'); if (wAttr) width = parseFloat(wAttr[1]); }
                ctx.lineWidth = width;
                const fill = getAttr(op, 'fill');
                if (fill && fill[1] === '(type background)') ctx.fillStyle = COLORS.symbolFill;
                else if (fill && fill[1] === '(type solid)') ctx.fillStyle = COLORS.symbolLine;

                ctx.beginPath();
                ctx.arc(parseFloat(center[1]) + offsetX, parseFloat(center[2]) + offsetY, parseFloat(rad[1]), 0, Math.PI * 2);
                if (ctx.fillStyle !== 'transparent') ctx.fill();
                ctx.stroke();
            }
        } else if (type === 'arc') {
            const start = getAttr(op, 'start');
            const end = getAttr(op, 'end');
            const mid = getAttr(op, 'mid');
            if (start && end && mid) {
                ctx.strokeStyle = COLORS.symbolLine;
                let width = 0.254;
                const stroke = getAttr(op, 'stroke');
                if (stroke) { const wAttr = getAttr(stroke, 'width'); if (wAttr) width = parseFloat(wAttr[1]); }
                ctx.lineWidth = width;

                ctx.beginPath();
                ctx.moveTo(parseFloat(start[1]) + offsetX, parseFloat(start[2]) + offsetY);
                ctx.quadraticCurveTo(parseFloat(mid[1]) + offsetX, parseFloat(mid[2]) + offsetY, parseFloat(end[1]) + offsetX, parseFloat(end[2]) + offsetY);
                ctx.stroke();
            }
        } else if (type === 'pin') {
            const at = getAttr(op, 'at');
            const lenNode = getAttr(op, 'length');
            if (at && lenNode) {
                const x = parseFloat(at[1]) + offsetX, y = parseFloat(at[2]) + offsetY;
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
                const sizeNode = nameNode ? getAttr(nameNode, 'effects') : null;
                let fontSize = 1.27;
                if (sizeNode) { const font = getAttr(sizeNode, 'font'); if (font) { const s = getAttr(font, 'size'); if (s) fontSize = parseFloat(s[2]); } }

                ctx.save();
                ctx.font = `${fontSize}px "Segoe UI", Arial, sans-serif`;

                if (nameNode && nameNode[1] !== '"~"') {
                    ctx.fillStyle = COLORS.pinName;
                    let nx = ex, ny = ey;
                    const nameText = nameNode[1];

                    let shouldRender = true;
                    for (const prev of gpn) {
                        if (prev.name === nameText && Math.abs(ex - prev.x) < 3.0 && Math.abs(ey - prev.y) < 3.0) {
                            shouldRender = false; break;
                        }
                    }

                    if (shouldRender) {
                        if (angDeg === 0) { nx += 0.5; ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.save(); ctx.translate(nx, ny); ctx.scale(1, -1); ctx.fillText(nameText, 0, 0); ctx.restore(); }
                        else if (angDeg === 180) { nx -= 0.5; ctx.textAlign = 'right'; ctx.textBaseline = 'middle'; ctx.save(); ctx.translate(nx, ny); ctx.scale(1, -1); ctx.fillText(nameText, 0, 0); ctx.restore(); }
                        else if (angDeg === 90 || angDeg === 270) { nx -= 0.8; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.save(); ctx.translate(nx, ny); ctx.scale(1, -1); ctx.rotate(-Math.PI / 2); ctx.fillText(nameText, 0, 0); ctx.restore(); }
                        gpn.push({ name: nameText, x: ex, y: ey });
                    }
                }

                if (numNode && numNode[1] !== '"~"') {
                    ctx.fillStyle = COLORS.pinNum;
                    let numx = x, numy = y;
                    if (angDeg === 0) { numx = x + len / 2; numy = y + 0.3; ctx.textAlign = 'center'; ctx.textBaseline = 'bottom'; }
                    else if (angDeg === 180) { numx = x - len / 2; numy = y + 0.3; ctx.textAlign = 'center'; ctx.textBaseline = 'bottom'; }
                    else if (angDeg === 90) { numx = x - 0.3; numy = y + len / 2; ctx.textAlign = 'right'; ctx.textBaseline = 'middle'; }
                    else if (angDeg === 270) { numx = x - 0.3; numy = y - len / 2; ctx.textAlign = 'right'; ctx.textBaseline = 'middle'; }
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
                    const x = parseFloat(at[1]) + offsetX, y = parseFloat(at[2]) + offsetY;
                    const ang = parseFloat(at[3] || 0);
                    ctx.translate(x, y);
                    ctx.scale(1, -1);
                    if (ang !== 0) ctx.rotate(-ang * Math.PI / 180);
                    ctx.fillStyle = (op[1] === '"Reference"') ? COLORS.propertyRef : COLORS.propertyVal;
                    if (type === 'text') ctx.fillStyle = COLORS.text;
                    let fontSize = 1.27;
                    const effects = getAttr(op, 'effects');
                    if (effects) { const font = getAttr(effects, 'font'); if (font) { const s = getAttr(font, 'size'); if (s) fontSize = parseFloat(s[2]); } }
                    ctx.font = `${fontSize}px "Segoe UI", Arial, sans-serif`;
                    ctx.textAlign = 'center';
                    ctx.textBaseline = 'middle';
                    ctx.fillText(txt, 0, 0);
                    ctx.restore();
                }
            }
        }
        ctx.restore();
    });
}

// --- Render full schematic (all components) ---
function drawSchematic() {
    if (!currentSchematic || !currentTransform || currentSchematic.components.length === 0) return;

    const { canvas, ctx } = getCanvasAndCtx();
    const t = currentTransform;

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Draw grid
    Schematic.drawGrid(ctx, t, zoomLevel, canvas.width, canvas.height);

    // Apply transform
    ctx.save();
    ctx.translate(t.cx + panX, t.cy + panY);
    ctx.scale(t.baseScale * zoomLevel, -t.baseScale * zoomLevel);
    ctx.translate(-t.midX, -t.midY);

    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    // Shared pin name deduplication state across all components
    const globalOpState = { renderedPinNames: [] };

    // Render each component at its position
    currentSchematic.components.forEach(comp => {
        renderComponentAt(ctx, comp.ops, comp.x, comp.y, globalOpState);
    });

    // Render wires (from auto-routing)
    if (currentSchematic.wirePaths) {
        ctx.strokeStyle = '#00A800';
        ctx.lineWidth = 0.254;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        currentSchematic.wirePaths.forEach(wire => {
            if (!wire.path || wire.path.length < 2) return;
            ctx.beginPath();
            ctx.moveTo(wire.path[0].x, wire.path[0].y);
            for (let i = 1; i < wire.path.length; i++) {
                ctx.lineTo(wire.path[i].x, wire.path[i].y);
            }
            ctx.stroke();
        });
    }

    // Render power/GND symbols (net labels instead of routed power wires)
    if (currentSchematic.powerLabels) {
        const STUB = 2.54;
        currentSchematic.powerLabels.forEach(lbl => {
            const dir = lbl.dir || 'right';
            const dx = dir === 'right' ? 1 : dir === 'left' ? -1 : 0;
            const dy = dir === 'up' ? 1 : dir === 'down' ? -1 : 0;
            const ex = lbl.x + dx * STUB;
            const ey = lbl.y + dy * STUB;
            const isGnd = lbl.net === 'GND';

            ctx.strokeStyle = isGnd ? '#4488ff' : '#cc4444';
            ctx.lineWidth = 0.254;

            // Stub from pin to symbol
            ctx.beginPath();
            ctx.moveTo(lbl.x, lbl.y);
            ctx.lineTo(ex, ey);
            ctx.stroke();

            if (isGnd) {
                // GND: three shrinking bars perpendicular to the stub
                const px = -dy, py = dx; // perpendicular
                for (let i = 0; i < 3; i++) {
                    const w = 1.27 - i * 0.42;
                    const ox = ex + dx * i * 0.64;
                    const oy = ey + dy * i * 0.64;
                    ctx.beginPath();
                    ctx.moveTo(ox - px * w, oy - py * w);
                    ctx.lineTo(ox + px * w, oy + py * w);
                    ctx.stroke();
                }
            } else {
                // Power: bar + net name
                const px = -dy, py = dx;
                ctx.beginPath();
                ctx.moveTo(ex - px * 1.27, ey - py * 1.27);
                ctx.lineTo(ex + px * 1.27, ey + py * 1.27);
                ctx.stroke();

                ctx.save();
                ctx.translate(ex + dx * 0.8, ey + dy * 0.8);
                ctx.scale(1, -1); // un-flip Y for text
                ctx.fillStyle = '#cc4444';
                ctx.font = '1.6px monospace';
                ctx.textAlign = dir === 'left' ? 'right' : 'left';
                ctx.textBaseline = 'middle';
                ctx.fillText(lbl.net, 0, 0);
                ctx.restore();
            }
        });
    }

    ctx.restore();
}

function enterSchematicMode() {
    if (!currentSchematic || currentSchematic.components.length === 0) return;

    currentSchematic.mode = 'schematic';
    const { canvas } = getCanvasAndCtx();
    setupCanvasSize();
    const transform = currentSchematic.computeTransform(canvas.width, canvas.height);
    currentTransform = transform;
    zoomLevel = 1;
    panX = 0;
    panY = 0;

    drawSchematic();
    attachZoomHandlers();
}

function exitSchematicMode() {
    currentSchematic.mode = 'single';
}

function drawCurrentMode() {
    if (currentSchematic && currentSchematic.mode === 'schematic' && currentSchematic.components.length > 0) {
        drawSchematic();
    } else if (currentOps.length > 0) {
        drawSymbol();
    }
}
