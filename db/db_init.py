"""Create (or verify) the floorplan SQLite database from schema.sql."""

import sqlite3
from pathlib import Path

SCHEMA = Path(__file__).parent / "schema.sql"
DEFAULT_DB = Path(__file__).parent / "floorplan.db"


def connect(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # safe concurrent reads
    return conn


def init(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    conn = connect(db_path)
    conn.executescript(SCHEMA.read_text())
    conn.commit()
    return conn


if __name__ == "__main__":
    conn = init()
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    print(f"DB initialised: {DEFAULT_DB}")
    print(f"{len(tables)} tables:")
    for (t,) in tables:
        print(f"  {t}")
    conn.close()
