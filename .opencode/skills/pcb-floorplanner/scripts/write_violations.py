"""
write_violations.py — Step 8
Compute final violations from best placement, write violations + placement_score.

Usage: python write_violations.py --run_id 1
Prints: {"violations": N, "hard_violations": N, "final_penalty": F}
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
    result = score(placements, constraints, nets)

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
    for con_id, actual_dist, delta in result["violations"]:
        is_hard = con_meta.get(con_id, 0)
        if is_hard and delta < 0:
            hard_count += 1
        conn.execute(
            "INSERT INTO violations(run_id, constraint_id, actual_dist_mm, delta_mm) VALUES (?,?,?,?)",
            (run_id, con_id, round(actual_dist, 4), round(delta, 4)),
        )

    conn.execute(
        "INSERT INTO placement_score(run_id, final_penalty, violation_count, hard_violation_count, net_length_total) VALUES (?,?,?,?,?)",
        (
            run_id,
            round(result["total_penalty"], 4),
            len(result["violations"]),
            hard_count,
            round(result["net_length_est"], 4),
        ),
    )
    conn.commit()
    conn.close()

    return {
        "run_id": run_id,
        "violations": len(result["violations"]),
        "hard_violations": hard_count,
        "final_penalty": round(result["total_penalty"], 2),
        "net_length_mm": round(result["net_length_est"], 2),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run_id", type=int, required=True)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    print(json.dumps(write_violations(args.run_id, args.db)))
