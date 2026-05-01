"""
Integration tests for the greedy placer.

Covers:
  - All components placed within board boundary
  - No component body overlaps any keep-out zone after greedy placement
  - No two components share continuous-space body overlap (overlap_penalty == 0)
  - fits()→score() consistency: greedy placement implies zero overlap_penalty
  - Keep-out cells are pre-marked and block placement (regression for the J11 bug)
"""
import sys
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "db"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".opencode" / "skills" / "pcb-floorplanner" / "scripts"))

from db_init import init
from placer_greedy import greedy_place
from scorer import score


def make_test_db(tmp_path, components, keep_outs=None, board=(50.0, 50.0, 1.0)):
    """
    Build a complete pipeline DB (session → version → board → BOM → geometry → LOCK)
    ready for greedy_place().
    """
    db = tmp_path / "placer_test.db"
    init(str(db))
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=ON")

    sid = conn.execute(
        "INSERT INTO design_sessions(prompt, model) VALUES (?,?)", ("test", "m")
    ).lastrowid
    vid = conn.execute(
        "INSERT INTO design_versions(session_id) VALUES (?)", (sid,)
    ).lastrowid
    W, H, RES = board
    conn.execute(
        "INSERT INTO board_outline(version_id, width_mm, height_mm, grid_resolution) VALUES (?,?,?,?)",
        (vid, W, H, RES),
    )
    for zo in (keep_outs or []):
        conn.execute(
            "INSERT INTO keep_out_zones(version_id, x_mm, y_mm, width_mm, height_mm, reason)"
            " VALUES (?,?,?,?,?,?)",
            (vid, zo[0], zo[1], zo[2], zo[3], zo[4]),
        )

    comp_ids = []
    for name, ctype, w, h, cyd in components:
        cid = conn.execute(
            "INSERT INTO components(version_id, name, type) VALUES (?,?,?)",
            (vid, name, ctype),
        ).lastrowid
        conn.execute(
            "INSERT INTO component_geometry(component_id, width_mm, height_mm, courtyard_margin)"
            " VALUES (?,?,?,?)",
            (cid, w, h, cyd),
        )
        comp_ids.append(cid)

    conn.execute(
        "UPDATE design_versions SET status='LOCKED', hash='h' WHERE id=?", (vid,)
    )
    conn.commit()
    conn.close()
    return str(db), vid, comp_ids


# ── helpers ───────────────────────────────────────────────────────────────────

def load_placements(db, run_id):
    conn = sqlite3.connect(db)
    rows = conn.execute(
        """SELECT p.component_id, p.x_mm, p.y_mm, g.width_mm, g.height_mm, g.courtyard_margin
           FROM placements p JOIN component_geometry g ON g.component_id=p.component_id
           WHERE p.run_id=?""",
        (run_id,),
    ).fetchall()
    conn.close()
    return {r[0]: {"x": r[1], "y": r[2], "w": r[3], "h": r[4], "cyd": r[5], "name": str(r[0])}
            for r in rows}


def load_keep_outs(db, version_id):
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT x_mm, y_mm, width_mm, height_mm FROM keep_out_zones WHERE version_id=?",
        (version_id,),
    ).fetchall()
    conn.close()
    return rows


# ── tests ─────────────────────────────────────────────────────────────────────

def test_all_components_placed_within_board(tmp_path):
    comps = [
        ("U1", "SoC",       14, 14, 0.5),
        ("U2", "PMIC",       6,  6, 0.5),
        ("U3", "Memory",     8,  4, 0.5),
        ("J1", "Connector",  5, 10, 0.5),
    ]
    db, vid, _ = make_test_db(tmp_path, comps, board=(50.0, 50.0, 1.0))
    result = greedy_place(vid, db)
    placements = load_placements(db, result["run_id"])

    W, H = 50.0, 50.0
    for cid, p in placements.items():
        assert p["x"] >= 0, f"comp {cid} x={p['x']} < 0"
        assert p["y"] >= 0, f"comp {cid} y={p['y']} < 0"
        assert p["x"] + p["w"] <= W, f"comp {cid} extends past board width"
        assert p["y"] + p["h"] <= H, f"comp {cid} extends past board height"


def test_no_component_overlaps_keep_out_zone(tmp_path):
    """Regression: greedy placer must not place components inside pre-marked keep-out cells."""
    keep_outs = [
        (0, 0, 10, 10, "corner TL"),
        (40, 0, 10, 10, "corner TR"),
        (0, 40, 10, 10, "corner BL"),
        (40, 40, 10, 10, "corner BR"),
    ]
    comps = [
        ("U1", "SoC",    12, 12, 0.5),
        ("U2", "PMIC",    6,  6, 0.5),
        ("U3", "Memory",  8,  4, 0.5),
        ("U4", "IC",      4,  4, 0.5),
        ("U5", "IC",      4,  4, 0.5),
    ]
    db, vid, _ = make_test_db(tmp_path, comps, keep_outs=keep_outs, board=(50.0, 50.0, 1.0))
    result = greedy_place(vid, db)
    placements = load_placements(db, result["run_id"])
    ko = load_keep_outs(db, vid)

    violations = []
    for cid, p in placements.items():
        px0, py0, px1, py1 = p["x"], p["y"], p["x"] + p["w"], p["y"] + p["h"]
        for kx, ky, kw, kh in ko:
            if px0 < kx + kw and px1 > kx and py0 < ky + kh and py1 > ky:
                violations.append(f"comp {cid} ({px0},{py0}) overlaps keep-out ({kx},{ky} {kw}x{kh})")

    assert violations == [], "\n".join(violations)


def test_greedy_placement_has_zero_overlap_penalty(tmp_path):
    """
    Cross-layer invariant: greedy placement (which uses fits() for each component)
    must result in zero continuous-space overlap_penalty from the scorer.
    """
    comps = [
        ("U1", "SoC",    10, 10, 0.5),
        ("U2", "PMIC",    6,  6, 0.5),
        ("U3", "Memory",  8,  4, 0.5),
        ("U4", "IC",      5,  5, 0.5),
        ("U5", "IC",      4,  4, 0.5),
        ("U6", "IC",      3,  3, 0.5),
    ]
    db, vid, _ = make_test_db(tmp_path, comps, board=(50.0, 50.0, 1.0))
    result = greedy_place(vid, db)
    placements = load_placements(db, result["run_id"])

    s = score(placements, [], [])
    assert s["overlap_penalty"] == 0.0, (
        f"Greedy-placed components must have zero overlap_penalty, got {s['overlap_penalty']}"
    )


def test_large_component_fits_within_board(tmp_path):
    """Regression for J11-class bug: component taller than usable height must not be placed off-board."""
    # Board 50x50, component 22x30 — must fit with y <= 20
    comps = [("J1", "Connector", 22, 30, 0.5)]
    db, vid, _ = make_test_db(tmp_path, comps, board=(50.0, 50.0, 1.0))
    result = greedy_place(vid, db)
    placements = load_placements(db, result["run_id"])
    p = list(placements.values())[0]
    assert p["x"] + p["w"] <= 50.0
    assert p["y"] + p["h"] <= 50.0
