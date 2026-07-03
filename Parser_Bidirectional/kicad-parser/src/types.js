/**
 * Core type definitions for the KiCad S-Expression Parser.
 *
 * This module defines the strict type structures for:
 * - Token representation (lexer output)
 * - AST Node structure (parser output / serializer input)
 * - Parser configuration options
 * - Formatting metadata for zero-data-loss round-tripping
 */

// =============================================================================
// TOKEN TYPES
// =============================================================================

/** @typedef {'lparen'|'rparen'|'string'|'number'|'identifier'|'whitespace'|'newline'} TokenType */

/**
 * @typedef {Object} Token
 * @property {TokenType} type
 * @property {string} value - The raw text of this token as it appeared in the source
 * @property {number} position - Character offset in the original source string
 */

// =============================================================================
// AST NODE TYPES
// =============================================================================

/** @typedef {string|number} ASTArg */

/**
 * Formatting metadata attached to each AST node to enable
 * zero-data-loss round-trip serialization.
 *
 * @typedef {Object} NodeFormat
 * @property {string} prefix - Whitespace/text preceding the opening '('
 * @property {string} afterName - Whitespace between the node name and the first arg/child
 * @property {readonly string[]} betweenArgs - Whitespace between arguments
 * @property {readonly string[]} betweenChildren - Whitespace between child expressions
 * @property {string} suffix - Whitespace/text after the closing ')'
 * @property {boolean} inline - Whether this node was formatted inline
 */

/**
 * Represents a single KiCad S-expression as a node in the Abstract Syntax Tree.
 *
 * @typedef {Object} ASTNode
 * @property {'expression'} type
 * @property {string} name - The identifier atom that names this expression
 * @property {readonly ASTArg[]} args - Positional arguments
 * @property {readonly ASTNode[]} children - Nested S-expression children
 * @property {NodeFormat} [_format] - Auto-generated formatting metadata (internal)
 * @property {string} [_source] - The exact original source text of this node (internal)
 */

// =============================================================================
// PARSER CONFIGURATION
// =============================================================================

/**
 * @typedef {Object} ParserOptions
 * @property {boolean} [preserveFormatting=true] - Capture formatting metadata for round-tripping
 */

/**
 * @typedef {Object} ParseResult
 * @property {ASTNode} ast - The root AST node
 * @property {string} source - The original source text
 */

// =============================================================================
// SERIALIZER OPTIONS
// =============================================================================

/**
 * @typedef {Object} SerializerOptions
 * @property {string} [indent='  '] - Indentation string for canonical formatting
 * @property {boolean} [useFormatting=true] - Use _format/_source metadata
 */

// =============================================================================
// PARSER ERROR TYPES
// =============================================================================

/** Custom error thrown when the tokenizer encounters invalid input. */
export class LexerError extends Error {
  /**
   * @param {string} message
   * @param {number} position
   * @param {string} sourceSnippet
   */
  constructor(message, position, sourceSnippet) {
    super(`Lexer error at position ${position}: ${message}\n  Near: "${sourceSnippet}"`);
    this.name = 'LexerError';
    this.position = position;
    this.sourceSnippet = sourceSnippet;
  }
}

/** Custom error thrown when the parser encounters a syntax error. */
export class ParseError extends Error {
  /**
   * @param {string} message
   * @param {Token|null} token
   * @param {string} context
   */
  constructor(message, token, context) {
    const pos = token ? `at position ${token.position}` : 'at end of input';
    super(`Parse error ${pos}: ${message}\n  Context: ${context}`);
    this.name = 'ParseError';
    this.token = token;
    this.context = context;
  }
}
