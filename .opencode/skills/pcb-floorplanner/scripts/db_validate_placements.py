"""
db_validate_placements.py — Pre-render placement validation gate.

Checks every placement for:
  1. Mount hole overlap  (component rect vs circular drill)
  2. Keep-out violation  (component rect vs rectangular zone, FIXED exempt from mount clearances)
  3. Component overlap   (any two component bodies intersect)

Exits 0 when clean, exits 1 and prints violations when any are found.
Can also be imported and called programmatically via validate().

Usage:
    python db_validate_placements.py --run_id 2
    python db_validate_placements.py --run_id 2 --db path/to/floorplan.db
"""

import argparse, json, math, sys
from pathlib import Path

_here = Path(__file__).resolve()
_db_dir = next(p / "db" for p in _here.parents if (p / "db" / "db_init.py").exists())
sys.path.insert(0, str(_db_dir))
from db_init import connect, DEFAULT_DB


def _load(conn, run_id):
    placements = conn.execute(
        """SELECT c.name, c.type, p.x_mm, p.y_mm, g.width_mm, g.height_mm, p.status
           FROM placements p
           JOIN components c ON c.id = p.component_id
           JOIN component_geometry g ON g.component_id = p.component_id
           WHERE p.run_id = ?""",
        (run_id,),
    ).fetchall()

    mount_holes = conn.execute(
        """SELECT m.x_mm, m.y_mm, m.diameter_mm
           FROM mount_holes m
           JOIN optimization_runs r ON r.version_id = m.version_id
           WHERE r.id = ?""",
        (run_id,),
    ).fetchall()

    keep_outs = conn.execute(
        """SELECT k.x_mm, k.y_mm, k.width_mm, k.height_mm, k.reason, k.is_mount_clearance
           FROM keep_out_zones k
           JOIN optimization_runs r ON r.version_id = k.version_id
           WHERE r.id = ?""",
        (run_id,),
    ).fetchall()

    board = conn.execute(
        """SELECT b.width_mm, b.height_mm
           FROM board_outline b
           JOIN optimization_runs r ON r.version_id = b.version_id
           WHERE r.id = ?""",
        (run_id,),
    ).fetchone()

    return placements, mount_holes, keep_outs, board


def _is_corner_adjacent(x, y, w, h, W, H, tol=2.0):
    """True when the component body touches two board edges simultaneously.

    Only a corner-adjacent FIXED component may legitimately overlap a mount-clearance
    keep-out.  A single-edge component (touching exactly one edge) can always be slid
    along that edge to clear the corner zone, so it is not exempt.
    """
    px1, py1 = x + w, y + h
    touches = sum([
        x   <= tol,
        px1 >= W - tol,
        y   <= tol,
        py1 >= H - tol,
    ])
    return touches >= 2


def _rect_circle_overlap(rx, ry, rw, rh, cx, cy, r):
    """True if rectangle [rx,ry,rx+rw,ry+rh] overlaps circle (cx,cy,r)."""
    nearest_x = max(rx, min(cx, rx + rw))
    nearest_y = max(ry, min(cy, ry + rh))
    return math.hypot(nearest_x - cx, nearest_y - cy) < r


def _rect_rect_overlap(ax, ay, aw, ah, bx, by, bw, bh):
    """True if two rectangles intersect (touching edges are not overlap)."""
    return ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by


def validate(run_id, db_path=DEFAULT_DB):
    """Return dict with 'ok' bool and 'violations' list of human-readable strings.

    db_path may be a file path (str/Path) or an already-open sqlite3.Connection.
    When a Connection is passed it is not closed after use.
    """
    import sqlite3 as _sqlite3

    if isinstance(db_path, _sqlite3.Connection):
        conn = db_path
        _close = False
    else:
        conn = connect(db_path)
        _close = True
    placements, mount_holes, keep_outs, board = _load(conn, run_id)
    if _close:
        conn.close()

    BW = board[0] if board else None
    BH = board[1] if board else None

    violations = []

    # 1. Mount hole overlaps (circle geometry).
    #    FIXED components are NOT blanket-exempt: only corner-adjacent FIXED
    #    connectors are allowed to share space with a mount hole.
    for name, ctype, x, y, w, h, status in placements:
        is_fixed = status == "FIXED"
        if is_fixed and BW is not None and _is_corner_adjacent(x, y, w, h, BW, BH):
            continue  # cornered connector — mount hole proximity is unavoidable
        for hx, hy, hd in mount_holes:
            if _rect_circle_overlap(x, y, w, h, hx, hy, hd / 2):
                violations.append(
                    f"MOUNT_HOLE: {name} rect=[{x},{y}->{x + w:.1f},{y + h:.1f}] "
                    f"overlaps hole at ({hx},{hy}) r={hd / 2:.2f}mm"
                )

    # 2. Keep-out zone overlaps (rectangular).
    #    FIXED components are exempt from mount-clearance keep-outs ONLY when
    #    corner-adjacent (touching two board edges).  Single-edge FIXED components
    #    are penalised so the SA optimiser slides them away from corner zones.
    for name, ctype, x, y, w, h, status in placements:
        is_fixed = status == "FIXED"
        corner_adj = is_fixed and BW is not None and _is_corner_adjacent(x, y, w, h, BW, BH)
        for kx, ky, kw, kh, reason, is_mount_clearance in keep_outs:
            if corner_adj and is_mount_clearance:
                continue  # cornered connector legitimately overlaps mount-clearance zone
            if _rect_rect_overlap(x, y, w, h, kx, ky, kw, kh):
                ovx = min(x + w, kx + kw) - max(x, kx)
                ovy = min(y + h, ky + kh) - max(y, ky)
                violations.append(f"KEEP_OUT: {name} overlaps zone '{reason}' overlap={ovx:.2f}x{ovy:.2f}mm")

    # 3. Component-to-component body overlaps
    comps = list(placements)
    for i in range(len(comps)):
        na, _, ax, ay, aw, ah, _ = comps[i]
        for j in range(i + 1, len(comps)):
            nb, _, bx, by, bw, bh, _ = comps[j]
            if _rect_rect_overlap(ax, ay, aw, ah, bx, by, bw, bh):
                ovx = min(ax + aw, bx + bw) - max(ax, bx)
                ovy = min(ay + ah, by + bh) - max(ay, by)
                violations.append(f"OVERLAP: {na} and {nb} overlap by {ovx:.2f}x{ovy:.2f}mm")

    return {"ok": len(violations) == 0, "violations": violations}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run_id", type=int, required=True)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()

    result = validate(args.run_id, args.db)
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["ok"] else 1)
