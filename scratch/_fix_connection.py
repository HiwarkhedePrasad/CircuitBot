path = r"C:\Users\phiwa\Desktop\CircuitBot\static\app.js"
with open(path, "r") as f:
    c = f.read()

old = """        socket.on('connect', () => {
            addLogEntry('Connected to agent backend.', 'system');
            if (!chatHydrated && window.circuitbotChatSessionId) {
                socket.emit('chat:resume', { session_id: window.circuitbotChatSessionId });
            }
        });
        socket.on('disconnect', () => {
            addLogEntry('Disconnected from agent backend.', 'system');
        });"""

new = """        socket.on('connect', () => {
            addLogEntry('Connected to agent backend.', 'system');
            const statusEl = document.getElementById('connectionStatus');
            if (statusEl) { statusEl.className = 'connection-status connected'; statusEl.title = 'Connected to backend'; }
            if (!chatHydrated && window.circuitbotChatSessionId) {
                socket.emit('chat:resume', { session_id: window.circuitbotChatSessionId });
            }
        });
        socket.on('disconnect', () => {
            addLogEntry('Disconnected from agent backend.', 'system');
            const statusEl = document.getElementById('connectionStatus');
            if (statusEl) { statusEl.className = 'connection-status disconnected'; statusEl.title = 'Disconnected from backend'; }
        });"""

if old in c:
    c = c.replace(old, new)
    with open(path, "w") as f:
        f.write(c)
    print("Patched successfully")
else:
    print("Target not found")
    idx = c.find("socket.on('connect'")
    print(repr(c[idx:idx+300]))
