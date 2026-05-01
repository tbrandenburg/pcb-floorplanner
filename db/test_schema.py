"""
Schema integrity tests.
Each test is self-contained: uses an in-memory DB, no shared state.
Tests cover:
  - happy path inserts through the full pipeline
  - FK violations are rejected
  - UNIQUE constraints are enforced
  - CHECK constraints are enforced
  - LOCKED version blocks component + constraint inserts
  - LOCKED version cannot be set back to DRAFT
"""

import sqlite3
import pytest
from pathlib import Path

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()


def make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


# ── helpers ──────────────────────────────────────────────────────────────────


def seed_session(conn) -> tuple[int, int]:
    """Insert a session + DRAFT version. Returns (session_id, version_id)."""
    sid = conn.execute(
        "INSERT INTO design_sessions(prompt, model) VALUES (?,?)",
        ("test prompt", "gpt-4o"),
    ).lastrowid
    vid = conn.execute("INSERT INTO design_versions(session_id) VALUES (?)", (sid,)).lastrowid
    conn.commit()
    return sid, vid


def seed_component(conn, version_id, name="MCU") -> int:
    cid = conn.execute(
        "INSERT INTO components(version_id, name, type) VALUES (?,?,?)",
        (version_id, name, "SoC"),
    ).lastrowid
    conn.commit()
    return cid


def lock_version(conn, version_id):
    conn.execute(
        "UPDATE design_versions SET status='LOCKED', hash='abc123' WHERE id=?",
        (version_id,),
    )
    conn.commit()


# ── happy path ────────────────────────────────────────────────────────────────


def test_session_and_version_created():
    conn = make_db()
    _, vid = seed_session(conn)
    row = conn.execute("SELECT status FROM design_versions WHERE id=?", (vid,)).fetchone()
    assert row[0] == "DRAFT"


def test_full_pipeline_inserts():
    """Walk through every table in pipeline order with valid data."""
    conn = make_db()
    _, vid = seed_session(conn)

    # arch
    bid = conn.execute(
        "INSERT INTO functional_blocks(version_id, name, category) VALUES (?,?,?)",
        (vid, "Compute", "COMPUTE"),
    ).lastrowid
    bid2 = conn.execute(
        "INSERT INTO functional_blocks(version_id, name, category) VALUES (?,?,?)",
        (vid, "Power", "POWER"),
    ).lastrowid
    conn.execute(
        "INSERT INTO block_connections(version_id, from_block_id, to_block_id, interface_type) VALUES (?,?,?,?)",
        (vid, bid, bid2, "I2C"),
    )
    conn.execute(
        "INSERT INTO architecture_decisions(version_id, decision, rationale) VALUES (?,?,?)",
        (vid, "Use BCM2712", "Best ecosystem for RPi clone"),
    )

    # BOM
    cid = seed_component(conn, vid, "MCU")
    cid2 = seed_component(conn, vid, "PMIC")
    nid = conn.execute("INSERT INTO nets(version_id, name, type) VALUES (?,?,?)", (vid, "VDD_CORE", "PWR")).lastrowid
    conn.execute(
        "INSERT INTO net_connections(net_id, component_id, pin_name) VALUES (?,?,?)",
        (nid, cid, "VDD"),
    )
    conn.execute(
        "INSERT INTO requirements(component_id, key, value) VALUES (?,?,?)",
        (cid, "near", "XTAL"),
    )

    # board
    conn.execute(
        "INSERT INTO board_outline(version_id, width_mm, height_mm) VALUES (?,?,?)",
        (vid, 85.0, 56.0),
    )
    conn.execute(
        "INSERT INTO keep_out_zones(version_id, x_mm, y_mm, width_mm, height_mm, reason) VALUES (?,?,?,?,?,?)",
        (vid, 0, 0, 5, 5, "mounting"),
    )
    conn.execute(
        "INSERT INTO mount_holes(version_id, x_mm, y_mm, diameter_mm) VALUES (?,?,?,?)",
        (vid, 3.5, 3.5, 2.7),
    )

    # geometry
    conn.execute(
        "INSERT INTO component_geometry(component_id, width_mm, height_mm) VALUES (?,?,?)",
        (cid, 14.0, 14.0),
    )
    conn.execute(
        "INSERT INTO pins(component_id, pin_name, rel_x_mm, rel_y_mm) VALUES (?,?,?,?)",
        (cid, "VDD", 1.0, 1.0),
    )

    # constraint
    conn.execute(
        "INSERT INTO constraints(version_id, type, comp_a_id, comp_b_id, max_dist_mm, reason) VALUES (?,?,?,?,?,?)",
        (vid, "NEAR", cid, cid2, 5.0, "power routing"),
    )

    # lock
    lock_version(conn, vid)

    # optimization
    rid = conn.execute("INSERT INTO optimization_runs(version_id) VALUES (?)", (vid,)).lastrowid
    conn.execute(
        "INSERT INTO placements(run_id, component_id, x_mm, y_mm, status) VALUES (?,?,?,?,?)",
        (rid, cid, 10.0, 10.0, "PLACED"),
    )
    conn.execute(
        "INSERT INTO occupancy_grid(run_id, cell_x, cell_y, component_id) VALUES (?,?,?,?)",
        (rid, 10, 10, cid),
    )
    conn.execute(
        "INSERT INTO score_history(run_id, iteration, total_penalty, constraint_penalty, overlap_penalty, net_length_est) VALUES (?,?,?,?,?,?)",
        (rid, 0, 42.0, 10.0, 2.0, 30.0),
    )

    # scoring
    con_id = conn.execute("SELECT id FROM constraints LIMIT 1").fetchone()[0]
    conn.execute(
        "INSERT INTO violations(run_id, constraint_id, actual_dist_mm, delta_mm) VALUES (?,?,?,?)",
        (rid, con_id, 8.0, -2.0),
    )
    conn.execute(
        "INSERT INTO placement_score(run_id, final_penalty, violation_count, hard_violation_count, net_length_total) VALUES (?,?,?,?,?)",
        (rid, 42.0, 1, 0, 30.0),
    )

    # review
    conn.execute(
        "INSERT INTO review_notes(run_id, note, action) VALUES (?,?,?)",
        (rid, "Looks good", "APPROVE"),
    )

    # artifact
    conn.execute(
        "INSERT INTO render_artifacts(run_id, type, file_path) VALUES (?,?,?)",
        (rid, "PNG", "/tmp/floorplan.png"),
    )

    conn.commit()
    # sanity: count placements
    count = conn.execute("SELECT COUNT(*) FROM placements").fetchone()[0]
    assert count == 1


# ── FK enforcement ─────────────────────────────────────────────────────────────


def test_component_requires_valid_version():
    conn = make_db()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO components(version_id, name, type) VALUES (?,?,?)",
            (999, "MCU", "SoC"),
        )


def test_net_connection_requires_valid_component():
    conn = make_db()
    _, vid = seed_session(conn)
    nid = conn.execute("INSERT INTO nets(version_id, name, type) VALUES (?,?,?)", (vid, "VDD", "PWR")).lastrowid
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO net_connections(net_id, component_id, pin_name) VALUES (?,?,?)",
            (nid, 999, "VDD"),
        )


def test_placement_requires_valid_run():
    conn = make_db()
    _, vid = seed_session(conn)
    cid = seed_component(conn, vid)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO placements(run_id, component_id, x_mm, y_mm, status) VALUES (?,?,?,?,?)",
            (999, cid, 0.0, 0.0, "PLACED"),
        )


# ── UNIQUE constraints ─────────────────────────────────────────────────────────


def test_one_geometry_per_component():
    conn = make_db()
    _, vid = seed_session(conn)
    cid = seed_component(conn, vid)
    conn.execute(
        "INSERT INTO component_geometry(component_id, width_mm, height_mm) VALUES (?,?,?)",
        (cid, 5.0, 5.0),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO component_geometry(component_id, width_mm, height_mm) VALUES (?,?,?)",
            (cid, 6.0, 6.0),
        )


def test_one_placement_per_component_per_run():
    conn = make_db()
    _, vid = seed_session(conn)
    cid = seed_component(conn, vid)  # insert component BEFORE locking
    lock_version(conn, vid)
    rid = conn.execute("INSERT INTO optimization_runs(version_id) VALUES (?)", (vid,)).lastrowid
    conn.execute(
        "INSERT INTO placements(run_id, component_id, x_mm, y_mm, status) VALUES (?,?,?,?,?)",
        (rid, cid, 0.0, 0.0, "PLACED"),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO placements(run_id, component_id, x_mm, y_mm, status) VALUES (?,?,?,?,?)",
            (rid, cid, 5.0, 5.0, "PLACED"),
        )


def test_one_component_per_cell_per_run():
    conn = make_db()
    _, vid = seed_session(conn)
    cid = seed_component(conn, vid, "MCU")  # insert components BEFORE locking
    cid2 = seed_component(conn, vid, "PMIC")
    lock_version(conn, vid)
    rid = conn.execute("INSERT INTO optimization_runs(version_id) VALUES (?)", (vid,)).lastrowid
    conn.execute(
        "INSERT INTO occupancy_grid(run_id, cell_x, cell_y, component_id) VALUES (?,?,?,?)",
        (rid, 5, 5, cid),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO occupancy_grid(run_id, cell_x, cell_y, component_id) VALUES (?,?,?,?)",
            (rid, 5, 5, cid2),  # same cell, different component
        )


def test_one_score_per_run():
    conn = make_db()
    _, vid = seed_session(conn)
    lock_version(conn, vid)
    rid = conn.execute("INSERT INTO optimization_runs(version_id) VALUES (?)", (vid,)).lastrowid
    conn.execute(
        "INSERT INTO placement_score(run_id, final_penalty, violation_count, hard_violation_count, net_length_total) VALUES (?,?,?,?,?)",
        (rid, 1.0, 0, 0, 10.0),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO placement_score(run_id, final_penalty, violation_count, hard_violation_count, net_length_total) VALUES (?,?,?,?,?)",
            (rid, 2.0, 0, 0, 10.0),
        )


# ── CHECK constraints ──────────────────────────────────────────────────────────


def test_invalid_net_type_rejected():
    conn = make_db()
    _, vid = seed_session(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO nets(version_id, name, type) VALUES (?,?,?)",
            (vid, "CLK", "INVALID"),
        )


def test_invalid_constraint_type_rejected():
    conn = make_db()
    _, vid = seed_session(conn)
    cid = seed_component(conn, vid)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO constraints(version_id, type, comp_a_id, reason) VALUES (?,?,?,?)",
            (vid, "CLOSE", cid, "bad type"),
        )


def test_invalid_rotation_rejected():
    conn = make_db()
    _, vid = seed_session(conn)
    cid = seed_component(conn, vid)  # insert BEFORE locking
    lock_version(conn, vid)
    rid = conn.execute("INSERT INTO optimization_runs(version_id) VALUES (?)", (vid,)).lastrowid
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO placements(run_id, component_id, x_mm, y_mm, rotation, status) VALUES (?,?,?,?,?,?)",
            (rid, cid, 0.0, 0.0, 45, "PLACED"),
        )


def test_zero_board_dimension_rejected():
    conn = make_db()
    _, vid = seed_session(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO board_outline(version_id, width_mm, height_mm) VALUES (?,?,?)",
            (vid, 0.0, 56.0),
        )


# ── Lock / immutability ────────────────────────────────────────────────────────


def test_locked_version_blocks_new_component():
    conn = make_db()
    _, vid = seed_session(conn)
    lock_version(conn, vid)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO components(version_id, name, type) VALUES (?,?,?)",
            (vid, "NewChip", "SoC"),
        )


def test_locked_version_blocks_new_constraint():
    conn = make_db()
    _, vid = seed_session(conn)
    cid = seed_component(conn, vid)
    lock_version(conn, vid)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO constraints(version_id, type, comp_a_id, reason) VALUES (?,?,?,?)",
            (vid, "FIXED", cid, "should fail"),
        )


def test_locked_version_cannot_be_unlocked():
    conn = make_db()
    _, vid = seed_session(conn)
    lock_version(conn, vid)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE design_versions SET status='DRAFT' WHERE id=?", (vid,))
        conn.commit()


def test_modify_cycle_requires_new_version():
    """Correct pattern: create a new version row instead of unlocking."""
    conn = make_db()
    sid, vid = seed_session(conn)
    lock_version(conn, vid)
    # create new version for the next iteration
    conn.execute("INSERT INTO design_versions(session_id) VALUES (?)", (sid,)).lastrowid
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM design_versions WHERE session_id=?", (sid,)).fetchone()[0]
    assert count == 2
