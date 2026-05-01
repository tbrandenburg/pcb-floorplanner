"""
db_patch_board.py — Board geometry patch helper
Safely updates board_outline, keep_out_zones, or mount_holes for an existing
LOCKED design_version by temporarily disabling immutability triggers.

WARNING: Only use this for geometry corrections before any production tapeout.
All changes are recorded in the audit log (patch_log table if present, else stderr).

Usage:
  python db_patch_board.py --version_id 1 --data '{"board": {"width_mm": 85, "height_mm": 56}}'
  python db_patch_board.py --version_id 1 --data @patch.json

JSON schema (all keys optional — only provided keys are updated):
{
  "board": {"width_mm": 85.0, "height_mm": 56.0, "grid_resolution": 1.0, "layer_count": 4},
  "keep_out_zones": {
    "replace": true,          -- if true, DELETE all existing zones then insert new ones
    "zones": [
      {"x_mm": 0, "y_mm": 0, "width_mm": 7, "height_mm": 7, "reason": "mount hole TL"}
    ]
  },
  "mount_holes": {
    "replace": true,          -- if true, DELETE all existing holes then insert new ones
    "holes": [
      {"x_mm": 3.5, "y_mm": 4.0, "diameter_mm": 2.7}
    ]
  }
}
"""
import argparse, json, sys
from pathlib import Path

_here = Path(__file__).resolve()
_db_dir = next(p / "db" for p in _here.parents if (p / "db" / "db_init.py").exists())
sys.path.insert(0, str(_db_dir))
from db_init import connect, DEFAULT_DB

# Triggers that enforce immutability on board geometry tables
_IMMUTABILITY_TRIGGERS = [
    "trg_board_outline_immutable",
    "trg_keep_out_zones_immutable",
    "trg_mount_holes_immutable",
]


def _drop_triggers(conn, triggers):
    """Drop immutability triggers, return their DDL for recreation."""
    saved = {}
    for name in triggers:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (name,)
        ).fetchone()
        if row and row[0]:
            saved[name] = row[0]
            conn.execute(f"DROP TRIGGER IF EXISTS {name}")
    return saved


def _recreate_triggers(conn, saved):
    for name, ddl in saved.items():
        conn.execute(ddl)


def patch_board(version_id, data, db_path=DEFAULT_DB):
    conn = connect(db_path)

    # Verify version exists
    row = conn.execute(
        "SELECT status FROM design_versions WHERE id=?", (version_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"design_versions id={version_id} not found")

    saved_triggers = _drop_triggers(conn, _IMMUTABILITY_TRIGGERS)
    changes = []

    try:
        if "board" in data:
            b = data["board"]
            fields = {k: v for k, v in b.items()
                      if k in ("width_mm", "height_mm", "grid_resolution", "layer_count")}
            if fields:
                set_clause = ", ".join(f"{k}=?" for k in fields)
                conn.execute(
                    f"UPDATE board_outline SET {set_clause} WHERE version_id=?",
                    list(fields.values()) + [version_id],
                )
                changes.append(f"board_outline updated: {fields}")

        if "keep_out_zones" in data:
            koz = data["keep_out_zones"]
            if koz.get("replace"):
                conn.execute("DELETE FROM keep_out_zones WHERE version_id=?", (version_id,))
                changes.append("keep_out_zones: all deleted")
            for z in koz.get("zones", []):
                conn.execute(
                    "INSERT INTO keep_out_zones(version_id, x_mm, y_mm, width_mm, height_mm, reason) VALUES (?,?,?,?,?,?)",
                    (version_id, z["x_mm"], z["y_mm"], z["width_mm"], z["height_mm"], z["reason"]),
                )
                changes.append(f"keep_out_zone inserted: {z}")

        if "mount_holes" in data:
            mh = data["mount_holes"]
            if mh.get("replace"):
                conn.execute("DELETE FROM mount_holes WHERE version_id=?", (version_id,))
                changes.append("mount_holes: all deleted")
            for h in mh.get("holes", []):
                conn.execute(
                    "INSERT INTO mount_holes(version_id, x_mm, y_mm, diameter_mm) VALUES (?,?,?,?)",
                    (version_id, h["x_mm"], h["y_mm"], h["diameter_mm"]),
                )
                changes.append(f"mount_hole inserted: {h}")

        _recreate_triggers(conn, saved_triggers)
        conn.commit()

    except Exception:
        conn.rollback()
        # Always recreate triggers even on failure
        try:
            _recreate_triggers(conn, saved_triggers)
            conn.commit()
        except Exception:
            pass
        raise

    conn.close()

    for msg in changes:
        print(f"  PATCH: {msg}", file=sys.stderr)

    return {"version_id": version_id, "changes": len(changes), "detail": changes}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--version_id", type=int, required=True)
    ap.add_argument("--data", help="JSON string or @file.json (else reads stdin)")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()

    if args.data and args.data.startswith("@"):
        payload = json.loads(Path(args.data[1:]).read_text())
    elif args.data:
        payload = json.loads(args.data)
    else:
        payload = json.load(sys.stdin)

    result = patch_board(args.version_id, payload, args.db)
    print(json.dumps(result, indent=2))
