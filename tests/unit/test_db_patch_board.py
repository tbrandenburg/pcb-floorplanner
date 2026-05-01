"""
Unit tests for db_patch_board.py — trigger-bypass safety.

Covers:
  - patch updates board geometry on a locked version
  - immutability triggers are active after a successful patch
  - immutability triggers are recreated even when the patch body raises
  - patching a non-existent version_id raises ValueError
"""
import pytest
import sys
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "db"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".opencode" / "skills" / "pcb-floorplanner" / "scripts"))

from db_init import init
from db_patch_board import patch_board


def make_locked_db(tmp_path):
    db = tmp_path / "patch_test.db"
    init(str(db))
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=ON")
    sid = conn.execute(
        "INSERT INTO design_sessions(prompt, model) VALUES (?,?)", ("t", "m")
    ).lastrowid
    vid = conn.execute(
        "INSERT INTO design_versions(session_id) VALUES (?)", (sid,)
    ).lastrowid
    conn.execute(
        "INSERT INTO board_outline(version_id, width_mm, height_mm) VALUES (?,?,?)",
        (vid, 85.0, 56.0),
    )
    conn.execute(
        "UPDATE design_versions SET status='LOCKED', hash='h' WHERE id=?", (vid,)
    )
    conn.commit()
    conn.close()
    return str(db), vid


def test_patch_updates_board_dimensions(tmp_path):
    db, vid = make_locked_db(tmp_path)
    result = patch_board(vid, {"board": {"width_mm": 100.0}}, db)
    assert result["changes"] >= 1
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT width_mm FROM board_outline WHERE version_id=?", (vid,)).fetchone()
    conn.close()
    assert row[0] == 100.0


def test_triggers_active_after_successful_patch(tmp_path):
    """Immutability triggers must be restored — adding a component after patch must fail."""
    db, vid = make_locked_db(tmp_path)
    patch_board(vid, {"board": {"width_mm": 90.0}}, db)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys=ON")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO components(version_id, name, type) VALUES (?,?,?)",
            (vid, "ShouldFail", "SoC"),
        )
        conn.commit()
    conn.close()


def test_triggers_restored_after_patch_exception(tmp_path):
    """Even when patch raises, triggers must survive."""
    db, vid = make_locked_db(tmp_path)
    with pytest.raises(Exception):
        # Pass a field name that doesn't exist in board_outline → UPDATE sets nothing,
        # but we force an error by patching a keep-out with missing key
        patch_board(vid, {"keep_out_zones": {"replace": False, "zones": [{"x_mm": 0}]}}, db)

    # Triggers must still block new components
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA foreign_keys=ON")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO components(version_id, name, type) VALUES (?,?,?)",
            (vid, "ShouldFail", "SoC"),
        )
        conn.commit()
    conn.close()


def test_patch_nonexistent_version_raises(tmp_path):
    db, _ = make_locked_db(tmp_path)
    with pytest.raises(ValueError, match="not found"):
        patch_board(999, {"board": {"width_mm": 90.0}}, db)
