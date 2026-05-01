"""
db_write_geometry.py — Step 3
Write component_geometry and pins for all components.

Input JSON schema:
{
  "version_id": 1,
  "geometry": [
    {
      "component_name": "U1",
      "width_mm": 14.0,
      "height_mm": 14.0,
      "courtyard_margin": 0.5,
      "allowed_rotations": "0,90,180,270",
      "pins": [
        {"pin_name": "VDD", "rel_x_mm": 1.0, "rel_y_mm": 1.0}
      ]
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


def write_geometry(data: dict, db_path=DEFAULT_DB) -> dict:
    conn = connect(db_path)
    vid = data["version_id"]

    # build name → id map for this version
    rows = conn.execute("SELECT id, name FROM components WHERE version_id=?", (vid,)).fetchall()
    comp_map = {name: cid for cid, name in rows}

    geom_count = pin_count = 0
    for g in data.get("geometry", []):
        cid = comp_map[g["component_name"]]
        conn.execute(
            "INSERT INTO component_geometry(component_id, width_mm, height_mm, courtyard_margin, allowed_rotations) VALUES (?,?,?,?,?)",
            (
                cid,
                g["width_mm"],
                g["height_mm"],
                g.get("courtyard_margin", 0.5),
                g.get("allowed_rotations", "0,90,180,270"),
            ),
        )
        geom_count += 1
        for p in g.get("pins", []):
            conn.execute(
                "INSERT INTO pins(component_id, pin_name, rel_x_mm, rel_y_mm) VALUES (?,?,?,?)",
                (cid, p["pin_name"], p["rel_x_mm"], p["rel_y_mm"]),
            )
            pin_count += 1

    conn.commit()

    # validate 100% coverage
    missing = conn.execute(
        "SELECT name FROM components WHERE version_id=? AND id NOT IN (SELECT component_id FROM component_geometry)",
        (vid,),
    ).fetchall()
    conn.close()

    if missing:
        raise ValueError(f"Missing geometry for: {[m[0] for m in missing]}")

    return {"geometry_written": geom_count, "pins_written": pin_count}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", help="JSON string (else reads stdin)")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    payload = json.loads(args.data) if args.data else json.load(sys.stdin)
    print(json.dumps(write_geometry(payload, args.db)))
