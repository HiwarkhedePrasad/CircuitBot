/**
 * KiCad S-Expression Serializer
 *
 * Converts a JSON AST back into KiCad's S-Expression text format.
 *
 * Provides two serialization strategies:
 * 1. **Exact round-trip**: When the AST carries _source / _format metadata,
 *    the serializer reproduces the original text character-for-character.
 * 2. **Canonical formatting**: When nodes have no metadata, the serializer
 *    generates clean, standard KiCad formatting.
 */

// =============================================================================
// ESCAPING
// =============================================================================

/** Characters that require escaping inside KiCad double-quoted strings. */
const ESCAPE_REGEX = /[\\"]/g;

/** Escapes a string value for use inside KiCad double quotes. */
function escapeString(value) {
  return value.replace(ESCAPE_REGEX, '\\$&');
}

/**
 * Determines if a string argument needs to be quoted in KiCad output.
 * @param {string} value
 * @returns {boolean}
 */
function needsQuoting(value) {
  if (value.length === 0) return true;

  for (let i = 0; i < value.length; i++) {
    const ch = value[i];
    if (ch === ' ' || ch === '\t' || ch === '\n' || ch === '\r') return true;
    if (ch === '(' || ch === ')') return true;
    if (ch === '"') return true;
    if (ch === '\\') return true;
  }

  // Check if it looks like a number
  const numRegex = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/;
  if (numRegex.test(value)) return true;

  return false;
}

/**
 * Formats a single argument for S-expression output.
 * @param {import('./types.js').ASTArg} arg
 * @returns {string}
 */
function formatArg(arg) {
  if (typeof arg === 'number') {
    if (Object.is(arg, -0)) return '-0';
    return String(arg);
  }

  if (needsQuoting(arg)) {
    return `"${escapeString(arg)}"`;
  }

  return arg;
}

// =============================================================================
// SERIALIZER
// =============================================================================

/**
 * Serializes an AST node back into KiCad S-expression text.
 *
 * @param {import('./types.js').ASTNode} ast - The AST node to serialize
 * @param {import('./types.js').SerializerOptions} [options] - Optional serializer configuration
 * @returns {string}
 */
export function serialize(ast, options = {}) {
  const useFormatting = options.useFormatting !== false;

  // Fast path: if the node has its original source text, use it directly
  // For root nodes with leading whitespace, prepend the prefix from _format
  if (useFormatting && ast._source) {
    if (ast._format?.prefix) {
      return ast._format.prefix + ast._source;
    }
    return ast._source;
  }

  // Otherwise, build the output with canonical formatting
  return serializeNode(ast, 0, '  ', useFormatting ? ast._format : undefined);
}

/**
 * Serializes an AST node into its S-expression string representation.
 *
 * @param {import('./types.js').ASTNode} node
 * @param {number} depth
 * @param {string} indent
 * @param {import('./types.js').NodeFormat} [format]
 * @returns {string}
 */
function serializeNode(node, depth, indent, format) {
  // Fast path: exact source available
  if (node._source) {
    return node._source;
  }

  const prefix = format?.prefix ?? '';

  // Build the opening of the expression: prefix + "(" + name
  let result = prefix + '(' + node.name;

  // Add arguments
  if (node.args.length > 0) {
    const betweenArgs = format?.betweenArgs;
    for (let i = 0; i < node.args.length; i++) {
      const separator = betweenArgs?.[i] ?? ' ';
      result += separator + formatArg(node.args[i]);
    }
  }

  // Add children
  if (node.children.length > 0) {
    const inline = format?.inline ?? (node.children.length === 0);
    const betweenChildren = format?.betweenChildren;

    if (inline && !betweenChildren) {
      for (let i = 0; i < node.children.length; i++) {
        const separator = betweenChildren?.[i] ?? ' ';
        const childText = serializeNodeInternal(node.children[i], depth + 1, indent, undefined);
        result += separator + childText;
      }
    } else {
      const childIndent = indent.repeat(depth + 1);
      for (let i = 0; i < node.children.length; i++) {
        const separator = betweenChildren?.[i] ?? '\n' + childIndent;
        const sep = separator || '\n' + childIndent;
        const childText = serializeNodeInternal(node.children[i], depth + 1, indent, undefined);
        result += sep + childText;
      }
      result += '\n' + indent.repeat(depth);
    }
  }

  result += ')';

  if (format?.suffix) {
    result += format.suffix;
  }

  return result;
}

/**
 * Internal serializer for child nodes (strips prefix).
 * @param {import('./types.js').ASTNode} node
 * @param {number} depth
 * @param {string} indent
 * @param {import('./types.js').NodeFormat} [format]
 * @returns {string}
 */
function serializeNodeInternal(node, depth, indent, format) {
  if (node._source) {
    return node._source.replace(/^\s*/, '');
  }

  let result = '(' + node.name;

  if (node.args.length > 0) {
    for (const arg of node.args) {
      result += ' ' + formatArg(arg);
    }
  }

  if (node.children.length > 0) {
    const childIndent = indent.repeat(depth + 1);
    for (let i = 0; i < node.children.length; i++) {
      const childText = serializeNodeInternal(node.children[i], depth + 1, indent, undefined);
      result += '\n' + childIndent + childText;
    }
    result += '\n' + indent.repeat(depth);
  }

  result += ')';
  return result;
}

/**
 * Serializes a complete AST document.
 * @param {import('./types.js').ASTNode} ast
 * @param {import('./types.js').SerializerOptions} [options]
 * @returns {string}
 */
export function serializeDocument(ast, options) {
  return serialize(ast, options);
}
