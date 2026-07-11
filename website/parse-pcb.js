#!/usr/bin/env node
/**
 * parse-pcb.js — Parse a KiCad .kicad_pcb file and output pcb-data.json
 * Run once: node parse-pcb.js example.kicad_pcb ../website/pcb-data.json
 */
const fs = require('fs');
const path = require('path');

const inputFile = process.argv[2] || 'example.kicad_pcb';
const outputFile = process.argv[3] || path.join(__dirname, '..', 'website', 'pcb-data.json');

// ─── S-Expression Parser ───
function parseSExpr(str) {
  const tokens = [];
  let i = 0;
  while (i < str.length) {
    const ch = str[i];
    if (ch === '(') { tokens.push('('); i++; }
    else if (ch === ')') { tokens.push(')'); i++; }
    else if (ch === '"') {
      let s = ''; i++;
      while (i < str.length && str[i] !== '"') {
        if (str[i] === '\\') { s += str[i + 1]; i += 2; }
        else { s += str[i]; i++; }
      }
      i++; // skip closing "
      tokens.push(s);
    }
    else if (/\s/.test(ch)) { i++; }
    else {
      let s = '';
      while (i < str.length && !/[\s()"]/.test(str[i])) { s += str[i]; i++; }
      tokens.push(s);
    }
  }

  function build(pos) {
    if (tokens[pos] === '(') {
      const arr = [];
      pos++;
      while (tokens[pos] !== ')') {
        const [child, next] = build(pos);
        arr.push(child);
        pos = next;
      }
      return [arr, pos + 1];
    } else {
      return [tokens[pos], pos + 1];
    }
  }
  return build(0)[0];
}

// ─── Helpers ───
function findNodes(tree, tag) {
  const results = [];
  if (!Array.isArray(tree)) return results;
  if (tree[0] === tag) results.push(tree);
  for (let i = 1; i < tree.length; i++) {
    results.push(...findNodes(tree[i], tag));
  }
  return results;
}

function findNode(tree, tag) {
  if (!Array.isArray(tree)) return null;
  if (tree[0] === tag) return tree;
  for (let i = 1; i < tree.length; i++) {
    const r = findNode(tree[i], tag);
    if (r) return r;
  }
  return null;
}

function getVal(node, tag) {
  const child = findNode(node, tag);
  return child ? child[1] : null;
}

function getXY(node) {
  const at = findNode(node, 'at');
  if (at && at.length >= 3) return { x: parseFloat(at[1]), y: parseFloat(at[2]) };
  return null;
}

// ─── Parse the file ───
console.log(`Parsing ${inputFile}...`);
const raw = fs.readFileSync(inputFile, 'utf8');
const tree = parseSExpr(raw);
console.log('Parsed S-expression OK');

// ─── Extract board outline (Edge.Cuts) ───
const outline = [];
const grLines = findNodes(tree, 'gr_line');
const grArcs = findNodes(tree, 'gr_arcs').length ? findNodes(tree, 'gr_arcs') : findNodes(tree, 'gr_arc');

for (const line of grLines) {
  const layer = getVal(line, 'layer');
  if (layer !== 'Edge.Cuts') continue;
  const start = findNode(line, 'start');
  const end = findNode(line, 'end');
  if (start && end) {
    outline.push({
      type: 'line',
      x1: parseFloat(start[1]), y1: parseFloat(start[2]),
      x2: parseFloat(end[1]), y2: parseFloat(end[2])
    });
  }
}

for (const arc of grArcs) {
  const layer = getVal(arc, 'layer');
  if (layer !== 'Edge.Cuts') continue;
  const start = findNode(arc, 'start');
  const mid = findNode(arc, 'mid');
  const end = findNode(arc, 'end');
  if (start && mid && end) {
    outline.push({
      type: 'arc',
      sx: parseFloat(start[1]), sy: parseFloat(start[2]),
      mx: parseFloat(mid[1]), my: parseFloat(mid[2]),
      ex: parseFloat(end[1]), ey: parseFloat(end[2])
    });
  }
}
console.log(`Board outline: ${outline.length} elements`);

// ─── Extract traces (segments) ───
const segments = findNodes(tree, 'segment');
const traces = { F_Cu: [], B_Cu: [] };
for (const seg of segments) {
  const layer = getVal(seg, 'layer');
  if (layer !== 'F.Cu' && layer !== 'B.Cu') continue;
  const start = findNode(seg, 'start');
  const end = findNode(seg, 'end');
  const width = parseFloat(getVal(seg, 'width') || '0.15');
  const net = parseInt(getVal(seg, 'net') || '0');
  if (start && end) {
    const t = {
      x1: parseFloat(start[1]), y1: parseFloat(start[2]),
      x2: parseFloat(end[1]), y2: parseFloat(end[2]),
      w: width, net
    };
    if (layer === 'F.Cu') traces.F_Cu.push(t);
    else traces.B_Cu.push(t);
  }
}
console.log(`Traces: F.Cu=${traces.F_Cu.length}, B.Cu=${traces.B_Cu.length}`);

// ─── Extract vias ───
const viasRaw = findNodes(tree, 'via');
const vias = [];
for (const v of viasRaw) {
  const pos = getXY(v);
  const size = parseFloat(getVal(v, 'size') || '0.6');
  const drill = parseFloat(getVal(v, 'drill') || '0.3');
  const net = parseInt(getVal(v, 'net') || '0');
  if (pos) vias.push({ x: pos.x, y: pos.y, size, drill, net });
}
console.log(`Vias: ${vias.length}`);

// ─── Extract pads from footprints ───
const footprints = findNodes(tree, 'footprint');
const pads = [];
const components = [];
for (const fp of footprints) {
  const layer = getVal(fp, 'layer');
  if (layer !== 'F.Cu' && layer !== 'B.Cu') continue;
  const fpPos = getXY(fp);
  const fpTexts = findNodes(fp, 'fp_text');
  let ref = '';
  for (const ft of fpTexts) {
    if (ft[1] === 'reference' && ft[2]) { ref = ft[2]; break; }
  }
  const footprintName = fp[1] || '';

  // Get pads
  const fpPads = findNodes(fp, 'pad');
  for (const pad of fpPads) {
    const padNum = pad[1];
    const padType = pad[2]; // smd, thru_hole, np_thru_hole
    const pos = getXY(pad);
    const size = findNode(pad, 'size');
    const drill = findNode(pad, 'drill');
    const padNet = findNode(pad, 'net');
    const netId = padNet && padNet[1] ? parseInt(padNet[1]) : 0;

    if (pos && size) {
      pads.push({
        x: pos.x, y: pos.y,
        w: parseFloat(size[1]), h: parseFloat(size[2]),
        type: padType,
        net: netId,
        drill: drill ? parseFloat(drill[1]) : 0,
        layer: layer,
        component: ref || ''
      });
    }
  }

  if (fpPos && ref) {
    components.push({
      x: fpPos.x, y: fpPos.y,
      ref: ref,
      footprint: footprintName,
      layer: layer
    });
  }
}
console.log(`Pads: ${pads.length}, Components: ${components.length}`);

// ─── Extract silkscreen text ───
const silkscreen = [];
const grTexts = findNodes(tree, 'gr_text');
for (const gt of grTexts) {
  const layer = getVal(gt, 'layer');
  if (layer !== 'F.SilkS' && layer !== 'B.SilkS') continue;
  const pos = getXY(gt);
  const text = gt[1];
  if (pos && text) {
    silkscreen.push({ x: pos.x, y: pos.y, text, layer });
  }
}
console.log(`Silkscreen texts: ${silkscreen.length}`);

// ─── Compute bounding box ───
let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
for (const t of traces.F_Cu.concat(traces.B_Cu)) {
  minX = Math.min(minX, t.x1, t.x2);
  minY = Math.min(minY, t.y1, t.y2);
  maxX = Math.max(maxX, t.x1, t.x2);
  maxY = Math.max(maxY, t.y1, t.y2);
}
for (const v of vias) {
  minX = Math.min(minX, v.x);
  minY = Math.min(minY, v.y);
  maxX = Math.max(maxX, v.x);
  maxY = Math.max(maxY, v.y);
}
for (const p of pads) {
  minX = Math.min(minX, p.x - p.w / 2);
  minY = Math.min(minY, p.y - p.h / 2);
  maxX = Math.max(maxX, p.x + p.w / 2);
  maxY = Math.max(maxY, p.y + p.h / 2);
}
const margin = 2;
minX -= margin; minY -= margin;
 maxX += margin; maxY += margin;
const boardW = maxX - minX;
const boardH = maxY - minY;
console.log(`Bounding box: ${boardW.toFixed(1)}mm x ${boardH.toFixed(1)}mm`);

// ─── Output JSON ───
const data = {
  meta: {
    source: path.basename(inputFile),
    boardWidth: parseFloat(boardW.toFixed(2)),
    boardHeight: parseFloat(boardH.toFixed(2)),
    offsetX: minX,
    offsetY: minY,
    unit: 'mm'
  },
  outline,
  traces,
  vias,
  pads,
  components,
  silkscreen
};

fs.writeFileSync(outputFile, JSON.stringify(data));
console.log(`\nWrote ${outputFile} (${(fs.statSync(outputFile).size / 1024).toFixed(1)} KB)`);
console.log('Done.');
