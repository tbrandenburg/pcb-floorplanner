"""
db_read_violations.py — Step 9
Read violations for a run, joined with constraint reasons.
Usage: python db_read_violations.py --run_id 1
Prints: JSON list of violations with human-readable context for LLM review.

Overlap violations (physical body overlaps) are always listed first and
marked as hard=True — two components sharing space is never acceptable.
"""

import argparse, json, sys
from pathlib import Path

# resolve db/ by walking up to repo root (db/db_init.py)
_here = Path(__file__).resolve()
_db_dir = next(p / "db" for p in _here.parents if (p / "db" / "db_init.py").exists())
sys.path.insert(0, str(_db_dir))
from db_init import connect, DEFAULT_DB


def read_violations(run_id: int, db_path=DEFAULT_DB) -> dict:
    conn = connect(db_path)

    score = conn.execute(
        "SELECT final_penalty, violation_count, hard_violation_count, "
        "COALESCE(overlap_violation_count, 0), net_length_total "
        "FROM placement_score WHERE run_id=?",
        (run_id,),
    ).fetchone()

    # Physical overlaps — always hard, listed first so LLM sees them immediately
    overlaps = conn.execute(
        "SELECT comp_a, comp_b, overlap_area_mm2 FROM overlap_violations "
        "WHERE run_id=? ORDER BY overlap_area_mm2 DESC",
        (run_id,),
    ).fetchall()

    constraint_violations = conn.execute(
        """
        SELECT v.actual_dist_mm, v.delta_mm,
               c.type, c.reason, c.hard, c.weight,
               ca.name AS comp_a, cb.name AS comp_b
        FROM violations v
        JOIN constraints c ON v.constraint_id = c.id
        JOIN components ca ON c.comp_a_id = ca.id
        LEFT JOIN components cb ON c.comp_b_id = cb.id
        WHERE v.run_id=?
        ORDER BY v.delta_mm ASC
        """,
        (run_id,),
    ).fetchall()

    conn.close()

    overlap_list = [
        {
            "type": "OVERLAP",
            "reason": f"{o[0]} and {o[1]} physically overlap ({o[2]:.1f} mm² courtyard area)",
            "hard": True,
            "weight": None,
            "comp_a": o[0],
            "comp_b": o[1],
            "overlap_area_mm2": round(o[2], 2),
        }
        for o in overlaps
    ]

    constraint_list = [
        {
            "type": v[2],
            "reason": v[3],
            "hard": bool(v[4]),
            "weight": v[5],
            "comp_a": v[6],
            "comp_b": v[7],
            "actual_dist_mm": round(v[0], 2),
            "delta_mm": round(v[1], 2),
        }
        for v in constraint_violations
    ]

    return {
        "score": {
            "final_penalty": score[0] if score else None,
            "violation_count": score[1] if score else 0,
            "hard_violation_count": score[2] if score else 0,
            "overlap_violation_count": score[3] if score else 0,
            "net_length_total_mm": score[4] if score else None,
        },
        "violations": overlap_list + constraint_list,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run_id", type=int, required=True)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    print(json.dumps(read_violations(args.run_id, args.db), indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run_id", type=int, required=True)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    print(json.dumps(read_violations(args.run_id, args.db), indent=2))
