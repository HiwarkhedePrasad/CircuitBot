function routePoint(point) {
    if (!point) return { x: 0, y: 0 };
    if (point.noSnap) return { x: point.x, y: point.y };
    return { x: Math.round(point.x * 100) / 100, y: Math.round(point.y * 100) / 100 };
}

function distanceToSegment(p, a, b) {
    if (!p || !a || !b) return Infinity;
    const l2 = (b.x - a.x) * (b.x - a.x) + (b.y - a.y) * (b.y - a.y);
    if (l2 === 0) return Math.hypot(p.x - a.x, p.y - a.y);
    let t = ((p.x - a.x) * (b.x - a.x) + (p.y - a.y) * (b.y - a.y)) / l2;
    t = Math.max(0, Math.min(1, t));
    const projX = a.x + t * (b.x - a.x);
    const projY = a.y + t * (b.y - a.y);
    return Math.hypot(p.x - projX, p.y - projY);
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

function dedupePath(path) {
    const out = [];
    for (const point of path || []) {
        if (!point) continue;
        const next = { x: point.x, y: point.y, noSnap: point.noSnap };
        const prev = out[out.length - 1];
        if (prev && Math.abs(prev.x - next.x) < 0.001 && Math.abs(prev.y - next.y) < 0.001) {
            continue;
        }
        out.push(next);
    }
    return out;
}

function getPathLength(pts) {
    let len = 0;
    for (let i = 1; i < pts.length; i++) {
        len += Math.hypot(pts[i].x - pts[i-1].x, pts[i].y - pts[i-1].y);
    }
    return len;
}

/**
 * KiCad-style obstacle avoidance routing algorithm.
 * Automatically computes 45-degree walkaround paths around obstacle pads.
 */
function computeObstacleAvoidancePath(p0, p1, obstacles = [], angleMode = '45', posture = 0, depth = 0) {
    const directSeg = computeConstrainedPathSegment(p0, p1, angleMode, posture);
    const fullDirect = [p0, ...directSeg];

    if (!obstacles || obstacles.length === 0 || depth >= 3) {
        return directSeg;
    }

    // Find first obstacle that intersects any segment in fullDirect
    let firstObstacle = null;
    let minIntersectDist = Infinity;

    for (const obs of obstacles) {
        const R = obs.radius;
        for (let i = 1; i < fullDirect.length; i++) {
            const segA = fullDirect[i-1];
            const segB = fullDirect[i];
            const d = distanceToSegment(obs, segA, segB);
            if (d < R - 0.001) {
                const distFromStart = Math.hypot(obs.x - p0.x, obs.y - p0.y);
                if (distFromStart < minIntersectDist) {
                    minIntersectDist = distFromStart;
                    firstObstacle = obs;
                }
            }
        }
    }

    if (!firstObstacle) {
        return directSeg;
    }

    // Offset radius for 45-degree tangent clearance: R * sqrt(2)
    const R = firstObstacle.radius * 1.414 + 0.15;
    const ox = firstObstacle.x;
    const oy = firstObstacle.y;

    // Candidate bypass points around obstacle
    const vertices = [
        { x: ox, y: oy + R },
        { x: ox, y: oy - R },
        { x: ox + R, y: oy },
        { x: ox - R, y: oy },
        { x: ox + R, y: oy + R },
        { x: ox - R, y: oy + R },
        { x: ox + R, y: oy - R },
        { x: ox - R, y: oy - R },
    ];

    let bestSubPath = null;
    let minPathLen = Infinity;

    for (const v of vertices) {
        const seg1 = computeConstrainedPathSegment(p0, v, angleMode, posture);
        const vActual = seg1.length > 0 ? seg1[seg1.length - 1] : v;
        const seg2 = computeConstrainedPathSegment(vActual, p1, angleMode, posture);

        const candidatePath = dedupePath([p0, ...seg1, ...seg2]);

        // Check if candidatePath collides with firstObstacle
        let collides = false;
        for (let i = 1; i < candidatePath.length; i++) {
            if (distanceToSegment(firstObstacle, candidatePath[i-1], candidatePath[i]) < firstObstacle.radius - 0.02) {
                collides = true;
                break;
            }
        }

        if (!collides) {
            const len = getPathLength(candidatePath);
            if (len < minPathLen) {
                minPathLen = len;
                bestSubPath = dedupePath([...seg1, ...seg2]);
            }
        }
    }

    return bestSubPath || directSeg;
}

// Test cases
const obs = [{ x: 5, y: 0, radius: 1.5 }];
console.log('Direct path without obstacle:', JSON.stringify(computeConstrainedPathSegment({x:0,y:0}, {x:10,y:0}, '45', 0)));
console.log('KiCad Walkaround Path around obstacle (5,0):', JSON.stringify(computeObstacleAvoidancePath({x:0,y:0}, {x:10,y:0}, obs, '45', 0)));
