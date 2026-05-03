"""
Unit tests for scorer.py — penalty computation.

Covers:
  - keep_out_penalty: zero outside, proportional inside
  - overlap_penalty: zero when separated, scales with overlap area
  - overlap uses courtyard (cyd), not just body
  - NEAR constraint fires only when d > max_dist (boundary must not trigger)
  - FAR constraint fires only when d < min_dist (boundary must not trigger)
  - ALIGN constraint penalises centroid Y difference
  - FIXED constraint penalises distance from nearest board edge when board dims provided
  - FIXED constraint with no board dims → zero penalty (graceful degradation)
  - total_penalty = sum of all terms (no silent drops)
  - fits()-then-score consistency: greedy placement implies zero overlap_penalty
"""

import math
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".opencode" / "skills" / "pcb-floorplanner" / "scripts"))
from scorer import score, keep_out_penalty


# ── helpers ───────────────────────────────────────────────────────────────────


def make_comp(x, y, w, h, cyd=0.0, name="C"):
    return {"x": x, "y": y, "w": w, "h": h, "cyd": cyd, "name": name}


def make_constraint(cid, ctype, a_id, b_id=None, min_d=None, max_d=None, weight=1.0, hard=0, edge=None):
    t = (cid, ctype, a_id, b_id, min_d, max_d, weight, hard)
    return t + (edge,) if edge is not None else t


# ── keep_out_penalty ──────────────────────────────────────────────────────────


def test_keep_out_penalty_zero_when_outside():
    p = {1: make_comp(10, 10, 5, 5)}
    ko = [(0, 0, 7, 7)]  # zone ends at x=7; component starts at x=10
    assert keep_out_penalty(p, ko) == 0.0


def test_keep_out_penalty_positive_when_overlapping():
    p = {1: make_comp(3, 3, 5, 5)}
    ko = [(0, 0, 7, 7)]
    penalty = keep_out_penalty(p, ko)
    assert penalty > 0.0


def test_keep_out_penalty_proportional_to_overlap_area():
    # 2x2 overlap → 500 * 4 = 2000
    p = {1: make_comp(5, 5, 4, 4)}  # body: x=5..9, y=5..9
    ko = [(3, 3, 4, 4)]  # zone: x=3..7, y=3..7 → overlap 5..7 = 2x2
    penalty = keep_out_penalty(p, ko)
    assert math.isclose(penalty, 500.0 * 2.0 * 2.0, rel_tol=1e-6)


def test_keep_out_penalty_touching_edge_is_zero():
    # component starts exactly where zone ends — touching, not overlapping
    p = {1: make_comp(7.0, 0, 3, 3)}
    ko = [(0, 0, 7.0, 7.0)]
    assert keep_out_penalty(p, ko) == 0.0


def test_keep_out_penalty_multiple_components_summed():
    p = {
        1: make_comp(1, 1, 2, 2),
        2: make_comp(3, 3, 2, 2),
    }
    ko = [(0, 0, 10, 10)]
    penalty = keep_out_penalty(p, ko)
    # both fully inside zone → each is 2x2 body × 500
    assert penalty == 500.0 * 4.0 + 500.0 * 4.0


def test_fixed_exempt_from_mount_clearance_keep_out():
    """FIXED component is exempt from is_mount_clearance=1 keep-out (backwards compat, no board)."""
    p = {1: make_comp(1, 1, 4, 4)}
    ko = [(0, 0, 10, 10, 1)]  # is_mount_clearance=1
    penalty = keep_out_penalty(p, ko, fixed_ids={1})
    assert penalty == 0.0, "FIXED component must be exempt from mount-clearance keep-out (no board → compat)"


def test_fixed_not_exempt_from_non_mount_keep_out():
    """FIXED component is NOT exempt from is_mount_clearance=0 keep-out (e.g. RF zone)."""
    p = {1: make_comp(1, 1, 4, 4)}
    ko = [(0, 0, 10, 10, 0)]  # is_mount_clearance=0
    penalty = keep_out_penalty(p, ko, fixed_ids={1})
    assert penalty > 0.0, "FIXED component must NOT be exempt from RF/non-mount keep-out"


# ── corner-adjacency exemption (new invariant) ────────────────────────────────


def test_corner_adjacent_fixed_exempt_from_mount_clearance():
    """Corner-adjacent FIXED component (touches two edges) is exempt from mount-clearance."""
    # Board 100x100, component at top-left corner touching both left and top edges
    p = {1: make_comp(0, 0, 5, 5)}  # body [0..5, 0..5] — touches left (x=0) and top (y=0)
    ko = [(0, 0, 7, 7, 1)]  # mount-clearance zone at TL corner
    penalty = keep_out_penalty(p, ko, fixed_ids={1}, board=(100, 100))
    assert penalty == 0.0, "Corner-adjacent FIXED connector must be exempt from corner mount-clearance"


def test_single_edge_fixed_not_exempt_from_mount_clearance():
    """Single-edge FIXED component (touches only bottom edge) is NOT exempt from corner keep-out.

    This is the J8 GPIO header scenario: a 44mm header sits along the bottom edge but
    its right end drifts into the bottom-right corner mount-clearance zone.  Since J8
    only touches the bottom edge (not the right edge) it is not corner-adjacent and
    must be penalised so the SA optimiser slides it clear.
    """
    # Board 100x50. Component spans x=30..74 (44mm), y=45..50 (5mm).
    # Touches bottom (y+h=50 >= 50-tol=48) but NOT right (x+w=74 < 100-2=98).
    p = {1: make_comp(30, 45, 44, 5)}
    # Bottom-right corner mount-clearance zone
    ko = [(93, 43, 7, 7, 1)]  # zone x=93..100, y=43..50
    penalty = keep_out_penalty(p, ko, fixed_ids={1}, board=(100, 50))
    # Component body x=30..74 does NOT overlap ko x=93..100, so penalty is 0 regardless.
    # Use a zone that J8's right end actually overlaps:
    ko2 = [(70, 43, 7, 7, 1)]  # zone x=70..77, y=43..50 — overlaps x=70..74
    penalty2 = keep_out_penalty(p, ko2, fixed_ids={1}, board=(100, 50))
    assert penalty2 > 0.0, (
        "Single-edge FIXED component must NOT be exempt from corner mount-clearance keep-out"
    )


def test_single_edge_fixed_bottom_touches_right_corner_is_penalised():
    """Regression: FIXED bottom-edge component overlapping a BR corner keep-out is penalised."""
    # Exact J8 scenario scaled down: 85x56 board, J8 at x=16,y=50 size 44x5
    # BR keep-out at x=58,y=49 size 7x7 (centred on mount hole at 61.5,52.5)
    p = {1: make_comp(16, 50, 44, 5)}   # x=[16,60], y=[50,55]
    ko = [(58, 49, 7, 7, 1)]            # x=[58,65], y=[49,56]
    # body overlaps ko at x=[58,60], y=[50,55] → 2×5 = 10mm²
    penalty = keep_out_penalty(p, ko, fixed_ids={1}, board=(85, 56))
    assert math.isclose(penalty, 500.0 * 2.0 * 5.0, rel_tol=1e-6), (
        f"Expected 5000.0 penalty for J8-style overlap, got {penalty}"
    )


# ── overlap_penalty ───────────────────────────────────────────────────────────


def test_overlap_penalty_zero_when_separated():
    p = {
        1: make_comp(0, 0, 5, 5, cyd=0),
        2: make_comp(6, 0, 5, 5, cyd=0),
    }
    result = score(p, [], [])
    assert result["overlap_penalty"] == 0.0


def test_overlap_penalty_positive_when_bodies_overlap():
    p = {
        1: make_comp(0, 0, 5, 5, cyd=0),
        2: make_comp(3, 0, 5, 5, cyd=0),  # overlaps by 2mm in x
    }
    result = score(p, [], [])
    assert result["overlap_penalty"] > 0.0


def test_overlap_penalty_uses_courtyard_not_body():
    # bodies don't touch, but courtyards do
    p = {
        1: make_comp(0, 0, 5, 5, cyd=1.0),
        2: make_comp(6.5, 0, 5, 5, cyd=1.0),
        # body gap = 1.5mm, but courtyard extends 1mm each side → gap = -0.5mm
    }
    result = score(p, [], [])
    assert result["overlap_penalty"] > 0.0


def test_overlap_penalty_scales_with_area():
    # 2×2 body overlap → 100 * 2 * 2 = 400
    p = {
        1: make_comp(0, 0, 5, 5, cyd=0),
        2: make_comp(3, 0, 5, 5, cyd=0),  # overlap_x = 2, overlap_y = 5
    }
    result = score(p, [], [])
    assert math.isclose(result["overlap_penalty"], 100.0 * 2.0 * 5.0, rel_tol=1e-6)


# ── NEAR / FAR constraints ────────────────────────────────────────────────────


def test_near_no_penalty_when_within_max():
    p = {1: make_comp(0, 0, 2, 2), 2: make_comp(3, 0, 2, 2)}
    # centroids: (1,1) and (4,1) → dist = 3.0; max_dist = 5.0
    c = [make_constraint(1, "NEAR", 1, 2, max_d=5.0)]
    result = score(p, c, [])
    assert result["constraint_penalty"] == 0.0


def test_near_penalty_fires_when_exceeding_max():
    p = {1: make_comp(0, 0, 2, 2), 2: make_comp(10, 0, 2, 2)}
    # centroids: (1,1) and (11,1) → dist = 10.0; max_dist = 5.0
    c = [make_constraint(1, "NEAR", 1, 2, max_d=5.0, weight=2.0)]
    result = score(p, c, [])
    assert result["constraint_penalty"] == pytest.approx(2.0 * (10.0 - 5.0), rel=1e-4)


def test_near_no_penalty_at_exact_boundary():
    p = {1: make_comp(0, 0, 0, 0), 2: make_comp(5, 0, 0, 0)}
    # centroids exactly 5.0 apart; max_dist = 5.0 → delta = 0
    c = [make_constraint(1, "NEAR", 1, 2, max_d=5.0)]
    result = score(p, c, [])
    assert result["constraint_penalty"] == 0.0


def test_far_no_penalty_when_beyond_min():
    p = {1: make_comp(0, 0, 2, 2), 2: make_comp(20, 0, 2, 2)}
    c = [make_constraint(1, "FAR", 1, 2, min_d=5.0)]
    result = score(p, c, [])
    assert result["constraint_penalty"] == 0.0


def test_far_penalty_fires_when_below_min():
    p = {1: make_comp(0, 0, 2, 2), 2: make_comp(2, 0, 2, 2)}
    # centroids: (1,1) and (3,1) → dist = 2.0; min_dist = 5.0
    c = [make_constraint(1, "FAR", 1, 2, min_d=5.0, weight=3.0)]
    result = score(p, c, [])
    assert result["constraint_penalty"] == pytest.approx(3.0 * (5.0 - 2.0), rel=1e-4)


def test_far_no_penalty_at_exact_boundary():
    p = {1: make_comp(0, 0, 0, 0), 2: make_comp(5, 0, 0, 0)}
    c = [make_constraint(1, "FAR", 1, 2, min_d=5.0)]
    result = score(p, c, [])
    assert result["constraint_penalty"] == 0.0


# ── ALIGN constraint ──────────────────────────────────────────────────────────


def test_align_no_penalty_when_same_y_centroid():
    p = {1: make_comp(0, 0, 4, 4), 2: make_comp(10, 0, 4, 4)}
    c = [make_constraint(1, "ALIGN", 1, 2)]
    result = score(p, c, [])
    assert result["constraint_penalty"] == 0.0


def test_align_penalty_when_y_differs():
    p = {1: make_comp(0, 0, 4, 4), 2: make_comp(10, 10, 4, 4)}
    # centroid Y: 2 vs 12 → delta = 10; penalty = weight * 10 * 0.1
    c = [make_constraint(1, "ALIGN", 1, 2, weight=1.0)]
    result = score(p, c, [])
    assert result["constraint_penalty"] == pytest.approx(1.0 * 10.0 * 0.1, rel=1e-4)


# ── FIXED constraint ─────────────────────────────────────────────────────────


def test_fixed_constraint_no_penalty_at_board_edge():
    """Component centroid on the top edge (y_c == 0) → min dist from edge == 0 → no penalty."""
    # Board 100x100. Component 4x0 so centroid at (42, 0) — exactly on top edge.
    p = {1: make_comp(40, 0, 4, 0)}
    c = [make_constraint(1, "FIXED", 1, weight=1.0)]
    result = score(p, c, [], board=(100, 100))
    assert result["constraint_penalty"] == 0.0


def test_fixed_constraint_penalises_component_far_from_edge():
    """Component in the centre of the board gets a large FIXED penalty."""
    # Board 100x100. Component centroid at (50, 50) → 50mm from every edge.
    p = {1: make_comp(48, 48, 4, 4)}
    c = [make_constraint(1, "FIXED", 1, weight=1.0)]
    result = score(p, c, [], board=(100, 100))
    # centroid (50,50): min(50, 50, 50, 50) = 50 → penalty = 1.0 * 50 = 50
    assert result["constraint_penalty"] == pytest.approx(50.0)


def test_fixed_constraint_near_bottom_edge_lower_penalty_than_centre():
    """Component near the bottom edge should have lower penalty than one in the centre."""
    board = (100, 100)
    c = [make_constraint(1, "FIXED", 1, weight=1.0)]
    p_centre = {1: make_comp(48, 48, 4, 4)}  # centroid (50, 50) → dist 50
    p_edge = {1: make_comp(48, 94, 4, 4)}  # centroid (50, 96) → dist min(50,50,96,4)=4
    centre_penalty = score(p_centre, c, [], board=board)["constraint_penalty"]
    edge_penalty = score(p_edge, c, [], board=board)["constraint_penalty"]
    assert edge_penalty < centre_penalty


def test_fixed_constraint_without_board_dims_is_zero():
    """When board dims are not provided, FIXED penalty degrades gracefully to 0."""
    p = {1: make_comp(40, 28, 5, 5)}
    c = [make_constraint(1, "FIXED", 1)]
    result = score(p, c, [])  # no board kwarg
    assert result["constraint_penalty"] == 0.0


def test_fixed_constraint_violation_recorded_when_far_from_edge():
    """Component > 5mm from any edge should appear in violations list."""
    p = {1: make_comp(48, 48, 4, 4)}  # centroid (50,50), dist=50
    c = [make_constraint(1, "FIXED", 1, weight=1.0)]
    result = score(p, c, [], board=(100, 100))
    assert len(result["violations"]) == 1
    con_id, actual, delta, hard_flag = result["violations"][0]
    assert actual == pytest.approx(50.0)
    assert delta == pytest.approx(45.0)  # 50 - 5 threshold


def test_fixed_violation_carries_hard_flag():
    """Violation 4-tuple must carry the hard flag from the constraint."""
    p = {1: make_comp(48, 48, 4, 4)}
    c_soft = [make_constraint(1, "FIXED", 1, weight=1.0, hard=0)]
    c_hard = [make_constraint(1, "FIXED", 1, weight=1.0, hard=1)]
    soft_violations = score(p, c_soft, [], board=(100, 100))["violations"]
    hard_violations = score(p, c_hard, [], board=(100, 100))["violations"]
    assert soft_violations[0][3] is False
    assert hard_violations[0][3] is True


def test_fixed_hard1_penalty_higher_than_soft():
    """hard=1 FIXED constraint must produce substantially larger penalty than hard=0
    when the component is far from the board edge, so SA is strongly driven to the edge."""
    p = {1: make_comp(48, 48, 4, 4)}  # centroid (50,50), 50mm from every edge
    c_soft = [make_constraint(1, "FIXED", 1, weight=1.0, hard=0)]
    c_hard = [make_constraint(1, "FIXED", 1, weight=1.0, hard=1)]
    soft_penalty = score(p, c_soft, [], board=(100, 100))["constraint_penalty"]
    hard_penalty = score(p, c_hard, [], board=(100, 100))["constraint_penalty"]
    assert hard_penalty > soft_penalty * 10, (
        f"hard=1 FIXED penalty ({hard_penalty}) should be >10x soft ({soft_penalty})"
    )


# ── FIXED edge-specific penalty ───────────────────────────────────────────────


def test_fixed_edge_right_zero_when_on_right():
    """Component centroid at x=W → dist from right edge = 0 → no penalty."""
    # Board 100x100, component 4x4 placed so centroid at x=100 (right edge).
    p = {1: make_comp(98, 48, 4, 4)}  # centroid (100, 50)
    c = [make_constraint(1, "FIXED", 1, weight=1.0, edge="right")]
    result = score(p, c, [], board=(100, 100))
    assert result["constraint_penalty"] == pytest.approx(0.0)


def test_fixed_edge_right_penalises_left_side():
    """Component on the left side with edge=right gets max penalty."""
    p = {1: make_comp(0, 48, 4, 4)}  # centroid (2, 50) → dist from right = 98
    c = [make_constraint(1, "FIXED", 1, weight=1.0, edge="right")]
    result = score(p, c, [], board=(100, 100))
    # dist = 100 - 2 = 98 → penalty = 1.0 * 98
    assert result["constraint_penalty"] == pytest.approx(98.0)


def test_fixed_edge_top_zero_when_on_top():
    """Component centroid at y=0 with edge=top → no penalty."""
    p = {1: make_comp(40, 0, 4, 0)}  # centroid (42, 0)
    c = [make_constraint(1, "FIXED", 1, weight=1.0, edge="top")]
    result = score(p, c, [], board=(100, 100))
    assert result["constraint_penalty"] == pytest.approx(0.0)


def test_fixed_edge_bottom_zero_when_on_bottom():
    """Component centroid at y=H with edge=bottom → no penalty."""
    p = {1: make_comp(40, 100, 4, 0)}  # centroid (42, 100)
    c = [make_constraint(1, "FIXED", 1, weight=1.0, edge="bottom")]
    result = score(p, c, [], board=(100, 100))
    assert result["constraint_penalty"] == pytest.approx(0.0)


def test_fixed_edge_left_zero_when_on_left():
    """Component centroid at x=0 with edge=left → no penalty."""
    p = {1: make_comp(0, 48, 0, 4)}  # centroid (0, 50)
    c = [make_constraint(1, "FIXED", 1, weight=1.0, edge="left")]
    result = score(p, c, [], board=(100, 100))
    assert result["constraint_penalty"] == pytest.approx(0.0)


def test_fixed_edge_right_vs_no_edge_differ_for_left_component():
    """edge=right and no-edge differ when component is on the left side.

    No-edge uses nearest edge (left, dist=2). edge=right uses right edge (dist=98).
    So edge-specific penalty must be much larger."""
    p = {1: make_comp(0, 48, 4, 4)}  # centroid (2, 50)
    c_no_edge = [make_constraint(1, "FIXED", 1, weight=1.0)]
    c_right = [make_constraint(1, "FIXED", 1, weight=1.0, edge="right")]
    pen_no_edge = score(p, c_no_edge, [], board=(100, 100))["constraint_penalty"]
    pen_right = score(p, c_right, [], board=(100, 100))["constraint_penalty"]
    assert pen_right > pen_no_edge * 10


def test_near_violation_carries_hard_flag():
    """NEAR violation 4-tuple must carry the hard flag."""
    p = {1: make_comp(0, 0, 2, 2), 2: make_comp(20, 0, 2, 2)}
    c = [make_constraint(1, "NEAR", 1, 2, max_d=5.0, hard=1)]
    violations = score(p, c, [])["violations"]
    assert len(violations) == 1
    assert violations[0][3] is True


def test_far_violation_carries_hard_flag():
    """FAR violation 4-tuple must carry the hard flag."""
    p = {1: make_comp(0, 0, 2, 2), 2: make_comp(1, 0, 2, 2)}
    c = [make_constraint(1, "FAR", 1, 2, min_d=20.0, hard=0)]
    violations = score(p, c, [])["violations"]
    assert len(violations) == 1
    assert violations[0][3] is False


# ── net length (HPWL) ─────────────────────────────────────────────────────────


def test_net_length_is_hpwl_not_sum_of_pairs():
    # Three components on one net in a line: (0,0), (5,0), (10,0)
    # HPWL = (10-0) + (0-0) = 10, NOT 5+5+10=20
    p = {
        1: make_comp(0, 0, 0, 0),
        2: make_comp(5, 0, 0, 0),
        3: make_comp(10, 0, 0, 0),
    }
    nets = [(1, 1), (1, 2), (1, 3)]
    result = score(p, [], nets)
    assert math.isclose(result["net_length_est"], 10.0, rel_tol=1e-6)


def test_net_length_single_component_is_zero():
    p = {1: make_comp(5, 5, 4, 4)}
    nets = [(1, 1)]
    result = score(p, [], nets)
    assert result["net_length_est"] == 0.0


# ── total = sum of all terms ──────────────────────────────────────────────────


def test_total_penalty_is_sum_of_all_terms():
    p = {
        1: make_comp(0, 0, 5, 5, cyd=0),
        2: make_comp(3, 0, 5, 5, cyd=0),  # overlap
    }
    ko = [(0, 0, 2, 2)]  # comp 1 partially in keep-out
    nets = [(1, 1), (1, 2)]
    c = [make_constraint(1, "FAR", 1, 2, min_d=20.0, weight=1.0)]
    result = score(p, c, nets, keep_outs=ko)
    expected = (
        result["constraint_penalty"] + result["overlap_penalty"] + result["net_length_est"] + result["keep_out_penalty"]
    )
    assert math.isclose(result["total_penalty"], expected, rel_tol=1e-9)


# ── fits() → overlap_penalty consistency ─────────────────────────────────────


def test_fits_implies_zero_overlap_penalty():
    """
    Core invariant: if greedy placer's fits() returns True for a sequence of
    placements (no cell shared), the continuous-space overlap_penalty must be 0.

    Uses placer's cells_for() and fits() directly to build a placement, then
    scores it — cross-layer regression test.
    """
    sys.path.insert(
        0, str(Path(__file__).resolve().parents[2] / ".opencode" / "skills" / "pcb-floorplanner" / "scripts")
    )
    from placer_greedy import fits, place_at

    W, H, RES = 50.0, 50.0, 1.0
    occupied = {}
    placements = {}

    comps = [
        (1, 0.0, 0.0, 8.0, 8.0, 0.5),
        (2, 0.0, 0.0, 6.0, 6.0, 0.5),
        (3, 0.0, 0.0, 4.0, 4.0, 0.5),
    ]

    # place greedily row by row
    for cid, _x, _y, w, h, cyd in comps:
        for row_y in range(0, int(H)):
            for col_x in range(0, int(W)):
                x, y = float(col_x), float(row_y)
                if fits(x, y, w, h, cyd, W, H, occupied, RES):
                    placements[cid] = make_comp(x, y, w, h, cyd)
                    place_at(cid, x, y, w, h, cyd, occupied, RES)
                    break
            if cid in placements:
                break

    assert len(placements) == 3, "All components must be placed"

    result = score(placements, [], [])
    assert result["overlap_penalty"] == 0.0, (
        f"fits()=True placements must produce zero overlap_penalty, got {result['overlap_penalty']}"
    )
