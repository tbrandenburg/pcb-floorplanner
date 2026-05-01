# PCB Floorplanner — Database Schema Reference

SQLite database: `db/floorplan.db`
Schema source: `db/schema.sql`
Init script: `db/db_init.py`

## Connection requirements

Every connection MUST set:

```python
conn.execute("PRAGMA foreign_keys = ON")
```

Without this, SQLite silently ignores FK violations.

## Design versioning model

```text
design_sessions (1) ──< design_versions (many)
design_versions status: DRAFT → LOCKED (one-way, enforced by trigger)
All input tables (components, nets, constraints, board_outline, etc.)
  reference version_id → design_versions.id
All output tables (placements, score_history, violations, etc.)
  reference run_id → optimization_runs.id
  which references version_id → design_versions.id
```

Modify cycle: create a NEW `design_versions` row (DRAFT), never unlock the old one.

---

## Tables by pipeline layer

### Layer 0 — Session

| Table | Key columns | Notes |
|---|---|---|
| `design_sessions` | id, prompt, model, created_at | One row per user request |
| `design_versions` | id, session_id, status, hash, created_at | status: DRAFT or LOCKED only |

### Layer 0.5 — Architecture

| Table | Key columns | Notes |
|---|---|---|
| `functional_blocks` | id, version_id, name, category | category CHECK: COMPUTE/MEMORY/POWER/IO/CLOCK/DEBUG/RF/OTHER |
| `block_connections` | id, version_id, from_block_id, to_block_id, interface_type, critical | critical: 0 or 1 |
| `architecture_decisions` | id, version_id, decision, rationale, alternatives, risk | ADR format |
| `architecture_artifacts` | id, version_id, file_path, created_at | Points to architecture.md |

### Layer 1 — BOM + Netlist

| Table | Key columns | Notes |
|---|---|---|
| `components` | id, version_id, name, type, package, datasheet_url, notes | package filled in Step 3 |
| `nets` | id, version_id, name, type | type CHECK: PWR/GND/SIG/DIFF |
| `net_connections` | id, net_id, component_id, pin_name | Many-to-many: net ↔ component |
| `requirements` | id, component_id, key, value | e.g. key=near value=XTAL |

### Layer 2 — Board

| Table | Key columns | Notes |
|---|---|---|
| `board_outline` | id, version_id, width_mm, height_mm, grid_resolution, layer_count | CHECK: width/height > 0 |
| `keep_out_zones` | id, version_id, x_mm, y_mm, width_mm, height_mm, reason | |
| `mount_holes` | id, version_id, x_mm, y_mm, diameter_mm | CHECK: diameter > 0 |

### Layer 3 — Geometry

| Table | Key columns | Notes |
|---|---|---|
| `component_geometry` | id, component_id, width_mm, height_mm, courtyard_margin, allowed_rotations | UNIQUE on component_id |
| `pins` | id, component_id, pin_name, rel_x_mm, rel_y_mm | UNIQUE(component_id, pin_name) |

### Layer 4 — Constraints

| Table | Key columns | Notes |
|---|---|---|
| `constraints` | id, version_id, type, comp_a_id, comp_b_id, min_dist_mm, max_dist_mm, weight, hard, reason | type CHECK: NEAR/FAR/FIXED/ALIGN. comp_b_id NULL for FIXED |

### Layer 6–7 — Placement + Optimization

| Table | Key columns | Notes |
|---|---|---|
| `optimization_runs` | id, version_id, algorithm, params, created_at | One per optimizer invocation |
| `placements` | id, run_id, component_id, x_mm, y_mm, rotation, status | UNIQUE(run_id, component_id). rotation CHECK: 0/90/180/270 |
| `occupancy_grid` | id, run_id, cell_x, cell_y, component_id | UNIQUE(run_id, cell_x, cell_y) — one component per cell |
| `score_history` | id, run_id, iteration, total_penalty, constraint_penalty, overlap_penalty, net_length_est | UNIQUE(run_id, iteration) |

### Layer 8 — Scoring

| Table | Key columns | Notes |
|---|---|---|
| `violations` | id, run_id, constraint_id, actual_dist_mm, delta_mm | delta_mm < 0 means violated |
| `placement_score` | id, run_id, final_penalty, violation_count, hard_violation_count, net_length_total | UNIQUE on run_id |

### Layer 9 — Review

| Table | Key columns | Notes |
|---|---|---|
| `review_notes` | id, run_id, note, action, created_at | action CHECK: APPROVE/MODIFY/RERUN |

### Layer 10 — Artifacts

| Table | Key columns | Notes |
|---|---|---|
| `render_artifacts` | id, run_id, type, file_path, created_at | type CHECK: SVG/PNG/HEATMAP/REPORT |

---

## Integrity guarantees

| Guarantee | Mechanism |
|---|---|
| No orphaned placements | FK placements.component_id → components.id |
| One placement per component per run | UNIQUE(run_id, component_id) |
| One component per grid cell | UNIQUE(run_id, cell_x, cell_y) |
| One geometry per component | UNIQUE on component_geometry.component_id |
| One final score per run | UNIQUE on placement_score.run_id |
| Valid enum values | CHECK on every categorical column |
| No zero-size board | CHECK(width_mm > 0) |
| Locked version immutable | Trigger trg_components_immutable + trg_constraints_immutable |
| No unlock of locked version | Trigger trg_version_no_unlock |
