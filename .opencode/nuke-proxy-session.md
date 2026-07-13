# Nuke Proxy Sessions

Delete all sessions routed through a proxy provider from the opencode SQLite database.

```powershell
sqlite3 "$env:USERPROFILE\.local\share\opencode\opencode.db" "DELETE FROM session WHERE title = 'Proxy: opencode/deepseek-v4-flash-free';"
```

To see what you're about to delete first:

```powershell
sqlite3 "$env:USERPROFILE\.local\share\opencode\opencode.db" "SELECT id, title, time_created FROM session WHERE title LIKE 'Proxy:%' ORDER BY time_created;"
```

**Location:** `%USERPROFILE%\.local\share\opencode\opencode.db`

**Note:** Foreign keys are CASCADE, so deleting a session also removes its inputs, messages, context epochs, and session diffs automatically.
