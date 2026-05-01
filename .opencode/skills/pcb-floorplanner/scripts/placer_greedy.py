"""
placer_greedy.py — Step 6
Initial placement: FIXED first, then NEAR-clustered groups, then free fill.
Creates optimization_runs row, writes placements + occupancy_grid.

Usage: python placer_greedy.py --version_id 1
Prints: {"run_id": N, "placed": N, "fixed": N}
"""
import argparse, json, math, sys
from pathlib import Path

_here = Path(__file__).resolve()
_db_dir = next(p / "db" for p in _here.parents if (p / "db" / "db_init.py").exists())
sys.path.insert(0, str(_db_dir))
from db_init import connect, DEFAULT_DB


# ── data loading ─────────────────────────────────────────────────────────────

def load_design(conn, version_id):
    board = conn.execute(
        "SELECT width_mm, height_mm, grid_resolution FROM board_outline WHERE version_id=?",
        (version_id,)
    ).fetchone()
    if not board:
        raise ValueError("No board_outline for version_id")
    W, H, RES = board

    components = {
        row[0]: {"name": row[1], "w": row[2], "h": row[3], "cyd": row[4]}
        for row in conn.execute(
            """SELECT c.id, c.name, g.width_mm, g.height_mm, g.courtyard_margin
               FROM components c JOIN component_geometry g ON g.component_id=c.id
               WHERE c.version_id=?""", (version_id,)
        ).fetchall()
    }

    fixed_names = {
        row[0] for row in conn.execute(
            """SELECT ca.name FROM constraints ct
               JOIN components ca ON ct.comp_a_id=ca.id
               WHERE ct.version_id=? AND ct.type='FIXED'""", (version_id,)
        ).fetchall()
    }

    near_pairs = conn.execute(
        """SELECT ca.id, cb.id FROM constraints ct
           JOIN components ca ON ct.comp_a_id=ca.id
           JOIN components cb ON ct.comp_b_id=cb.id
           WHERE ct.version_id=? AND ct.type='NEAR'""", (version_id,)
    ).fetchall()

    keep_outs = conn.execute(
        "SELECT x_mm, y_mm, width_mm, height_mm FROM keep_out_zones WHERE version_id=?",
        (version_id,)
    ).fetchall()

    requirements = {}
    for row in conn.execute(
        """SELECT c.name, r.key, r.value FROM requirements r
           JOIN components c ON r.component_id=c.id
           WHERE c.version_id=?""", (version_id,)
    ).fetchall():
        requirements.setdefault(row[0], {})[row[1]] = row[2]

    return W, H, RES, components, fixed_names, near_pairs, keep_outs, requirements


# ── occupancy helpers ─────────────────────────────────────────────────────────

def cells_for(x, y, w, h, cyd, res):
    """Return grid cells (cx, cy) covered by component including courtyard."""
    x0 = max(0, x - cyd)
    y0 = max(0, y - cyd)
    x1 = x + w + cyd
    y1 = y + h + cyd
    cx0, cy0 = int(x0 / res), int(y0 / res)
    cx1, cy1 = math.ceil(x1 / res), math.ceil(y1 / res)
    return [(cx, cy) for cx in range(cx0, cx1) for cy in range(cy0, cy1)]


def fits(x, y, w, h, cyd, W, H, occupied, res):
    if x < 0 or y < 0 or x + w > W or y + h > H:
        return False
    for cell in cells_for(x, y, w, h, cyd, res):
        if cell in occupied:
            return False
    return True


def place_at(comp_id, x, y, w, h, cyd, occupied, res):
    for cell in cells_for(x, y, w, h, cyd, res):
        occupied[cell] = comp_id


def snap(v, res):
    return round(round(v / res) * res, 6)


# ── fixed-position heuristic ──────────────────────────────────────────────────

def fixed_position(name, w, h, W, H, requirements):
    """Place connectors at their required board edge."""
    edge = requirements.get(name, {}).get("edge", "")
    margin = 1.0
    if edge == "top":
        return snap((W - w) / 2, 1.0), margin
    if edge == "bottom":
        return snap((W - w) / 2, 1.0), snap(H - h - margin, 1.0)
    if edge == "right":
        return snap(W - w - margin, 1.0), snap((H - h) / 2, 1.0)
    if edge == "left":
        return margin, snap((H - h) / 2, 1.0)
    # fallback centre
    return snap((W - w) / 2, 1.0), snap((H - h) / 2, 1.0)


# ── main greedy placer ────────────────────────────────────────────────────────

def greedy_place(version_id, db_path=DEFAULT_DB):
    conn = connect(db_path)
    W, H, RES, components, fixed_names, near_pairs, keep_outs, requirements = \
        load_design(conn, version_id)

    # Pre-mark keep-out cells
    occupied = {}
    for (kx, ky, kw, kh) in keep_outs:
        for cx in range(int(kx / RES), math.ceil((kx + kw) / RES)):
            for cy in range(int(ky / RES), math.ceil((ky + kh) / RES)):
                occupied[(cx, cy)] = -1  # -1 = keep-out

    placements = {}  # comp_id → (x, y)

    # --- Pass 1: FIXED connectors (edge-placed) ---
    for comp_id, comp in components.items():
        if comp["name"] not in fixed_names:
            continue
        x, y = fixed_position(comp["name"], comp["w"], comp["h"], W, H, requirements)
        x, y = snap(x, RES), snap(y, RES)
        # nudge until it fits
        for attempt in range(200):
            ox = (attempt % 10) * RES * (1 if attempt % 2 == 0 else -1)
            oy = (attempt // 10) * RES * (1 if (attempt // 10) % 2 == 0 else -1)
            tx, ty = snap(x + ox, RES), snap(y + oy, RES)
            if fits(tx, ty, comp["w"], comp["h"], comp["cyd"], W, H, occupied, RES):
                placements[comp_id] = (tx, ty)
                place_at(comp_id, tx, ty, comp["w"], comp["h"], comp["cyd"], occupied, RES)
                break
        else:
            # force place even if overlap
            placements[comp_id] = (x, y)
            place_at(comp_id, x, y, comp["w"], comp["h"], comp["cyd"], occupied, RES)

    # --- Pass 2: NEAR-clustered free components (place near their anchor) ---
    near_by_anchor = {}
    for (a_id, b_id) in near_pairs:
        if a_id in placements and b_id not in placements:
            near_by_anchor.setdefault(a_id, []).append(b_id)

    def place_near_anchor(comp_id, anchor_id):
        comp = components[comp_id]
        ax, ay = placements[anchor_id]
        for r in range(1, 30):
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    if abs(dx) != r and abs(dy) != r:
                        continue
                    tx = snap(ax + dx * RES, RES)
                    ty = snap(ay + dy * RES, RES)
                    if fits(tx, ty, comp["w"], comp["h"], comp["cyd"], W, H, occupied, RES):
                        placements[comp_id] = (tx, ty)
                        place_at(comp_id, tx, ty, comp["w"], comp["h"], comp["cyd"], occupied, RES)
                        return True
        return False

    for anchor_id, children in near_by_anchor.items():
        for child_id in children:
            if child_id not in placements:
                place_near_anchor(child_id, anchor_id)

    # --- Pass 3: Fill remaining row-by-row ---
    for comp_id, comp in components.items():
        if comp_id in placements:
            continue
        placed = False
        for row_start_y in [snap(y, RES) for y in
                             [i * RES for i in range(2, int(H / RES) - 2)]]:
            for col_x in [snap(x, RES) for x in
                          [i * RES for i in range(2, int(W / RES) - 2)]]:
                if fits(col_x, row_start_y, comp["w"], comp["h"], comp["cyd"],
                        W, H, occupied, RES):
                    placements[comp_id] = (col_x, row_start_y)
                    place_at(comp_id, col_x, row_start_y, comp["w"], comp["h"],
                              comp["cyd"], occupied, RES)
                    placed = True
                    break
            if placed:
                break
        if not placed:
            # last resort: any free cell
            placements[comp_id] = (RES, RES)

    # --- Write to DB ---
    run_id = conn.execute(
        "INSERT INTO optimization_runs(version_id, algorithm, params) VALUES (?,?,?)",
        (version_id, "greedy", json.dumps({"grid_resolution": RES})),
    ).lastrowid

    for comp_id, (x, y) in placements.items():
        status = "FIXED" if components[comp_id]["name"] in fixed_names else "PLACED"
        conn.execute(
            "INSERT INTO placements(run_id, component_id, x_mm, y_mm, rotation, status) VALUES (?,?,?,?,?,?)",
            (run_id, comp_id, x, y, 0, status),
        )

    for (cx, cy), comp_id in occupied.items():
        if comp_id == -1:
            continue
        try:
            conn.execute(
                "INSERT INTO occupancy_grid(run_id, cell_x, cell_y, component_id) VALUES (?,?,?,?)",
                (run_id, cx, cy, comp_id),
            )
        except Exception:
            pass  # duplicate cell from courtyard overlap — keep first occupant

    conn.commit()
    conn.close()

    fixed_count = sum(1 for cid in placements if components[cid]["name"] in fixed_names)
    return {"run_id": run_id, "placed": len(placements), "fixed": fixed_count}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--version_id", type=int, required=True)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    print(json.dumps(greedy_place(args.version_id, args.db)))
