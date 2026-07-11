path = r"C:\Users\phiwa\Desktop\CircuitBot\static\style.css"
with open(path, "r") as f:
    c = f.read()

# Remove properties-view block
old = """.properties-view {
    padding: 10px;
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--text-main);
    overflow-y: auto;
}

.prop-group {
    display: flex;
    flex-direction: column;
    gap: 8px;
}

.prop-row {
    display: flex;
    justify-content: space-between;
    border-bottom: 1px solid rgba(255,255,255,0.05);
    padding-bottom: 4px;
}

.prop-key {
    color: var(--text-dim);
    font-weight: bold;
    flex-shrink: 0;
    margin-right: 12px;
}

.prop-val {
    color: var(--kicad-teal);
    text-align: right;
    word-break: break-all;
}

.empty-state {
    color: var(--text-dim);
    text-align: center;
    margin-top: 20px;
    font-style: italic;
}"""

if old in c:
    c = c.replace(old, "")
    with open(path, "w") as f:
        f.write(c)
    print("Removed dead CSS")
else:
    print("Target not found")
