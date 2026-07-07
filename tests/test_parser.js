const fs = require('fs');

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

const txt = fs.readFileSync('kicad_rag/kicad-symbols/Regulator_Linear.kicad_symdir/AMS1117-1.8.kicad_sym', 'utf8');
const ast = parseSExpr(txt);

let extendsName = null;
let ops_arr = [];

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
            ops_arr.push(node);
            break;
    }
}

for (let i = 1; i < ast.length; i++) {
    if (Array.isArray(ast[i]) && ast[i][0] === 'symbol') {
        extractNodes(ast[i]);
    }
}

console.log("Extends:", extendsName);
console.log("Ops:", ops_arr.length);

if (extendsName) {
    const parentTxt = fs.readFileSync('kicad_rag/kicad-symbols/Regulator_Linear.kicad_symdir/' + extendsName + '.kicad_sym', 'utf8');
    const parentAst = parseSExpr(parentTxt);
    for (let i = 1; i < parentAst.length; i++) {
        if (Array.isArray(parentAst[i]) && parentAst[i][0] === 'symbol') {
            extractNodes(parentAst[i]);
        }
    }
    console.log("Ops after parent:", ops_arr.length);
}
