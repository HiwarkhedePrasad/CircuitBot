const { snapToGrid } = require('../static/pcb_view/constants.js') || {};

function routePoint(point) {
    if (point.noSnap) return { x: point.x, y: point.y };
    return { x: Math.round(point.x * 10) / 10, y: Math.round(point.y * 10) / 10 };
}

function computeConstrainedPathSegment(p0, p1, angleMode = '45', posture = 0) {
    if (!p0 || !p1) return p1 ? [routePoint(p1)] : [];
    const p1Pt = routePoint(p1);
    const p0Pt = { x: p0.x, y: p0.y };

    if (Math.abs(p0Pt.x - p1Pt.x) < 0.001 && Math.abs(p0Pt.y - p1Pt.y) < 0.001) {
        return [p1Pt];
    }
    if (angleMode === 'free') {
        return [p1Pt];
    }

    const dx = p1Pt.x - p0Pt.x;
    const dy = p1Pt.y - p0Pt.y;
    const absDx = Math.abs(dx);
    const absDy = Math.abs(dy);
    const signX = Math.sign(dx) || 1;
    const signY = Math.sign(dy) || 1;

    if (angleMode === '90') {
        let elbow;
        if (posture === 0) {
            elbow = { x: p1Pt.x, y: p0Pt.y };
        } else {
            elbow = { x: p0Pt.x, y: p1Pt.y };
        }
        const res = [];
        if (Math.abs(elbow.x - p0Pt.x) > 0.001 || Math.abs(elbow.y - p0Pt.y) > 0.001) {
            res.push(elbow);
        }
        if (Math.abs(p1Pt.x - elbow.x) > 0.001 || Math.abs(p1Pt.y - elbow.y) > 0.001) {
            res.push(p1Pt);
        }
        return res;
    }

    // Default: '45' (45-degree octagonal routing)
    let elbow;
    if (absDx > absDy) {
        if (posture === 0) {
            const horizLen = absDx - absDy;
            elbow = { x: p0Pt.x + signX * horizLen, y: p0Pt.y };
        } else {
            const diagLen = absDy;
            elbow = { x: p0Pt.x + signX * diagLen, y: p0Pt.y + signY * diagLen };
        }
    } else {
        if (posture === 0) {
            const vertLen = absDy - absDx;
            elbow = { x: p0Pt.x, y: p0Pt.y + signY * vertLen };
        } else {
            const diagLen = absDx;
            elbow = { x: p0Pt.x + signX * diagLen, y: p0Pt.y + signY * diagLen };
        }
    }

    const res = [];
    if ((Math.abs(elbow.x - p0Pt.x) > 0.001 || Math.abs(elbow.y - p0Pt.y) > 0.001) &&
        (Math.abs(elbow.x - p1Pt.x) > 0.001 || Math.abs(elbow.y - p1Pt.y) > 0.001)) {
        res.push(elbow);
    }
    res.push(p1Pt);
    return res;
}

// Tests
console.log('Testing 45-degree math:');
const t1 = computeConstrainedPathSegment({x:0, y:0}, {x:10, y:3}, '45', 0);
console.log('Test 1 (absDx > absDy, posture 0):', JSON.stringify(t1));

const t2 = computeConstrainedPathSegment({x:0, y:0}, {x:10, y:3}, '45', 1);
console.log('Test 2 (absDx > absDy, posture 1):', JSON.stringify(t2));

const t3 = computeConstrainedPathSegment({x:0, y:0}, {x:2, y:8}, '45', 0);
console.log('Test 3 (absDy > absDx, posture 0):', JSON.stringify(t3));

const t4 = computeConstrainedPathSegment({x:0, y:0}, {x:2, y:8}, '45', 1);
console.log('Test 4 (absDy > absDx, posture 1):', JSON.stringify(t4));
