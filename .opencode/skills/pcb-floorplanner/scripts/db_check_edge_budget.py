"""
db_check_edge_budget.py — Step 2 gate (run immediately after db_write_board.py)

Checks that all edge-assigned connectors fit on their designated board edges
without overcommitting the available space.  Pure arithmetic — no LLM judgment.

Algorithm
---------
For each edge (top / bottom / left / right):
  1. Compute usable_mm = edge_length - sum of keep-out widths/heights that cut into that edge
  2. Collect all components with requirements.key='edge' and requirements.value=<edge>
     that also have geometry written (width_mm / height_mm)
  3. committed_mm = sum of component body dimensions along the edge axis
     + courtyard margins (2 × courtyard_margin per component)
  4. ok = committed_mm <= usable_mm

Corner conflict detection
-------------------------
Two FIXED connectors on perpendicular edges conflict if both claim the same corner cell.
A connector on the LEFT  edge claims the TOP-LEFT  corner when its body starts at y=0
A connector on the TOP   edge claims the TOP-LEFT  corner when its body starts at x=0
Conflict = both claim the same corner simultaneously.

Output JSON
-----------
{
  "version_id": 1,
  "edges": {
    "top":    {"usable_mm": 71.0, "committed_mm": 51.0, "components": ["J8"],         "ok": true},
    "bottom": {"usable_mm": 71.0, "committed_mm": 63.0, "components": ["J9","J10","J11"], "ok": true},
    "left":   {"usable_mm": 49.0, "committed_mm": 31.0, "components": ["J1","J2","J3","J4"], "ok": true},
    "right":  {"usable_mm": 49.0, "committed_mm": 46.0, "components": ["J5","J6","J7"], "ok": true}
  },
  "corner_conflicts": [
    {"corner": "top-left", "top_edge": "J8", "left_edge": "J3"}
  ],
  "feasible": false,
  "errors": ["Corner conflict: J8 (top) and J3 (left) both claim the top-left corner"]
}

Exit codes
----------
0  feasible=true  (all edges ok, no corner conflicts)
1  feasible=false (edge overcommitted or corner conflict detected)
"""

import argparse
import json
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_db_dir = next(p / "db" for p in _here.parents if (p / "db" / "db_init.py").exists())
sys.path.insert(0, str(_db_dir))
from db_init import connect, DEFAULT_DB

# Axis along which connectors are laid out per edge:
#   top/bottom → connectors span X axis → their width_mm consumes horizontal space
#   left/right → connectors span Y axis → their height_mm consumes vertical space
_EDGE_AXIS = {
    "top": "width_mm",
    "bottom": "width_mm",
    "left": "height_mm",
    "right": "height_mm",
}

# Which board dimension gives the full edge length
_EDGE_LENGTH_DIM = {
    "top": "width_mm",
    "bottom": "width_mm",
    "left": "height_mm",
    "right": "height_mm",
}

# For each corner, which two edges share it and how to detect a conflict:
# An edge-connector claims a corner when its body would start at coordinate 0
# on its axis (i.e. it is the first component from that corner).
# We use a conservative check: the connector body on edge A overlaps the
# keep-out zone that protects the corner — meaning it physically intrudes into
# the region where the perpendicular edge connector also sits.
_CORNERS = [
    # (corner_name, h_edge, v_edge, corner_x, corner_y)
    ("top-left",     "top",    "left",  0.0, 0.0),
    ("top-right",    "top",    "right", None, 0.0),   # x = board_width
    ("bottom-left",  "bottom", "left",  0.0, None),   # y = board_height
    ("bottom-right", "bottom", "right", None, None),
]


def check_edge_budget(version_id: int, db_path=DEFAULT_DB) -> dict:
    conn = connect(db_path)

    # --- board dimensions ---------------------------------------------------
    row = conn.execute(
        "SELECT width_mm, height_mm FROM board_outline WHERE version_id=?",
        (version_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"No board_outline found for version_id={version_id}. Run db_write_board.py first.")
    board_w, board_h = row
    board_dims = {"width_mm": board_w, "height_mm": board_h}

    # --- keep-out zones: compute how much each edge loses -------------------
    keep_outs = conn.execute(
        "SELECT x_mm, y_mm, width_mm, height_mm FROM keep_out_zones WHERE version_id=?",
        (version_id,),
    ).fetchall()

    # For each edge, sum the keep-out extents that touch that edge
    # (conservative: any keep-out that abuts the edge reduces usable space)
    edge_blocked = {"top": 0.0, "bottom": 0.0, "left": 0.0, "right": 0.0}
    for kx, ky, kw, kh in keep_outs:
        if ky == 0:                          # touches top edge
            edge_blocked["top"] += kw
        if ky + kh >= board_h:               # touches bottom edge
            edge_blocked["bottom"] += kw
        if kx == 0:                          # touches left edge
            edge_blocked["left"] += kh
        if kx + kw >= board_w:               # touches right edge
            edge_blocked["right"] += kh

    usable = {
        "top":    board_w - edge_blocked["top"],
        "bottom": board_w - edge_blocked["bottom"],
        "left":   board_h - edge_blocked["left"],
        "right":  board_h - edge_blocked["right"],
    }

    # --- components with edge requirements + geometry -----------------------
    rows = conn.execute(
        """
        SELECT c.name, r.value AS edge, g.width_mm, g.height_mm, g.courtyard_margin
        FROM requirements r
        JOIN components c ON r.component_id = c.id
        LEFT JOIN component_geometry g ON g.component_id = c.id
        WHERE c.version_id = ?
          AND r.key = 'edge'
        """,
        (version_id,),
    ).fetchall()

    # Group by edge
    by_edge: dict[str, list[dict]] = {e: [] for e in ("top", "bottom", "left", "right")}
    no_geometry: list[str] = []
    for name, edge, w, h, cyd in rows:
        edge = edge.lower().strip()
        if edge not in by_edge:
            continue
        if w is None:
            no_geometry.append(name)
            continue
        by_edge[edge].append({"name": name, "width_mm": w, "height_mm": h, "courtyard_margin": cyd or 0.5})

    # --- per-edge budget calculation ----------------------------------------
    edges_out: dict[str, dict] = {}
    errors: list[str] = []

    for edge, comps in by_edge.items():
        axis_dim = _EDGE_AXIS[edge]          # "width_mm" or "height_mm"
        committed = sum(
            c[axis_dim] + 2 * c["courtyard_margin"]
            for c in comps
        )
        ok = committed <= usable[edge]
        edges_out[edge] = {
            "usable_mm": round(usable[edge], 3),
            "committed_mm": round(committed, 3),
            "components": [c["name"] for c in comps],
            "ok": ok,
        }
        if not ok:
            errors.append(
                f"Edge '{edge}' overcommitted: {committed:.1f}mm committed > {usable[edge]:.1f}mm usable "
                f"(components: {', '.join(c['name'] for c in comps)})"
            )

    # --- corner conflict detection ------------------------------------------
    corner_conflicts: list[dict] = []

    for corner_name, h_edge, v_edge, cx, cy in _CORNERS:
        # Resolve None coordinates to board edges
        corner_x = cx if cx is not None else board_w
        corner_y = cy if cy is not None else board_h

        h_comps = by_edge.get(h_edge, [])
        v_comps = by_edge.get(v_edge, [])
        if not h_comps or not v_comps:
            continue

        # A top/bottom connector claims this corner if its body width_mm reaches
        # into the corner column (i.e. its body starts at or near x=corner_x for
        # top-left, or ends at x=corner_x for top-right).
        # A left/right connector claims this corner if its height_mm reaches into
        # the corner row (starts at y=corner_y for top edges, ends for bottom).
        #
        # Conservative heuristic: check whether any keep-out zone at this corner
        # would be overlapped by both a horizontal-edge connector AND a
        # vertical-edge connector simultaneously.  We find the keep-out that sits
        # at the corner coordinates and use its dimensions as the conflict zone.
        corner_ko = None
        for kx, ky, kw, kh in keep_outs:
            # keep-out touches this corner
            at_x = abs(kx - corner_x) < 0.1 or abs((kx + kw) - corner_x) < 0.1
            at_y = abs(ky - corner_y) < 0.1 or abs((ky + kh) - corner_y) < 0.1
            if at_x and at_y:
                corner_ko = (kx, ky, kw, kh)
                break

        if corner_ko is None:
            # No keep-out at this corner — nothing to contest, skip conflict check
            continue

        ckx, cky, ckw, ckh = corner_ko

        # Check each horizontal-edge connector: does its body extend into the corner zone?
        h_claimants = []
        for c in h_comps:
            body_w = c["width_mm"] + 2 * c["courtyard_margin"]
            if h_edge in ("top", "bottom"):
                # connector laid along X axis; it claims the corner when its body is
                # wider than the keep-out zone that guards that corner side.
                if corner_x == 0:
                    if body_w > ckw:   # body extends past the keep-out width from x=0
                        h_claimants.append(c["name"])
                else:
                    if body_w > ckw:   # body extends past the keep-out width from x=board_w
                        h_claimants.append(c["name"])

        # Check each vertical-edge connector: does its body extend into the corner zone?
        v_claimants = []
        for c in v_comps:
            body_h = c["height_mm"] + 2 * c["courtyard_margin"]
            if v_edge in ("left", "right"):
                if corner_y == 0:
                    if body_h > ckh:   # body extends past the keep-out height from y=0
                        v_claimants.append(c["name"])
                else:
                    if body_h > ckh:   # body extends past the keep-out height from y=board_h
                        v_claimants.append(c["name"])

        for hc in h_claimants:
            for vc in v_claimants:
                conflict = {
                    "corner": corner_name,
                    f"{h_edge}_edge": hc,
                    f"{v_edge}_edge": vc,
                }
                corner_conflicts.append(conflict)
                errors.append(
                    f"Corner conflict at {corner_name}: {hc} ({h_edge} edge) body intrudes into "
                    f"corner zone also claimed by {vc} ({v_edge} edge)"
                )

    if no_geometry:
        errors.append(
            f"Components with edge requirement but no geometry written yet "
            f"(run db_write_geometry.py first): {', '.join(no_geometry)}"
        )

    feasible = len(errors) == 0

    result = {
        "version_id": version_id,
        "board_mm": f"{board_w}x{board_h}",
        "edges": edges_out,
        "corner_conflicts": corner_conflicts,
        "feasible": feasible,
    }
    if errors:
        result["errors"] = errors

    conn.close()
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Check edge-connector budget after board and geometry are written."
    )
    ap.add_argument("--version_id", type=int, required=True)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()

    out = check_edge_budget(args.version_id, args.db)
    print(json.dumps(out, indent=2))
    if not out["feasible"]:
        for err in out.get("errors", []):
            print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)
