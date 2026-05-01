"""
db_write_board.py — Step 2
Write board_outline, keep_out_zones, mount_holes. Validates after writing.

Input JSON schema:
{
  "version_id": 1,
  "board": {"width_mm": 85.0, "height_mm": 56.0, "grid_resolution": 1.0, "layer_count": 4},
  "keep_out_zones": [
    {"x_mm": 0, "y_mm": 0, "width_mm": 5, "height_mm": 5, "reason": "mounting corner"}
  ],
  "mount_holes": [
    {"x_mm": 3.5, "y_mm": 3.5, "diameter_mm": 2.7}
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


def write_board(data: dict, db_path=DEFAULT_DB) -> dict:
    conn = connect(db_path)
    vid = data["version_id"]
    b = data["board"]

    conn.execute(
        "INSERT INTO board_outline(version_id, width_mm, height_mm, grid_resolution, layer_count) VALUES (?,?,?,?,?)",
        (vid, b["width_mm"], b["height_mm"], b.get("grid_resolution", 1.0), b.get("layer_count", 2)),
    )

    keep_out_zones = data.get("keep_out_zones", [])
    for z in keep_out_zones:
        # validate zone fits within board
        if z["x_mm"] + z["width_mm"] > b["width_mm"] or z["y_mm"] + z["height_mm"] > b["height_mm"]:
            raise ValueError(f"Keep-out zone '{z['reason']}' exceeds board boundary")
        conn.execute(
            "INSERT INTO keep_out_zones(version_id, x_mm, y_mm, width_mm, height_mm, reason) VALUES (?,?,?,?,?,?)",
            (vid, z["x_mm"], z["y_mm"], z["width_mm"], z["height_mm"], z["reason"]),
        )

    for h in data.get("mount_holes", []):
        # validate hole (with annular ring clearance) does not overlap any keep-out zone
        annular = h["diameter_mm"] / 2 + 0.5  # drill radius + 0.5mm copper ring
        hx0 = h["x_mm"] - annular
        hx1 = h["x_mm"] + annular
        hy0 = h["y_mm"] - annular
        hy1 = h["y_mm"] + annular
        for z in keep_out_zones:
            # mount hole keep-outs are intentionally centred on holes — skip those
            if "mount hole" in z.get("reason", ""):
                continue
            if hx0 < z["x_mm"] + z["width_mm"] and hx1 > z["x_mm"] \
                    and hy0 < z["y_mm"] + z["height_mm"] and hy1 > z["y_mm"]:
                raise ValueError(
                    f"Mount hole at ({h['x_mm']},{h['y_mm']}) annular ring overlaps "
                    f"keep-out '{z['reason']}'"
                )
        # validate hole is within board minus annular clearance
        if hx0 < 0 or hx1 > b["width_mm"] or hy0 < 0 or hy1 > b["height_mm"]:
            raise ValueError(
                f"Mount hole at ({h['x_mm']},{h['y_mm']}) annular ring exceeds board boundary"
            )
        conn.execute(
            "INSERT INTO mount_holes(version_id, x_mm, y_mm, diameter_mm) VALUES (?,?,?,?)",
            (vid, h["x_mm"], h["y_mm"], h["diameter_mm"]),
        )

    conn.commit()
    conn.close()
    return {
        "board": f"{b['width_mm']}x{b['height_mm']}mm",
        "keep_out_zones": len(data.get("keep_out_zones", [])),
        "mount_holes": len(data.get("mount_holes", [])),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", help="JSON string (else reads stdin)")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    payload = json.loads(args.data) if args.data else json.load(sys.stdin)
    print(json.dumps(write_board(payload, args.db)))
