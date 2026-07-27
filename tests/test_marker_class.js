const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

function sexprStr(val) {
    if (val == null) return '';
    return String(val).replace(/^"+|"+$/g, '').trim();
}
function isHiddenSexprText(val) {
    const s = sexprStr(val);
    return !s || s === '~';
}
function computeJunctionPoints(wirePaths) { return []; }

const ctx = vm.createContext({
    console, GRID_SIZE: 1.27, sexprStr, isHiddenSexprText, computeJunctionPoints,
    getAttr(node, name) {
        return node.slice(1).find(c => Array.isArray(c) && c[0] === name) || null;
    },
});

const src = fs.readFileSync('static/schematic.js', 'utf8');
vm.runInContext(
    src + '\nglobalThis.ImageMarker = ImageMarker;\nglobalThis.Schematic = Schematic;',
    ctx
);

const ImageMarker = ctx.ImageMarker;
const Schematic = ctx.Schematic;

console.log('ImageMarker type:', typeof ImageMarker);
console.log('Schematic type:', typeof Schematic);

// 1. Constructor
const m = new ImageMarker('img_1', 10, 20, 'data:a', 'Test', 20, 15, 1, 1, 0, 'ast_abc');
assert.strictEqual(m.id, 'img_1');
assert.strictEqual(m.markerNumber, 1);
assert.strictEqual(m.assetId, 'ast_abc');
console.log('PASS: constructor');

// 2. toDesignSnapshot
const snap = new ImageMarker('img_2', 20.32, 15.24, 'data:...', 'Power', 20, 15, 1).toDesignSnapshot();
assert.strictEqual(snap.marker_number, 1);
assert.strictEqual(snap.label, 'Power');
console.log('PASS: toDesignSnapshot');

// 3. fromSnapshot
const restored = ImageMarker.fromSnapshot({ id: 'img_3', x: 5, y: 10, label: 'R', width: 30, height: 20, marker_number: 3, asset_id: 'ast_r' });
assert.strictEqual(restored.markerNumber, 3);
assert.strictEqual(restored.assetId, 'ast_r');
console.log('PASS: fromSnapshot');

// 4. Schematic.addImageMarkerAt
const sch = new Schematic();
const m1 = sch.addImageMarkerAt(10, 20, 'data:a', 'First');
assert.strictEqual(m1.markerNumber, 1);
assert.strictEqual(m1.id, 'img_1');
assert.strictEqual(m1.label, 'First');
const m2 = sch.addImageMarkerAt(30, 40, 'data:b', 'Second');
assert.strictEqual(m2.markerNumber, 2);
console.log('PASS: addImageMarkerAt');

// 5. Numbers increment
const sch2 = new Schematic();
for (let i = 0; i < 5; i++) {
    const m = sch2.addImageMarkerAt(i * 10, 0, 'data:', 'M' + (i+1));
    assert.strictEqual(m.markerNumber, i + 1);
}
console.log('PASS: numbers increment');

// 6. Delete does not reuse number
const sch3 = new Schematic();
const a = sch3.addImageMarkerAt(0, 0, 'data:a', 'A');
const b = sch3.addImageMarkerAt(10, 0, 'data:b', 'B');
const c = sch3.addImageMarkerAt(20, 0, 'data:c', 'C');
sch3.removeImageMarker(b.id);
const d = sch3.addImageMarkerAt(30, 0, 'data:d', 'D');
assert.strictEqual(d.markerNumber, 4);
console.log('PASS: delete does not reuse number');

// 7. getNextImageMarkerNumber
const sch4 = new Schematic();
assert.strictEqual(sch4.getNextImageMarkerNumber(), 1);
sch4.addImageMarkerAt(0, 0, 'data:', 'A');
assert.strictEqual(sch4.getNextImageMarkerNumber(), 2);
console.log('PASS: getNextImageMarkerNumber');

// 8. removeImageMarker
const sch5 = new Schematic();
const mx = sch5.addImageMarkerAt(0, 0, 'data:', 'X');
assert.strictEqual(sch5.removeImageMarker(mx.id), true);
assert.strictEqual(sch5.removeImageMarker('nonexistent'), false);
console.log('PASS: removeImageMarker');

// 9. getImageMarkerById
const sch6 = new Schematic();
const mf = sch6.addImageMarkerAt(5, 5, 'data:', 'FindMe');
assert.strictEqual(sch6.getImageMarkerById(mf.id), mf);
assert.strictEqual(sch6.getImageMarkerById('img_999'), null);
console.log('PASS: getImageMarkerById');

// 10. toDesignSnapshot includes image_markers
const sch7 = new Schematic();
sch7.addImageMarkerAt(10, 20, 'data:a', 'A', 20, 15, 'ast_a');
sch7.addImageMarkerAt(30, 40, 'data:b', 'B', 20, 15, 'ast_b');
const s = sch7.toDesignSnapshot(1);
assert.ok(Array.isArray(s.image_markers));
assert.strictEqual(s.image_markers.length, 2);
assert.strictEqual(s.image_markers[0].marker_number, 1);
assert.strictEqual(s.image_markers[0].asset_id, 'ast_a');
assert.strictEqual(s.image_markers[1].marker_number, 2);
assert.strictEqual(s.image_markers[1].asset_id, 'ast_b');
console.log('PASS: toDesignSnapshot includes markers');

// 11. Clear resets
const sch8 = new Schematic();
sch8.addImageMarkerAt(0, 0, 'data:', 'A');
sch8.addImageMarkerAt(10, 0, 'data:', 'B');
sch8.clear();
assert.strictEqual(sch8.imageMarkers.length, 0);
assert.strictEqual(sch8.getNextImageMarkerNumber(), 1);
console.log('PASS: clear resets');

console.log('All marker tests passed');
