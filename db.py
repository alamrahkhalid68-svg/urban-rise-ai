import os
import shutil
import sqlite3

SOURCE_DB = "urbanrise.db"
DISK_DB = "/opt/render/project/src/data/urbanrise.db"
DB_PATH = DISK_DB


def disk_db_has_zero_users() -> bool:
    if not os.path.exists(DISK_DB):
        return False

    conn = sqlite3.connect(DISK_DB)
    try:
        users_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'users'"
        ).fetchone()
        if not users_table:
            return False

        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        return user_count == 0
    finally:
        conn.close()


def bootstrap_disk_db():
    # One-time bootstrap for production disk seeding: copy the packaged/local
    # SQLite database onto the Render persistent disk only when the disk file
    # does not exist yet, so existing persistent data is never overwritten.
    if os.path.exists(DISK_DB) and os.path.getsize(DISK_DB) == 0:
        os.remove(DISK_DB)
        print(f"Deleted empty disk database: {DISK_DB}")
    elif disk_db_has_zero_users():
        os.remove(DISK_DB)
        print(f"Deleted invalid disk database with zero users: {DISK_DB}")

    if not os.path.exists(DISK_DB) and os.path.exists(SOURCE_DB):
        os.makedirs(os.path.dirname(DISK_DB), exist_ok=True)
        shutil.copy2(SOURCE_DB, DISK_DB)
        print(f"Bootstrapped disk database from source: {SOURCE_DB} -> {DISK_DB}")
    else:
        print(f"Bootstrap skipped for disk database: {DISK_DB}")


bootstrap_disk_db()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
