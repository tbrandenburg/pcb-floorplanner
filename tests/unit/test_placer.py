"""
Unit tests for placer_greedy.py — grid math and placement functions.

Covers:
  - cells_for(): includes courtyard in coverage
  - fits(): board boundary enforcement
  - fits(): keep-out pre-marking blocks placement
  - snap(): rounds to nearest grid step (not floor)
  - place_at(): marks all courtyard cells in occupied
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".opencode" / "skills" / "pcb-floorplanner" / "scripts"))
from placer_greedy import cells_for, fits, place_at, snap


# ── cells_for ─────────────────────────────────────────────────────────────────

def test_cells_for_no_courtyard_covers_body_only():
    cells = cells_for(0, 0, 3, 3, cyd=0, res=1.0)
    assert (0, 0) in cells
    assert (2, 2) in cells
    assert (3, 3) not in cells  # body ends at 3 exclusive


def test_cells_for_courtyard_extends_coverage():
    # body 2x2 at (2,2), cyd=1 → coverage from (1,1) to (4,4)
    cells = cells_for(2, 2, 2, 2, cyd=1, res=1.0)
    assert (1, 1) in cells   # courtyard start
    assert (3, 3) in cells   # courtyard end (ceil(5/1)=5 exclusive → cell 4 is last)
    assert (0, 0) not in cells


def test_cells_for_courtyard_is_required_for_clearance():
    # Two bodies flush at x=5: body A is 0..5, body B is 5..10
    # Without courtyard they share no cell → fits() would allow them adjacent
    cells_a = set(cells_for(0, 0, 5, 5, cyd=0, res=1.0))
    cells_b = set(cells_for(5, 0, 5, 5, cyd=0, res=1.0))
    assert cells_a.isdisjoint(cells_b)

    # With cyd=0.5 their courtyards overlap at the boundary
    cells_a_cyd = set(cells_for(0, 0, 5, 5, cyd=0.5, res=1.0))
    cells_b_cyd = set(cells_for(5, 0, 5, 5, cyd=0.5, res=1.0))
    assert not cells_a_cyd.isdisjoint(cells_b_cyd)


# ── fits ──────────────────────────────────────────────────────────────────────

def test_fits_rejects_negative_x():
    assert fits(-1, 0, 5, 5, 0, 50, 50, {}, 1.0) is False


def test_fits_rejects_exceeding_board_width():
    assert fits(46, 0, 5, 5, 0, 50, 50, {}, 1.0) is False  # 46+5=51 > 50


def test_fits_rejects_exceeding_board_height():
    assert fits(0, 46, 5, 5, 0, 50, 50, {}, 1.0) is False


def test_fits_accepts_valid_position():
    assert fits(0, 0, 5, 5, 0, 50, 50, {}, 1.0) is True


def test_fits_rejects_occupied_cell():
    occupied = {(2, 2): 99}
    # component at (2,2) 3x3 will cover cell (2,2) → blocked
    assert fits(2, 2, 3, 3, 0, 50, 50, occupied, 1.0) is False


def test_fits_rejects_keep_out_cell():
    occupied = {(3, 3): -1}  # -1 = keep-out marker
    assert fits(3, 3, 2, 2, 0, 50, 50, occupied, 1.0) is False


def test_fits_allows_position_after_occupied_area():
    occupied = {(0, 0): 1, (1, 0): 1}
    # component at (2,0) should not conflict with (0,0) or (1,0)
    assert fits(2, 0, 2, 2, 0, 50, 50, occupied, 1.0) is True


# ── snap ─────────────────────────────────────────────────────────────────────

def test_snap_rounds_to_nearest_not_floor():
    # 1.6 with res=1.0 → nearest is 2.0, not floor(1.6)=1.0
    assert snap(1.6, 1.0) == 2.0


def test_snap_rounds_down_when_closer():
    assert snap(1.4, 1.0) == 1.0


def test_snap_exact_grid_value_unchanged():
    assert snap(3.0, 1.0) == 3.0


def test_snap_half_resolution():
    # Python uses banker's rounding: round(2.5)=2, round(1.5)=2 (rounds to even)
    assert snap(2.5, 1.0) == 2.0  # banker's rounding: 2.5 → 2 (even)
    assert snap(1.5, 1.0) == 2.0  # banker's rounding: 1.5 → 2 (even)


# ── place_at ─────────────────────────────────────────────────────────────────

def test_place_at_marks_body_cells():
    occupied = {}
    place_at(7, 0, 0, 3, 3, 0, occupied, 1.0)
    assert (0, 0) in occupied
    assert (2, 2) in occupied
    assert occupied[(0, 0)] == 7


def test_place_at_marks_courtyard_cells():
    occupied = {}
    place_at(7, 2, 2, 2, 2, 1, occupied, 1.0)
    # courtyard extends from (1,1) to (4,4)
    assert (1, 1) in occupied
    assert (4, 4) in occupied  # ceil((2+2+1)/1) = 5, so cells 1..4


def test_place_at_after_fits_blocks_subsequent_fit():
    occupied = {}
    place_at(1, 0, 0, 5, 5, 0, occupied, 1.0)
    # same spot now blocked
    assert fits(0, 0, 5, 5, 0, 50, 50, occupied, 1.0) is False
    # adjacent spot still free
    assert fits(5, 0, 5, 5, 0, 50, 50, occupied, 1.0) is True
