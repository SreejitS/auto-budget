"""Check for recent RiyadBank messages in iMessage DB."""
import sqlite3, os
from datetime import datetime

db_path = os.path.expanduser("~/Library/Messages/chat.db")
conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

# Get latest RiyadBank messages regardless of ROWID
rows = conn.execute("""
    SELECT m.ROWID, m.date,
           COALESCE(m.text, '') as text
    FROM message m
    JOIN handle h ON m.handle_id = h.ROWID
    WHERE h.id = 'RiyadBank'
    ORDER BY m.ROWID DESC
    LIMIT 10
""").fetchall()

# Apple Cocoa timestamp: nanoseconds since 2001-01-01
APPLE_EPOCH = 978307200

print(f"Latest 10 RiyadBank messages:")
print(f"{'ROWID':>8}  {'Date':>20}  Text (first 80 chars)")
print("-" * 120)
for rowid, date_ns, text in rows:
    ts = (date_ns / 1_000_000_000) + APPLE_EPOCH
    dt = datetime.fromtimestamp(ts)
    text_preview = (text or "[no text]").replace('\n', ' ')[:80]
    print(f"{rowid:>8}  {dt.strftime('%Y-%m-%d %H:%M:%S'):>20}  {text_preview}")

print(f"\nMax ROWID from RiyadBank: {rows[0][0] if rows else 'none'}")
print(f"Current sync watermark:   46593")
conn.close()
