"""
Unit tests for db_write_board.py — input validation.

Covers:
  - keep-out zone exceeding board boundary raises ValueError
  - mount hole annular ring outside board boundary raises ValueError
  - mount hole overlapping a non-mount-hole keep-out raises ValueError
  - mount hole inside its own mount-hole keep-out does NOT raise (correct design)
  - valid board data writes without error
  - full-edge keep-out zone emits a warning (would block FIXED edge connectors)
"""

import pytest
import sys
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "db"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".opencode" / "skills" / "pcb-floorplanner" / "scripts"))

from db_init import init
from db_write_board import write_board


def make_db_path(tmp_path):
    db = tmp_path / "test.db"
    init(str(db))
    # seed a session + version
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA foreign_keys=ON")
    sid = conn.execute("INSERT INTO design_sessions(prompt, model) VALUES (?,?)", ("t", "m")).lastrowid
    conn.execute("INSERT INTO design_versions(session_id) VALUES (?)", (sid,))
    conn.commit()
    conn.close()
    return str(db)


BOARD = {"width_mm": 85.0, "height_mm": 56.0, "grid_resolution": 1.0, "layer_count": 4}


def test_valid_board_writes_successfully(tmp_path):
    db = make_db_path(tmp_path)
    result = write_board(
        {
            "version_id": 1,
            "board": BOARD,
            "keep_out_zones": [{"x_mm": 0, "y_mm": 0, "width_mm": 7, "height_mm": 7, "reason": "mount hole TL"}],
            "mount_holes": [{"x_mm": 3.5, "y_mm": 3.5, "diameter_mm": 2.7}],
        },
        db,
    )
    assert result["keep_out_zones"] == 1
    assert result["mount_holes"] == 1


def test_keep_out_exceeding_width_raises(tmp_path):
    db = make_db_path(tmp_path)
    with pytest.raises(ValueError, match="exceeds board boundary"):
        write_board(
            {
                "version_id": 1,
                "board": BOARD,
                "keep_out_zones": [{"x_mm": 80, "y_mm": 0, "width_mm": 10, "height_mm": 5, "reason": "bad"}],
                "mount_holes": [],
            },
            db,
        )


def test_keep_out_exceeding_height_raises(tmp_path):
    db = make_db_path(tmp_path)
    with pytest.raises(ValueError, match="exceeds board boundary"):
        write_board(
            {
                "version_id": 1,
                "board": BOARD,
                "keep_out_zones": [{"x_mm": 0, "y_mm": 52, "width_mm": 5, "height_mm": 10, "reason": "bad"}],
                "mount_holes": [],
            },
            db,
        )


def test_mount_hole_annular_ring_off_board_raises(tmp_path):
    db = make_db_path(tmp_path)
    with pytest.raises(ValueError, match="annular ring exceeds board boundary"):
        write_board(
            {
                "version_id": 1,
                "board": BOARD,
                "keep_out_zones": [],
                # hole at x=0.1, radius=1.35+0.5=1.85 → extends to x=-1.75
                "mount_holes": [{"x_mm": 0.1, "y_mm": 28.0, "diameter_mm": 2.7}],
            },
            db,
        )


def test_mount_hole_overlapping_non_mount_keep_out_raises(tmp_path):
    db = make_db_path(tmp_path)
    with pytest.raises(ValueError, match="annular ring overlaps keep-out"):
        write_board(
            {
                "version_id": 1,
                "board": BOARD,
                "keep_out_zones": [
                    {"x_mm": 0, "y_mm": 0, "width_mm": 85, "height_mm": 1.5, "reason": "board edge margin top"},
                ],
                # hole at y=1.0, annular extends to y=-0.85 → overlaps board edge margin
                "mount_holes": [{"x_mm": 42.5, "y_mm": 1.0, "diameter_mm": 2.7}],
            },
            db,
        )


def test_mount_hole_inside_own_keep_out_does_not_raise(tmp_path):
    """Mount holes are intentionally co-located with their corner keep-outs."""
    db = make_db_path(tmp_path)
    result = write_board(
        {
            "version_id": 1,
            "board": BOARD,
            "keep_out_zones": [
                {"x_mm": 0, "y_mm": 0, "width_mm": 7, "height_mm": 7, "reason": "mount hole keep-out TL"},
            ],
            "mount_holes": [{"x_mm": 3.5, "y_mm": 3.5, "diameter_mm": 2.7}],
        },
        db,
    )
    assert result["mount_holes"] == 1


def test_full_edge_keep_out_emits_warning(tmp_path, capsys):
    """Keep-out spanning entire top edge should warn — it will block FIXED edge connectors."""
    db = make_db_path(tmp_path)
    result = write_board(
        {
            "version_id": 1,
            "board": BOARD,
            "keep_out_zones": [
                # spans full top edge (x=0, y=0, width=full board width)
                {"x_mm": 0, "y_mm": 0, "width_mm": 85.0, "height_mm": 8.0, "reason": "top connector zone"},
            ],
            "mount_holes": [],
        },
        db,
    )
    assert result["keep_out_zones"] == 1
    assert "warnings" in result
    assert len(result["warnings"]) == 1
    assert "full board edge" in result["warnings"][0]
    captured = capsys.readouterr()
    assert "WARNING" in captured.err


def test_corner_keep_out_does_not_warn(tmp_path, capsys):
    """Corner keep-outs for mount holes should not trigger the edge-blocking warning."""
    db = make_db_path(tmp_path)
    result = write_board(
        {
            "version_id": 1,
            "board": BOARD,
            "keep_out_zones": [
                {"x_mm": 0, "y_mm": 0, "width_mm": 7, "height_mm": 7, "reason": "mount hole corner TL"},
                {"x_mm": 78, "y_mm": 0, "width_mm": 7, "height_mm": 7, "reason": "mount hole corner TR"},
            ],
            "mount_holes": [],
        },
        db,
    )
    assert "warnings" not in result
    captured = capsys.readouterr()
    assert "WARNING" not in captured.err
