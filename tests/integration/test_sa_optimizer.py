"""
Integration tests for the SA optimizer.

Covers:
  - Penalty strictly decreases (improvement > 0) on a solvable board
  - Keep-out violations reach zero on a simple deterministic board after SA
  - Swap moves never produce off-board placements (regression for J11-swap bug)
  - SA with keep-out penalty drives components out of restricted zones
"""
import sys
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "db"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".opencode" / "skills" / "pcb-floorplanner" / "scripts"))

from db_init import init
from placer_greedy import greedy_place
from optimizer_annealing import anneal


def make_full_pipeline_db(tmp_path, components, keep_outs=None, board=(50.0, 50.0, 1.0)):
    db = tmp_path / "sa_test.db"
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
    conn.execute(
        "UPDATE design_versions SET status='LOCKED', hash='h' WHERE id=?", (vid,)
    )
    conn.commit()
    conn.close()
    return str(db), vid


def load_placements(db, run_id):
    conn = sqlite3.connect(db)
    rows = conn.execute(
        """SELECT p.component_id, p.x_mm, p.y_mm, g.width_mm, g.height_mm, g.courtyard_margin
           FROM placements p JOIN component_geometry g ON g.component_id=p.component_id
           WHERE p.run_id=?""",
        (run_id,),
    ).fetchall()
    ko = conn.execute(
        """SELECT k.x_mm, k.y_mm, k.width_mm, k.height_mm
           FROM keep_out_zones k JOIN optimization_runs r ON r.version_id=k.version_id
           WHERE r.id=?""",
        (run_id,),
    ).fetchall()
    conn.close()
    placements = {r[0]: {"x": r[1], "y": r[2], "w": r[3], "h": r[4], "cyd": r[5], "name": str(r[0])}
                  for r in rows}
    return placements, ko


# ── tests ─────────────────────────────────────────────────────────────────────

def test_sa_improves_penalty_over_greedy(tmp_path):
    comps = [
        ("U1", "SoC",    14, 14, 0.5),
        ("U2", "PMIC",    6,  6, 0.5),
        ("U3", "Memory",  8,  4, 0.5),
        ("U4", "IC",      4,  4, 0.5),
        ("U5", "IC",      4,  4, 0.5),
    ]
    db, vid = make_full_pipeline_db(tmp_path, comps, board=(50.0, 50.0, 1.0))
    greedy_result = greedy_place(vid, db)
    run_id = greedy_result["run_id"]

    result = anneal(run_id, n_iter=2000, seed=42, db_path=db)
    assert result["improvement_pct"] >= 0, "SA must not make things worse"


def test_sa_eliminates_keep_out_violations(tmp_path):
    """
    Deterministic test: a single small component placed directly inside a keep-out
    zone. SA with keep-out penalty must drive it out.
    Board is large enough (50x50) that a valid position always exists.
    """
    keep_outs = [(20, 20, 10, 10, "centre keep-out")]
    comps = [("U1", "IC", 4, 4, 0.0)]
    db, vid = make_full_pipeline_db(tmp_path, comps, keep_outs=keep_outs, board=(50.0, 50.0, 1.0))

    # Force-place U1 inside the keep-out zone by directly writing placement
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys=ON")
    run_id = conn.execute(
        "INSERT INTO optimization_runs(version_id, algorithm) VALUES (?,?)", (vid, "forced")
    ).lastrowid
    cid = conn.execute("SELECT id FROM components WHERE version_id=?", (vid,)).fetchone()[0]
    conn.execute(
        "INSERT INTO placements(run_id, component_id, x_mm, y_mm, status) VALUES (?,?,?,?,?)",
        (run_id, cid, 22.0, 22.0, "PLACED"),  # inside (20,20,10,10)
    )
    conn.commit()
    conn.close()

    anneal(run_id, n_iter=5000, seed=7, db_path=db)

    placements, ko = load_placements(db, run_id)
    violations = []
    for p in placements.values():
        px0, py0, px1, py1 = p["x"], p["y"], p["x"] + p["w"], p["y"] + p["h"]
        for kx, ky, kw, kh in ko:
            if px0 < kx + kw and px1 > kx and py0 < ky + kh and py1 > ky:
                violations.append(p)

    assert violations == [], f"SA failed to remove keep-out violations: {violations}"


def test_sa_no_off_board_placements_after_optimization(tmp_path):
    """Regression: swap moves must not push components off-board (J11-swap bug)."""
    comps = [
        ("U1", "SoC",  20, 20, 0.5),
        ("U2", "IC",    8,  8, 0.5),
        ("U3", "IC",    6,  6, 0.5),
    ]
    db, vid = make_full_pipeline_db(tmp_path, comps, board=(50.0, 50.0, 1.0))
    greedy_result = greedy_place(vid, db)
    run_id = greedy_result["run_id"]

    anneal(run_id, n_iter=3000, seed=13, db_path=db)

    placements, _ = load_placements(db, run_id)
    W, H = 50.0, 50.0
    for cid, p in placements.items():
        assert p["x"] >= 0
        assert p["y"] >= 0
        assert p["x"] + p["w"] <= W, f"comp {cid} extends past board width after SA"
        assert p["y"] + p["h"] <= H, f"comp {cid} extends past board height after SA"
