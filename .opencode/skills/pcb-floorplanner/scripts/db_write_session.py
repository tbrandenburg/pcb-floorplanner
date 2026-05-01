"""
db_write_session.py — Step 0
Create a new design_session + DRAFT design_version.
Usage: python db_write_session.py --prompt "..." --model "gpt-4o"
Prints: session_id, version_id
"""
import argparse, json, sys
from pathlib import Path
# resolve db/ by walking up to repo root (db/db_init.py)
_here = Path(__file__).resolve()
_db_dir = next(p / "db" for p in _here.parents if (p / "db" / "db_init.py").exists())
sys.path.insert(0, str(_db_dir))
from db_init import connect, DEFAULT_DB


def write_session(prompt: str, model: str, db_path=DEFAULT_DB) -> dict:
    conn = connect(db_path)
    sid = conn.execute(
        "INSERT INTO design_sessions(prompt, model) VALUES (?,?)", (prompt, model)
    ).lastrowid
    vid = conn.execute(
        "INSERT INTO design_versions(session_id) VALUES (?)", (sid,)
    ).lastrowid
    conn.commit()
    conn.close()
    return {"session_id": sid, "version_id": vid}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--model", default="claude-sonnet-4-5")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    print(json.dumps(write_session(args.prompt, args.model, args.db)))
