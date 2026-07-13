/* tscircuit PCB viewer bridge
 *
 * Drives the @tscircuit/runframe standalone-preview bundle (CircuitJsonPreview).
 *
 * The standalone bundle is a one-shot renderer: when its <script> is evaluated
 * it calls `createRoot(document.getElementById("root")).render(...)` exactly
 * once, reading `window.CIRCUIT_JSON` at that moment.  There is no reactive
 * update path.  To show a new board we therefore must:
 *   1. set window.CIRCUIT_JSON to the fresh data,
 *   2. put a fresh <div id="root"> in the DOM, and
 *   3. re-run the bundle by removing the old <script> and appending a new one.
 *
 * The host container ("tscircuit-container") is kept stable and is never
 * renamed, so other code (app.js) can keep querying it.  The bundle renders
 * into a child #root that we own and replace on each render.
 *
 * API exposed on window.TscircuitViewer:
 *   mount(containerId, circuitJson?) – fetch Circuit JSON (or use the arg),
 *                                      clear container, inject #root, load bundle
 *   unmount()                        – remove bundle script + #root, clear global
 *   refresh(circuitJson?)            – re-render: mount() again into the same container
 *   isMounted()                      – true after a successful mount, false after unmount
 */

(function () {
  "use strict"

  let _containerEl = null      // the stable host element (e.g. #tscircuit-container)
  let _scriptEl = null         // the <script> currently driving the bundle
  let _pendingCircuitJson = null
  let _mounted = false
  let _pendingEditEvents = []
  let _flushTimer = null
  let _isFlushing = false
  var MAX_PENDING_EDIT_EVENTS = 250

  var BUNDLE_SRC = "/static/tscircuit-viewer.min.js"
  var BASE_PROPS = {
    defaultActiveTab: "pcb",
    availableTabs: ["pcb", "cad"],
    showRightHeaderContent: false,
  }

  /* ---- helpers ------------------------------------------------------- */

  function _sessionUrl(path) {
    var sid = (window.circuitbotChatSessionId || "").trim()
    return sid ? path + "?session_id=" + encodeURIComponent(sid) : path
  }

  function fetchCircuitJson() {
    var body = null
    if (window.pcbState && window.pcbState.boardModel) {
      body = JSON.stringify({ board_model: window.pcbState.boardModel })
    }
    return fetch(_sessionUrl("/api/circuit_json"), {
      method: body ? "POST" : "GET",
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body,
    }).then(function (r) {
      if (!r.ok) throw new Error("Failed to fetch Circuit JSON: " + r.status)
      return r.json()
    })
  }

  function dispatchSyncEvent(name, detail) {
    try {
      window.dispatchEvent(new CustomEvent(name, { detail: detail || {} }))
    } catch (_) {}
  }

  async function persistBoardFallback() {
    if (!window.pcbState || !window.pcbState.boardModel) return
    await fetch(_sessionUrl("/api/save_board_model"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ board_model: window.pcbState.boardModel }),
    })
  }

  function refreshBoardRatsnest() {
    if (!window.pcbState || !window.pcbState.boardModel) return
    var bm = window.pcbState.boardModel
    if (!bm.ratsnest || Object.keys(bm.ratsnest).length === 0) {
      if (typeof window.pcbFetchRatsnest === "function") {
        window.pcbFetchRatsnest()
      }
    }
  }

  function shouldCommitEvent(evt) {
    return !!evt && typeof evt === "object" && evt.in_progress === false
  }

  function scheduleFlush() {
    if (_flushTimer) return
    _flushTimer = setTimeout(function () {
      _flushTimer = null
      flushEditEvents()
    }, 120)
  }

  async function flushEditEvents() {
    if (_isFlushing || _pendingEditEvents.length === 0) return
    _isFlushing = true
    var batch = _pendingEditEvents
    _pendingEditEvents = []
    var committed = batch.filter(shouldCommitEvent)
    if (committed.length === 0) {
      _isFlushing = false
      return
    }
    try {
      var resp = await fetch(_sessionUrl("/api/apply_edits"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ edit_events: committed }),
      })
      var data = await resp.json()
      if (!resp.ok || !data.ok) {
        throw new Error(data.error || ("apply_edits failed (" + resp.status + ")"))
      }
      if (data.board_model && window.pcbState) {
        window.pcbState.boardModel = data.board_model
      }
      refreshBoardRatsnest()
      dispatchSyncEvent("tscircuit:edit-sync", {
        ok: true,
        applied: data.applied || 0,
        ignored: data.ignored || 0,
      })
      dispatchSyncEvent("tscircuit:board-model-updated", {
        source: "apply_edits",
        board_model: data.board_model || null,
      })
    } catch (err) {
      try {
        await persistBoardFallback()
        dispatchSyncEvent("tscircuit:edit-sync", {
          ok: false,
          fallback_saved: true,
          error: err.message || String(err),
        })
      } catch (fallbackErr) {
        dispatchSyncEvent("tscircuit:edit-sync", {
          ok: false,
          fallback_saved: false,
          error: (err.message || String(err)) + "; fallback failed: " + (fallbackErr.message || String(fallbackErr)),
        })
      }
    } finally {
      _isFlushing = false
      if (_pendingEditEvents.length > 0) {
        scheduleFlush()
      }
    }
  }

  function enqueueEditEvent(evt) {
    if (!evt || typeof evt !== "object") return
    _pendingEditEvents.push(evt)
    if (_pendingEditEvents.length > MAX_PENDING_EDIT_EVENTS) {
      _pendingEditEvents = _pendingEditEvents.slice(-MAX_PENDING_EDIT_EVENTS)
    }
    scheduleFlush()
  }

  function buildProps() {
    return {
      ...BASE_PROPS,
      onEditEvent: enqueueEditEvent,
      onCreateEditEvent: enqueueEditEvent,
      onModifyEditEvent: enqueueEditEvent,
      onEditEventsChanged: function (events) {
        if (!Array.isArray(events)) return
        for (var i = 0; i < events.length; i++) {
          enqueueEditEvent(events[i])
        }
      },
    }
  }

  /* Remove the previous bundle <script> so the browser will re-execute a
   * freshly appended one (the standalone preview runs only at load time). */
  function removeBundleScript() {
    if (_scriptEl && _scriptEl.parentNode) {
      _scriptEl.parentNode.removeChild(_scriptEl)
    }
    _scriptEl = null
  }

  /* Tear down whatever the previous render created inside the host container,
   * then inject a brand-new <div id="root"> for the bundle to mount into. */
  function resetContainer() {
    if (!_containerEl) return null
    _containerEl.innerHTML = ""
    var root = document.createElement("div")
    root.id = "root"
    root.style.width = "100%"
    root.style.height = "100%"
    _containerEl.appendChild(root)
    return root
  }

  /* Append a fresh bundle <script>.  Because standalone-preview reads
   * window.CIRCUIT_JSON at evaluation time, the global must already hold the
   * data we want rendered. */
  function loadBundle() {
    return new Promise(function (resolve, reject) {
      var s = document.createElement("script")
      s.src = BUNDLE_SRC
      s.onload = function () {
        resolve()
      }
      s.onerror = function () {
        reject(new Error("Failed to load tscircuit viewer script"))
      }
      document.body.appendChild(s)
      _scriptEl = s
    })
  }

  /* ---- public API --------------------------------------------------- */

  window.TscircuitViewer = {
    mount: function (containerId, circuitJson) {
      var container = document.getElementById(containerId)
      if (!container) {
        return Promise.reject(new Error("Container #" + containerId + " not found"))
      }
      _containerEl = container

      var jsonPromise = circuitJson
        ? Promise.resolve(circuitJson)
        : fetchCircuitJson()

      return jsonPromise
        .then(function (circuitJson) {
          _pendingCircuitJson = circuitJson
          window.CIRCUIT_JSON = circuitJson
          window.CIRCUIT_JSON_PREVIEW_PROPS = buildProps()

          // Fresh DOM + fresh script => the bundle re-renders the new JSON.
          removeBundleScript()
          resetContainer()
          return loadBundle()
        })
        .then(function () {
          _mounted = true
          console.log("[TscircuitViewer] mounted")
        })
        .catch(function (err) {
          console.error("[TscircuitViewer] mount error:", err)
          _mounted = false
          if (_containerEl) {
            _containerEl.innerHTML =
              '<div style="color:#c00;padding:2em">Failed to load PCB viewer: ' +
              err.message +
              "</div>"
          }
          throw err
        })
    },

    unmount: function () {
      if (_flushTimer) {
        clearTimeout(_flushTimer)
        _flushTimer = null
      }
      _pendingEditEvents = []
      removeBundleScript()
      if (_containerEl) {
        _containerEl.innerHTML = ""
      }
      _containerEl = null
      _pendingCircuitJson = null
      _mounted = false
      window.CIRCUIT_JSON = null
    },

    /* Re-render into the same container.  If the viewer was never mounted,
     * callers should use mount() instead; refresh() will reject so the caller
     * can fall back to mount(). */
    refresh: function (circuitJson) {
      if (!_containerEl) {
        return Promise.reject(new Error("Not mounted"))
      }
      return this.mount(_containerEl.id, circuitJson)
    },

    isMounted: function () {
      return _mounted
    },
  }
})()
