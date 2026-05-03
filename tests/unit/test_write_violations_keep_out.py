"""
Unit tests for keep-out violation detection in write_violations.py.
Verifies that components overlapping keep-out zones are detected, persisted
in keep_out_violations, and reflected in placement_score.keep_out_violation_count.
"""

import sys
import pytest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "db"))
sys.path.insert(0, str(_ROOT / ".opencode" / "skills" / "pcb-floorplanner" / "scripts"))

import db_init
from write_violations import write_violations


# ── helpers ───────────────────────────────────────────────────────────────────


def _setup(db_path, comp_positions, keep_out_zones):
    """
    Seed a minimal locked design with placements and keep-out zones.
    comp_positions: list of (name, x, y, w, h)
    keep_out_zones: list of (x, y, w, h, reason)
    Returns run_id.
    """
    conn = db_init.init(db_path)

    sid = conn.execute(
        "INSERT INTO design_sessions(prompt, model) VALUES (?,?)",
        ("test", "test"),
    ).lastrowid
    vid = conn.execute("INSERT INTO design_versions(session_id) VALUES (?)", (sid,)).lastrowid

    comp_ids = {}
    for name, x, y, w, h in comp_positions:
        cid = conn.execute(
            "INSERT INTO components(version_id, name, type) VALUES (?,?,?)",
            (vid, name, "IC"),
        ).lastrowid
        conn.execute(
            "INSERT INTO component_geometry(component_id, width_mm, height_mm, courtyard_margin) VALUES (?,?,?,?)",
            (cid, w, h, 0.0),
        )
        comp_ids[name] = (cid, x, y)

    conn.execute(
        "INSERT INTO board_outline(version_id, width_mm, height_mm, grid_resolution) VALUES (?,?,?,?)",
        (vid, 100.0, 100.0, 1.0),
    )
    for kx, ky, kw, kh, reason in keep_out_zones:
        conn.execute(
            "INSERT INTO keep_out_zones(version_id, x_mm, y_mm, width_mm, height_mm, reason, is_mount_clearance)"
            " VALUES (?,?,?,?,?,?,0)",
            (vid, kx, ky, kw, kh, reason),
        )

    conn.execute("UPDATE design_versions SET status='LOCKED', hash='testhash' WHERE id=?", (vid,))
    rid = conn.execute(
        "INSERT INTO optimization_runs(version_id, algorithm) VALUES (?,?)",
        (vid, "test"),
    ).lastrowid
    for name, x, y, w, h in comp_positions:
        cid, px, py = comp_ids[name]
        conn.execute(
            "INSERT INTO placements(run_id, component_id, x_mm, y_mm, status) VALUES (?,?,?,?,?)",
            (rid, cid, px, py, "PLACED"),
        )
    conn.commit()
    conn.close()
    return rid


# ── tests ─────────────────────────────────────────────────────────────────────


def test_no_violation_when_clear(tmp_path):
    """Component placed well outside keep-out zone — zero violations."""
    db_file = str(tmp_path / "test.db")
    rid = _setup(
        db_file,
        comp_positions=[("U1", 50.0, 50.0, 10.0, 10.0)],
        keep_out_zones=[(0.0, 0.0, 5.0, 5.0, "corner clearance")],
    )
    result = write_violations(rid, db_file)
    assert result["keep_out_violations"] == 0


def test_full_overlap_detected(tmp_path):
    """Component body fully inside keep-out zone — violation recorded."""
    db_file = str(tmp_path / "test.db")
    rid = _setup(
        db_file,
        comp_positions=[("U1", 2.0, 2.0, 4.0, 4.0)],  # occupies (2,2)→(6,6)
        keep_out_zones=[(0.0, 0.0, 10.0, 10.0, "full zone")],
    )
    result = write_violations(rid, db_file)
    assert result["keep_out_violations"] == 1

    conn = db_init.connect(db_file)
    row = conn.execute(
        "SELECT component_name, keep_out_reason, overlap_area_mm2 FROM keep_out_violations WHERE run_id=?",
        (rid,),
    ).fetchone()
    assert row[0] == "U1"
    assert row[1] == "full zone"
    assert row[2] == pytest.approx(16.0)  # 4×4 body fully inside zone


def test_partial_overlap_detected(tmp_path):
    """Component partially overlapping keep-out zone — correct area recorded."""
    db_file = str(tmp_path / "test.db")
    rid = _setup(
        db_file,
        comp_positions=[("J1", 8.0, 0.0, 6.0, 4.0)],  # occupies (8,0)→(14,4)
        keep_out_zones=[(0.0, 0.0, 10.0, 10.0, "corner keep-out")],
    )
    result = write_violations(rid, db_file)
    assert result["keep_out_violations"] == 1

    conn = db_init.connect(db_file)
    row = conn.execute("SELECT overlap_area_mm2 FROM keep_out_violations WHERE run_id=?", (rid,)).fetchone()
    # overlap x: min(14,10) - max(8,0) = 2mm; overlap y: min(4,10) - max(0,0) = 4mm → 8mm²
    assert row[0] == pytest.approx(8.0)


def test_multiple_components_multiple_violations(tmp_path):
    """Two components each violating a different keep-out — both recorded."""
    db_file = str(tmp_path / "test.db")
    rid = _setup(
        db_file,
        comp_positions=[
            ("U1", 1.0, 1.0, 4.0, 4.0),  # inside zone A
            ("U2", 50.0, 1.0, 4.0, 4.0),  # inside zone B
        ],
        keep_out_zones=[
            (0.0, 0.0, 10.0, 10.0, "zone A"),
            (45.0, 0.0, 15.0, 15.0, "zone B"),
        ],
    )
    result = write_violations(rid, db_file)
    assert result["keep_out_violations"] == 2


def test_placement_score_count_persisted(tmp_path):
    """keep_out_violation_count in placement_score matches detected violations."""
    db_file = str(tmp_path / "test.db")
    rid = _setup(
        db_file,
        comp_positions=[("U1", 1.0, 1.0, 4.0, 4.0)],
        keep_out_zones=[(0.0, 0.0, 10.0, 10.0, "mount clearance")],
    )
    write_violations(rid, db_file)

    conn = db_init.connect(db_file)
    row = conn.execute("SELECT keep_out_violation_count FROM placement_score WHERE run_id=?", (rid,)).fetchone()
    assert row is not None
    assert row[0] == 1


def test_touching_boundary_not_a_violation(tmp_path):
    """Component edge exactly touching keep-out boundary — not a violation (open interval)."""
    db_file = str(tmp_path / "test.db")
    rid = _setup(
        db_file,
        comp_positions=[("U1", 10.0, 0.0, 5.0, 5.0)],  # starts at x=10
        keep_out_zones=[(0.0, 0.0, 10.0, 10.0, "corner")],  # ends at x=10
    )
    result = write_violations(rid, db_file)
    assert result["keep_out_violations"] == 0


def test_fixed_component_exempt_from_mount_clearance_keep_out(tmp_path):
    """FIXED edge connectors overlapping is_mount_clearance=1 zones must NOT be violations.

    Edge connectors are intentionally placed at board corners which overlap mount-hole
    clearance zones.  SA cannot move FIXED components, so reporting them as violations
    is misleading and irresolvable.
    """
    db_file = str(tmp_path / "test.db")
    conn = db_init.init(db_file)

    sid = conn.execute("INSERT INTO design_sessions(prompt, model) VALUES (?,?)", ("test", "test")).lastrowid
    vid = conn.execute("INSERT INTO design_versions(session_id) VALUES (?)", (sid,)).lastrowid

    cid = conn.execute("INSERT INTO components(version_id, name, type) VALUES (?,?,?)", (vid, "J1", "CONN")).lastrowid
    conn.execute(
        "INSERT INTO component_geometry(component_id, width_mm, height_mm, courtyard_margin) VALUES (?,?,?,?)",
        (cid, 4.0, 4.0, 0.0),
    )
    conn.execute(
        "INSERT INTO board_outline(version_id, width_mm, height_mm, grid_resolution) VALUES (?,?,?,?)",
        (vid, 100.0, 100.0, 1.0),
    )
    # is_mount_clearance = 1 → FIXED connectors are exempt
    conn.execute(
        "INSERT INTO keep_out_zones(version_id, x_mm, y_mm, width_mm, height_mm, reason, is_mount_clearance)"
        " VALUES (?,?,?,?,?,?,1)",
        (vid, 0.0, 0.0, 10.0, 10.0, "mount hole clearance TL"),
    )
    conn.execute("UPDATE design_versions SET status='LOCKED', hash='testhash' WHERE id=?", (vid,))
    rid = conn.execute("INSERT INTO optimization_runs(version_id, algorithm) VALUES (?,?)", (vid, "test")).lastrowid
    conn.execute(
        "INSERT INTO placements(run_id, component_id, x_mm, y_mm, status) VALUES (?,?,?,?,?)",
        (rid, cid, 1.0, 1.0, "FIXED"),
    )
    conn.commit()
    conn.close()

    result = write_violations(rid, db_file)
    assert result["keep_out_violations"] == 0, (
        "FIXED components overlapping mount-clearance keep-outs must not be reported as violations"
    )


def test_fixed_single_edge_component_violates_mount_clearance_keep_out(tmp_path):
    """A FIXED component that is NOT corner-adjacent but overlaps a mount-clearance
    keep-out is a real violation and must NOT be silently suppressed.

    Regression for the root cause: write_violations.py previously used a blanket
    `continue` for any FIXED+is_mount_clearance overlap, hiding valid violations from
    single-edge connectors that drifted into corner zones.
    """
    db_file = str(tmp_path / "test.db")
    conn = db_init.init(db_file)

    sid = conn.execute("INSERT INTO design_sessions(prompt, model) VALUES (?,?)", ("test", "test")).lastrowid
    vid = conn.execute("INSERT INTO design_versions(session_id) VALUES (?)", (sid,)).lastrowid

    # J_wide: 40mm wide, placed in middle of bottom edge (y=93), touches only bottom edge
    cid = conn.execute("INSERT INTO components(version_id, name, type) VALUES (?,?,?)", (vid, "J_wide", "CONN")).lastrowid
    conn.execute(
        "INSERT INTO component_geometry(component_id, width_mm, height_mm, courtyard_margin) VALUES (?,?,?,?)",
        (cid, 40.0, 5.0, 0.0),
    )
    conn.execute(
        "INSERT INTO board_outline(version_id, width_mm, height_mm, grid_resolution) VALUES (?,?,?,?)",
        (vid, 100.0, 100.0, 1.0),
    )
    # Mount-clearance keep-out at bottom-right corner (x=90..100, y=90..100)
    conn.execute(
        "INSERT INTO keep_out_zones(version_id, x_mm, y_mm, width_mm, height_mm, reason, is_mount_clearance)"
        " VALUES (?,?,?,?,?,?,1)",
        (vid, 90.0, 90.0, 10.0, 10.0, "mount hole clearance BR"),
    )
    conn.execute("UPDATE design_versions SET status='LOCKED', hash='testhash' WHERE id=?", (vid,))
    rid = conn.execute("INSERT INTO optimization_runs(version_id, algorithm) VALUES (?,?)", (vid, "test")).lastrowid
    # Place J_wide at x=55, y=93 — body spans x=55..95, y=93..98.
    # Overlaps BR keep-out (x=90..100, y=90..100) by 5×5mm.
    # touches_right: px1=95 < 98 (100-2) → False.  touches_bottom: py1=98 >= 98 → True.
    # Only touches bottom edge → NOT corner-adjacent → must be a violation.
    conn.execute(
        "INSERT INTO placements(run_id, component_id, x_mm, y_mm, status) VALUES (?,?,?,?,?)",
        (rid, cid, 55.0, 93.0, "FIXED"),
    )
    conn.commit()
    conn.close()

    result = write_violations(rid, db_file)
    assert result["keep_out_violations"] == 1, (
        "Single-edge FIXED component overlapping a corner mount-clearance zone "
        "must be reported as a violation (not silently suppressed)"
    )
    """FIXED components overlapping a non-mount-clearance keep-out (e.g. RF zone) ARE violations."""
    db_file = str(tmp_path / "test.db")
    conn = db_init.init(db_file)

    sid = conn.execute("INSERT INTO design_sessions(prompt, model) VALUES (?,?)", ("test", "test")).lastrowid
    vid = conn.execute("INSERT INTO design_versions(session_id) VALUES (?)", (sid,)).lastrowid

    cid = conn.execute("INSERT INTO components(version_id, name, type) VALUES (?,?,?)", (vid, "J1", "CONN")).lastrowid
    conn.execute(
        "INSERT INTO component_geometry(component_id, width_mm, height_mm, courtyard_margin) VALUES (?,?,?,?)",
        (cid, 4.0, 4.0, 0.0),
    )
    conn.execute(
        "INSERT INTO board_outline(version_id, width_mm, height_mm, grid_resolution) VALUES (?,?,?,?)",
        (vid, 100.0, 100.0, 1.0),
    )
    # is_mount_clearance = 0 → FIXED connectors are NOT exempt
    conn.execute(
        "INSERT INTO keep_out_zones(version_id, x_mm, y_mm, width_mm, height_mm, reason, is_mount_clearance)"
        " VALUES (?,?,?,?,?,?,0)",
        (vid, 0.0, 0.0, 10.0, 10.0, "RF antenna no-go zone"),
    )
    conn.execute("UPDATE design_versions SET status='LOCKED', hash='testhash' WHERE id=?", (vid,))
    rid = conn.execute("INSERT INTO optimization_runs(version_id, algorithm) VALUES (?,?)", (vid, "test")).lastrowid
    conn.execute(
        "INSERT INTO placements(run_id, component_id, x_mm, y_mm, status) VALUES (?,?,?,?,?)",
        (rid, cid, 1.0, 1.0, "FIXED"),
    )
    conn.commit()
    conn.close()

    result = write_violations(rid, db_file)
    assert result["keep_out_violations"] == 1, (
        "FIXED components overlapping non-mount-clearance keep-outs must be reported as violations"
    )
