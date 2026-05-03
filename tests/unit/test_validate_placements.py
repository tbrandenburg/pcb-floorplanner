"""
Unit tests for db_validate_placements.py

Covers:
  - clean placement returns ok=True, no violations
  - mount hole overlap detected (circular geometry)
  - keep-out zone overlap detected
  - component-to-component body overlap detected
  - FIXED components are exempt from mount-clearance keep-outs
  - multiple violations reported together
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT / "db"))
sys.path.insert(0, str(_ROOT / ".opencode" / "skills" / "pcb-floorplanner" / "scripts"))

from db_validate_placements import validate, _rect_circle_overlap, _rect_rect_overlap

sys.path.insert(0, str(_ROOT / "tests"))
from conftest import make_db, seed_session, seed_component, seed_geometry, lock_version


# ── geometry helpers ──────────────────────────────────────────────────────────


def test_rect_circle_overlap_inside():
    assert _rect_circle_overlap(0, 0, 10, 10, 5, 5, 2) is True


def test_rect_circle_overlap_corner():
    # circle centered just outside corner, radius small enough to miss
    assert _rect_circle_overlap(0, 0, 10, 10, 11, 11, 1) is False


def test_rect_circle_overlap_corner_touch():
    # circle just reaches the corner
    import math

    assert _rect_circle_overlap(0, 0, 10, 10, 11, 11, math.sqrt(2) + 0.01) is True


def test_rect_rect_overlap_yes():
    assert _rect_rect_overlap(0, 0, 5, 5, 4, 4, 5, 5) is True


def test_rect_rect_overlap_no():
    assert _rect_rect_overlap(0, 0, 5, 5, 6, 0, 5, 5) is False


def test_rect_rect_overlap_touching_edge():
    # touching at edge x=5 is not an overlap
    assert _rect_rect_overlap(0, 0, 5, 5, 5, 0, 5, 5) is False


# ── helpers to build a minimal run in an in-memory DB ─────────────────────────


def _make_run(
    conn,
    x1,
    y1,
    x2=None,
    y2=None,
    w1=10,
    h1=10,
    w2=6,
    h2=6,
    mount_holes=None,
    keep_outs=None,
    status1="PLACED",
    status2="PLACED",
):
    """Seed a full run with 1 or 2 components.  Returns (conn, run_id)."""
    _, vid = seed_session(conn)
    cid1 = seed_component(conn, vid, "U1", "SoC")
    seed_geometry(conn, cid1, w=w1, h=h1, cyd=0.0)

    # Seed second component BEFORE locking (immutability trigger blocks inserts on LOCKED)
    cid2 = None
    if x2 is not None:
        cid2 = seed_component(conn, vid, "U2", "PMIC")
        seed_geometry(conn, cid2, w=w2, h=h2, cyd=0.0)

    conn.execute(
        "INSERT INTO board_outline(version_id, width_mm, height_mm, grid_resolution) VALUES (?,?,?,?)",
        (vid, 85.0, 56.0, 1.0),
    )

    if mount_holes:
        for hx, hy, hd in mount_holes:
            conn.execute(
                "INSERT INTO mount_holes(version_id, x_mm, y_mm, diameter_mm) VALUES (?,?,?,?)",
                (vid, hx, hy, hd),
            )

    if keep_outs:
        for kx, ky, kw, kh, reason, is_mount in keep_outs:
            conn.execute(
                "INSERT INTO keep_out_zones(version_id, x_mm, y_mm, width_mm, height_mm, reason, is_mount_clearance)"
                " VALUES (?,?,?,?,?,?,?)",
                (vid, kx, ky, kw, kh, reason, is_mount),
            )

    lock_version(conn, vid)
    rid = conn.execute("INSERT INTO optimization_runs(version_id, algorithm) VALUES (?,?)", (vid, "test")).lastrowid

    conn.execute(
        "INSERT INTO placements(run_id, component_id, x_mm, y_mm, rotation, status) VALUES (?,?,?,?,?,?)",
        (rid, cid1, x1, y1, 0, status1),
    )

    if cid2 is not None:
        conn.execute(
            "INSERT INTO placements(run_id, component_id, x_mm, y_mm, rotation, status) VALUES (?,?,?,?,?,?)",
            (rid, cid2, x2, y2, 0, status2),
        )

    conn.commit()
    return conn, rid


# ── validate() tests ──────────────────────────────────────────────────────────


def test_clean_placement():
    conn = make_db()
    _, rid = _make_run(conn, x1=20, y1=20)
    result = validate(run_id=rid, db_path=conn)
    assert result["ok"] is True
    assert result["violations"] == []


def test_mount_hole_overlap_detected():
    conn = make_db()
    # component at (0,0) 10x10, hole at (5,5) r=3 — clearly inside
    _, rid = _make_run(conn, x1=0, y1=0, mount_holes=[(5, 5, 6)])
    result = validate(run_id=rid, db_path=conn)
    assert result["ok"] is False
    assert any("MOUNT_HOLE" in v for v in result["violations"])


def test_mount_hole_no_overlap():
    conn = make_db()
    # component at (20,20), hole at (3,3) — far away
    _, rid = _make_run(conn, x1=20, y1=20, mount_holes=[(3, 3, 2.7)])
    result = validate(run_id=rid, db_path=conn)
    assert result["ok"] is True


def test_keep_out_overlap_detected():
    conn = make_db()
    _, rid = _make_run(conn, x1=5, y1=5, keep_outs=[(0, 0, 8, 8, "RF zone", 0)])
    result = validate(run_id=rid, db_path=conn)
    assert result["ok"] is False
    assert any("KEEP_OUT" in v for v in result["violations"])


def test_keep_out_no_overlap():
    conn = make_db()
    _, rid = _make_run(conn, x1=30, y1=30, keep_outs=[(0, 0, 5, 5, "RF zone", 0)])
    result = validate(run_id=rid, db_path=conn)
    assert result["ok"] is True


def test_fixed_exempt_from_mount_clearance():
    conn = make_db()
    # Corner-adjacent FIXED component (0,0) on 85x56 board — touches left and top edges
    _, rid = _make_run(conn, x1=0, y1=0, w1=10, h1=10, status1="FIXED",
                       keep_outs=[(0, 0, 8, 8, "mount hole TL clearance", 1)])
    result = validate(run_id=rid, db_path=conn)
    assert result["ok"] is True


def test_fixed_single_edge_not_exempt_from_corner_mount_clearance():
    """Single-edge FIXED component overlapping a corner mount-clearance zone must be reported.

    Regression for J8 GPIO header: it spans only the bottom edge (not a corner) so it
    is not exempt from the bottom-right corner keep-out zone.
    """
    conn = make_db()
    # Component at (40, 46): x=[40,54], y=[46,56] — touches only the bottom edge (y+h=56)
    # Board is 85x56. Keep-out at (58,49,7,7) — x=[58,65], y=[49,56]. No overlap here.
    # Use a keep-out that the right side of this component overlaps:
    # Component x=[40,54] → keep-out at (50,49,7,7) → overlaps x=[50,54], y=[49,56]
    _, rid = _make_run(
        conn,
        x1=40, y1=46, w1=14, h1=10, status1="FIXED",
        keep_outs=[(50, 49, 7, 7, "mount hole BR clearance", 1)],
    )
    result = validate(run_id=rid, db_path=conn)
    assert result["ok"] is False
    assert any("KEEP_OUT" in v for v in result["violations"]), (
        "Single-edge FIXED component must trigger KEEP_OUT violation when overlapping corner zone"
    )


def test_fixed_not_exempt_from_rf_keep_out():
    conn = make_db()
    _, rid = _make_run(conn, x1=0, y1=0, status1="FIXED", keep_outs=[(0, 0, 8, 8, "RF antenna zone", 0)])
    result = validate(run_id=rid, db_path=conn)
    assert result["ok"] is False
    assert any("KEEP_OUT" in v for v in result["violations"])


def test_component_overlap_detected():
    conn = make_db()
    # two 10x10 components placed at (0,0) and (5,5) — overlap
    _, rid = _make_run(conn, x1=0, y1=0, x2=5, y2=5)
    result = validate(run_id=rid, db_path=conn)
    assert result["ok"] is False
    assert any("OVERLAP" in v for v in result["violations"])


def test_component_no_overlap():
    conn = make_db()
    # two components placed side by side with no overlap
    _, rid = _make_run(conn, x1=0, y1=0, x2=15, y2=0)
    result = validate(run_id=rid, db_path=conn)
    assert result["ok"] is True


def test_multiple_violations_all_reported():
    conn = make_db()
    # mount hole + component overlap simultaneously
    _, rid = _make_run(conn, x1=0, y1=0, x2=5, y2=5, mount_holes=[(2, 2, 3)])
    result = validate(run_id=rid, db_path=conn)
    assert result["ok"] is False
    types = {v.split(":")[0] for v in result["violations"]}
    assert "MOUNT_HOLE" in types
    assert "OVERLAP" in types
