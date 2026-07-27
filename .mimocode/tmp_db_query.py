import sqlite3
import json

DB = r'C:\Users\phiwa\.local\share\mimocode\mimocode.db'
conn = sqlite3.connect(DB)
c = conn.cursor()

# Project ID for CircuitBot
proj_prefix = 'f8036d87'

# List tables
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
print("=== TABLES ===")
for r in c.fetchall():
    print(r[0])

# Recent sessions
print("\n=== ALL SESSIONS FOR PROJECT ===")
c.execute("""
    SELECT id, substr(title,1,100) as title, time_created
    FROM session 
    WHERE project_id LIKE ?
    ORDER BY time_created DESC
""", (f'%{proj_prefix}%',))
sessions = c.fetchall()
print(f"Total: {len(sessions)} sessions")
for r in sessions:
    print(f"  {r[0]} | {r[1]} | {r[2]}")

# Message counts
print("\n=== MESSAGE COUNTS ===")
for s in sessions[:20]:
    sid = s[0]
    c.execute("SELECT COUNT(*) FROM message WHERE session_id=?", (sid,))
    cnt = c.fetchone()[0]
    print(f"  {sid}: {cnt} messages")

conn.close()
