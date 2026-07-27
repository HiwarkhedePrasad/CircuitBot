const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

// Minimal helpers needed by schematic.js
function sexprStr(val) {
    if (val == null) return '';
    return String(val).replace(/^"+|"+$/g, '').trim();
}
function isHiddenSexprText(val) {
    const s = sexprStr(val);
    return !s || s === '~';
}
function computeJunctionPoints(wirePaths) {
    return [];
}

const context = {
    console,
    GRID_SIZE: 1.27,
    sexprStr,
    isHiddenSexprText,
    computeJunctionPoints,
    getAttr(node, name) {
        return node.slice(1).find(
            child => Array.isArray(child) && child[0] === name
        ) || null;
    },
};

vm.createContext(context);
const schematicSource = fs.readFileSync('static/schematic.js', 'utf8');
vm.runInContext(schematicSource, context);

const { Schematic, ImageMarker } = context;

// ── ImageMarker Tests ──────────────────────────────────────────────────────

// 1. Constructor with markerNumber
{
    const m = new ImageMarker('img_1', 10, 20, 'data:image/png;base64,a', 'Test', 20, 15, 1, 1, 0, 'ast_abc');
    assert.strictEqual(m.id, 'img_1');
    assert.strictEqual(m.markerNumber, 1);
    assert.strictEqual(m.x, 10);
    assert.strictEqual(m.y, 20);
    assert.strictEqual(m.label, 'Test');
    assert.strictEqual(m.assetId, 'ast_abc');
    assert.strictEqual(m.scale, 1);
    assert.strictEqual(m.rotation, 0);
}

// 2. toDesignSnapshot()
{
    const m = new ImageMarker('img_1', 20.32, 15.24, 'data:...', 'Power section', 20, 15, 1, 1, 0, 'ast_abc123');
    const snap = m.toDesignSnapshot();
    assert.strictEqual(snap.id, 'img_1');
    assert.strictEqual(snap.marker_number, 1);
    assert.strictEqual(snap.label, 'Power section');
    assert.strictEqual(snap.x, 20.32);
    assert.strictEqual(snap.y, 15.24);
    assert.strictEqual(snap.asset_id, 'ast_abc123');
}

// 3. fromSnapshot()
{
    const m = ImageMarker.fromSnapshot({
        id: 'img_2',
        x: 5, y: 10,
        label: 'Restored',
        width: 30, height: 20,
        marker_number: 2,
        asset_id: 'ast_restored',
    });
    assert.strictEqual(m.id, 'img_2');
    assert.strictEqual(m.markerNumber, 2);
    assert.strictEqual(m.label, 'Restored');
    assert.strictEqual(m.assetId, 'ast_restored');
    assert.strictEqual(m.imageDataUrl, null); // loaded separately
}

// ── Schematic Marker Methods ────────────────────────────────────────────────

// 4. addImageMarkerAt creates numbered markers
{
    const sch = new Schematic();
    const m1 = sch.addImageMarkerAt(10, 20, 'data:a', 'First');
    assert.strictEqual(m1.markerNumber, 1);
    assert.strictEqual(m1.id, 'img_1');
    assert.strictEqual(m1.x, 10);
    assert.strictEqual(m1.y, 20);
    assert.strictEqual(m1.label, 'First');

    const m2 = sch.addImageMarkerAt(30, 40, 'data:b', 'Second');
    assert.strictEqual(m2.markerNumber, 2);
    assert.strictEqual(m2.id, 'img_2');
}

// 5. Marker numbers increment correctly
{
    const sch = new Schematic();
    for (let i = 0; i < 5; i++) {
        const m = sch.addImageMarkerAt(i * 10, 0, 'data:', `M${i+1}`);
        assert.strictEqual(m.markerNumber, i + 1);
    }
    assert.strictEqual(sch.imageMarkers.length, 5);
}

// 6. Deleting marker 2 does not cause the next marker to reuse number 2
{
    const sch = new Schematic();
    const m1 = sch.addImageMarkerAt(0, 0, 'data:a', 'A');
    const m2 = sch.addImageMarkerAt(10, 0, 'data:b', 'B');
    const m3 = sch.addImageMarkerAt(20, 0, 'data:c', 'C');
    assert.strictEqual(m2.markerNumber, 2);

    sch.removeImageMarker(m2.id);
    assert.strictEqual(sch.imageMarkers.length, 2);

    const m4 = sch.addImageMarkerAt(30, 0, 'data:d', 'D');
    assert.strictEqual(m4.markerNumber, 4); // Does not reuse 2
}

// 7. getNextImageMarkerNumber
{
    const sch = new Schematic();
    assert.strictEqual(sch.getNextImageMarkerNumber(), 1);
    sch.addImageMarkerAt(0, 0, 'data:', 'A');
    assert.strictEqual(sch.getNextImageMarkerNumber(), 2);
    sch.addImageMarkerAt(10, 0, 'data:', 'B');
    assert.strictEqual(sch.getNextImageMarkerNumber(), 3);
}

// 8. removeImageMarker returns boolean
{
    const sch = new Schematic();
    const m = sch.addImageMarkerAt(0, 0, 'data:', 'X');
    assert.strictEqual(sch.removeImageMarker(m.id), true);
    assert.strictEqual(sch.removeImageMarker('nonexistent'), false);
}

// 9. getImageMarkerById
{
    const sch = new Schematic();
    const m = sch.addImageMarkerAt(5, 5, 'data:', 'FindMe');
    assert.strictEqual(sch.getImageMarkerById(m.id), m);
    assert.strictEqual(sch.getImageMarkerById('img_999'), null);
}

// 10. toDesignSnapshot on Schematic includes image_markers
{
    const sch = new Schematic();
    sch.addImageMarkerAt(10, 20, 'data:a', 'Marker A', 20, 15, 'ast_a');
    sch.addImageMarkerAt(30, 40, 'data:b', 'Marker B', 20, 15, 'ast_b');
    const snap = sch.toDesignSnapshot(1);
    assert.ok(Array.isArray(snap.image_markers));
    assert.strictEqual(snap.image_markers.length, 2);
    assert.strictEqual(snap.image_markers[0].marker_number, 1);
    assert.strictEqual(snap.image_markers[0].asset_id, 'ast_a');
    assert.strictEqual(snap.image_markers[1].marker_number, 2);
    assert.strictEqual(snap.image_markers[1].asset_id, 'ast_b');
}

// 11. Clear resets markers
{
    const sch = new Schematic();
    sch.addImageMarkerAt(0, 0, 'data:', 'A');
    sch.addImageMarkerAt(10, 0, 'data:', 'B');
    assert.strictEqual(sch.imageMarkers.length, 2);
    sch.clear();
    assert.strictEqual(sch.imageMarkers.length, 0);
    assert.strictEqual(sch.getNextImageMarkerNumber(), 1);
}

console.log('schematic marker tests passed');
