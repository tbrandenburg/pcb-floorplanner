"""
Unit tests for scorer.py — penalty computation.

Covers:
  - keep_out_penalty: zero outside, proportional inside
  - overlap_penalty: zero when separated, scales with overlap area
  - overlap uses courtyard (cyd), not just body
  - NEAR constraint fires only when d > max_dist (boundary must not trigger)
  - FAR constraint fires only when d < min_dist (boundary must not trigger)
  - ALIGN constraint penalises centroid Y difference
  - FIXED constraint is currently a known gap (always 0 — documented)
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


def make_constraint(cid, ctype, a_id, b_id=None, min_d=None, max_d=None, weight=1.0, hard=0):
    return (cid, ctype, a_id, b_id, min_d, max_d, weight, hard)


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
    p = {1: make_comp(5, 5, 4, 4)}   # body: x=5..9, y=5..9
    ko = [(3, 3, 4, 4)]              # zone: x=3..7, y=3..7 → overlap 5..7 = 2x2
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


# ── FIXED constraint (known gap) ──────────────────────────────────────────────

def test_fixed_constraint_currently_no_penalty():
    """FIXED constraints currently produce zero penalty regardless of position.
    This test documents the known gap — update when FIXED scoring is implemented."""
    p = {1: make_comp(40, 28, 5, 5)}  # centre of board, not near any edge
    c = [make_constraint(1, "FIXED", 1)]
    result = score(p, c, [])
    assert result["constraint_penalty"] == 0.0  # known gap: FIXED is not penalised


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
        result["constraint_penalty"]
        + result["overlap_penalty"]
        + result["net_length_est"]
        + result["keep_out_penalty"]
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
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".opencode" / "skills" / "pcb-floorplanner" / "scripts"))
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
