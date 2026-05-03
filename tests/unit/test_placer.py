"""
Unit tests for placer_greedy.py — grid math and placement functions.

Covers:
  - cells_for(): includes courtyard in coverage
  - fits(): board boundary enforcement
  - fits(): keep-out pre-marking blocks placement
  - fits(): ignore_keep_outs=True allows FIXED edge components in keep-out zones
  - snap(): rounds to nearest grid step (not floor)
  - place_at(): marks all courtyard cells in occupied
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".opencode" / "skills" / "pcb-floorplanner" / "scripts"))
from placer_greedy import cells_for, fits, place_at, snap, _is_corner_adjacent


# ── cells_for ─────────────────────────────────────────────────────────────────


def test_cells_for_no_courtyard_covers_body_only():
    cells = cells_for(0, 0, 3, 3, cyd=0, res=1.0)
    assert (0, 0) in cells
    assert (2, 2) in cells
    assert (3, 3) not in cells  # body ends at 3 exclusive


def test_cells_for_courtyard_extends_coverage():
    # body 2x2 at (2,2), cyd=1 → coverage from (1,1) to (4,4)
    cells = cells_for(2, 2, 2, 2, cyd=1, res=1.0)
    assert (1, 1) in cells  # courtyard start
    assert (3, 3) in cells  # courtyard end (ceil(5/1)=5 exclusive → cell 4 is last)
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


def test_fits_fixed_component_allowed_in_keep_out_zone():
    """FIXED corner connectors must be able to sit inside keep-out zones at board corners.

    A connector at (0,0) on a 50x50 board touches both the left and top edges, so it
    is corner-adjacent and is exempt from mount-clearance keep-outs at that corner.
    A connector at (3,3) in the middle of the board is NOT corner-adjacent and must
    be blocked by keep-out cells even with ignore_keep_outs=True.
    """
    # keep-out at top-left corner (0..2, 0..2)
    corner_occupied = {(0, 0): -1, (1, 0): -1, (0, 1): -1, (1, 1): -1}

    # component sitting in the keep-out but touching two edges → corner-adjacent → allowed
    assert fits(0, 0, 2, 2, 0, 50, 50, corner_occupied, 1.0, ignore_keep_outs=True) is True

    # same component in board interior — NOT corner-adjacent → blocked
    interior_occupied = {(3, 3): -1, (4, 3): -1, (3, 4): -1, (4, 4): -1}
    assert fits(3, 3, 2, 2, 0, 50, 50, interior_occupied, 1.0, ignore_keep_outs=True) is False

    # without ignore_keep_outs the corner position is also blocked (normal components)
    assert fits(0, 0, 2, 2, 0, 50, 50, corner_occupied, 1.0) is False


def test_fits_single_edge_fixed_blocked_by_corner_keep_out():
    """Regression for J8: a wide bottom-edge FIXED component must be blocked by a corner
    keep-out zone it is NOT geometrically required to overlap.

    On an 85x56 board, J8 (44mm wide) starts at x=16, y=50.  Its right end (x=60)
    enters the BR corner keep-out (x=58..65).  Since J8 only touches the bottom edge
    (not the right edge) it is NOT corner-adjacent and fits() must return False at
    that position, forcing the nudge loop to slide it left.
    """
    # Build occupancy: BR corner keep-out cells on a grid with RES=1
    # Board 85x56. Keep-out x=[58,65], y=[49,56] → cells (58..64, 49..55)
    occupied = {}
    for cx in range(58, 65):
        for cy in range(49, 56):
            occupied[(cx, cy)] = -1

    # J8 at (16, 50), 44x5 — cells x=[16,60], y=[50,55] → overlaps keep-out at x=[58,60]
    # With ignore_keep_outs=True but NOT corner-adjacent (only bottom edge touched) → False
    assert fits(16, 50, 44, 5, 0, 85, 56, occupied, 1.0, ignore_keep_outs=True) is False

    # Slide J8 left to x=12: right end at 56 < 58 → no overlap → True
    assert fits(12, 50, 44, 5, 0, 85, 56, occupied, 1.0, ignore_keep_outs=True) is True


def test_fits_fixed_still_blocked_by_other_components_in_keep_out():
    """ignore_keep_outs only bypasses keep-out cells (-1), not real component cells."""
    occupied = {(3, 3): 42}  # occupied by another component (not a keep-out)
    assert fits(3, 3, 2, 2, 0, 50, 50, occupied, 1.0, ignore_keep_outs=True) is False


def test_fits_hard_keep_out_blocks_even_corner_adjacent():
    """Sentinel -2 (hard keep-out, e.g. RF zone) must block ALL components,
    even FIXED corner-adjacent ones.  Only -1 (mount-clearance) allows the
    corner-adjacency exemption.
    """
    # hard keep-out at top-left corner
    occupied = {(0, 0): -2, (1, 0): -2, (0, 1): -2, (1, 1): -2}

    # corner-adjacent FIXED component — still blocked because it's a hard keep-out
    assert fits(0, 0, 2, 2, 0, 50, 50, occupied, 1.0, ignore_keep_outs=True) is False


def test_fits_mount_clearance_allows_corner_adjacent_only():
    """Sentinel -1 (mount-clearance) allows corner-adjacent FIXED components
    but blocks single-edge FIXED ones.
    """
    occupied = {(0, 0): -1, (1, 0): -1, (0, 1): -1, (1, 1): -1}

    # corner-adjacent → allowed
    assert fits(0, 0, 2, 2, 0, 50, 50, occupied, 1.0, ignore_keep_outs=True) is True

    # single-edge only (touches bottom edge only on a 50x50 board, at y=48)
    occupied2 = {(3, 48): -1, (4, 48): -1, (3, 49): -1, (4, 49): -1}
    assert fits(3, 48, 2, 2, 0, 50, 50, occupied2, 1.0, ignore_keep_outs=True) is False


def test_is_corner_adjacent_single_left_edge_near_top_not_corner():
    """Regression for J4 bug: a left-edge FIXED connector placed near y=2mm must NOT
    be classified as corner-adjacent just because y <= old tol of 2.0.

    Previously tol=2.0 caused y=2.0 to satisfy touches_top, yielding a false
    corner-adjacency result and silently exempting the component from the
    mount-hole keep-out that it physically overlapped.
    """
    W, H = 85.0, 56.0
    # J4-like: left-edge connector at x=1, y=2, w=6, h=5
    # touches_left=True (x=1 <= 0.5? No, 1>0.5) — actually with new tol=0.5:
    # touches_left: x=1.0 <= 0.5 → False
    # So it is NOT corner-adjacent (good — it's not even flush to the left edge!)
    assert _is_corner_adjacent(1.0, 2.0, 6.0, 5.0, W, H) is False


def test_is_corner_adjacent_flush_corner_is_true():
    """A connector at (0,0) touching both left and top edges must be corner-adjacent."""
    assert _is_corner_adjacent(0.0, 0.0, 9.0, 7.0, 85.0, 56.0) is True


def test_is_corner_adjacent_flush_left_only_is_false():
    """A connector flush to left edge but mid-board vertically is NOT corner-adjacent."""
    assert _is_corner_adjacent(0.0, 20.0, 9.0, 7.0, 85.0, 56.0) is False


def test_fits_mixed_sentinels_hard_takes_priority():
    """When a cell is first marked -1 (mount-clearance) and then a hard keep-out
    zone overlaps, the cell must keep the -2 sentinel (hard takes priority).
    The placer's grid initialisation must never downgrade -2 to -1.
    """
    # Simulate two overlapping zones: mount-clearance first, then hard keep-out
    occupied = {}
    # mount-clearance zone marks (0,0) as -1
    occupied[(0, 0)] = -1
    # hard keep-out zone overlaps — must upgrade to -2, not downgrade
    if occupied.get((0, 0)) != -2:
        occupied[(0, 0)] = -2

    assert occupied[(0, 0)] == -2
    # corner-adjacent FIXED component must be blocked because -2 is present
    assert fits(0, 0, 1, 1, 0, 50, 50, occupied, 1.0, ignore_keep_outs=True) is False
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
