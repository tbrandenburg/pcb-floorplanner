"""
Unit tests for db_check_edge_budget.py

Covers:
  - all edges ok → feasible=true, exit 0
  - single edge overcommitted → feasible=false, error message
  - all four edges overcommitted simultaneously
  - corner conflict: top-edge connector body intrudes into corner also claimed by left-edge connector
  - no corner conflict when connectors are short enough
  - missing board_outline raises ValueError
  - components without geometry emit an error (not a crash)
  - keep-out zones correctly reduce usable space per edge
  - corner conflict detected for all four corners
"""

import sqlite3
import sys
import pytest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "db"))
sys.path.insert(0, str(_ROOT / ".opencode" / "skills" / "pcb-floorplanner" / "scripts"))

from db_init import init
from db_check_edge_budget import check_edge_budget


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(tmp_path) -> str:
    db = tmp_path / "test.db"
    init(str(db))
    return str(db)


def _seed(db_path, board_w=85.0, board_h=56.0, keep_outs=None, components=None):
    """
    Seed a minimal design_session + design_version + board_outline.
    components: list of dicts with keys: name, edge, width_mm, height_mm, courtyard_margin
                width_mm/height_mm=None → skip geometry (test missing geometry path)
    keep_outs:  list of dicts with keys: x_mm, y_mm, width_mm, height_mm
    Returns version_id.
    """
    keep_outs = keep_outs or []
    components = components or []

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")

    sid = conn.execute("INSERT INTO design_sessions(prompt, model) VALUES (?,?)", ("t", "m")).lastrowid
    vid = conn.execute("INSERT INTO design_versions(session_id) VALUES (?)", (sid,)).lastrowid
    conn.execute(
        "INSERT INTO board_outline(version_id, width_mm, height_mm, grid_resolution) VALUES (?,?,?,?)",
        (vid, board_w, board_h, 1.0),
    )
    for ko in keep_outs:
        conn.execute(
            "INSERT INTO keep_out_zones(version_id, x_mm, y_mm, width_mm, height_mm, reason, is_mount_clearance)"
            " VALUES (?,?,?,?,?,?,?)",
            (vid, ko["x_mm"], ko["y_mm"], ko["width_mm"], ko["height_mm"], ko.get("reason", "ko"), 1),
        )
    for c in components:
        cid = conn.execute(
            "INSERT INTO components(version_id, name, type) VALUES (?,?,?)",
            (vid, c["name"], "CONNECTOR"),
        ).lastrowid
        conn.execute(
            "INSERT INTO requirements(component_id, key, value) VALUES (?,?,?)",
            (cid, "edge", c["edge"]),
        )
        if c.get("width_mm") is not None:
            conn.execute(
                "INSERT INTO component_geometry(component_id, width_mm, height_mm, courtyard_margin) VALUES (?,?,?,?)",
                (cid, c["width_mm"], c["height_mm"], c.get("courtyard_margin", 0.5)),
            )

    conn.commit()
    conn.close()
    return vid


# ---------------------------------------------------------------------------
# Tests: basic feasibility
# ---------------------------------------------------------------------------


def test_all_edges_ok_feasible(tmp_path):
    db = _make_db(tmp_path)
    # Components are intentionally placed away from corners (no corner keep-outs seeded)
    # so no corner conflicts fire.
    vid = _seed(
        db,
        board_w=85.0,
        board_h=56.0,
        components=[
            {"name": "J1", "edge": "left", "width_mm": 9.0, "height_mm": 7.0},
            {"name": "J2", "edge": "right", "width_mm": 16.0, "height_mm": 16.0},
            {"name": "J3", "edge": "top", "width_mm": 30.0, "height_mm": 5.0},
            {"name": "J4", "edge": "bottom", "width_mm": 20.0, "height_mm": 4.0},
        ],
    )
    result = check_edge_budget(vid, db)
    assert result["feasible"] is True
    assert "errors" not in result
    assert result["edges"]["left"]["ok"] is True
    assert result["edges"]["right"]["ok"] is True
    assert result["edges"]["top"]["ok"] is True
    assert result["edges"]["bottom"]["ok"] is True


def test_single_edge_overcommitted(tmp_path):
    db = _make_db(tmp_path)
    # left edge: board height=56, two connectors totalling 60mm body → overcommit
    vid = _seed(
        db,
        board_w=85.0,
        board_h=56.0,
        components=[
            {"name": "J1", "edge": "left", "width_mm": 9.0, "height_mm": 30.0},
            {"name": "J2", "edge": "left", "width_mm": 9.0, "height_mm": 30.0},
        ],
    )
    result = check_edge_budget(vid, db)
    assert result["feasible"] is False
    assert result["edges"]["left"]["ok"] is False
    assert any("left" in e for e in result["errors"])


def test_courtyard_margins_included_in_committed(tmp_path):
    """Courtyard margins must be added — they are physical exclusion zones."""
    db = _make_db(tmp_path)
    # left edge 56mm: one connector 55mm body + 2×0.5mm courtyard = 56mm → exactly fits
    vid = _seed(
        db,
        board_w=85.0,
        board_h=56.0,
        components=[
            {"name": "J1", "edge": "left", "width_mm": 9.0, "height_mm": 55.0, "courtyard_margin": 0.5},
        ],
    )
    result = check_edge_budget(vid, db)
    assert result["feasible"] is True
    assert result["edges"]["left"]["committed_mm"] == 56.0

    # Now add 0.1mm more — should overcommit
    db2 = str(tmp_path / "test2.db")
    init(db2)
    vid2 = _seed(
        db2,
        board_w=85.0,
        board_h=56.0,
        components=[
            {"name": "J1", "edge": "left", "width_mm": 9.0, "height_mm": 55.1, "courtyard_margin": 0.5},
        ],
    )
    result2 = check_edge_budget(vid2, db2)
    assert result2["edges"]["left"]["ok"] is False


def test_keep_outs_reduce_usable_space(tmp_path):
    """Corner keep-outs on the top edge should reduce usable_mm."""
    db = _make_db(tmp_path)
    # Two 7×7 corner keep-outs touching top edge: usable_top = 85 - 7 - 7 = 71mm
    vid = _seed(
        db,
        board_w=85.0,
        board_h=56.0,
        keep_outs=[
            {"x_mm": 0, "y_mm": 0, "width_mm": 7, "height_mm": 7},
            {"x_mm": 57.5, "y_mm": 0, "width_mm": 7, "height_mm": 7},
        ],
        components=[
            {"name": "J8", "edge": "top", "width_mm": 51.0, "height_mm": 5.0},
        ],
    )
    result = check_edge_budget(vid, db)
    assert result["edges"]["top"]["usable_mm"] == pytest.approx(71.0)
    assert result["edges"]["top"]["ok"] is True  # 51 + 2×0.5 = 52 < 71


def test_keep_out_causes_overcommit(tmp_path):
    """A large keep-out that leaves too little space for a wide connector."""
    db = _make_db(tmp_path)
    # top edge 85mm, 40mm keep-out → 45mm usable; connector is 50mm → overcommit
    vid = _seed(
        db,
        board_w=85.0,
        board_h=56.0,
        keep_outs=[{"x_mm": 0, "y_mm": 0, "width_mm": 40, "height_mm": 5}],
        components=[
            {"name": "J1", "edge": "top", "width_mm": 50.0, "height_mm": 5.0, "courtyard_margin": 0.0},
        ],
    )
    result = check_edge_budget(vid, db)
    assert result["edges"]["top"]["usable_mm"] == pytest.approx(45.0)
    assert result["edges"]["top"]["ok"] is False


def test_all_four_edges_overcommitted(tmp_path):
    db = _make_db(tmp_path)
    vid = _seed(
        db,
        board_w=20.0,
        board_h=20.0,
        components=[
            {"name": "JT", "edge": "top", "width_mm": 25.0, "height_mm": 5.0, "courtyard_margin": 0.0},
            {"name": "JB", "edge": "bottom", "width_mm": 25.0, "height_mm": 5.0, "courtyard_margin": 0.0},
            {"name": "JL", "edge": "left", "width_mm": 5.0, "height_mm": 25.0, "courtyard_margin": 0.0},
            {"name": "JR", "edge": "right", "width_mm": 5.0, "height_mm": 25.0, "courtyard_margin": 0.0},
        ],
    )
    result = check_edge_budget(vid, db)
    assert result["feasible"] is False
    for edge in ("top", "bottom", "left", "right"):
        assert result["edges"][edge]["ok"] is False
    assert len(result["errors"]) >= 4


# ---------------------------------------------------------------------------
# Tests: corner conflicts
# ---------------------------------------------------------------------------


def test_top_left_corner_conflict_detected(tmp_path):
    """J8 on top edge and J3 on left edge both reach into the top-left corner keep-out."""
    db = _make_db(tmp_path)
    vid = _seed(
        db,
        board_w=85.0,
        board_h=56.0,
        keep_outs=[{"x_mm": 0, "y_mm": 0, "width_mm": 7, "height_mm": 7}],
        components=[
            # top-edge connector: body 51+1=52mm wide > keep-out width 7mm → claims corner
            {"name": "J8", "edge": "top", "width_mm": 51.0, "height_mm": 5.0, "courtyard_margin": 0.5},
            # left-edge connector: body 8+1=9mm tall > keep-out height 7mm → claims corner
            {"name": "J3", "edge": "left", "width_mm": 7.4, "height_mm": 8.0, "courtyard_margin": 0.5},
        ],
    )
    result = check_edge_budget(vid, db)
    assert len(result["corner_conflicts"]) >= 1
    conflict = result["corner_conflicts"][0]
    assert conflict["corner"] == "top-left"
    assert result["feasible"] is False


def test_no_corner_conflict_when_connectors_are_short(tmp_path):
    """Connectors shorter than the corner keep-out dimension do not reach the corner."""
    db = _make_db(tmp_path)
    vid = _seed(
        db,
        board_w=85.0,
        board_h=56.0,
        keep_outs=[{"x_mm": 0, "y_mm": 0, "width_mm": 7, "height_mm": 7}],
        components=[
            # top connector body 5mm wide < keep-out 7mm → does not intrude into corner zone
            {"name": "JT", "edge": "top", "width_mm": 5.0, "height_mm": 3.0, "courtyard_margin": 0.0},
            # left connector body 5mm tall < keep-out 7mm → does not intrude into corner zone
            {"name": "JL", "edge": "left", "width_mm": 3.0, "height_mm": 5.0, "courtyard_margin": 0.0},
        ],
    )
    result = check_edge_budget(vid, db)
    assert result["corner_conflicts"] == []


def test_bottom_right_corner_conflict_detected(tmp_path):
    db = _make_db(tmp_path)
    # BR corner keep-out at x=78,y=49 size 7×7 on an 85×56 board
    vid = _seed(
        db,
        board_w=85.0,
        board_h=56.0,
        keep_outs=[{"x_mm": 78.0, "y_mm": 49.0, "width_mm": 7.0, "height_mm": 7.0}],
        components=[
            # bottom connector: body reaches rightward into BR corner
            {"name": "JB", "edge": "bottom", "width_mm": 30.0, "height_mm": 5.0, "courtyard_margin": 0.0},
            # right connector: body reaches downward into BR corner
            {"name": "JR", "edge": "right", "width_mm": 5.0, "height_mm": 20.0, "courtyard_margin": 0.0},
        ],
    )
    result = check_edge_budget(vid, db)
    assert any(c["corner"] == "bottom-right" for c in result["corner_conflicts"])
    assert result["feasible"] is False


# ---------------------------------------------------------------------------
# Tests: error cases
# ---------------------------------------------------------------------------


def test_missing_board_outline_raises(tmp_path):
    db = _make_db(tmp_path)
    with pytest.raises(ValueError, match="No board_outline found"):
        check_edge_budget(999, db)


def test_components_without_geometry_reported(tmp_path):
    """Components with edge requirement but no geometry should produce an error, not a crash."""
    db = _make_db(tmp_path)
    vid = _seed(
        db,
        board_w=85.0,
        board_h=56.0,
        components=[
            # width_mm=None → no geometry written
            {"name": "J_NOGEO", "edge": "top", "width_mm": None, "height_mm": None},
        ],
    )
    result = check_edge_budget(vid, db)
    assert result["feasible"] is False
    assert any("J_NOGEO" in e for e in result["errors"])


def test_no_edge_components_is_feasible(tmp_path):
    """A board with no edge-assigned components should trivially pass."""
    db = _make_db(tmp_path)
    vid = _seed(
        db,
        board_w=85.0,
        board_h=56.0,
        components=[
            {"name": "U1", "edge": "top", "width_mm": 10.0, "height_mm": 10.0},
        ],
    )
    # overwrite requirement key to something other than 'edge'
    conn = sqlite3.connect(db)
    conn.execute("UPDATE requirements SET key='near' WHERE key='edge'")
    conn.commit()
    conn.close()
    result = check_edge_budget(vid, db)
    assert result["feasible"] is True
    for edge_data in result["edges"].values():
        assert edge_data["committed_mm"] == 0.0


# ---------------------------------------------------------------------------
# Tests: RPi-like scenario (regression)
# ---------------------------------------------------------------------------


def test_rpi4_scenario_detects_top_left_conflict(tmp_path):
    """
    Reproduces the RPi 4 layout: J8 (51mm GPIO header, top edge) and J3
    (micro-HDMI 1, left edge) both reach into the top-left corner keep-out.
    The check must flag this before any placement runs.
    """
    db = _make_db(tmp_path)
    vid = _seed(
        db,
        board_w=85.0,
        board_h=56.0,
        keep_outs=[
            {"x_mm": 0.0, "y_mm": 0.0, "width_mm": 7.0, "height_mm": 7.0},  # TL
            {"x_mm": 57.5, "y_mm": 0.0, "width_mm": 7.0, "height_mm": 7.0},  # TR
            {"x_mm": 0.0, "y_mm": 49.0, "width_mm": 7.0, "height_mm": 7.0},  # BL
            {"x_mm": 57.5, "y_mm": 49.0, "width_mm": 7.0, "height_mm": 7.0},  # BR
        ],
        components=[
            # top edge
            {"name": "J8", "edge": "top", "width_mm": 51.0, "height_mm": 5.0, "courtyard_margin": 0.5},
            # left edge
            {"name": "J1", "edge": "left", "width_mm": 9.0, "height_mm": 7.0, "courtyard_margin": 0.5},
            {"name": "J2", "edge": "left", "width_mm": 7.4, "height_mm": 5.0, "courtyard_margin": 0.5},
            {"name": "J3", "edge": "left", "width_mm": 7.4, "height_mm": 5.0, "courtyard_margin": 0.5},
            {"name": "J4", "edge": "left", "width_mm": 6.0, "height_mm": 12.0, "courtyard_margin": 0.5},
            # right edge
            {"name": "J5", "edge": "right", "width_mm": 16.0, "height_mm": 16.0, "courtyard_margin": 0.5},
            {"name": "J6", "edge": "right", "width_mm": 15.0, "height_mm": 14.0, "courtyard_margin": 0.5},
            {"name": "J7", "edge": "right", "width_mm": 15.0, "height_mm": 14.0, "courtyard_margin": 0.5},
            # bottom edge
            {"name": "J9", "edge": "bottom", "width_mm": 15.0, "height_mm": 11.0, "courtyard_margin": 0.5},
            {"name": "J10", "edge": "bottom", "width_mm": 24.0, "height_mm": 4.0, "courtyard_margin": 0.5},
            {"name": "J11", "edge": "bottom", "width_mm": 24.0, "height_mm": 4.0, "courtyard_margin": 0.5},
        ],
    )
    result = check_edge_budget(vid, db)

    # All edge budgets should pass (connectors fit)
    for edge in ("top", "bottom", "left", "right"):
        assert result["edges"][edge]["ok"] is True, f"Edge '{edge}' unexpectedly overcommitted: {result['edges'][edge]}"

    # But top-left corner conflict must be detected
    tl_conflicts = [c for c in result["corner_conflicts"] if c["corner"] == "top-left"]
    assert len(tl_conflicts) >= 1, "Expected top-left corner conflict between J8 and a left-edge connector"
    assert result["feasible"] is False
