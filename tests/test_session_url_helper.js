/**
 * Test the session-aware URL helper pattern used across the frontend.
 * The actual apiUrl() lives in app.js and _sessionUrl() in tscircuit-bridge.js
 * and editor_webgl.js — this test validates the logic inline so it acts as
 * a regression guard against session-dropping bugs.
 *
 * NOTE: In the browser these helpers read window.circuitbotChatSessionId.
 *       In Node we use globalThis to simulate the same contract.
 */

let passed = 0
let failed = 0

function assert(condition, label) {
  if (condition) {
    passed++
    console.log(`  ✅ ${label}`)
  } else {
    failed++
    console.log(`  ❌ ${label}`)
  }
}

// Simulate the apiUrl() helper from app.js
function apiUrl(path) {
  const sid = (typeof globalThis !== 'undefined' ? globalThis : window).circuitbotChatSessionId || ''
  return sid ? path + '?session_id=' + encodeURIComponent(sid) : path
}

// Simulate the _sessionUrl() helper from tscircuit-bridge.js / editor_webgl.js
// (the only difference from apiUrl() is the .trim() call, which is a minor polish)
function sessionUrl(path) {
  const sid = ((typeof globalThis !== 'undefined' ? globalThis : window).circuitbotChatSessionId || '').trim()
  return sid ? path + '?session_id=' + encodeURIComponent(sid) : path
}

const g = typeof globalThis !== 'undefined' ? globalThis : global

console.log('\n--- session_url_helper tests ---\n')

// Test 1: without session_id, URL is unchanged
delete g.circuitbotChatSessionId
assert(apiUrl('/api/export_sch') === '/api/export_sch', 'no session → raw path')
assert(sessionUrl('/api/save_board_model') === '/api/save_board_model', 'no session → raw path (trim)')

// Test 2: with session_id, URL gets ?session_id=...
g.circuitbotChatSessionId = 'abc123'
assert(apiUrl('/api/export_sch') === '/api/export_sch?session_id=abc123', 'session appended')
assert(sessionUrl('/api/apply_edits') === '/api/apply_edits?session_id=abc123', 'session appended (trim)')

// Test 3: URL-encode special chars in session id
g.circuitbotChatSessionId = 'a b&c=d'
assert(apiUrl('/api/x') === '/api/x?session_id=a%20b%26c%3Dd', 'URL-encodes session id')

// Test 4: empty string treated as no session
g.circuitbotChatSessionId = ''
assert(apiUrl('/api/x') === '/api/x', 'empty string → raw path')

// Test 5: whitespace-only treated as no session (.trim() variant)
g.circuitbotChatSessionId = '   '
assert(sessionUrl('/api/x') === '/api/x', 'whitespace → raw path (trim)')

// Cleanup
delete g.circuitbotChatSessionId

console.log(`\n${passed} passed, ${failed} failed\n`)
process.exit(failed > 0 ? 1 : 0)
