import os
import sys
import sqlite3
from dotenv import load_dotenv

load_dotenv()
DB_PATH = os.getenv("DB_PATH", "./database/dev_tracker.db")

args = sys.argv[1:]
date_filter = None
name_filter = None
for arg in args:
    if arg.startswith("--name="):
        name_filter = arg[7:]
    else:
        date_filter = arg

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

if date_filter and name_filter:
    cur.execute(
        "SELECT username, date, content, created_at FROM confessions WHERE date = ? AND username LIKE ? ORDER BY created_at ASC",
        (date_filter, f"%{name_filter}%"),
    )
elif date_filter:
    cur.execute(
        "SELECT username, date, content, created_at FROM confessions WHERE date = ? ORDER BY created_at ASC",
        (date_filter,),
    )
elif name_filter:
    cur.execute(
        "SELECT username, date, content, created_at FROM confessions WHERE username LIKE ? ORDER BY created_at DESC",
        (f"%{name_filter}%",),
    )
else:
    cur.execute(
        "SELECT username, date, content, created_at FROM confessions ORDER BY created_at DESC",
    )

rows = cur.fetchall()
conn.close()

if not rows:
    print("고해 내역이 없습니다.")
else:
    for username, date, content, created_at in rows:
        print(f"[{date}] {username} ({created_at[:16]})")
        print(f"  {content}")
        print()
