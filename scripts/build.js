/**
 * CircuitBot frontend build script
 *
 * Concatenates all local JS files into a single bundle (plus a 3D bundle for
 * the lazy-loaded 3D viewer).  CDN scripts (socket.io, pixi.js, three.js etc.)
 * remain as external <script> tags.
 *
 * Generates content-hash cache busters and patches them into index.html.
 *
 * Usage:  node scripts/build.js
 *         npm run build        (from package.json)
 */

const crypto = require('crypto')
const esbuild = require('esbuild')
const fs = require('fs')
const path = require('path')

const STATIC = path.resolve(__dirname, '..', 'static')
const INDEX_HTML = path.resolve(__dirname, '..', 'static', 'index.html')

/* ── Source files in load order (from index.html) ─────────────────────── */

const MAIN_SOURCES = [
  'schematic.js',
  'renderer_legacy.js',
  'schematic_renderer.js',
  'pcb_view/constants.js',
  'pcb_view/state.js',
  'pcb_view/utils.js',
  'pcb_view/gl_math.js',
  'pcb_view/kicanvas_transform.js',
  'pcb_view/kicanvas_webgl_helpers.js',
  'pcb_view/kicanvas_webgl_vector.js',
  'pcb_view/kicanvas_webgl_renderer.js',
  'pcb_view/kicanvas_board_painter.js',
  'pcb_view/stroke-font-data.js',
  'pcb_view/stroke-font.js',
  'pcb_view/editor_webgl.js',
  'pcb_view/events.js',
  'tscircuit-bridge.js',
  'app.js',
  'panels.js',
  'ui-enhancements.js',
  'pipeline-panel.js',
]

/* 3D viewer sources (lazy-loaded, separate bundle) */
const THREE_D_SOURCES = [
  'pcb_view/pcb_viewer_3d/constants_3d.js',
  'pcb_view/pcb_viewer_3d/model_cache.js',
  'pcb_view/pcb_viewer_3d/scene_setup.js',
  'pcb_view/pcb_viewer_3d/camera_controller.js',
  'pcb_view/pcb_viewer_3d/board_mesh_builder.js',
  'pcb_view/pcb_viewer_3d/placeholder_builder.js',
  'pcb_view/pcb_viewer_3d/component_model_loader.js',
  'pcb_view/pcb_viewer_3d/component_placer.js',
  'pcb_view/pcb_viewer_3d/layer_panel_3d.js',
  'pcb_view/pcb_viewer_3d/pcb_viewer_3d.js',
]

/* ── Helpers ──────────────────────────────────────────────────────────── */

function concatSources(sources) {
  const parts = []
  for (const rel of sources) {
    const abs = path.join(STATIC, rel)
    if (!fs.existsSync(abs)) {
      console.warn('  ⚠  missing:', rel)
      continue
    }
    const content = fs.readFileSync(abs, 'utf8')
    parts.push('/* ----- ' + rel + ' ----- */\n' + content)
  }
  return parts.join('\n\n')
}

/** Compute a short content-hash for cache busting */
function contentHash(code) {
  return 'v=' + crypto.createHash('md5').update(code).digest('hex').slice(0, 10)
}

/** Replace cache-bust placeholders (first build) or existing hashes (re-builds) */
function patchIndexHtml(mainHash, threeDHash) {
  let html = fs.readFileSync(INDEX_HTML, 'utf8')
  const before = html
  // First build: replace __BUILD_HASH__ placeholder
  html = html.replace(/__BUILD_HASH__/g, mainHash)
  html = html.replace(/__BUILD_HASH_3D__/g, threeDHash)
  // Re-build: match existing ?v=... pattern and replace hash portion
  html = html.replace(/(app\.bundle\.js\?v=)[a-f0-9]+/g, '$1' + mainHash.replace('v=', ''))
  html = html.replace(/(app\.bundle\.3d\.js\?v=)[a-f0-9]+/g, '$1' + threeDHash.replace('v=', ''))
  if (html !== before) {
    fs.writeFileSync(INDEX_HTML, html, 'utf8')
    console.log('  ✓ patched index.html with cache busters')
  } else {
    console.log('  ℹ  index.html cache busters are up-to-date')
  }
}

/* ── Build functions ──────────────────────────────────────────────────── */

async function buildMain() {
  console.log('Building main bundle …')
  const concat = concatSources(MAIN_SOURCES)

  const result = await esbuild.transform(concat, {
    minify: true,
    sourcemap: false,
    target: 'es2020',
  })

  const code = result.code
  const hash = contentHash(code)
  const outPath = path.join(STATIC, 'app.bundle.js')
  fs.writeFileSync(outPath, code, 'utf8')
  console.log(`  ✓ ${outPath}  (${(code.length / 1024).toFixed(1)} KB)  [${hash}]`)
  return hash
}

async function build3D() {
  console.log('Building 3D viewer bundle …')
  const concat = concatSources(THREE_D_SOURCES)

  const result = await esbuild.transform(concat, {
    minify: true,
    sourcemap: false,
    target: 'es2020',
  })

  const code = result.code
  const hash = contentHash(code)
  const outPath = path.join(STATIC, 'app.bundle.3d.js')
  fs.writeFileSync(outPath, code, 'utf8')
  console.log(`  ✓ ${outPath}  (${(code.length / 1024).toFixed(1)} KB)  [${hash}]`)
  return hash
}

/* ── Run ──────────────────────────────────────────────────────────────── */

async function main() {
  const start = Date.now()
  try {
    const mainHash = await buildMain()
    const threeDHash = await build3D()
    patchIndexHtml(mainHash, threeDHash)
    console.log(`\nDone in ${Date.now() - start} ms`)
  } catch (err) {
    console.error('Build failed:', err.message)
    process.exit(1)
  }
}

main()
