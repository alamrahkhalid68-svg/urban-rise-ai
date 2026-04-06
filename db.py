import os
import shutil
import sqlite3

SOURCE_DB = "urbanrise.db"
DISK_DB = "/opt/render/project/src/data/urbanrise.db"
DB_PATH = DISK_DB


def bootstrap_disk_db():
    # One-time bootstrap for production disk seeding: copy the packaged/local
    # SQLite database onto the Render persistent disk only when the disk file
    # does not exist yet, so existing persistent data is never overwritten.
    if not os.path.exists(DISK_DB) and os.path.exists(SOURCE_DB):
        os.makedirs(os.path.dirname(DISK_DB), exist_ok=True)
        shutil.copy2(SOURCE_DB, DISK_DB)


bootstrap_disk_db()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
