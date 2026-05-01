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
        """SELECT k.x_mm, k.y_mm, k.width_mm, k.height_mm
           FROM keep_out_zones k JOIN optimization_runs r ON r.version_id=k.version_id
           WHERE r.id=?""",
        (run_id,),
    ).fetchall()

    result = score(placements, constraints, nets, keep_outs=keep_outs or None, board=board_dims)

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

    conn.execute(
        """INSERT INTO placement_score
           (run_id, final_penalty, violation_count, hard_violation_count,
            overlap_violation_count, net_length_total)
           VALUES (?,?,?,?,?,?)""",
        (
            run_id,
            round(result["total_penalty"], 4),
            len(result["violations"]),
            hard_count,
            len(overlap_pairs),
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
        "final_penalty": round(result["total_penalty"], 2),
        "net_length_mm": round(result["net_length_est"], 2),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run_id", type=int, required=True)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    print(json.dumps(write_violations(args.run_id, args.db)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run_id", type=int, required=True)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    print(json.dumps(write_violations(args.run_id, args.db)))
