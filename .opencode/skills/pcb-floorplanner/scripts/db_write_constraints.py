"""
db_write_constraints.py — Step 4
Write placement constraints derived from electrical requirements.

Input JSON schema:
{
  "version_id": 1,
  "constraints": [
    {
      "type": "NEAR",
      "comp_a": "U1",
      "comp_b": "C1",
      "max_dist_mm": 2.0,
      "weight": 2.0,
      "hard": 0,
      "reason": "Decoupling cap for U1 VDD_CORE"
    },
    {
      "type": "FIXED",
      "comp_a": "J1",
      "comp_b": null,
      "min_dist_mm": null,
      "max_dist_mm": null,
      "weight": 1.0,
      "hard": 1,
      "reason": "USB connector must be at board edge"
    }
  ]
}
"""

import argparse, json, sys
from pathlib import Path

# resolve db/ by walking up to repo root (db/db_init.py)
_here = Path(__file__).resolve()
_db_dir = next(p / "db" for p in _here.parents if (p / "db" / "db_init.py").exists())
sys.path.insert(0, str(_db_dir))
from db_init import connect, DEFAULT_DB


def write_constraints(data: dict, db_path=DEFAULT_DB) -> dict:
    conn = connect(db_path)
    vid = data["version_id"]

    rows = conn.execute("SELECT id, name FROM components WHERE version_id=?", (vid,)).fetchall()
    comp_map = {name: cid for cid, name in rows}

    count = 0
    for c in data.get("constraints", []):
        comp_b_id = comp_map[c["comp_b"]] if c.get("comp_b") else None
        conn.execute(
            "INSERT INTO constraints(version_id, type, comp_a_id, comp_b_id, min_dist_mm, max_dist_mm, weight, hard, reason) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                vid,
                c["type"],
                comp_map[c["comp_a"]],
                comp_b_id,
                c.get("min_dist_mm"),
                c.get("max_dist_mm"),
                c.get("weight", 1.0),
                c.get("hard", 0),
                c["reason"],
            ),
        )
        count += 1

    conn.commit()
    conn.close()
    return {"constraints_written": count}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", help="JSON string (else reads stdin)")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    payload = json.loads(args.data) if args.data else json.load(sys.stdin)
    print(json.dumps(write_constraints(payload, args.db)))
