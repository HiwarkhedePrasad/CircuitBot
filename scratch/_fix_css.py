path = r"C:\Users\phiwa\Desktop\CircuitBot\static\style.css"
with open(path, "r") as f:
    c = f.read()

old = """.pcb-tool-chip.active {
    border-color: rgba(0, 122, 204, 0.9);
    background: rgba(0, 122, 204, 0.18);
    color: var(--text-bright);
}"""

new = """.pcb-tool-chip.active {
    border-color: rgba(0, 122, 204, 0.9);
    background: rgba(0, 122, 204, 0.18);
    color: var(--text-bright);
}
.pcb-tool-chip:active {
    transform: scale(0.96);
}
.pcb-tool-chip:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 1px;
}"""

if old in c:
    c = c.replace(old, new)
    with open(path, "w") as f:
        f.write(c)
    print("Patched successfully")
else:
    print("Target not found")
