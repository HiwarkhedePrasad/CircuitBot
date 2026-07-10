const fs = require('fs');
const src = fs.readFileSync('kicanvas/src/kicad/text/newstroke-glyphs.ts', 'utf8');

// Extract shared_glyphs
const sharedMatch = src.match(/export const shared_glyphs = (\[[\s\S]*?\]);/);
let sharedGlyphs;
eval('sharedGlyphs = ' + sharedMatch[1] + ';');
console.log(`Shared glyphs: ${sharedGlyphs.length}`);

// Extract glyph_data — find = then [
const dataStart = src.indexOf('export const glyph_data');
const eq = src.indexOf('=', dataStart);
const bracket = src.indexOf('[', eq);
console.log(`glyph_data at offset ${dataStart}, = at ${eq}, [ at ${bracket}`);

let depth = 0;
let arrText = '';
for (let i = bracket; i < src.length; i++) {
    const ch = src[i];
    arrText += ch;
    if (ch === '[') depth++;
    else if (ch === ']') {
        depth--;
        if (depth === 0) break;
    }
}

console.log(`Array text: ${arrText.length} chars, depth=${depth}`);
console.log('First 100:', arrText.slice(0, 100));
console.log('Last 100:', arrText.slice(-100));

let entries;
eval('entries = ' + arrText + ';');
console.log(`Total entries: ${entries.length}`);
console.log('Entry 0:', entries[0], '(type:', typeof entries[0] + ')');
console.log('Entry 1:', entries[1], '(type:', typeof entries[1] + ')');
console.log('Entry 10:', entries[10], '(type:', typeof entries[10] + ')');

const asciiEntries = entries.slice(0, 95);
console.log(`ASCII entries: ${asciiEntries.length} (0-94)`);

let out = '// Generated from KiCad newstroke font data\n';
out += '// Only ASCII printable characters (32-126)\n\n';
out += 'const SHARED_GLYPHS = ' + JSON.stringify(sharedGlyphs) + ';\n\n';
out += 'const GLYPH_DATA = ' + JSON.stringify(asciiEntries) + ';\n\n';
out += 'if (typeof module !== "undefined") { module.exports = { SHARED_GLYPHS, GLYPH_DATA }; }\n';

fs.writeFileSync('static/pcb_view/stroke-font-data.js', out);
console.log('\nWritten to static/pcb_view/stroke-font-data.js');
console.log('File size:', (fs.statSync('static/pcb_view/stroke-font-data.js').size / 1024).toFixed(1), 'KB');
