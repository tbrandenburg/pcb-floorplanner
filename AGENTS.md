# AGENTS.md — PCB Floorplanner

## Repository overview

LLM-guided PCB floorplanner. Takes a natural-language hardware description and produces a
placed, optimised PCB floorplan via an 11-step pipeline. All state lives in a single SQLite
database (`db/floorplan.db`). Steps alternate between LLM reasoning and deterministic Python.

---

## Directory structure

```text
db/                          Schema, DB helpers, and legacy schema tests
  schema.sql                 24-table SQLite schema (FKs, CHECKs, immutability triggers)
  db_init.py                 Initialise DB from schema — connect() + init() + DEFAULT_DB
  db_status.py               Print design versions and optimization runs
  db_summary.py              Print component count, violations, latest score
  test_schema.py             Legacy schema tests (also in tests/unit/test_schema.py)

.opencode/skills/pcb-floorplanner/scripts/
  db_write_session.py        Step 0 — create session + design_version
  db_write_arch.py           Step 1 — write functional blocks + connections
  db_write_bom.py            Step 2 — write components, nets, connections
  db_write_board.py          Step 3 — write board outline, keep-outs, mount holes
  db_write_geometry.py       Step 4 — write component footprint sizes
  db_write_constraints.py    Step 5 — write NEAR/FAR/FIXED/ALIGN constraints
  db_lock_version.py         Step 6 — lock design version (immutable after this)
  placer_greedy.py           Step 7 — initial placement (FIXED → NEAR → fill)
  optimizer_annealing.py     Step 8 — simulated annealing with keep-out penalty
  scorer.py                  Shared — penalty computation (overlap, constraints, nets, keep-outs)
  write_violations.py        Step 9 — persist violations + placement_score to DB
  db_read_violations.py      Step 9 — read violations for LLM review
  db_write_review.py         Step 10 — persist LLM review decision (APPROVE/REVISE/REJECT)
  render_png.py              Step 11 — cairocffi PNG render + heatmap
  render_report.py           Step 11 — HTML report with BOM, constraints, violations
  db_patch_board.py          Utility — safe board geometry patch on locked version
  db_check_edge_budget.py    Utility — validate edge connector budget before locking
  db_validate_placements.py  Utility — detect mount hole, keep-out, and overlap violations

tests/
  conftest.py                Shared fixtures: make_db(), seed_session(), seed_component(), etc.
  unit/
    test_schema.py           17 DB integrity tests (FK, UNIQUE, CHECK, immutability)
    test_scorer.py           38 scorer unit tests (keep-out, overlap, NEAR/FAR/ALIGN, HPWL)
    test_placer.py           19 placer unit tests (cells_for, fits, snap, place_at)
    test_db_write_board.py   9  input validation tests (keep-out bounds, mount hole annular ring)
    test_db_patch_board.py   4  trigger-bypass safety tests
    test_db_check_edge_budget.py  13 edge budget validation tests
    test_validate_placements.py   16 placement violation detection tests
    test_write_violations_keep_out.py  8 keep-out violation persistence tests
  integration/
    test_placer_integration.py  4 tests — boundary, keep-out, overlap invariant, large component
    test_sa_optimizer.py        3 tests — improvement, keep-out elimination, no off-board placements

output/
  floorplan.png              Current render
  heatmap.png                Occupancy density heatmap
  report.html                Full HTML report
  pcb-floorplanner.skill     Packaged OpenCode skill
```

---

## Make targets

| Target | Description |
|---|---|
| `make db-init` | Initialise `db/floorplan.db` from `schema.sql`. Asks before overwriting an existing DB. |
| `make db-verify` | Run the 17 schema integrity tests against the live DB structure. |
| `make db-status` | Show all design versions and optimization runs in the live DB. |
| `make db-summary` | Show component/net/constraint counts and latest placement score. |
| `make lint` | Run ruff over `db/`, `scripts/`, and `tests/`. Enforces F (pyflakes) and E/W errors. |
| `make test` | Run all 131 tests (unit + integration) with verbose output. |

---

## Key invariants (enforced by tests)

- **Immutability**: once a `design_version` is LOCKED, no components or constraints can be added.
  Modify cycle requires a new `design_versions` row.
- **Keep-out enforcement**: greedy placer pre-marks keep-out cells. SA optimiser penalises
  keep-out overlaps at 500×area. Both are regression-tested.
- **fits() → overlap_penalty == 0**: greedy placement using `fits()` must imply zero
  continuous-space overlap penalty in the scorer. This cross-layer invariant is tested explicitly.
- **Swap boundary safety**: SA swap moves validate board boundaries for both components before
  accepting the move (regression for the J11 off-board bug).
- **Mount hole validation**: annular ring (drill/2 + 0.5mm) must not exit the board or overlap
  non-mount-hole keep-out zones.

---

## Development rules

- All DB connections must set `PRAGMA foreign_keys = ON`.
- Never unlock a locked version — use `db_patch_board.py` for geometry corrections.
- `db_patch_board.py` drops immutability triggers, applies patch, recreates them atomically.
  If the patch body raises, triggers are recreated in the rollback path.
- The `score()` function is pure: takes `placements`, `constraints`, `nets`, optional `keep_outs`.
  The SA optimizer must always pass `keep_outs` so keep-out violations are penalised.
- Before re-running SA on an existing `run_id`, clear: `score_history`, `violations`, `placement_score`.
- Pre-push hook runs `make lint && make test`. Never skip it.

---

## Edge placement and scoring invariants (enforced by tests)

- **FIXED placer:** `placer_greedy.py` uses `ignore_keep_outs=True` for FIXED components
  so edge connectors can sit inside corner/edge keep-out zones without being nudged inward.
- **FIXED scorer penalty:** `scorer.py` penalises FIXED components proportional to their distance
  from the nearest board edge when `board=(W, H)` is passed. SA is therefore incentivised to keep
  FIXED components at board edges. Pass `board=None` for backward-compatible zero penalty.
- **Edge keep-out warning:** `db_write_board.py` emits a `WARNING` to stderr when a keep-out zone
  spans a full board edge — this pattern blocks FIXED edge connectors and is almost always wrong.
