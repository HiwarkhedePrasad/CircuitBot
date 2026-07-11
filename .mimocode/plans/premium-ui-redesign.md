# CircuitBot EDA Premium UI Redesign Plan

## Overview
Transform CircuitBot's frontend from generic VS Code-style dark theme to a distinctive, premium engineering-grade UI inspired by the landing page's copper/green palette, layered depth, and textured surfaces. All changes are pure CSS + minimal JS — no new libraries.

**Files to modify:**
- `static/style.css` — bulk of changes (color system, surfaces, chat, animations)
- `static/index.html` — font imports, avatar markup, empty state markup, noise SVG overlay
- `static/app.js` — message entrance animation logic, avatar rendering in `addConversationMessage`, `handleConversationEvent`, `hydrateChatState`

---

## A. Color System Overhaul

### A1. New CSS Custom Properties (`:root` in style.css)

Replace the current VS Code blue palette with a copper/green palette that matches the landing page. Keep dark base but add warmth and depth.

```css
:root {
    /* ── Core Palette — Copper & Forest ── */
    --bg-main: #0a0e0c;
    --bg-panel: #0d1210;
    --bg-header: #0b0f0d;
    --bg-canvas: #050807;

    --border: rgba(232, 240, 220, 0.08);
    --border-bright: rgba(232, 240, 220, 0.14);
    --border-glow: rgba(217, 132, 67, 0.25);

    --text-main: #c6d1c6;
    --text-dim: #6b7d6e;
    --text-bright: #e8f0dc;

    --accent: #d98443;        /* copper — primary accent */
    --accent-hover: #f0ae69;  /* copper-soft */
    --accent-dim: rgba(217, 132, 67, 0.12);
    --accent-glow: rgba(217, 132, 67, 0.25);

    --teal: #9ccbb3;          /* trace — secondary accent */
    --teal-dim: rgba(156, 203, 179, 0.12);
    --teal-glow: rgba(156, 203, 179, 0.25);

    --mask: #2f755d;          /* forest green — tertiary */
    --silk: #e8f0dc;

    --danger: #f0745d;
    --danger-hover: #ff8a72;
    --success: #9ccbb3;
    --success-hover: #b5dcc9;

    --kicad-red: #E34E32;
    --kicad-teal: #00A8A8;
    --kicad-green: #00A800;

    /* ── Fonts ── */
    --font-display: 'Chakra Petch', 'Inter', sans-serif;
    --font-ui: 'Space Grotesk', 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    --font-mono: 'Space Mono', 'JetBrains Mono', 'Fira Code', monospace;

    /* ── Surfaces — layered depth ── */
    --surface-1: #0d1210;   /* deepest — base panels */
    --surface-2: #111916;   /* mid — sidebar backgrounds */
    --surface-3: #161e1a;   /* elevated — cards, chat bg */
    --surface-4: #1c2622;   /* highest — hover states */

    /* Agent workspace — warmer tones */
    --agent-bg: #0c1110;
    --agent-surface: #121a17;
    --agent-border: rgba(232, 240, 220, 0.08);
    --agent-text: #d4ddd5;
    --agent-text-dim: #6b7d6e;
    --agent-accent: #d98443;
    --agent-green: #9ccbb3;
    --agent-red: #f0745d;
    --agent-amber: #d29922;

    /* Spacing & radius */
    --space-xs: 4px;
    --space-sm: 8px;
    --space-md: 12px;
    --space-lg: 16px;
    --space-xl: 24px;

    --radius-sm: 4px;
    --radius-md: 8px;
    --radius-lg: 12px;

    --sidebar-width: 300px;
    --header-height: 80px;
    --status-height: 25px;
}
```

**Rationale:** The copper accent is distinctive and engineering-appropriate (solder, copper traces). Forest green ties to PCB substrate. The layered surface tokens (`surface-1` through `surface-4`) create natural depth without manual brightness tweaks.

---

## B. Surface Treatment

### B1. Noise Texture Overlay (style.css)

Add a subtle film grain texture to the entire app using a pseudo-element on body. This is the same technique the landing page uses.

```css
body::after {
    content: '';
    position: fixed;
    inset: 0;
    z-index: 9999;
    pointer-events: none;
    opacity: 0.035;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='200' height='200' filter='url(%23n)' opacity='1'/%3E%3C/svg%3E");
}
```

**Rationale:** Very low opacity (0.035) so it's barely perceptible but adds tactile quality. SVG filter means zero network requests.

### B2. Layered Panel Depth (style.css)

Each panel should have a slightly different background brightness to create depth:

```css
/* Left sidebar — slightly lighter */
.eda-sidebar.left-panel {
    background-color: var(--surface-2);
    border-right: 1px solid var(--border);
}

/* Right sidebar (chat) — warmest */
.eda-sidebar.right-panel {
    background-color: var(--surface-1);
    border-left: 1px solid var(--border);
}

/* Canvas — darkest */
.canvas-viewport {
    background-color: var(--bg-canvas);
}

/* Status bar */
.eda-status-bar {
    background-color: var(--surface-1);
    border-top: 1px solid var(--border);
}
```

### B3. Gradient Borders on Active Elements (style.css)

Replace flat borders with gradient or glow borders for key interactive elements:

```css
/* Active tab gets a copper bottom border with glow */
.tab.active {
    color: var(--accent);
    border-bottom: 2px solid var(--accent);
    box-shadow: 0 2px 8px var(--accent-glow);
}

/* Panel headers — subtle gradient background */
.panel-header {
    background: linear-gradient(180deg, rgba(217, 132, 67, 0.04), transparent);
    border-bottom: 1px solid var(--border);
}
```

### B4. Header Gradient (style.css)

```css
.eda-header {
    background: linear-gradient(180deg, var(--surface-3), var(--surface-1));
    border-bottom: 1px solid var(--border);
}
```

---

## C. Chat / Conversation UI

### C1. Bot Avatar for Agent Messages (index.html + style.css)

Add a small circuit-chip SVG avatar next to agent messages. Modify the HTML empty state and add CSS for avatar rendering.

**index.html changes:**
- Add an inline SVG icon definition at the top of `<body>`:
```html
<svg style="display:none">
    <symbol id="icon-circuitbot" viewBox="0 0 24 24">
        <rect x="4" y="4" width="16" height="16" rx="2" fill="none" stroke="currentColor" stroke-width="1.5"/>
        <circle cx="12" cy="12" r="3" fill="currentColor" opacity="0.6"/>
        <line x1="8" y1="2" x2="8" y2="4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        <line x1="12" y1="2" x2="12" y2="4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        <line x1="16" y1="2" x2="16" y2="4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        <line x1="8" y1="22" x2="8" y2="20" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        <line x1="12" y1="22" x2="12" y2="20" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        <line x1="16" y1="22" x2="16" y2="20" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        <line x1="2" y1="8" x2="4" y2="8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        <line x1="2" y1="12" x2="4" y2="12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        <line x1="2" y1="16" x2="4" y2="16" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        <line x1="22" y1="8" x2="20" y2="8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        <line x1="22" y1="12" x2="20" y2="12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
        <line x1="22" y1="16" x2="20" y2="16" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
    </symbol>
</svg>
```

**style.css — Avatar styles:**
```css
.conv-msg-row {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    animation: msgEnter 0.25s ease-out both;
}
.conv-msg-row.user {
    flex-direction: row-reverse;
}

.conv-avatar {
    width: 28px;
    height: 28px;
    border-radius: var(--radius-sm);
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
}
.conv-avatar.bot {
    background: linear-gradient(135deg, rgba(217, 132, 67, 0.15), rgba(156, 203, 179, 0.1));
    border: 1px solid rgba(217, 132, 67, 0.25);
    color: var(--accent);
}
.conv-avatar.bot svg {
    width: 16px;
    height: 16px;
}
.conv-avatar.user {
    background: linear-gradient(135deg, rgba(156, 203, 179, 0.12), rgba(47, 117, 93, 0.12));
    border: 1px solid rgba(156, 203, 179, 0.2);
    color: var(--teal);
    font-size: 12px;
    font-weight: 600;
    font-family: var(--font-mono);
}
```

### C2. Agent Message Card Treatment (style.css)

Agent messages get a subtle card background with left copper accent border:

```css
.conv-agent-msg {
    font-size: 13px;
    line-height: 1.6;
    color: var(--agent-text);
    font-family: var(--font-ui);
    padding: 10px 14px;
    background: rgba(217, 132, 67, 0.03);
    border-left: 2px solid rgba(217, 132, 67, 0.2);
    border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
    max-width: 92%;
    word-wrap: break-word;
    overflow-wrap: break-word;
    white-space: pre-wrap;
}
```

### C3. User Message Styling (style.css)

```css
.conv-user-msg {
    font-size: 12px;
    line-height: 1.6;
    color: var(--agent-text);
    font-family: var(--font-ui);
    background: linear-gradient(135deg, rgba(156, 203, 179, 0.08), rgba(47, 117, 93, 0.06));
    border: 1px solid rgba(156, 203, 179, 0.12);
    border-radius: var(--radius-md) var(--radius-md) 2px var(--radius-md);
    padding: 10px 14px;
    margin-left: auto;
    max-width: 82%;
    word-wrap: break-word;
    overflow-wrap: break-word;
    white-space: pre-wrap;
}
```

### C4. Message Entrance Animation (style.css + app.js)

```css
@keyframes msgEnter {
    from {
        opacity: 0;
        transform: translateY(8px);
    }
    to {
        opacity: 1;
        transform: translateY(0);
    }
}

.conv-msg-row {
    animation: msgEnter 0.25s ease-out both;
}
```

**app.js changes:**
Modify `addConversationMessage` and `handleConversationEvent` to wrap messages in `.conv-msg-row` divs and add avatar elements. The key change is in the message creation functions:

In `addConversationMessage` (around line 864), wrap the entry in a row:
```js
function addConversationMessage(type, text) {
    if (type !== 'log' && type !== 'system') {
        const empty = agentConversation.querySelector('.conv-empty');
        if (empty) empty.remove();
    }

    const row = document.createElement('div');
    row.className = 'conv-msg-row';

    // Add avatar for agent messages
    if (type === 'assistant' || type === 'milestone') {
        const avatar = document.createElement('div');
        avatar.className = 'conv-avatar bot';
        avatar.innerHTML = '<svg><use href="#icon-circuitbot"/></svg>';
        row.appendChild(avatar);
    }

    const entry = document.createElement('div');
    // ... existing className logic stays the same ...
    // but entry is now inside row

    const ts = document.createElement('span');
    ts.className = 'conv-timestamp';
    ts.textContent = _timeStamp();
    entry.appendChild(ts);
    row.appendChild(entry);
    agentConversation.appendChild(row);
    trimConversationDom();
    agentConversation.scrollTop = agentConversation.scrollHeight;
}
```

Same pattern for `handleConversationEvent` — wrap `.conv-agent-msg` in a `.conv-msg-row` with bot avatar, and for user messages in `agentBtn` click handler — add user avatar.

### C5. Improved Markdown Rendering (app.js)

Upgrade `_renderMarkdown` to handle fenced code blocks and better inline code:

```js
function _renderMarkdown(text) {
    let html = text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');

    // Fenced code blocks: ```lang\ncode\n```
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g,
        '<pre class="conv-code-block"><code>$2</code></pre>');

    // Bold: **text**
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    // Italic: *text*
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
    // Inline code: `text`
    html = html.replace(/`(.+?)`/g,
        '<code class="conv-inline-code">$1</code>');
    // Bullet lines
    html = html.replace(/^[\-\*] (.+)$/gm,
        '<div class="conv-bullet">• $1</div>');
    // Line breaks
    html = html.replace(/\n/g, '<br>');
    return html;
}
```

Add CSS for code blocks:
```css
.conv-code-block {
    background: rgba(5, 8, 7, 0.6);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 10px 12px;
    margin: 8px 0;
    overflow-x: auto;
    font-family: var(--font-mono);
    font-size: 11px;
    line-height: 1.5;
    color: var(--teal);
}
.conv-inline-code {
    background: rgba(5, 8, 7, 0.5);
    padding: 1px 5px;
    border-radius: 3px;
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--accent-hover);
    border: 1px solid var(--border);
}
.conv-bullet {
    padding-left: 14px;
    line-height: 1.6;
}
```

### C6. Proposal Cards (style.css)

```css
.proposal-card {
    background: linear-gradient(135deg, rgba(217, 132, 67, 0.04), rgba(156, 203, 179, 0.03));
    border: 1px solid rgba(217, 132, 67, 0.15);
    border-radius: var(--radius-md);
    padding: 12px 14px;
    margin: 8px 0;
    max-width: 92%;
    font-size: 12px;
    font-family: var(--font-ui);
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2), inset 0 1px 0 rgba(255, 255, 255, 0.03);
    animation: msgEnter 0.25s ease-out both;
}

.proposal-header {
    font-family: var(--font-display);
    font-weight: 600;
    color: var(--accent);
    margin-bottom: 6px;
    font-size: 12px;
    letter-spacing: 0.3px;
    text-transform: uppercase;
}
```

### C7. Empty State (index.html + style.css)

Replace the plain text empty state with a more atmospheric design:

**index.html changes:**
```html
<div class="conv-empty" id="convEmpty">
    <div class="empty-icon">
        <svg width="48" height="48" viewBox="0 0 48 48" fill="none">
            <rect x="8" y="8" width="32" height="32" rx="4" stroke="var(--accent)" stroke-width="1.5" opacity="0.4"/>
            <circle cx="24" cy="24" r="6" stroke="var(--teal)" stroke-width="1.5" opacity="0.5"/>
            <circle cx="24" cy="24" r="2" fill="var(--accent)" opacity="0.6"/>
            <!-- pin lines -->
            <line x1="16" y1="4" x2="16" y2="8" stroke="var(--accent)" stroke-width="1.5" opacity="0.3"/>
            <line x1="24" y1="4" x2="24" y2="8" stroke="var(--accent)" stroke-width="1.5" opacity="0.3"/>
            <line x1="32" y1="4" x2="32" y2="8" stroke="var(--accent)" stroke-width="1.5" opacity="0.3"/>
            <line x1="16" y1="44" x2="16" y2="40" stroke="var(--accent)" stroke-width="1.5" opacity="0.3"/>
            <line x1="24" y1="44" x2="24" y2="40" stroke="var(--accent)" stroke-width="1.5" opacity="0.3"/>
            <line x1="32" y1="44" x2="32" y2="40" stroke="var(--accent)" stroke-width="1.5" opacity="0.3"/>
            <line x1="4" y1="16" x2="8" y2="16" stroke="var(--accent)" stroke-width="1.5" opacity="0.3"/>
            <line x1="4" y1="24" x2="8" y2="24" stroke="var(--accent)" stroke-width="1.5" opacity="0.3"/>
            <line x1="4" y1="32" x2="8" y2="32" stroke="var(--accent)" stroke-width="1.5" opacity="0.3"/>
            <line x1="44" y1="16" x2="40" y2="16" stroke="var(--accent)" stroke-width="1.5" opacity="0.3"/>
            <line x1="44" y1="24" x2="40" y2="24" stroke="var(--accent)" stroke-width="1.5" opacity="0.3"/>
            <line x1="44" y1="32" x2="40" y2="32" stroke="var(--accent)" stroke-width="1.5" opacity="0.3"/>
        </svg>
    </div>
    <div class="empty-title">Describe a circuit</div>
    <div class="empty-subtitle">I'll design the schematic and PCB layout for you.</div>
    <div class="suggestion-chips" id="suggestionChips">
        <button class="suggestion-chip" data-prompt="Design a 3.3V USB power supply with LED indicator">3.3V USB power supply</button>
        <button class="suggestion-chip" data-prompt="ESP32 with button and status LED">ESP32 with button & LED</button>
        <button class="suggestion-chip" data-prompt="Temperature sensor with ADC and display">Temperature sensor</button>
        <button class="suggestion-chip" data-prompt="Battery charger with status LED">Battery charger</button>
        <button class="suggestion-chip" data-prompt="Motor driver circuit with ESP32">Motor driver</button>
    </div>
</div>
```

**style.css:**
```css
.conv-empty {
    text-align: center;
    padding: 32px 20px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
}
.empty-icon {
    margin-bottom: 12px;
    opacity: 0.8;
}
.empty-icon svg {
    filter: drop-shadow(0 0 12px rgba(217, 132, 67, 0.2));
}
.empty-title {
    font-family: var(--font-display);
    font-size: 16px;
    font-weight: 600;
    color: var(--text-bright);
    letter-spacing: 0.02em;
}
.empty-subtitle {
    font-size: 12px;
    color: var(--text-dim);
    margin-bottom: 8px;
}
```

### C8. Suggestion Chips (style.css)

```css
.suggestion-chip {
    background: rgba(217, 132, 67, 0.06);
    border: 1px solid rgba(217, 132, 67, 0.15);
    border-radius: 20px;
    padding: 6px 14px;
    font-size: 11px;
    font-family: var(--font-ui);
    color: var(--accent-hover);
    cursor: pointer;
    transition: all 0.2s ease;
    white-space: nowrap;
}
.suggestion-chip:hover {
    background: rgba(217, 132, 67, 0.12);
    border-color: rgba(217, 132, 67, 0.3);
    transform: translateY(-1px);
    box-shadow: 0 2px 8px rgba(217, 132, 67, 0.15);
}
```

---

## D. Input Area

### D1. Enhanced Input Styling (style.css)

```css
.agent-input-group {
    display: flex;
    gap: 8px;
    padding: 12px 14px;
    border-top: 1px solid var(--border);
    align-items: flex-end;
    flex-shrink: 0;
    background: linear-gradient(180deg, transparent, rgba(217, 132, 67, 0.02));
}

.agent-input-group textarea {
    flex: 1;
    resize: none;
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    color: var(--agent-text);
    padding: 10px 14px;
    font-size: 13px;
    font-family: var(--font-ui);
    line-height: 1.5;
    outline: none;
    transition: border-color 0.2s, box-shadow 0.2s;
    min-height: 42px;
}
.agent-input-group textarea:focus {
    border-color: rgba(217, 132, 67, 0.4);
    box-shadow: 0 0 0 2px rgba(217, 132, 67, 0.08), 0 0 16px rgba(217, 132, 67, 0.06);
}
.agent-input-group textarea::placeholder {
    color: var(--text-dim);
    opacity: 0.6;
}

.btn-send {
    background: linear-gradient(135deg, var(--accent), #c47a3a);
    color: #0a0e0c;
    border: none;
    border-radius: var(--radius-md);
    padding: 10px 18px;
    font-size: 13px;
    font-weight: 700;
    font-family: var(--font-display);
    cursor: pointer;
    transition: all 0.2s ease;
    flex-shrink: 0;
    height: 42px;
    display: flex;
    align-items: center;
    gap: 6px;
    letter-spacing: 0.3px;
    box-shadow: 0 2px 8px rgba(217, 132, 67, 0.2);
}
.btn-send:hover:not(:disabled) {
    background: linear-gradient(135deg, var(--accent-hover), var(--accent));
    box-shadow: 0 4px 16px rgba(217, 132, 67, 0.3);
    transform: translateY(-1px);
}
.btn-send:active:not(:disabled) {
    transform: translateY(0) scale(0.98);
}
.btn-send:disabled {
    opacity: 0.3;
    cursor: not-allowed;
}
.btn-send.running {
    background: transparent;
    color: var(--accent);
    border: 1px solid rgba(217, 132, 67, 0.3);
    box-shadow: none;
}
```

---

## E. Header / Navigation

### E1. Logo and Brand (style.css)

```css
.brand {
    display: flex;
    align-items: center;
    padding: 8px 14px;
    gap: 10px;
    border-bottom: 1px solid var(--border);
    background: linear-gradient(180deg, rgba(217, 132, 67, 0.03), transparent);
}

.logo {
    font-size: 20px;
    color: var(--accent);
    filter: drop-shadow(0 0 6px rgba(217, 132, 67, 0.3));
}

.title {
    font-family: var(--font-display);
    font-weight: 700;
    font-size: 14px;
    color: var(--text-bright);
    letter-spacing: 0.08em;
    text-transform: uppercase;
}

.version {
    font-size: 9px;
    color: var(--text-dim);
    font-weight: 400;
    font-family: var(--font-mono);
    letter-spacing: 0.05em;
}
```

### E2. Toolbar Buttons (style.css)

```css
.tool-btn {
    background: transparent;
    border: 1px solid transparent;
    color: var(--text-main);
    padding: 5px 12px;
    font-size: 12px;
    font-family: var(--font-ui);
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 6px;
    border-radius: var(--radius-sm);
    transition: all 0.15s ease;
}
.tool-btn:hover:not(:disabled) {
    background: rgba(217, 132, 67, 0.08);
    border-color: rgba(217, 132, 67, 0.15);
    color: var(--text-bright);
}
.tool-btn.highlight {
    background: rgba(217, 132, 67, 0.1);
    border-color: rgba(217, 132, 67, 0.2);
    color: var(--accent-hover);
}
.tool-btn.highlight:hover:not(:disabled) {
    background: rgba(217, 132, 67, 0.18);
    border-color: rgba(217, 132, 67, 0.35);
    box-shadow: 0 0 12px rgba(217, 132, 67, 0.1);
}
```

### E3. Panel Title Typography (style.css)

```css
.panel-title {
    font-family: var(--font-display);
    font-weight: 600;
    text-transform: uppercase;
    font-size: 10px;
    color: var(--text-dim);
    letter-spacing: 0.1em;
    flex-shrink: 0;
}
```

---

## F. Animations and Transitions

### F1. Message Entrance (style.css) — defined in C4 above

### F2. Thinking Bar Premium Animation (style.css)

```css
.agent-thinking-bar {
    height: 2px;
    background: transparent;
    flex-shrink: 0;
    transition: background 0.3s;
    position: relative;
    overflow: hidden;
}
.agent-thinking-bar.active {
    background: linear-gradient(90deg,
        transparent,
        var(--accent),
        var(--teal),
        transparent
    );
    background-size: 200% 100%;
    animation: think-bar-sweep 2s ease-in-out infinite;
}
@keyframes think-bar-sweep {
    0% { background-position: -200% 0; }
    100% { background-position: 200% 0; }
}
```

### F3. Button Hover Glow (style.css)

Already covered in D1 for btn-send. Add to tool-btn and other interactive elements:

```css
.tool-btn:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
    box-shadow: 0 0 12px var(--accent-glow);
}
```

### F4. Typing Dots Premium (style.css)

```css
.typing-dots span {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--accent);
    animation: dotPulse 1.4s ease-in-out infinite;
    box-shadow: 0 0 4px rgba(217, 132, 67, 0.3);
}
```

### F5. Smooth Scroll (style.css)

```css
.agent-conversation {
    scroll-behavior: smooth;
    scrollbar-width: thin;
    scrollbar-color: rgba(217, 132, 67, 0.15) transparent;
}
.agent-conversation::-webkit-scrollbar {
    width: 6px;
}
.agent-conversation::-webkit-scrollbar-track {
    background: transparent;
}
.agent-conversation::-webkit-scrollbar-thumb {
    background: rgba(217, 132, 67, 0.15);
    border-radius: 3px;
}
.agent-conversation::-webkit-scrollbar-thumb:hover {
    background: rgba(217, 132, 67, 0.3);
}
```

---

## G. Typography Refinement

### G1. Font Import (index.html)

Replace the Google Fonts link:

```html
<link href="https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@500;600;700&family=Space+Grotesk:wght@400;500;600;700&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
```

### G2. Typography Rules (style.css)

```css
body {
    font-family: var(--font-ui);
    background-color: var(--bg-main);
    color: var(--text-main);
    font-size: 13px;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
    height: 100vh;
    overflow: hidden;
}

/* Display font for headings and labels */
.panel-title,
.proposal-header,
.empty-title,
.title {
    font-family: var(--font-display);
}

/* Monospace for code, timestamps, badges */
.conv-timestamp,
.badge,
.conv-code-block,
.conv-inline-code,
.result-id,
.comp-name {
    font-family: var(--font-mono);
}
```

### G3. Body Background Gradient (style.css)

Add the layered radial gradient background similar to the landing page:

```css
body {
    background:
        radial-gradient(circle at 85% 10%, rgba(217, 132, 67, 0.04), transparent 30rem),
        radial-gradient(circle at 10% 80%, rgba(47, 117, 93, 0.05), transparent 28rem),
        var(--bg-main);
}
```

---

## H. Implementation Order

1. **Color system** — Update `:root` variables in style.css (foundation for everything else)
2. **Fonts** — Update Google Fonts link in index.html, add font-family rules
3. **Surface treatment** — noise texture, layered panel backgrounds, gradients
4. **Header** — brand typography, toolbar button styles, header gradient
5. **Chat UI** — avatar system, message wrapping, agent message cards, user message style
6. **Empty state** — new markup in index.html, CSS for icon + title
7. **Input area** — textarea focus glow, gradient send button
8. **Proposal cards** — gradient borders, glow treatment
9. **Animations** — message entrance, thinking bar sweep, typing dots, smooth scroll
10. **Markdown** — upgraded rendering in app.js, code block CSS
11. **Polish** — scrollbar styling, toast updates, badge styles

## I. Testing Checklist

- [ ] Noise texture visible but subtle (opacity ~0.035)
- [ ] Copper accent (#d98443) used consistently for interactive elements
- [ ] Panel backgrounds have visible but subtle depth difference
- [ ] Messages animate in with fade+slide
- [ ] Bot avatar appears next to agent messages
- [ ] Code blocks in chat render with monospace font and dark background
- [ ] Proposal cards have gradient border and glow
- [ ] Empty state shows circuit icon + gradient title
- [ ] Input textarea has copper glow on focus
- [ ] Send button has gradient background and hover lift
- [ ] Thinking bar shows animated copper-to-teal sweep
- [ ] All transitions are 200-300ms ease
- [ ] No performance regressions (no heavy backdrop-filter on many elements)
- [ ] Existing functionality preserved (no broken selectors, no missing event handlers)
