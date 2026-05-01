"""
db_lock_version.py — Step 5
Validate design completeness, compute hash, lock the design version.
Usage: python db_lock_version.py --version_id 1
Prints: {"status": "LOCKED", "hash": "...", "components": N, "constraints": N}
Exits with code 1 if validation fails.
"""

import argparse, hashlib, json, sys
from pathlib import Path

# resolve db/ by walking up to repo root (db/db_init.py)
_here = Path(__file__).resolve()
_db_dir = next(p / "db" for p in _here.parents if (p / "db" / "db_init.py").exists())
sys.path.insert(0, str(_db_dir))
from db_init import connect, DEFAULT_DB


def lock_version(version_id: int, db_path=DEFAULT_DB) -> dict:
    conn = connect(db_path)

    # 1. check current status
    row = conn.execute("SELECT status FROM design_versions WHERE id=?", (version_id,)).fetchone()
    if not row:
        raise ValueError(f"version_id {version_id} not found")
    if row[0] == "LOCKED":
        raise ValueError(f"version_id {version_id} is already LOCKED")

    # 2. assert all components have geometry
    missing = conn.execute(
        "SELECT name FROM components WHERE version_id=? AND id NOT IN (SELECT component_id FROM component_geometry)",
        (version_id,),
    ).fetchall()
    if missing:
        raise ValueError(f"Missing geometry for: {[m[0] for m in missing]}")

    # 3. assert at least one constraint exists
    n_constraints = conn.execute("SELECT COUNT(*) FROM constraints WHERE version_id=?", (version_id,)).fetchone()[0]
    if n_constraints == 0:
        raise ValueError("No constraints defined — run Step 4 first")

    # 4. compute hash of components + geometry + constraints
    comp_rows = conn.execute(
        "SELECT id,name,type,package FROM components WHERE version_id=? ORDER BY id",
        (version_id,),
    ).fetchall()
    geom_rows = conn.execute(
        "SELECT component_id,width_mm,height_mm,courtyard_margin FROM component_geometry "
        "WHERE component_id IN (SELECT id FROM components WHERE version_id=?) ORDER BY component_id",
        (version_id,),
    ).fetchall()
    con_rows = conn.execute(
        "SELECT type,comp_a_id,comp_b_id,min_dist_mm,max_dist_mm,weight,hard FROM constraints "
        "WHERE version_id=? ORDER BY id",
        (version_id,),
    ).fetchall()

    digest = hashlib.sha256(json.dumps([comp_rows, geom_rows, con_rows], default=str).encode()).hexdigest()

    # 5. lock
    conn.execute(
        "UPDATE design_versions SET status='LOCKED', hash=? WHERE id=?",
        (digest, version_id),
    )
    conn.commit()
    conn.close()

    return {
        "status": "LOCKED",
        "hash": digest[:12] + "...",
        "components": len(comp_rows),
        "constraints": n_constraints,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--version_id", type=int, required=True)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    try:
        print(json.dumps(lock_version(args.version_id, args.db)))
    except ValueError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)
