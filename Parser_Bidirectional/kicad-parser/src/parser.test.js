/**
 * Comprehensive test suite for the KiCad S-Expression Parser.
 *
 * Uses Node.js built-in test runner (node:test) and assert module.
 * Covers:
 *  1. Tokenizer unit tests
 *  2. Parser unit tests
 *  3. Serializer unit tests
 *  4. Round-trip identity tests (zero-data-loss guarantee)
 *  5. Stress tests
 *  6. Edge case tests
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { tokenize, parse, parseTokens, parseFull } from './parser.js';
import { serialize, serializeDocument } from './serializer.js';
import { LexerError, ParseError } from './types.js';

// =============================================================================
// TEST FIXTURES
// =============================================================================

const FOOTPRINT_SEXP = `(footprint "Capacitor_SMD:C_0603" (layer "F.Cu") (at 121.5 95.2 90) (descr "Resistor")
  (pad "1" smd roundrect (at -0.85 0 90) (size 1 0.85) (layers "F.Cu" "F.Paste" "F.Mask"))
)`;

const SIMPLE_AT = `(at 121.5 95.2 90)`;

const COMPLEX_SEXP = `(kicad_pcb (version 20240101) (generator "pcbnew")
  (general
    (thickness 1.6)
    (legacy_teardrops no)
  )
  (paper "A4")
  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (32 "B.Adhes" user "B.Adhesive")
  )
)`;

const EMPTY_STRING = `(descr "")`;
const NUMERIC_ARGS = `(xyz -12.34 56.78 -90.0)`;

// =============================================================================
// 1. TOKENIZER UNIT TESTS
// =============================================================================

describe('Tokenizer', () => {
  it('tokenizes simple expression', () => {
    const tokens = tokenize(SIMPLE_AT);

    assert.deepEqual(tokens.map(t => t.type), [
      'lparen', 'identifier', 'whitespace', 'number', 'whitespace', 'number', 'whitespace', 'number', 'rparen'
    ]);

    assert.deepEqual(tokens[0], { type: 'lparen', value: '(', position: 0 });
    assert.deepEqual(tokens[1], { type: 'identifier', value: 'at', position: 1 });
    assert.deepEqual(tokens[3], { type: 'number', value: '121.5', position: 4 });
    assert.deepEqual(tokens[5], { type: 'number', value: '95.2', position: 10 });
    assert.deepEqual(tokens[7], { type: 'number', value: '90', position: 15 });
  });

  it('tokenizes quoted strings', () => {
    const tokens = tokenize(`(name "Hello World")`);
    const strToken = tokens.find(t => t.type === 'string');
    assert.ok(strToken);
    assert.equal(strToken.value, '"Hello World"');
    assert.equal(strToken.position, 6);
  });

  it('handles escape sequences inside strings', () => {
    const tokens = tokenize(`(x "foo\\"bar")`);
    const strToken = tokens.find(t => t.type === 'string');
    assert.equal(strToken.value, '"foo\\"bar"');
  });

  it('handles backslash escape inside strings', () => {
    const tokens = tokenize(`(x "C:\\\\path\\\\file")`);
    const strToken = tokens.find(t => t.type === 'string');
    assert.equal(strToken.value, '"C:\\\\path\\\\file"');
  });

  it('handles empty string', () => {
    const tokens = tokenize(EMPTY_STRING);
    const strToken = tokens.find(t => t.type === 'string');
    assert.equal(strToken.value, '""');
  });

  it('handles whitespace and newlines', () => {
    const input = `(a\n  (b\n    c\n  )\n)`;
    const tokens = tokenize(input);

    const newlines = tokens.filter(t => t.type === 'newline');
    assert.equal(newlines.length, 4);

    const whitespace = tokens.filter(t => t.type === 'whitespace');
    assert.equal(whitespace.length, 3);
  });

  it('handles carriage return + newline', () => {
    const tokens = tokenize(`(a\r\nb)`);
    const newline = tokens.find(t => t.type === 'newline');
    assert.ok(newline);
    assert.equal(newline.value, '\r\n');
  });

  it('distinguishes identifiers from numbers', () => {
    const tokens = tokenize(`(x 42 3.14 -7 -2.5 signal)`);
    const types = tokens.map(t => `${t.type}:${t.value}`);
    assert.ok(types.includes('number:42'));
    assert.ok(types.includes('number:3.14'));
    assert.ok(types.includes('number:-7'));
    assert.ok(types.includes('number:-2.5'));
    assert.ok(types.includes('identifier:signal'));
  });

  it('treats unquoted text with dots as identifier', () => {
    const tokens = tokenize(`(layer F.Cu)`);
    const idToken = tokens.find(t => t.value === 'F.Cu');
    assert.ok(idToken);
    assert.equal(idToken.type, 'identifier');
  });

  it('reconstructs original text from tokens', () => {
    const inputs = [SIMPLE_AT, FOOTPRINT_SEXP, COMPLEX_SEXP];
    for (const input of inputs) {
      const tokens = tokenize(input);
      const reconstructed = tokens.map(t => t.value).join('');
      assert.equal(reconstructed, input);
    }
  });

  it('throws on unterminated string', () => {
    assert.throws(() => tokenize(`(x "unterminated)`), LexerError);
  });

  it('handles deeply nested structure tokenization', () => {
    const depth = 1000;
    // Tokenizer doesn't validate structure, so unbalanced is fine for tokenizing
    const input = '('.repeat(depth) + 'x' + ')'.repeat(depth);
    const tokens = tokenize(input);

    assert.equal(tokens.length, depth * 2 + 1);
    assert.equal(tokens[0].type, 'lparen');
    assert.equal(tokens[tokens.length - 1].type, 'rparen');
  });

  it('handles 50,000 tokens efficiently', () => {
    const pairs = 25000;
    const input = '(a "test" 42)\n'.repeat(pairs);

    const start = performance.now();
    const tokens = tokenize(input);
    const elapsed = performance.now() - start;

    assert.ok(tokens.length > pairs * 5, 'Expected more than 125k tokens');
    assert.ok(elapsed < 5000, `Tokenization took ${elapsed}ms, expected < 5000ms`);
  });
});

// =============================================================================
// 2. PARSER UNIT TESTS
// =============================================================================

describe('Parser', () => {
  it('parses simple leaf expression', () => {
    const ast = parse(SIMPLE_AT);
    assert.equal(ast.type, 'expression');
    assert.equal(ast.name, 'at');
    assert.deepEqual(ast.args, [121.5, 95.2, 90]);
    assert.deepEqual(ast.children, []);
  });

  it('parses expression with string args', () => {
    const ast = parse(`(layer "F.Cu")`);
    assert.equal(ast.name, 'layer');
    assert.deepEqual(ast.args, ['F.Cu']);
  });

  it('parses mixed string and numeric args', () => {
    const ast = parse(`(pad "1" smd roundrect)`);
    assert.equal(ast.name, 'pad');
    assert.deepEqual(ast.args, ['1', 'smd', 'roundrect']);
  });

  it('parses nested children', () => {
    const ast = parse(FOOTPRINT_SEXP);
    assert.equal(ast.name, 'footprint');
    assert.deepEqual(ast.args, ['Capacitor_SMD:C_0603']);
    assert.ok(ast.children.length > 0);

    const childNames = ast.children.map(c => c.name);
    assert.ok(childNames.includes('layer'));
    assert.ok(childNames.includes('at'));
    assert.ok(childNames.includes('descr'));
    assert.ok(childNames.includes('pad'));
  });

  it('preserves child order', () => {
    const ast = parse(`(root (first) (second) (third))`);
    assert.deepEqual(ast.children.map(c => c.name), ['first', 'second', 'third']);
  });

  it('handles deeply nested expressions', () => {
    const depth = 2000;
    // Valid S-expression: (nest (nest (... (nest deepest)...)))
    // 'deepest' is an arg of the innermost 'nest', not a child node name
    const input = '(nest '.repeat(depth) + 'deepest' + ')'.repeat(depth);

    let ast;
    assert.doesNotThrow(() => {
      ast = parse(input);
    });

    let current = ast;
    let count = 0;
    while (current.children.length > 0) {
      current = current.children[0];
      count++;
    }

    // depth nest expressions, but innermost has 'deepest' as arg (not child)
    assert.equal(count, depth - 1);
    // Innermost nest has 'deepest' as argument
    assert.equal(current.name, 'nest');
    assert.deepEqual(current.args, ['deepest']);
  });

  it('handles empty string argument', () => {
    const ast = parse(EMPTY_STRING);
    assert.equal(ast.name, 'descr');
    assert.deepEqual(ast.args, ['']);
  });

  it('handles negative numbers', () => {
    const ast = parse(NUMERIC_ARGS);
    assert.equal(ast.name, 'xyz');
    assert.deepEqual(ast.args, [-12.34, 56.78, -90]);
  });

  it('preserves floating-point precision', () => {
    const ast = parse(`(x 15.405 0.0001 123.456789)`);
    assert.equal(ast.args[0], 15.405);
    assert.equal(ast.args[1], 0.0001);
    assert.equal(ast.args[2], 123.456789);
  });

  it('parses escaped strings correctly', () => {
    const ast = parse(`(property "Value" "Hello \\\"World\\\"")`);
    assert.deepEqual(ast.args, ['Value', 'Hello "World"']);
  });

  it('handles backslash escapes', () => {
    const ast = parse(`(path "C:\\\\Users\\\\Test")`);
    assert.deepEqual(ast.args, ['C:\\Users\\Test']);
  });

  it('throws on unexpected closing paren', () => {
    assert.throws(() => parse(`)extra`), ParseError);
  });

  it('throws on unclosed expression', () => {
    assert.throws(() => parse(`(unclosed`), ParseError);
  });

  it('throws on multiple root expressions', () => {
    assert.throws(() => parse(`(first)(second)`), ParseError);
  });

  it('throws on empty input', () => {
    assert.throws(() => parse(``), ParseError);
  });

  it('throws on whitespace-only input', () => {
    assert.throws(() => parse(`   \n\n  `), ParseError);
  });

  it('throws on completely missing expression name', () => {
    // An empty paren pair has no name
    assert.throws(() => parse(`()`), ParseError);
  });

  it('parses the complete FOOTPRINT fixture', () => {
    const ast = parse(FOOTPRINT_SEXP);
    assert.equal(ast.type, 'expression');
    assert.equal(ast.name, 'footprint');
    assert.deepEqual(ast.args, ['Capacitor_SMD:C_0603']);

    const pad = ast.children.find(c => c.name === 'pad');
    assert.ok(pad);
    assert.deepEqual(pad.args, ['1', 'smd', 'roundrect']);

    const padAt = pad.children.find(c => c.name === 'at');
    assert.ok(padAt);
    assert.deepEqual(padAt.args, [-0.85, 0, 90]);

    const padSize = pad.children.find(c => c.name === 'size');
    assert.ok(padSize);
    assert.deepEqual(padSize.args, [1, 0.85]);

    const padLayers = pad.children.find(c => c.name === 'layers');
    assert.ok(padLayers);
    assert.deepEqual(padLayers.args, ['F.Cu', 'F.Paste', 'F.Mask']);
  });

  it('parses the COMPLEX fixture', () => {
    const ast = parse(COMPLEX_SEXP);
    assert.equal(ast.name, 'kicad_pcb');

    const version = ast.children.find(c => c.name === 'version');
    assert.ok(version);
    assert.deepEqual(version.args, [20240101]);

    const layers = ast.children.find(c => c.name === 'layers');
    assert.ok(layers);
    assert.equal(layers.children.length, 3);

    const layer0 = layers.children[0];
    // In KiCad, layer entries use the layer number as the expression name
    assert.equal(layer0.name, '0');
    assert.deepEqual(layer0.args, ['F.Cu', 'signal']);
  });

  it('produces AST with _source metadata', () => {
    const ast = parse(SIMPLE_AT);
    assert.ok(ast._source !== undefined);
    assert.equal(ast._source, SIMPLE_AT);
  });

  it('produces AST with _format metadata', () => {
    const ast = parse(SIMPLE_AT);
    assert.ok(ast._format !== undefined);
    assert.equal(ast._format.prefix, '');
    assert.equal(ast._format.inline, true);
  });

  it('parseFull returns source alongside AST', () => {
    const result = parseFull(FOOTPRINT_SEXP);
    assert.equal(result.source, FOOTPRINT_SEXP);
    assert.equal(result.ast.name, 'footprint');
  });

  it('can disable formatting preservation', () => {
    const ast = parse(SIMPLE_AT, { preserveFormatting: false });
    assert.equal(ast._source, undefined);
    assert.equal(ast._format, undefined);
    assert.equal(ast.name, 'at');
    assert.deepEqual(ast.args, [121.5, 95.2, 90]);
  });
});

// =============================================================================
// 3. SERIALIZER UNIT TESTS
// =============================================================================

describe('Serializer', () => {
  it('serializes simple expression', () => {
    const ast = parse(SIMPLE_AT);
    const output = serialize(ast);
    assert.equal(output, SIMPLE_AT);
  });

  it('serializes expression with string args', () => {
    const input = `(layer "F.Cu")`;
    const ast = parse(input);
    const output = serialize(ast);
    assert.equal(output, input);
  });

  it('serializes nested structure', () => {
    const input = `(footprint "Test"\n  (pad "1" smd)\n)`;
    const ast = parse(input);
    const output = serialize(ast);
    assert.equal(output, input);
  });

  it('serializes empty string argument', () => {
    const ast = parse(EMPTY_STRING);
    const output = serialize(ast);
    assert.equal(output, EMPTY_STRING);
  });

  it('handles canonical formatting for programmatic AST', () => {
    const ast = {
      type: 'expression',
      name: 'test',
      args: ['hello', 42],
      children: [],
    };
    const output = serialize(ast, { useFormatting: false });
    // 'hello' is a valid bare identifier — no quotes needed
    assert.equal(output, '(test hello 42)');
  });

  it('handles canonical formatting with children', () => {
    const ast = {
      type: 'expression',
      name: 'root',
      args: [],
      children: [
        { type: 'expression', name: 'child1', args: [1], children: [] },
        { type: 'expression', name: 'child2', args: ['a'], children: [] },
      ],
    };
    const output = serialize(ast, { useFormatting: false });
    // 'a' is a valid bare identifier — no quotes needed
    assert.equal(output, '(root\n  (child1 1)\n  (child2 a)\n)');
  });

  it('escapes quotes in strings', () => {
    const ast = {
      type: 'expression',
      name: 'prop',
      args: ['He said "Hello"'],
      children: [],
    };
    const output = serialize(ast, { useFormatting: false });
    assert.equal(output, '(prop "He said \\"Hello\\"")');
  });

  it('escapes backslashes in strings', () => {
    const ast = {
      type: 'expression',
      name: 'path',
      args: ['C:\\Windows\\System'],
      children: [],
    };
    const output = serialize(ast, { useFormatting: false });
    assert.equal(output, '(path "C:\\\\Windows\\\\System")');
  });

  it('quotes strings that look like numbers', () => {
    const ast = {
      type: 'expression',
      name: 'x',
      args: ['3.14'],
      children: [],
    };
    const output = serialize(ast, { useFormatting: false });
    assert.equal(output, '(x "3.14")');
  });

  it('does not quote strings that are valid bare identifiers', () => {
    const ast = {
      type: 'expression',
      name: 'x',
      args: ['smd', 'roundrect', 'F.Cu'],
      children: [],
    };
    const output = serialize(ast, { useFormatting: false });
    assert.equal(output, '(x smd roundrect F.Cu)');
  });

  it('serializes document correctly', () => {
    const ast = parse(FOOTPRINT_SEXP);
    const output = serializeDocument(ast);
    assert.equal(output, FOOTPRINT_SEXP);
  });
});

// =============================================================================
// 4. ROUND-TRIP / IDENTITY TESTS (Zero-Data-Loss)
// =============================================================================

describe('Round-trip identity (serialize(parse(text)) === text)', () => {
  it('round-trips simple expression', () => {
    assert.equal(serialize(parse(SIMPLE_AT)), SIMPLE_AT);
  });

  it('round-trips footprint expression', () => {
    assert.equal(serialize(parse(FOOTPRINT_SEXP)), FOOTPRINT_SEXP);
  });

  it('round-trips complex nested structure', () => {
    assert.equal(serialize(parse(COMPLEX_SEXP)), COMPLEX_SEXP);
  });

  it('round-trips escaped strings', () => {
    const input = `(property "Value" "Hello \\\"World\\\"" (at 1 2))`;
    assert.equal(serialize(parse(input)), input);
  });

  it('round-trips empty string', () => {
    assert.equal(serialize(parse(EMPTY_STRING)), EMPTY_STRING);
  });

  it('round-trips negative numbers', () => {
    assert.equal(serialize(parse(NUMERIC_ARGS)), NUMERIC_ARGS);
  });

  it('round-trips multi-line with indentation', () => {
    const input = `(parent\n  (child1\n    (grandchild)\n  )\n  (child2)\n)`;
    assert.equal(serialize(parse(input)), input);
  });

  it('round-trips mixed tabs and spaces', () => {
    const input = `(root\n\t(child)\n)`;
    assert.equal(serialize(parse(input)), input);
  });

  it('round-trips CR+LF line endings', () => {
    const input = `(root\r\n  (child)\r\n)`;
    assert.equal(serialize(parse(input)), input);
  });

  it('round-trips real-world-like PCB snippet', () => {
    const input = `(kicad_pcb (version 20240101) (generator "pcbnew")
  (general
    (thickness 1.6)
    (legacy_teardrops no)
  )
  (paper "A4" portrait)
  (title_block
    (title "My Project")
    (date "2024-01-15")
    (rev "1.0")
  )
  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (32 "B.Adhes" user "B.Adhesive")
    (33 "F.Adhes" user "F.Adhesive")
  )
  (setup
    (pad_to_mask_clearance 0)
    (pcbplotparams
      (layerselection 0x00010fc_ffffffff)
      (plot_on_all_layers_selection 0x0000000_00000000)
    )
  )
)`;
    assert.equal(serialize(parse(input)), input);
  });

  it('round-trips multiple spaces between args', () => {
    const input = `(test  a   b    c)`;
    assert.equal(serialize(parse(input)), input);
  });

  it('round-trips no spaces between parens and content', () => {
    const input = `(x(y(z)))`;
    assert.equal(serialize(parse(input)), input);
  });

  it('round-trips a complete pad definition', () => {
    const input = `(pad "1" smd roundrect
  (at -0.85 0 90)
  (size 1 0.85)
  (layers "F.Cu" "F.Paste" "F.Mask")
  (net 1 "GND")
  (solder_mask_margin 0.05)
  (clearance 0.1)
)`;
    assert.equal(serialize(parse(input)), input);
  });

  it('round-trips scientific notation numbers', () => {
    const input = `(x 1.23e-4 5.67e+8)`;
    const ast = parse(input);
    assert.equal(ast.args[0], 1.23e-4);
    assert.equal(ast.args[1], 5.67e+8);
    assert.equal(serialize(ast), input);
  });
});

// =============================================================================
// 5. STRESS TESTS
// =============================================================================

describe('Stress tests', () => {
  it('handles 1,000 nesting levels without stack overflow', () => {
    const depth = 1000;
    const input = '(level '.repeat(depth) + 'core' + ')'.repeat(depth);

    const ast = parse(input);
    let current = ast;
    let count = 0;
    while (current.children.length > 0) {
      current = current.children[0];
      count++;
    }

    assert.equal(count, depth - 1);
    assert.equal(current.name, 'level');
    assert.deepEqual(current.args, ['core']);
  });

  it('handles 10,000 nesting levels', () => {
    const depth = 10000;
    const input = '(level '.repeat(depth) + 'bottom' + ')'.repeat(depth);

    const ast = parse(input);
    let current = ast;
    let count = 0;
    while (current.children.length > 0) {
      current = current.children[0];
      count++;
    }

    assert.equal(count, depth - 1);
    assert.equal(current.name, 'level');
    assert.deepEqual(current.args, ['bottom']);
  });

  it('handles 50,000-line document', () => {
    const lines = [];
    const count = 50000;

    lines.push('(kicad_pcb');
    for (let i = 0; i < count; i++) {
      lines.push(`  (element ${i} "value_${i}" (at ${i} ${i * 2} ${i % 360}))`);
    }
    lines.push(')');

    const input = lines.join('\n');

    const parseStart = performance.now();
    const ast = parse(input);
    const parseTime = performance.now() - parseStart;

    assert.equal(ast.name, 'kicad_pcb');
    assert.equal(ast.children.length, count);

    const serializeStart = performance.now();
    const output = serialize(ast);
    const serializeTime = performance.now() - serializeStart;

    assert.equal(output, input);
    assert.ok(parseTime + serializeTime < 10000,
      `Total time ${parseTime + serializeTime}ms exceeded 10s limit`);
  });

  it('handles very wide lines (many args)', () => {
    const argCount = 10000;
    const args = Array.from({ length: argCount }, (_, i) => `"arg_${i}"`).join(' ');
    const input = `(test ${args})`;

    const ast = parse(input);
    assert.equal(ast.args.length, argCount);

    const output = serialize(ast);
    assert.equal(output, input);
  });

  it('handles document with many siblings at same level', () => {
    const siblingCount = 5000;
    const children = Array.from(
      { length: siblingCount },
      (_, i) => `  (item ${i} "name_${i}")`
    ).join('\n');
    const input = `(root\n${children}\n)`;

    const ast = parse(input);
    assert.equal(ast.children.length, siblingCount);

    const output = serialize(ast);
    assert.equal(output, input);
  });

  it('round-trips after parse+serialize cycles multiple times', () => {
    let current = FOOTPRINT_SEXP;

    for (let i = 0; i < 10; i++) {
      current = serialize(parse(current));
    }

    assert.equal(current, FOOTPRINT_SEXP);
  });

  it('handles deeply nested mixed content', () => {
    let input = '(root "arg"';
    const depth = 500;
    for (let i = 0; i < depth; i++) {
      input += `\n  (level${i} ${i} "str_${i}"`;
    }
    input += ')'.repeat(depth + 1);

    const ast = parse(input);
    const output = serialize(ast);
    assert.equal(output, input);
  });
});

// =============================================================================
// 6. EDGE CASE TESTS
// =============================================================================

describe('Edge cases', () => {
  it('handles zero as argument', () => {
    const input = `(x 0)`;
    const ast = parse(input);
    assert.equal(ast.args[0], 0);
    assert.equal(serialize(ast), input);
  });

  it('handles negative zero', () => {
    const input = `(x -0)`;
    const ast = parse(input);
    assert.equal(ast.args[0], -0);
    assert.equal(serialize(ast), input);
  });

  it('handles very large integer', () => {
    const input = `(x 9007199254740991)`;
    const ast = parse(input);
    assert.equal(ast.args[0], 9007199254740991);
    assert.equal(serialize(ast), input);
  });

  it('handles very small positive float', () => {
    const input = `(x 0.0000001)`;
    const ast = parse(input);
    assert.equal(ast.args[0], 0.0000001);
    assert.equal(serialize(ast), input);
  });

  it('handles string with only special chars', () => {
    const input = `(x "   ")`;
    const ast = parse(input);
    assert.equal(ast.args[0], '   ');
    assert.equal(serialize(ast), input);
  });

  it('handles string with parentheses', () => {
    const input = `(note "This (and that) works")`;
    const ast = parse(input);
    assert.equal(ast.args[0], 'This (and that) works');
    assert.equal(serialize(ast), input);
  });

  it('handles identifiers starting with digits but not valid numbers', () => {
    const tokens = tokenize(`(x 1abc)`);
    const idToken = tokens.find(t => t.value === '1abc');
    assert.ok(idToken);
    assert.equal(idToken.type, 'identifier');
  });

  it('handles identifiers with underscores and dots', () => {
    const ast = parse(`(layer F.Cu_Signal)`);
    assert.equal(ast.args[0], 'F.Cu_Signal');
  });

  it('handles consecutive newlines', () => {
    const input = `(root\n\n\n  (child)\n\n)`;
    const ast = parse(input);
    const output = serialize(ast);
    assert.equal(output, input);
  });

  it('handles leading whitespace before root', () => {
    const input = `   \n  (root)`;
    const ast = parse(input);
    assert.equal(ast.name, 'root');
    assert.equal(serialize(ast), input);
  });

  it('handles expression with only children, no args', () => {
    const input = `(parent\n  (child)\n)`;
    const ast = parse(input);
    assert.deepEqual(ast.args, []);
    assert.equal(ast.children.length, 1);
    assert.equal(serialize(ast), input);
  });

  it('handles expression with only args, no children', () => {
    const input = `(leaf a b c)`;
    const ast = parse(input);
    assert.deepEqual(ast.args, ['a', 'b', 'c']);
    assert.deepEqual(ast.children, []);
    assert.equal(serialize(ast), input);
  });

  it('handles expression with no args and no children', () => {
    const input = `(empty)`;
    const ast = parse(input);
    assert.deepEqual(ast.args, []);
    assert.deepEqual(ast.children, []);
    assert.equal(serialize(ast), input);
  });

  it('handles all-whitespace string argument', () => {
    const input = `(x "\t  \n  ")`;
    const ast = parse(input);
    assert.equal(ast.args[0], '\t  \n  ');
    assert.equal(serialize(ast), input);
  });
});
