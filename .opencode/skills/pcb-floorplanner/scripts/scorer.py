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
                  ct.min_dist_mm, ct.max_dist_mm, ct.weight, ct.hard,
                  COALESCE(ct.edge, '')
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


def _is_corner_adjacent(px0, py0, px1, py1, W, H, tol=2.0):
    """Return True when the component bounding box touches TWO board edges simultaneously.

    A component that sits at a board corner (e.g. a USB-C connector at the top-left
    corner) unavoidably overlaps the mount-hole clearance keep-out that protects the
    screw hole at that corner.  Such overlap is intentional and should not be penalised.

    A component that only touches ONE edge (e.g. a 44 mm GPIO header along the bottom
    edge) can always be slid along that edge to avoid a corner keep-out, so it is NOT
    exempt and should be penalised when it overlaps a corner keep-out.

    tol: tolerance in mm — the component body must be within this distance of the edge
    to count as "touching" it.
    """
    touches_left   = px0 <= tol
    touches_right  = px1 >= W - tol
    touches_top    = py0 <= tol
    touches_bottom = py1 >= H - tol
    edges_touched = sum([touches_left, touches_right, touches_top, touches_bottom])
    return edges_touched >= 2


def keep_out_penalty(placements, keep_outs, fixed_ids=None, board=None):
    """Penalise components whose body overlaps any keep-out zone.

    FIXED components are exempt from mount-clearance keep-outs ONLY when they are
    corner-adjacent — i.e. their body touches two board edges simultaneously.  A
    connector at a corner (e.g. USB-C at top-left) physically cannot avoid the
    corner screw clearance zone.  A connector that only spans one edge (e.g. a
    44 mm GPIO header on the bottom edge) can be slid away from the corner and
    therefore receives a full penalty when it overlaps a corner mount-clearance zone.

    keep_outs entries: (x, y, w, h) or (x, y, w, h, is_mount_clearance).
    The 5th element is optional for backwards compatibility (defaults to False).

    board: optional (W, H) tuple required for corner-adjacency test.  When omitted
    the old blanket-exemption behaviour is preserved for backwards compatibility.
    """
    W, H = (board[0], board[1]) if board else (None, None)
    penalty = 0.0
    for comp_id, p in placements.items():
        is_fixed = fixed_ids and comp_id in fixed_ids
        px0, py0 = p["x"], p["y"]
        px1, py1 = px0 + p["w"], py0 + p["h"]
        for ko in keep_outs:
            kx, ky, kw, kh = ko[:4]
            is_mount_clearance = bool(ko[4]) if len(ko) > 4 else False
            if is_fixed and is_mount_clearance:
                if W is None:
                    # No board dims — fall back to blanket exemption (backwards compat)
                    continue
                if _is_corner_adjacent(px0, py0, px1, py1, W, H):
                    continue  # truly cornered connector: exemption is legitimate
                # Single-edge FIXED component — apply penalty so SA slides it away
            kx1, ky1 = kx + kw, ky + kh
            if px0 < kx1 and px1 > kx and py0 < ky1 and py1 > ky:
                ovx = min(px1, kx1) - max(px0, kx)
                ovy = min(py1, ky1) - max(py0, ky)
                penalty += 500.0 * ovx * ovy  # high weight — hard constraint
    return penalty


def score(placements, constraints, nets, keep_outs=None, board=None, fixed_ids=None):
    """
    board: optional (width_mm, height_mm) tuple — required for FIXED edge penalty.
    fixed_ids: optional set of component IDs that are FIXED (exempt from keep-out penalty).
    Coordinate system: y=0 is top edge, y=H is bottom edge (screen coords).

    Constraint tuple format: (id, type, a_id, b_id, min_d, max_d, weight, hard[, edge])
    The 9th element (edge) is optional for backwards compatibility; when present and
    non-empty it pins FIXED components to that specific edge rather than the nearest one.
    """
    constraint_penalty = 0.0
    violations = []

    W, H = (board[0], board[1]) if board else (None, None)

    for con in constraints:
        con_id, ctype, a_id, b_id, min_d, max_d, weight, hard = con[:8]
        edge = con[8] if len(con) > 8 else ""
        if a_id not in placements:
            continue

        if ctype == "FIXED":
            # Penalise distance from the target edge.
            # If edge is specified (top/bottom/left/right) use that exact edge.
            # Otherwise fall back to nearest-edge behaviour for backwards compatibility.
            if W is None or H is None:
                penalty = 0.0
            else:
                pa = placements[a_id]
                ax, ay = centroid(pa)
                if edge == "top":
                    dist_from_edge = ay  # y=0 is top
                elif edge == "bottom":
                    dist_from_edge = H - ay  # y=H is bottom
                elif edge == "left":
                    dist_from_edge = ax
                elif edge == "right":
                    dist_from_edge = W - ax
                else:
                    dist_from_edge = min(ax, W - ax, ay, H - ay)
                penalty = weight * dist_from_edge
                threshold = 5.0
                if dist_from_edge > threshold:
                    delta = dist_from_edge - threshold
                    violations.append((con_id, dist_from_edge, delta, bool(hard)))
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
        ko_pen = keep_out_penalty(placements, keep_outs, fixed_ids=fixed_ids, board=board)
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
