/**
 * KiCad S-Expression Parser
 *
 * Provides a fast, iterative tokenizer and parser that converts KiCad's
 * Lisp-like S-Expression format into a structured JSON AST.
 *
 * Key features:
 * - Iterative stack-based parsing (no recursion depth limits)
 * - Full whitespace capture for zero-data-loss round-tripping
 * - Proper escape handling inside quoted strings
 * - Numeric vs string argument distinction
 * - Handles files with 50,000+ lines efficiently
 */

import { LexerError, ParseError } from './types.js';

// Re-export error types
export { LexerError, ParseError };

// =============================================================================
// UTILITIES
// =============================================================================

/** Checks if a character is a line break. */
function isNewline(ch) {
  return ch === '\n' || ch === '\r';
}

/** Checks if a character is horizontal whitespace (space or tab). */
function isHWhitespace(ch) {
  return ch === ' ' || ch === '\t';
}

/** Checks if a character is any whitespace. */
function isWhitespace(ch) {
  return isHWhitespace(ch) || isNewline(ch);
}

/** Checks if a character is a digit. */
function isDigit(ch) {
  return ch >= '0' && ch <= '9';
}

/**
 * Determines whether an unquoted token should be treated as a number.
 * KiCad numbers: integers, floats, negative numbers, scientific notation.
 */
function parseNumeric(token) {
  if (token.length === 0) return null;

  // Hexadecimal check — KiCad doesn't use hex in expressions
  if (token.startsWith('0x') || token.startsWith('0X')) return null;

  // Strict regex for KiCad numeric literals
  const numRegex = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/;
  if (!numRegex.test(token)) return null;

  const num = Number(token);
  if (!Number.isFinite(num)) return null;

  return num;
}

// =============================================================================
// TOKENIZER / LEXER
// =============================================================================

/**
 * Converts the raw S-expression text into an ordered sequence of tokens.
 * Every character of the input is assigned to exactly one token,
 * guaranteeing that concatenating all token values reproduces the original text.
 *
 * @param {string} input
 * @returns {import('./types.js').Token[]}
 */
export function tokenize(input) {
  /** @type {import('./types.js').Token[]} */
  const tokens = [];
  let pos = 0;

  /**
   * @param {import('./types.js').TokenType} type
   * @param {string} value
   * @param {number} position
   */
  function emit(type, value, position) {
    tokens.push({ type, value, position });
  }

  while (pos < input.length) {
    const ch = input[pos];

    // Parentheses (single-character tokens)
    if (ch === '(') {
      emit('lparen', '(', pos);
      pos++;
      continue;
    }

    if (ch === ')') {
      emit('rparen', ')', pos);
      pos++;
      continue;
    }

    // Newlines
    if (ch === '\r') {
      if (pos + 1 < input.length && input[pos + 1] === '\n') {
        emit('newline', '\r\n', pos);
        pos += 2;
      } else {
        emit('newline', '\r', pos);
        pos++;
      }
      continue;
    }

    if (ch === '\n') {
      emit('newline', '\n', pos);
      pos++;
      continue;
    }

    // Horizontal whitespace
    if (isHWhitespace(ch)) {
      const start = pos;
      while (pos < input.length && isHWhitespace(input[pos])) {
        pos++;
      }
      emit('whitespace', input.slice(start, pos), start);
      continue;
    }

    // Quoted strings
    if (ch === '"') {
      const start = pos;
      pos++; // consume opening quote

      while (pos < input.length) {
        const c = input[pos];

        if (c === '\\') {
          // Escape sequence — consume both chars
          pos += 2;
          continue;
        }

        if (c === '"') {
          pos++; // consume closing quote
          emit('string', input.slice(start, pos), start);
          break;
        }

        pos++;
      }

      if (pos > input.length || (pos === input.length && input[pos - 1] !== '"')) {
        throw new LexerError('Unterminated string literal', start, input.slice(start, start + 20));
      }
      continue;
    }

    // Unquoted tokens (identifiers or numbers)
    const start = pos;
    while (pos < input.length) {
      const c = input[pos];
      if (isWhitespace(c) || c === '(' || c === ')') break;
      pos++;
    }

    const value = input.slice(start, pos);
    if (value.length === 0) {
      throw new LexerError(`Unexpected character "${ch}" (code ${ch.charCodeAt(0)})`, pos, input.slice(pos, pos + 10));
    }

    const num = parseNumeric(value);
    emit(num !== null ? 'number' : 'identifier', value, start);
  }

  return tokens;
}

// =============================================================================
// STACK-BASED ITERATIVE PARSER
// =============================================================================

/**
 * Converts a sequence of tokens into a structured AST.
 *
 * Uses an explicit stack instead of recursion, so it can handle
 * arbitrarily deeply nested KiCad files without call-stack overflow.
 *
 * @param {import('./types.js').Token[]} tokens
 * @param {string} source
 * @param {import('./types.js').ParserOptions} [options]
 * @returns {import('./types.js').ASTNode}
 */
export function parseTokens(tokens, source, options = {}) {
  const preserveFormatting = options.preserveFormatting !== false;

  /** @type {Array<{name: string, args: any[], children: import('./types.js').ASTNode[], tokenStart: number, nameTokenIndex: number, _pendingPrefix?: string}>} */
  const stack = [];
  let i = 0;
  let expectingName = false;
  /** @type {import('./types.js').ASTNode|null} */
  let rootNode = null;
  let pendingWhitespace = '';

  while (i < tokens.length) {
    const tok = tokens[i];

    // Accumulate whitespace/newline as pending formatting
    if (tok.type === 'whitespace' || tok.type === 'newline') {
      pendingWhitespace += tok.value;
      i++;
      continue;
    }

    if (tok.type === 'lparen') {
      // Starting a new expression
      expectingName = true;
      stack.push({
        name: '',
        args: [],
        children: [],
        tokenStart: i,
        nameTokenIndex: -1,
        _pendingPrefix: pendingWhitespace,
      });
      pendingWhitespace = '';
      i++;
      continue;
    }

    if (tok.type === 'rparen') {
      // Closing the current expression
      if (stack.length === 0) {
        throw new ParseError('Unexpected closing parenthesis', tok, 'top level');
      }

      const frame = stack.pop();
      if (!frame) {
        throw new ParseError('Unexpected closing parenthesis', tok, 'top level');
      }

      // Validate that the expression has a non-empty name
      if (frame.name === '') {
        throw new ParseError(
          'Expression has no name — expected identifier after "("',
          tok,
          'closing expression'
        );
      }

      /** @type {import('./types.js').NodeFormat|undefined} */
      let format = undefined;
      /** @type {string|undefined} */
      let nodeSource = undefined;

      if (preserveFormatting) {
        const prefix = frame._pendingPrefix ?? '';

        // Calculate _source: slice from the position of the opening '('
        // to the position after the closing ')'
        const openPos = tokens[frame.tokenStart].position;
        const closePos = tokens[i].position + 1; // +1 to include ')'
        nodeSource = source.slice(openPos, closePos);

        // Build formatting metadata
        format = extractFormat(nodeSource, frame.args.length, frame.children.length);
        format = { ...format, prefix };
      }

      /** @type {import('./types.js').ASTNode} */
      const node = {
        type: 'expression',
        name: frame.name,
        args: frame.args,
        children: frame.children,
      };

      if (preserveFormatting) {
        Object.defineProperty(node, '_format', { value: format, writable: true, configurable: true, enumerable: true });
        Object.defineProperty(node, '_source', { value: nodeSource, writable: true, configurable: true, enumerable: true });
      }

      if (stack.length > 0) {
        // Add as child to parent
        const parent = stack[stack.length - 1];
        parent.children.push(node);
      } else {
        // This is a root-level node
        if (rootNode !== null) {
          throw new ParseError(
            'Multiple root-level expressions detected. KiCad files should have a single root.',
            tok,
            'root level'
          );
        }
        rootNode = node;
      }

      pendingWhitespace = '';
      i++;
      continue;
    }

    // String, number, or identifier token
    if (stack.length === 0) {
      throw new ParseError(
        `Unexpected token "${tok.value}" outside of any expression`,
        tok,
        'top level'
      );
    }

    const frame = stack[stack.length - 1];

    if (expectingName) {
      // This token must be the expression name (identifier, string, or number)
      // KiCad allows numbers as expression names, e.g. (0 "F.Cu" signal)
      if (tok.type !== 'identifier' && tok.type !== 'string' && tok.type !== 'number') {
        throw new ParseError(
          `Expected expression name (identifier, string, or number), got ${tok.type}`,
          tok,
          `inside expression starting at ${frame.tokenStart}`
        );
      }
      frame.name = tok.type === 'string' ? unescapeString(tok.value) : tok.value;
      frame.nameTokenIndex = i;
      expectingName = false;
    } else {
      // This is an argument
      if (tok.type === 'string') {
        frame.args.push(unescapeString(tok.value));
      } else if (tok.type === 'number') {
        frame.args.push(Number(tok.value));
      } else if (tok.type === 'identifier') {
        const num = parseNumeric(tok.value);
        frame.args.push(num !== null ? num : tok.value);
      } else {
        throw new ParseError(`Unexpected token type: ${tok.type}`, tok, `inside "${frame.name}"`);
      }
    }

    i++;
  }

  if (stack.length > 0) {
    const frame = stack[stack.length - 1];
    throw new ParseError(
      `Unclosed expression "${frame.name}"`,
      tokens[frame.tokenStart] ?? null,
      'end of input'
    );
  }

  if (rootNode === null) {
    throw new ParseError('No valid expression found in input', null, 'empty input');
  }

  return rootNode;
}

/**
 * Extracts formatting metadata from the original source text of a node.
 * @param {string} source
 * @param {number} argCount
 * @param {number} childCount
 * @returns {import('./types.js').NodeFormat}
 */
function extractFormat(source, argCount, childCount) {
  const hasNewline = source.includes('\n');
  const prefixMatch = source.match(/^(\s*)\(/);
  const prefix = prefixMatch ? prefixMatch[1] : '';
  const suffix = '';

  return {
    prefix,
    afterName: ' ',
    betweenArgs: Array(argCount).fill(' '),
    betweenChildren: Array(childCount).fill(hasNewline ? '\n  ' : ' '),
    suffix,
    inline: !hasNewline,
  };
}

/**
 * Unescapes a KiCad quoted string.
 * Handles \" -> " and \\ -> \
 * @param {string} quoted
 * @returns {string}
 */
function unescapeString(quoted) {
  // Remove surrounding quotes
  const inner = quoted.slice(1, -1);
  let result = '';
  let i = 0;

  while (i < inner.length) {
    if (inner[i] === '\\' && i + 1 < inner.length) {
      const next = inner[i + 1];
      if (next === '"' || next === '\\') {
        result += next;
        i += 2;
        continue;
      }
      result += '\\';
      i++;
      continue;
    }
    result += inner[i];
    i++;
  }

  return result;
}

// =============================================================================
// HIGH-LEVEL API
// =============================================================================

/**
 * Parses a KiCad S-expression string into an AST.
 *
 * @param {string} input - The raw KiCad S-expression text
 * @param {import('./types.js').ParserOptions} [options]
 * @returns {import('./types.js').ASTNode}
 */
export function parse(input, options) {
  const tokens = tokenize(input);
  return parseTokens(tokens, input, options);
}

/**
 * Full parse returning both the AST and the original source.
 * @param {string} input
 * @param {import('./types.js').ParserOptions} [options]
 * @returns {import('./types.js').ParseResult}
 */
export function parseFull(input, options) {
  const tokens = tokenize(input);
  const ast = parseTokens(tokens, input, options);
  return { ast, source: input };
}
