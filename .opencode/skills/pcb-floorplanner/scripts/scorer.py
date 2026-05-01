"""
scorer.py — shared penalty computation (Steps 7 + 8)
Computes: constraint_penalty, overlap_penalty, net_length_est, total_penalty.
Importable as a module or run standalone for a quick score check.

Usage: python scorer.py --run_id 1
"""

import argparse, json, math, sys
from pathlib import Path

_here = Path(__file__).resolve()
_db_dir = next(p / "db" for p in _here.parents if (p / "db" / "db_init.py").exists())
sys.path.insert(0, str(_db_dir))
from db_init import connect, DEFAULT_DB


def load_run(conn, run_id):
    """Return placements dict, constraints list, net_connections list."""
    placements = {
        row[0]: {"x": row[1], "y": row[2], "w": row[3], "h": row[4], "cyd": row[5], "name": row[6]}
        for row in conn.execute(
            """SELECT p.component_id, p.x_mm, p.y_mm,
                      g.width_mm, g.height_mm, g.courtyard_margin, c.name
               FROM placements p
               JOIN component_geometry g ON g.component_id=p.component_id
               JOIN components c ON c.id=p.component_id
               WHERE p.run_id=?""",
            (run_id,),
        ).fetchall()
    }

    constraints = conn.execute(
        """SELECT ct.id, ct.type, ct.comp_a_id, ct.comp_b_id,
                  ct.min_dist_mm, ct.max_dist_mm, ct.weight, ct.hard
           FROM constraints ct
           JOIN optimization_runs r ON r.version_id=ct.version_id
           WHERE r.id=?""",
        (run_id,),
    ).fetchall()

    nets = conn.execute(
        """SELECT nc.net_id, nc.component_id
           FROM net_connections nc
           JOIN components c ON nc.component_id=c.id
           JOIN optimization_runs r ON r.version_id=c.version_id
           WHERE r.id=?""",
        (run_id,),
    ).fetchall()

    return placements, constraints, nets


def centroid(p):
    return p["x"] + p["w"] / 2, p["y"] + p["h"] / 2


def dist(p1, p2):
    ax, ay = centroid(p1)
    bx, by = centroid(p2)
    return math.hypot(ax - bx, ay - by)


def keep_out_penalty(placements, keep_outs):
    """Penalise components whose body overlaps any keep-out zone."""
    penalty = 0.0
    for p in placements.values():
        px0, py0 = p["x"], p["y"]
        px1, py1 = px0 + p["w"], py0 + p["h"]
        for kx, ky, kw, kh in keep_outs:
            kx1, ky1 = kx + kw, ky + kh
            if px0 < kx1 and px1 > kx and py0 < ky1 and py1 > ky:
                ovx = min(px1, kx1) - max(px0, kx)
                ovy = min(py1, ky1) - max(py0, ky)
                penalty += 500.0 * ovx * ovy  # high weight — hard constraint
    return penalty


def score(placements, constraints, nets, keep_outs=None, board=None):
    """
    board: optional (width_mm, height_mm) tuple — required for FIXED edge penalty.
    Coordinate system: y=0 is top edge, y=H is bottom edge (screen coords).
    """
    constraint_penalty = 0.0
    violations = []

    W, H = (board[0], board[1]) if board else (None, None)

    for con in constraints:
        con_id, ctype, a_id, b_id, min_d, max_d, weight, hard = con
        if a_id not in placements:
            continue

        if ctype == "FIXED":
            # Penalise distance from nearest board edge.
            # Without board dimensions we cannot compute this — skip gracefully.
            if W is None or H is None:
                penalty = 0.0
            else:
                pa = placements[a_id]
                ax, ay = centroid(pa)
                dist_from_edge = min(ax, W - ax, ay, H - ay)
                # dist_from_edge == 0 means centroid is exactly on a board edge.
                # Penalise proportional to how far inside the board the component sits.
                penalty = weight * dist_from_edge
                threshold = 5.0
                if dist_from_edge > threshold:
                    delta = dist_from_edge - threshold
                    violations.append((con_id, dist_from_edge, delta, bool(hard)))
                    # hard=1 FIXED violations get a large additional penalty so SA
                    # is strongly incentivised to drive them to the edge.
                    if hard:
                        penalty += 500.0 * delta
        elif ctype in ("NEAR", "FAR", "ALIGN"):
            if b_id is None or b_id not in placements:
                continue
            pa, pb = placements[a_id], placements[b_id]
            d = dist(pa, pb)

            if ctype == "NEAR" and max_d is not None and d > max_d:
                delta = d - max_d
                penalty = weight * delta
                violations.append((con_id, d, d - max_d, bool(hard)))
            elif ctype == "FAR" and min_d is not None and d < min_d:
                delta = min_d - d
                penalty = weight * delta
                violations.append((con_id, d, d - min_d, bool(hard)))
            elif ctype == "ALIGN" and b_id in placements:
                # penalise non-alignment (centroid Y diff for horizontal align)
                ax, ay = centroid(pa)
                bx, by = centroid(pb)
                delta = abs(ay - by)
                penalty = weight * delta * 0.1
            else:
                penalty = 0.0
        else:
            penalty = 0.0

        constraint_penalty += penalty

    # overlap penalty: check all pairs
    ids = list(placements.keys())
    overlap_penalty = 0.0
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            pa, pb = placements[ids[i]], placements[ids[j]]
            # axis-aligned bounding box overlap with courtyard
            ax0 = pa["x"] - pa["cyd"]
            ax1 = pa["x"] + pa["w"] + pa["cyd"]
            ay0 = pa["y"] - pa["cyd"]
            ay1 = pa["y"] + pa["h"] + pa["cyd"]
            bx0 = pb["x"] - pb["cyd"]
            bx1 = pb["x"] + pb["w"] + pb["cyd"]
            by0 = pb["y"] - pb["cyd"]
            by1 = pb["y"] + pb["h"] + pb["cyd"]
            if ax0 < bx1 and ax1 > bx0 and ay0 < by1 and ay1 > by0:
                overlap_x = min(ax1, bx1) - max(ax0, bx0)
                overlap_y = min(ay1, by1) - max(ay0, by0)
                overlap_penalty += 100.0 * overlap_x * overlap_y

    # net length estimate: half-perimeter bounding box (HPWL) per net
    net_groups: dict = {}
    for net_id, comp_id in nets:
        if comp_id in placements:
            net_groups.setdefault(net_id, []).append(placements[comp_id])

    net_length_est = 0.0
    for comps in net_groups.values():
        if len(comps) < 2:
            continue
        xs = [centroid(p)[0] for p in comps]
        ys = [centroid(p)[1] for p in comps]
        net_length_est += (max(xs) - min(xs)) + (max(ys) - min(ys))

    total = constraint_penalty + overlap_penalty + net_length_est
    if keep_outs:
        ko_pen = keep_out_penalty(placements, keep_outs)
        total += ko_pen
    else:
        ko_pen = 0.0
    return {
        "total_penalty": total,
        "constraint_penalty": constraint_penalty,
        "overlap_penalty": overlap_penalty,
        "net_length_est": net_length_est,
        "keep_out_penalty": ko_pen,
        "violations": violations,  # list of (constraint_id, actual_dist, delta)
    }


def score_run(run_id, db_path=DEFAULT_DB):
    conn = connect(db_path)
    placements, constraints, nets = load_run(conn, run_id)
    conn.close()
    return score(placements, constraints, nets)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run_id", type=int, required=True)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    result = score_run(args.run_id, args.db)
    result.pop("violations")  # raw violations not useful on CLI
    print(json.dumps(result, indent=2))
