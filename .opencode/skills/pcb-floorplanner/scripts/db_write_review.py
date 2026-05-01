"""
db_write_review.py — Step 9
Write LLM review decision to review_notes.

Usage: python db_write_review.py --run_id 1 --action APPROVE --note "..."
       python db_write_review.py --run_id 1 --action RERUN   --note "Optimizer plateaued"
       python db_write_review.py --run_id 1 --action MODIFY  --note "Relax DDR constraint"
Prints: {"review_id": N, "action": "..."}
"""
import argparse, json, sys
from pathlib import Path

_here = Path(__file__).resolve()
_db_dir = next(p / "db" for p in _here.parents if (p / "db" / "db_init.py").exists())
sys.path.insert(0, str(_db_dir))
from db_init import connect, DEFAULT_DB


def write_review(run_id, action, note, db_path=DEFAULT_DB):
    if action not in ("APPROVE", "MODIFY", "RERUN"):
        raise ValueError(f"Invalid action: {action}")
    conn = connect(db_path)
    rid = conn.execute(
        "INSERT INTO review_notes(run_id, note, action) VALUES (?,?,?)",
        (run_id, note, action),
    ).lastrowid
    conn.commit()
    conn.close()
    return {"review_id": rid, "action": action}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run_id", type=int, required=True)
    ap.add_argument("--action", required=True, choices=["APPROVE", "MODIFY", "RERUN"])
    ap.add_argument("--note", required=True)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    print(json.dumps(write_review(args.run_id, args.action, args.note, args.db)))
