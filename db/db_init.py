"""Create (or verify) the floorplan SQLite database from schema.sql."""

import argparse
import sqlite3
import sys
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
    ap = argparse.ArgumentParser(description="Initialise the floorplan SQLite database from schema.sql")
    ap.add_argument("--force", action="store_true", help="Remove existing DB without prompting (for automation)")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="Path to the database file")
    args = ap.parse_args()

    db_path = Path(args.db)

    if db_path.exists():
        if args.force:
            db_path.unlink()
        else:
            answer = input(f"{db_path} already exists. Remove and re-initialise? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("Aborted.")
                sys.exit(0)
            db_path.unlink()

    conn = init(db_path)
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    print(f"DB initialised: {db_path}")
    print(f"{len(tables)} tables:")
    for (t,) in tables:
        print(f"  {t}")
    conn.close()
