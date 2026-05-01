"""
Shared fixtures for all tests.
Provides in-memory SQLite DBs with schema applied, and a minimal seeded design.
"""

import sys
import sqlite3
import pytest
from pathlib import Path

# Make db/ and scripts/ importable
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "db"))
sys.path.insert(0, str(_ROOT / ".opencode" / "skills" / "pcb-floorplanner" / "scripts"))

SCHEMA = (_ROOT / "db" / "schema.sql").read_text()


def make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def seed_session(conn) -> tuple[int, int]:
    sid = conn.execute(
        "INSERT INTO design_sessions(prompt, model) VALUES (?,?)",
        ("test prompt", "test-model"),
    ).lastrowid
    vid = conn.execute("INSERT INTO design_versions(session_id) VALUES (?)", (sid,)).lastrowid
    conn.commit()
    return sid, vid


def seed_component(conn, version_id, name="MCU", ctype="SoC") -> int:
    cid = conn.execute(
        "INSERT INTO components(version_id, name, type) VALUES (?,?,?)",
        (version_id, name, ctype),
    ).lastrowid
    conn.commit()
    return cid


def seed_geometry(conn, comp_id, w=10.0, h=10.0, cyd=0.5) -> None:
    conn.execute(
        "INSERT INTO component_geometry(component_id, width_mm, height_mm, courtyard_margin) VALUES (?,?,?,?)",
        (comp_id, w, h, cyd),
    )
    conn.commit()


def lock_version(conn, version_id) -> None:
    conn.execute(
        "UPDATE design_versions SET status='LOCKED', hash='testhash' WHERE id=?",
        (version_id,),
    )
    conn.commit()


# ── pytest fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def db():
    """Blank in-memory DB with schema applied."""
    return make_db()


@pytest.fixture
def seeded_db():
    """In-memory DB with a DRAFT session+version, two components with geometry."""
    conn = make_db()
    _, vid = seed_session(conn)
    cid1 = seed_component(conn, vid, "U1", "SoC")
    cid2 = seed_component(conn, vid, "U2", "PMIC")
    seed_geometry(conn, cid1, w=10.0, h=10.0, cyd=0.5)
    seed_geometry(conn, cid2, w=6.0, h=6.0, cyd=0.5)
    conn.execute(
        "INSERT INTO board_outline(version_id, width_mm, height_mm, grid_resolution) VALUES (?,?,?,?)",
        (vid, 85.0, 56.0, 1.0),
    )
    conn.commit()
    return conn, vid, cid1, cid2


@pytest.fixture
def locked_db(seeded_db):
    """Seeded DB with the version LOCKED and an optimization_run created."""
    conn, vid, cid1, cid2 = seeded_db
    lock_version(conn, vid)
    rid = conn.execute(
        "INSERT INTO optimization_runs(version_id, algorithm) VALUES (?,?)",
        (vid, "test"),
    ).lastrowid
    conn.commit()
    return conn, vid, cid1, cid2, rid
