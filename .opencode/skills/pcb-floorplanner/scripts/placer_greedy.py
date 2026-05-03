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
        "SELECT width_mm, height_mm, grid_resolution FROM board_outline WHERE version_id=?", (version_id,)
    ).fetchone()
    if not board:
        raise ValueError("No board_outline for version_id")
    W, H, RES = board

    components = {
        row[0]: {"name": row[1], "w": row[2], "h": row[3], "cyd": row[4]}
        for row in conn.execute(
            """SELECT c.id, c.name, g.width_mm, g.height_mm, g.courtyard_margin
               FROM components c JOIN component_geometry g ON g.component_id=c.id
               WHERE c.version_id=?""",
            (version_id,),
        ).fetchall()
    }

    fixed_names = {
        row[0]
        for row in conn.execute(
            """SELECT ca.name FROM constraints ct
               JOIN components ca ON ct.comp_a_id=ca.id
               WHERE ct.version_id=? AND ct.type='FIXED'""",
            (version_id,),
        ).fetchall()
    }

    # Build edge map: component name → target edge.
    # Primary source: constraints.edge (authoritative, set by LLM in Step 4).
    # Fallback: requirements table (legacy / manual override).
    edge_from_constraints = {
        row[0]: row[1]
        for row in conn.execute(
            """SELECT ca.name, ct.edge FROM constraints ct
               JOIN components ca ON ct.comp_a_id=ca.id
               WHERE ct.version_id=? AND ct.type='FIXED' AND ct.edge IS NOT NULL""",
            (version_id,),
        ).fetchall()
    }

    near_pairs = conn.execute(
        """SELECT ca.id, cb.id FROM constraints ct
           JOIN components ca ON ct.comp_a_id=ca.id
           JOIN components cb ON ct.comp_b_id=cb.id
           WHERE ct.version_id=? AND ct.type='NEAR'""",
        (version_id,),
    ).fetchall()

    keep_outs = conn.execute(
        "SELECT x_mm, y_mm, width_mm, height_mm, is_mount_clearance FROM keep_out_zones WHERE version_id=?",
        (version_id,),
    ).fetchall()

    requirements = {}
    for row in conn.execute(
        """SELECT c.name, r.key, r.value FROM requirements r
           JOIN components c ON r.component_id=c.id
           WHERE c.version_id=?""",
        (version_id,),
    ).fetchall():
        requirements.setdefault(row[0], {})[row[1]] = row[2]

    # Merge constraints.edge into requirements, taking precedence over any
    # legacy requirements.edge value so the DB stays the single source of truth.
    for name, edge in edge_from_constraints.items():
        requirements.setdefault(name, {})["edge"] = edge

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


def _is_corner_adjacent(x, y, w, h, W, H, tol=2.0):
    """True when the component body touches two board edges simultaneously.

    Only corner-adjacent FIXED components may overlap mount-clearance keep-outs.
    Single-edge FIXED components (e.g. a GPIO header spanning one full edge) must
    still be nudged away from corner keep-out zones.
    """
    px1, py1 = x + w, y + h
    touches_left   = x  <= tol
    touches_right  = px1 >= W - tol
    touches_top    = y   <= tol
    touches_bottom = py1 >= H - tol
    return sum([touches_left, touches_right, touches_top, touches_bottom]) >= 2


def fits(x, y, w, h, cyd, W, H, occupied, res, ignore_keep_outs=False, ignore_fixed_ids=None):
    if x < 0 or y < 0 or x + w > W or y + h > H:
        return False
    # Determine whether this FIXED component is corner-adjacent so we only exempt
    # truly cornered connectors from mount-clearance keep-out cells (-1).
    # Hard keep-out cells (-2, e.g. RF/antenna zones) block ALL components, even
    # corner-adjacent FIXED connectors.
    corner_adj = _is_corner_adjacent(x, y, w, h, W, H) if ignore_keep_outs else False
    for cell in cells_for(x, y, w, h, cyd, res):
        occupant = occupied.get(cell)
        if occupant is None:
            continue
        if occupant == -1 and ignore_keep_outs and corner_adj:
            # Mount-clearance zone + corner-adjacent FIXED connector → allowed
            continue
        if occupant == -2:
            # Hard keep-out (RF, antenna, etc.) — blocks every component unconditionally
            return False
        if occupant in (-1, -2) and ignore_keep_outs and not corner_adj:
            # Any keep-out cell for a single-edge FIXED component → blocked
            return False
        if occupant in (-1, -2):
            # Normal (non-FIXED) component — always blocked by any keep-out cell
            return False
        if ignore_fixed_ids and occupant in ignore_fixed_ids:
            continue  # FIXED components may have courtyard overlap with other FIXED components
        return False
    return True


def place_at(comp_id, x, y, w, h, cyd, occupied, res):
    for cell in cells_for(x, y, w, h, cyd, res):
        occupied[cell] = comp_id


def snap(v, res):
    return round(round(v / res) * res, 6)


# ── fixed-position heuristic ──────────────────────────────────────────────────


def fixed_position(name, w, h, W, H, requirements, edge_clearances=None):
    """Return the starting search position for a FIXED connector.

    For edges with multiple connectors the nudge loop will slide along the
    edge to find a free slot.  We start from the low end of each edge so
    connectors pack consecutively from one corner rather than all starting
    from the centre (which causes large connectors to block the centre and
    leave no room for others on the same edge).

    edge_clearances: optional dict with keys 'left_width', 'right_width',
    'top_height', 'bottom_height' giving the maximum body dimension of
    connectors on each edge.  Used to offset the starting position for
    top/bottom connectors so they don't start in the corner where
    left/right connectors are already placed.
    """
    edge = requirements.get(name, {}).get("edge", "")
    margin = 1.0
    ec = edge_clearances or {}
    # Extra offsets along edge axes to avoid corner conflicts with perpendicular
    # edge connectors.  Each edge starts away from the two adjacent corners.
    left_clear = ec.get("left_width", 0.0) + margin
    top_clear = ec.get("top_height", 0.0) + margin
    if edge == "top":
        return left_clear, margin  # start after left-edge connectors
    if edge == "bottom":
        return left_clear, snap(H - h - margin, 1.0)  # start after left-edge connectors
    if edge == "right":
        return snap(W - w - margin, 1.0), top_clear  # start after top-edge connectors
    if edge == "left":
        return margin, top_clear  # start after top-edge connectors
    # fallback centre
    return snap((W - w) / 2, 1.0), snap((H - h) / 2, 1.0)


def _edge_nudge_offsets(edge, W, H, RES, n=400):
    """Return (ox, oy) offsets that slide along the given edge axis only.

    For top/bottom edges: slide in X (along the edge), then try Y nudge as
    last resort if board boundary clips.  For left/right edges: slide in Y.
    This keeps FIXED components pinned to their edge even when a previous
    component already occupies the ideal centre position.
    """
    offsets = []
    # primary: slide along the edge (up to half-board-width steps)
    if edge in ("top", "bottom"):
        max_slide = int(W / RES)
        for i in range(1, max_slide + 1):
            offsets.append((i * RES, 0))
            offsets.append((-i * RES, 0))
    else:  # left / right
        max_slide = int(H / RES)
        for i in range(1, max_slide + 1):
            offsets.append((0, i * RES))
            offsets.append((0, -i * RES))
    return offsets[:n]


# ── main greedy placer ────────────────────────────────────────────────────────


def greedy_place(version_id, db_path=DEFAULT_DB):
    conn = connect(db_path)
    W, H, RES, components, fixed_names, near_pairs, keep_outs, requirements = load_design(conn, version_id)

    # Pre-mark keep-out cells using two sentinels so fits() can distinguish zone types:
    #   -1  mount-clearance keep-out  (corner screw zones)
    #       → FIXED corner-adjacent components may overlap these (unavoidable geometry)
    #   -2  hard keep-out             (RF, antenna, any non-mount zone)
    #       → NO component may ever overlap these, not even FIXED edge connectors
    occupied = {}
    for row in keep_outs:
        kx, ky, kw, kh = row[0], row[1], row[2], row[3]
        is_mc = bool(row[4]) if len(row) > 4 else False
        sentinel = -1 if is_mc else -2
        for cx in range(int(kx / RES), math.ceil((kx + kw) / RES)):
            for cy in range(int(ky / RES), math.ceil((ky + kh) / RES)):
                # Never downgrade a hard keep-out cell to mount-clearance
                if occupied.get((cx, cy)) != -2:
                    occupied[(cx, cy)] = sentinel

    placements = {}  # comp_id → (x, y)

    # --- Pass 1: FIXED connectors (edge-placed) ---
    # Group by edge, then sort largest-first within each edge group so
    # bin-packing leaves fewest gaps per edge.  Processing all connectors on
    # the same edge together prevents a large connector on a different edge
    # from stealing the only viable slot for a smaller same-edge connector.
    fixed_items = [(comp_id, comp) for comp_id, comp in components.items() if comp["name"] in fixed_names]

    def _edge_sort_key(t):
        comp_id, comp = t
        name = comp["name"]
        edge = requirements.get(name, {}).get("edge", "")
        edge_order = {"top": 0, "bottom": 1, "left": 2, "right": 3}.get(edge, 4)
        return (edge_order, -(comp["w"] * comp["h"]))

    fixed_items.sort(key=_edge_sort_key)

    # Compute edge clearances: the maximum body dimension of connectors on each
    # edge so that perpendicular edges start their connectors after the corner zone.
    edge_max: dict[str, float] = {"top": 0.0, "bottom": 0.0, "left": 0.0, "right": 0.0}
    for _, comp in fixed_items:
        name = comp["name"]
        edge = requirements.get(name, {}).get("edge", "")
        if edge in ("left", "right"):
            edge_max[edge] = max(edge_max[edge], comp["w"] + comp["cyd"])
        elif edge in ("top", "bottom"):
            edge_max[edge] = max(edge_max[edge], comp["h"] + comp["cyd"])
    edge_clearances = {
        "left_width": edge_max["left"],
        "right_width": edge_max["right"],
        "top_height": edge_max["top"],
        "bottom_height": edge_max["bottom"],
    }

    # Track placed FIXED IDs per edge so same-edge connectors still respect each
    # other's footprints while perpendicular-edge connectors' courtyard cells at
    # shared corners are allowed to overlap.
    fixed_placed_by_edge: dict[str, set] = {}  # edge → set of comp_ids placed on that edge
    for comp_id, comp in fixed_items:
        name = comp["name"]
        edge = requirements.get(name, {}).get("edge", "")
        x, y = fixed_position(name, comp["w"], comp["h"], W, H, requirements, edge_clearances)
        x, y = snap(x, RES), snap(y, RES)
        # Ignore courtyard overlaps with FIXED connectors on OTHER edges only.
        # Same-edge connectors must not physically overlap each other.
        other_edge_fixed = set()
        for other_edge, ids in fixed_placed_by_edge.items():
            if other_edge != edge:
                other_edge_fixed.update(ids)
        placed = False
        if fits(
            x,
            y,
            comp["w"],
            comp["h"],
            comp["cyd"],
            W,
            H,
            occupied,
            RES,
            ignore_keep_outs=True,
            ignore_fixed_ids=other_edge_fixed,
        ):
            placements[comp_id] = (x, y)
            place_at(comp_id, x, y, comp["w"], comp["h"], comp["cyd"], occupied, RES)
            placed = True
            fixed_placed_by_edge.setdefault(edge, set()).add(comp_id)
        else:
            for ox, oy in _edge_nudge_offsets(edge, W, H, RES):
                tx, ty = snap(x + ox, RES), snap(y + oy, RES)
                if fits(
                    tx,
                    ty,
                    comp["w"],
                    comp["h"],
                    comp["cyd"],
                    W,
                    H,
                    occupied,
                    RES,
                    ignore_keep_outs=True,
                    ignore_fixed_ids=other_edge_fixed,
                ):
                    placements[comp_id] = (tx, ty)
                    place_at(comp_id, tx, ty, comp["w"], comp["h"], comp["cyd"], occupied, RES)
                    placed = True
                    fixed_placed_by_edge.setdefault(edge, set()).add(comp_id)
                    break
        if not placed:
            # No gap found on this edge — force place and log so the LLM
            # review step can flag the over-commitment to the user.
            import sys

            print(
                f"WARNING: FIXED component '{name}' could not fit on '{edge}' edge "
                f"({comp['w']}x{comp['h']}mm) — edge may be over-committed. "
                "Force-placing; expect overlap penalty.",
                file=sys.stderr,
            )
            placements[comp_id] = (x, y)
            place_at(comp_id, x, y, comp["w"], comp["h"], comp["cyd"], occupied, RES)

    # --- Pass 2: NEAR-clustered free components (place near their anchor) ---
    near_by_anchor = {}
    for a_id, b_id in near_pairs:
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
        for row_start_y in [snap(y, RES) for y in [i * RES for i in range(2, int(H / RES) - 2)]]:
            for col_x in [snap(x, RES) for x in [i * RES for i in range(2, int(W / RES) - 2)]]:
                if fits(col_x, row_start_y, comp["w"], comp["h"], comp["cyd"], W, H, occupied, RES):
                    placements[comp_id] = (col_x, row_start_y)
                    place_at(comp_id, col_x, row_start_y, comp["w"], comp["h"], comp["cyd"], occupied, RES)
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
        if comp_id in (-1, -2):  # keep-out sentinels — not real components
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
