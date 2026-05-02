"""
optimizer_annealing.py — Step 7
Simulated annealing over placements. Mutates placements + occupancy_grid in DB.
Logs every iteration to score_history.

Usage: python optimizer_annealing.py --run_id 1 [--iterations 5000] [--seed 42] [--overwrite]
Prints: {"best_penalty": N, "iterations": N, "improvement_pct": N}

Flags:
  --overwrite   Clear score_history, violations, and placement_score for the run_id
                before starting. Required when re-running SA on an existing run_id,
                otherwise the INSERT into score_history hits a UNIQUE constraint.
"""

import argparse, copy, json, math, random, sys
from pathlib import Path

_here = Path(__file__).resolve()
_db_dir = next(p / "db" for p in _here.parents if (p / "db" / "db_init.py").exists())
sys.path.insert(0, str(_db_dir))
from db_init import connect, DEFAULT_DB
from scorer import load_run, score

LOG_EVERY = 100  # write score_history row every N iterations


def load_board(conn, run_id):
    return conn.execute(
        """SELECT b.width_mm, b.height_mm, b.grid_resolution
           FROM board_outline b JOIN optimization_runs r ON r.version_id=b.version_id
           WHERE r.id=?""",
        (run_id,),
    ).fetchone()


def load_keep_outs(conn, run_id):
    """Return list of (x, y, w, h, is_mount_clearance) keep-out rectangles for the run's version."""
    return conn.execute(
        """SELECT k.x_mm, k.y_mm, k.width_mm, k.height_mm, k.is_mount_clearance
           FROM keep_out_zones k JOIN optimization_runs r ON r.version_id=k.version_id
           WHERE r.id=?""",
        (run_id,),
    ).fetchall()


def snap(v, res):
    return round(round(v / res) * res, 6)


def propose_move(placements, fixed_ids, W, H, res, rng):
    """Return (new_placements, move_description). Never moves FIXED components."""
    free_ids = [k for k in placements if k not in fixed_ids]
    if not free_ids:
        return placements, "none"

    move_type = rng.choice(["translate", "translate", "swap"])  # translate 2x more likely

    if move_type == "swap" and len(free_ids) >= 2:
        a, b = rng.sample(free_ids, 2)
        new_p = dict(placements)
        ax, ay = placements[b]["x"], placements[b]["y"]
        bx, by = placements[a]["x"], placements[a]["y"]
        # validate swap positions fit within board
        pa, pb = placements[a], placements[b]
        if ax + pa["w"] > W or ay + pa["h"] > H or bx + pb["w"] > W or by + pb["h"] > H:
            return placements, "none"  # reject invalid swap
        new_p[a] = {**new_p[a], "x": ax, "y": ay}
        new_p[b] = {**new_p[b], "x": bx, "y": by}
        return new_p, f"swap {a}<->{b}"

    comp_id = rng.choice(free_ids)
    comp = placements[comp_id]
    step = rng.choice([-4, -2, -1, 1, 2, 4]) * res
    axis = rng.choice(["x", "y"])
    new_val = snap(comp[axis] + step, res)
    new_val = max(0.0, min(new_val, (W if axis == "x" else H) - comp["w" if axis == "x" else "h"]))
    new_p = dict(placements)
    new_p[comp_id] = {**comp, axis: new_val}
    return new_p, f"translate {comp_id} {axis}+{step:.1f}"


def anneal(run_id, n_iter=5000, seed=42, db_path=DEFAULT_DB, overwrite=False):
    rng = random.Random(seed)
    conn = connect(db_path)

    if overwrite:
        conn.execute("DELETE FROM score_history WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM violations WHERE run_id=?", (run_id,))
        conn.execute("DELETE FROM placement_score WHERE run_id=?", (run_id,))
        conn.commit()

    board = load_board(conn, run_id)
    if not board:
        raise ValueError(f"No board for run_id {run_id}")
    W, H, RES = board

    keep_outs = load_keep_outs(conn, run_id)
    placements, constraints, nets = load_run(conn, run_id)

    # identify FIXED component ids
    fixed_ids = set(
        row[0]
        for row in conn.execute(
            "SELECT component_id FROM placements WHERE run_id=? AND status='FIXED'", (run_id,)
        ).fetchall()
    )

    board_dims = (W, H)
    current_score = score(placements, constraints, nets, keep_outs, board=board_dims, fixed_ids=fixed_ids)
    best_score = current_score["total_penalty"]
    best_placements = copy.deepcopy(placements)

    T_start = best_score * 0.3 if best_score > 0 else 100.0
    T_min = T_start * 0.001
    cooling = math.exp(math.log(T_min / T_start) / n_iter)
    T = T_start

    score_rows = []  # batch inserts for performance

    for i in range(n_iter):
        new_placements, _ = propose_move(placements, fixed_ids, W, H, RES, rng)
        new_s = score(new_placements, constraints, nets, keep_outs, board=board_dims, fixed_ids=fixed_ids)
        delta = new_s["total_penalty"] - current_score["total_penalty"]

        if delta < 0 or rng.random() < math.exp(-delta / max(T, 1e-9)):
            placements = new_placements
            current_score = new_s
            if current_score["total_penalty"] < best_score:
                best_score = current_score["total_penalty"]
                best_placements = copy.deepcopy(placements)

        if i % LOG_EVERY == 0 or i == n_iter - 1:
            score_rows.append(
                (
                    run_id,
                    i,
                    current_score["total_penalty"],
                    current_score["constraint_penalty"],
                    current_score["overlap_penalty"],
                    current_score["net_length_est"],
                )
            )

        T *= cooling

    # persist best placement
    for comp_id, p in best_placements.items():
        conn.execute(
            "UPDATE placements SET x_mm=?, y_mm=? WHERE run_id=? AND component_id=?",
            (p["x"], p["y"], run_id, comp_id),
        )

    # rebuild occupancy grid from best placement
    conn.execute("DELETE FROM occupancy_grid WHERE run_id=?", (run_id,))
    for comp_id, p in best_placements.items():
        cx0 = int((p["x"] - p["cyd"]) / RES)
        cy0 = int((p["y"] - p["cyd"]) / RES)
        cx1 = math.ceil((p["x"] + p["w"] + p["cyd"]) / RES)
        cy1 = math.ceil((p["y"] + p["h"] + p["cyd"]) / RES)
        for cx in range(max(0, cx0), cx1):
            for cy in range(max(0, cy0), cy1):
                try:
                    conn.execute(
                        "INSERT INTO occupancy_grid(run_id, cell_x, cell_y, component_id) VALUES (?,?,?,?)",
                        (run_id, cx, cy, comp_id),
                    )
                except Exception:
                    pass

    # write score history
    conn.executemany(
        "INSERT INTO score_history(run_id, iteration, total_penalty, constraint_penalty, overlap_penalty, net_length_est) VALUES (?,?,?,?,?,?)",
        score_rows,
    )
    conn.commit()
    conn.close()

    initial = score_rows[0][2] if score_rows else 0
    improvement = round((1 - best_score / initial) * 100, 1) if initial > 0 else 0

    return {
        "run_id": run_id,
        "best_penalty": round(best_score, 2),
        "initial_penalty": round(initial, 2),
        "improvement_pct": improvement,
        "iterations": n_iter,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run_id", type=int, required=True)
    ap.add_argument("--iterations", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--overwrite",
        action="store_true",
        help="Clear score_history/violations/placement_score before running (required for reruns)",
    )
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    print(json.dumps(anneal(args.run_id, args.iterations, args.seed, args.db, args.overwrite)))
