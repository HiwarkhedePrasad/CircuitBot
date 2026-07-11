---
name: graph-visualization-debug
description: Debug graph visualization rendering failures, especially in VS Code extensions using GraphViewProvider or similar webview-based graph components. Use when the user reports "graph not showing", "Failed to render graph", or "Cannot read properties of undefined" in graph-related code.
---

# Graph Visualization Debugging

Systematic workflow for diagnosing and fixing graph rendering failures, particularly in VS Code extension webviews.

## When to Use

- "Graph is not showing" or "graph not rendered"
- "Failed to render graph: Cannot read properties of undefined"
- GraphViewProvider or similar webview component errors
- D3/vis.js/cytoscape graph not displaying in webview
- VS Code extension webview showing blank or error state

## Diagnostic Workflow

### Step 1: Identify the Graph Library

Check the project's dependencies:
```bash
cat package.json | grep -E "d3|vis|cytoscape|graphology|sigma|force-graph"
```

Common libraries:
- **D3.js** — Low-level, manual SVG/Canvas rendering
- **vis-network** — Network graph with physics simulation
- **cytoscape.js** — Graph theory library
- **graphology** — Graph data structure + Sigma.js rendering
- **sigma.js** — WebGL graph renderer

### Step 2: Check for Null/Undefined Data

The most common cause: graph data contains null/undefined values that crash the renderer.

```typescript
// BAD — crashes if any field is missing
g.addNode(node.id, {
    x, y,
    size,
    color,
    label: node.name,
    nodeType: node.type,
    filePath: node.path,
    cluster: node.cluster
});

// GOOD — defensive defaults
g.addNode(node.id, {
    x: x || 0,
    y: y || 0,
    size: size || 6,
    color: color || '#6b7280',
    label: node.name || node.id,
    nodeType: node.type || 'unknown',
    filePath: node.path || '',
    cluster: node.cluster ?? -1
});
```

**Always check:**
- Are node positions (x, y) guaranteed to be numbers?
- Are node labels guaranteed to be strings?
- Are edge source/target IDs guaranteed to exist in the node set?
- Is the graph container element guaranteed to be in the DOM?

### Step 3: Check Webview Communication

In VS Code extensions, graph data often passes through webview messaging:

```typescript
// In the provider (extension side)
panel.webview.postMessage({ type: 'graph-data', nodes, edges });

// In the webview (renderer side)
window.addEventListener('message', (event) => {
    const { type, nodes, edges } = event.data;
    if (type === 'graph-data') {
        renderGraph(nodes, edges);
    }
});
```

**Common issues:**
- Message sent before webview is ready
- Data serialized incorrectly (functions, circular references)
- Webview HTML not loaded yet when message arrives

### Step 4: Check Webview HTML/CSS

```html
<!-- Ensure the graph container has dimensions -->
<div id="graph" style="width: 100%; height: 100%;"></div>

<!-- NOT -->
<div id="graph"></div>  <!-- zero height = nothing renders -->
```

**Common issues:**
- Container has `display: none` or zero dimensions
- CSS `overflow: hidden` clipping the graph
- Z-index conflicts with other webview elements

### Step 5: Check Console Errors

In VS Code webview:
1. Open Developer Tools (Help > Toggle Developer Tools)
2. Check Console tab for errors
3. Look for:
   - `Cannot read properties of undefined (reading 'process')` — Node.js API used in webview context
   - `ResizeObserver loop` — Container size changing during render
   - `WebGL context lost` — GPU/rendering issue

### Step 6: Check VS Code Webview Restrictions

VS Code webviews have restricted APIs:
- No `require()` or `process` access
- No Node.js APIs directly
- Must use `@vscode/webview-ui-toolkit` or bundled libraries

```typescript
// BAD — crashes in webview
const fs = require('fs');

// GOOD — data passed from extension host
// Extension reads file, sends content via postMessage
```

### Step 7: Verify Data Pipeline

Trace the data flow from source to renderer:

```
Source (indexer/parser) → Extension host → postMessage → Webview → Graph library → DOM/Canvas
```

Check each handoff:
1. **Source**: Does the indexer produce valid node/edge data?
2. **Extension host**: Is data correctly extracted from the indexer output?
3. **postMessage**: Is JSON serialization successful? Any circular references?
4. **Webview receive**: Is the message handler registered before messages arrive?
5. **Graph library**: Are nodes/edges in the expected format for the library?
6. **DOM**: Is the container element present and sized?

## Common Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| `Cannot read properties of undefined (reading 'process')` | Node.js API in webview | Move logic to extension host, pass data via postMessage |
| Graph container blank | Zero dimensions | Set explicit `width` and `height` on container |
| Nodes not positioned | x/y undefined | Add defensive defaults: `x: x \|\| 0` |
| Edges not showing | Source/target ID mismatch | Verify edge IDs match node IDs exactly |
| Graph renders then disappears | Container resize | Debounce resize handler, use `ResizeObserver` |
| `WebGL context lost` | GPU overload | Reduce node count, use SVG instead of Canvas |

## Verification

After fixing:
1. Rebuild extension (`npm run build`)
2. Launch Extension Development Host (F5)
3. Trigger graph rendering
4. Check Developer Tools console for errors
5. Verify nodes and edges render correctly
6. Test with edge cases: empty graph, single node, large dataset
