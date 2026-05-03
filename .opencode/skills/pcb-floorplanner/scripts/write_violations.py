"""
write_violations.py — Step 8
Compute final violations from best placement, write violations + placement_score.

Usage: python write_violations.py --run_id 1
Prints: {"violations": N, "hard_violations": N, "overlap_violations": N, "final_penalty": F}
"""

import argparse, json, sys
from pathlib import Path

_here = Path(__file__).resolve()
_db_dir = next(p / "db" for p in _here.parents if (p / "db" / "db_init.py").exists())
sys.path.insert(0, str(_db_dir))
from db_init import connect, DEFAULT_DB
from scorer import load_run, score
from placer_greedy import _is_corner_adjacent


def write_violations(run_id, db_path=DEFAULT_DB):
    conn = connect(db_path)
    placements, constraints, nets = load_run(conn, run_id)

    board = conn.execute(
        """SELECT b.width_mm, b.height_mm FROM board_outline b
           JOIN optimization_runs r ON r.version_id=b.version_id
           WHERE r.id=?""",
        (run_id,),
    ).fetchone()
    board_dims = (board[0], board[1]) if board else None

    keep_outs = conn.execute(
        """SELECT k.x_mm, k.y_mm, k.width_mm, k.height_mm, k.is_mount_clearance
           FROM keep_out_zones k JOIN optimization_runs r ON r.version_id=k.version_id
           WHERE r.id=?""",
        (run_id,),
    ).fetchall()

    # FIXED components (edge connectors) are exempt only from mount-clearance keep-outs —
    # they may legally overlap corner screw zones. They must NOT overlap RF/antenna keep-outs.
    fixed_ids = set(
        row[0]
        for row in conn.execute(
            "SELECT component_id FROM placements WHERE run_id=? AND status='FIXED'",
            (run_id,),
        ).fetchall()
    )

    result = score(
        placements,
        constraints,
        nets,
        keep_outs=keep_outs or None,
        board=board_dims,
        fixed_ids=fixed_ids,
    )

    # build constraint metadata for hard flag lookup
    con_meta = {
        row[0]: row[1]  # id → hard
        for row in conn.execute(
            """SELECT ct.id, ct.hard FROM constraints ct
               JOIN optimization_runs r ON r.version_id=ct.version_id
               WHERE r.id=?""",
            (run_id,),
        ).fetchall()
    }

    hard_count = 0
    for con_id, actual_dist, delta, _hard_flag in result["violations"]:
        is_hard = con_meta.get(con_id, 0)
        if is_hard and delta < 0:
            hard_count += 1
        conn.execute(
            "INSERT INTO violations(run_id, constraint_id, actual_dist_mm, delta_mm) VALUES (?,?,?,?)",
            (run_id, con_id, round(actual_dist, 4), round(delta, 4)),
        )

    # Detect and persist physical body overlaps — these are always hard errors
    # regardless of constraints.  Two components sharing space is never valid.
    ids = list(placements.keys())
    overlap_pairs = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            pa = placements[ids[i]]
            pb = placements[ids[j]]
            ax0 = pa["x"] - pa["cyd"]
            ax1 = pa["x"] + pa["w"] + pa["cyd"]
            ay0 = pa["y"] - pa["cyd"]
            ay1 = pa["y"] + pa["h"] + pa["cyd"]
            bx0 = pb["x"] - pb["cyd"]
            bx1 = pb["x"] + pb["w"] + pb["cyd"]
            by0 = pb["y"] - pb["cyd"]
            by1 = pb["y"] + pb["h"] + pb["cyd"]
            if ax0 < bx1 and ax1 > bx0 and ay0 < by1 and ay1 > by0:
                ox = min(ax1, bx1) - max(ax0, bx0)
                oy = min(ay1, by1) - max(ay0, by0)
                area = round(ox * oy, 4)
                name_a = pa.get("name", str(ids[i]))
                name_b = pb.get("name", str(ids[j]))
                overlap_pairs.append((name_a, name_b, area))
                conn.execute(
                    "INSERT INTO overlap_violations(run_id, comp_a, comp_b, overlap_area_mm2) VALUES (?,?,?,?)",
                    (run_id, name_a, name_b, area),
                )

    # Detect and persist keep-out zone violations — non-FIXED component body
    # overlaps a restricted zone.  FIXED components (edge connectors) are
    # intentionally allowed in corner/edge keep-outs and are exempt.
    fixed_names = set(
        row[0]
        for row in conn.execute(
            """SELECT c.name FROM placements p JOIN components c ON c.id=p.component_id
               WHERE p.run_id=? AND p.status='FIXED'""",
            (run_id,),
        ).fetchall()
    )

    keep_out_rows = conn.execute(
        """SELECT k.x_mm, k.y_mm, k.width_mm, k.height_mm, k.reason, k.is_mount_clearance
           FROM keep_out_zones k JOIN optimization_runs r ON r.version_id=k.version_id
           WHERE r.id=?""",
        (run_id,),
    ).fetchall()

    ko_violations = []
    for p in placements.values():
        is_fixed = p["name"] in fixed_names
        px0, py0 = p["x"], p["y"]
        px1, py1 = px0 + p["w"], py0 + p["h"]
        for kx, ky, kw, kh, kreason, is_mount_clearance in keep_out_rows:
            # FIXED edge connectors may legally overlap mount-hole clearance zones
            # only when they are corner-adjacent (touching two board edges at once).
            # A single-edge FIXED component that merely drifts into a corner keep-out
            # is a real violation and must be reported.
            if is_fixed and is_mount_clearance and board_dims is not None:
                if _is_corner_adjacent(px0, py0, p["w"], p["h"], board_dims[0], board_dims[1]):
                    continue
            elif is_fixed and is_mount_clearance and board_dims is None:
                # Legacy: no board dims available — preserve old blanket-exempt behaviour
                continue
            kx1, ky1 = kx + kw, ky + kh
            if px0 < kx1 and px1 > kx and py0 < ky1 and py1 > ky:
                ovx = min(px1, kx1) - max(px0, kx)
                ovy = min(py1, ky1) - max(py0, ky)
                area = round(ovx * ovy, 4)
                ko_violations.append((p["name"], kreason, area))
                conn.execute(
                    "INSERT INTO keep_out_violations"
                    "(run_id, component_name, keep_out_reason, overlap_area_mm2)"
                    " VALUES (?,?,?,?)",
                    (run_id, p["name"], kreason, area),
                )

    conn.execute(
        """INSERT INTO placement_score
           (run_id, final_penalty, violation_count, hard_violation_count,
            overlap_violation_count, keep_out_violation_count, net_length_total)
           VALUES (?,?,?,?,?,?,?)""",
        (
            run_id,
            round(result["total_penalty"], 4),
            len(result["violations"]),
            hard_count,
            len(overlap_pairs),
            len(ko_violations),
            round(result["net_length_est"], 4),
        ),
    )
    conn.commit()
    conn.close()

    return {
        "run_id": run_id,
        "violations": len(result["violations"]),
        "hard_violations": hard_count,
        "overlap_violations": len(overlap_pairs),
        "keep_out_violations": len(ko_violations),
        "final_penalty": round(result["total_penalty"], 2),
        "net_length_mm": round(result["net_length_est"], 2),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run_id", type=int, required=True)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    print(json.dumps(write_violations(args.run_id, args.db)))
