import sqlite3

DB_PATH = "/opt/render/project/src/data/urbanrise.db"


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
