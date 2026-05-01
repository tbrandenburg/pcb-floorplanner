-- PCB Floorplanner Schema
-- Rules:
--   1. Every table has a single INTEGER PRIMARY KEY
--   2. All FKs are explicit and non-nullable
--   3. Enums are enforced via CHECK constraints
--   4. created_at on every table — rows are otherwise immutable
--   5. The only mutable state is design_versions.status (DRAFT -> LOCKED)
--      protected by a trigger so no step can silently overwrite it
--   6. PRAGMA foreign_keys = ON must be set on every connection

-- ─────────────────────────────────────────────
-- LAYER 0: Session
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS design_sessions (
    id         INTEGER PRIMARY KEY,
    prompt     TEXT    NOT NULL,
    model      TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- One version per session. Status moves DRAFT -> LOCKED exactly once.
-- Unlocking (for constraint edits) creates a NEW version row, never mutates.
CREATE TABLE IF NOT EXISTS design_versions (
    id         INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES design_sessions(id),
    status     TEXT    NOT NULL DEFAULT 'DRAFT'
                       CHECK(status IN ('DRAFT', 'LOCKED')),
    hash       TEXT,                          -- set at lock time, NULL while DRAFT
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Prevent updating status backwards (LOCKED -> DRAFT).
-- A new constraint edit cycle must INSERT a new design_versions row.
CREATE TRIGGER IF NOT EXISTS trg_version_no_unlock
BEFORE UPDATE OF status ON design_versions
WHEN OLD.status = 'LOCKED'
BEGIN
    SELECT RAISE(ABORT, 'Cannot unlock a LOCKED version. Create a new design_version instead.');
END;

-- ─────────────────────────────────────────────
-- LAYER 0.5: Hardware Architecture
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS functional_blocks (
    id         INTEGER PRIMARY KEY,
    version_id INTEGER NOT NULL REFERENCES design_versions(id),
    name       TEXT    NOT NULL,
    category   TEXT    NOT NULL
                       CHECK(category IN ('COMPUTE','MEMORY','POWER','IO','CLOCK','DEBUG','RF','OTHER')),
    notes      TEXT,
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS block_connections (
    id             INTEGER PRIMARY KEY,
    version_id     INTEGER NOT NULL REFERENCES design_versions(id),
    from_block_id  INTEGER NOT NULL REFERENCES functional_blocks(id),
    to_block_id    INTEGER NOT NULL REFERENCES functional_blocks(id),
    interface_type TEXT    NOT NULL,   -- e.g. LPDDR4, USB3, SPI, I2C
    critical       INTEGER NOT NULL DEFAULT 0 CHECK(critical IN (0, 1)),
    notes          TEXT,
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS architecture_decisions (
    id           INTEGER PRIMARY KEY,
    version_id   INTEGER NOT NULL REFERENCES design_versions(id),
    decision     TEXT    NOT NULL,
    rationale    TEXT    NOT NULL,
    alternatives TEXT,                -- free text, comma-separated options considered
    risk         TEXT,
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Stores the rendered architecture doc (Markdown + ASCII diagram)
CREATE TABLE IF NOT EXISTS architecture_artifacts (
    id         INTEGER PRIMARY KEY,
    version_id INTEGER NOT NULL REFERENCES design_versions(id),
    file_path  TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ─────────────────────────────────────────────
-- LAYER 1: BOM + Netlist
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS components (
    id             INTEGER PRIMARY KEY,
    version_id     INTEGER NOT NULL REFERENCES design_versions(id),
    name           TEXT    NOT NULL,
    type           TEXT    NOT NULL,   -- e.g. SoC, PMIC, PHY, Connector
    package        TEXT,               -- e.g. BGA-485, QFN-48 — filled in step 3
    datasheet_url  TEXT,
    notes          TEXT,
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS nets (
    id         INTEGER PRIMARY KEY,
    version_id INTEGER NOT NULL REFERENCES design_versions(id),
    name       TEXT    NOT NULL,
    type       TEXT    NOT NULL CHECK(type IN ('PWR','GND','SIG','DIFF')),
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS net_connections (
    id           INTEGER PRIMARY KEY,
    net_id       INTEGER NOT NULL REFERENCES nets(id),
    component_id INTEGER NOT NULL REFERENCES components(id),
    pin_name     TEXT    NOT NULL,
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Key-value store for per-component layout requirements
-- e.g. ('MCU', 'max_temp_c', '85'), ('MCU', 'near', 'XTAL')
CREATE TABLE IF NOT EXISTS requirements (
    id           INTEGER PRIMARY KEY,
    component_id INTEGER NOT NULL REFERENCES components(id),
    key          TEXT    NOT NULL,
    value        TEXT    NOT NULL,
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ─────────────────────────────────────────────
-- LAYER 2: Board
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS board_outline (
    id              INTEGER PRIMARY KEY,
    version_id      INTEGER NOT NULL REFERENCES design_versions(id),
    width_mm        REAL    NOT NULL CHECK(width_mm > 0),
    height_mm       REAL    NOT NULL CHECK(height_mm > 0),
    grid_resolution REAL    NOT NULL DEFAULT 1.0 CHECK(grid_resolution > 0),
    layer_count     INTEGER NOT NULL DEFAULT 2 CHECK(layer_count > 0),
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS keep_out_zones (
    id         INTEGER PRIMARY KEY,
    version_id INTEGER NOT NULL REFERENCES design_versions(id),
    x_mm       REAL    NOT NULL,
    y_mm       REAL    NOT NULL,
    width_mm   REAL    NOT NULL CHECK(width_mm > 0),
    height_mm  REAL    NOT NULL CHECK(height_mm > 0),
    reason     TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS mount_holes (
    id           INTEGER PRIMARY KEY,
    version_id   INTEGER NOT NULL REFERENCES design_versions(id),
    x_mm         REAL    NOT NULL,
    y_mm         REAL    NOT NULL,
    diameter_mm  REAL    NOT NULL CHECK(diameter_mm > 0),
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ─────────────────────────────────────────────
-- LAYER 3: Component Geometry
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS component_geometry (
    id                INTEGER PRIMARY KEY,
    component_id      INTEGER NOT NULL UNIQUE REFERENCES components(id),
    width_mm          REAL    NOT NULL CHECK(width_mm > 0),
    height_mm         REAL    NOT NULL CHECK(height_mm > 0),
    courtyard_margin  REAL    NOT NULL DEFAULT 0.5 CHECK(courtyard_margin >= 0),
    allowed_rotations TEXT    NOT NULL DEFAULT '0,90,180,270',
    created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- UNIQUE on component_id ensures exactly one geometry row per component
-- Pin positions are relative to component origin (bottom-left)
CREATE TABLE IF NOT EXISTS pins (
    id           INTEGER PRIMARY KEY,
    component_id INTEGER NOT NULL REFERENCES components(id),
    pin_name     TEXT    NOT NULL,
    rel_x_mm     REAL    NOT NULL,
    rel_y_mm     REAL    NOT NULL,
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(component_id, pin_name)
);

-- ─────────────────────────────────────────────
-- LAYER 4: Constraints
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS constraints (
    id           INTEGER PRIMARY KEY,
    version_id   INTEGER NOT NULL REFERENCES design_versions(id),
    type         TEXT    NOT NULL CHECK(type IN ('NEAR','FAR','FIXED','ALIGN')),
    comp_a_id    INTEGER NOT NULL REFERENCES components(id),
    comp_b_id    INTEGER          REFERENCES components(id),  -- NULL for FIXED (no second component)
    min_dist_mm  REAL,
    max_dist_mm  REAL,
    weight       REAL    NOT NULL DEFAULT 1.0 CHECK(weight > 0),
    hard         INTEGER NOT NULL DEFAULT 0 CHECK(hard IN (0, 1)),
    reason       TEXT    NOT NULL,
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ─────────────────────────────────────────────
-- LAYER 6-7: Placement + Optimization
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS optimization_runs (
    id         INTEGER PRIMARY KEY,
    version_id INTEGER NOT NULL REFERENCES design_versions(id),
    algorithm  TEXT    NOT NULL DEFAULT 'greedy+annealing',
    params     TEXT,              -- JSON string of hyperparameters
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS placements (
    id           INTEGER PRIMARY KEY,
    run_id       INTEGER NOT NULL REFERENCES optimization_runs(id),
    component_id INTEGER NOT NULL REFERENCES components(id),
    x_mm         REAL    NOT NULL,
    y_mm         REAL    NOT NULL,
    rotation     INTEGER NOT NULL DEFAULT 0 CHECK(rotation IN (0, 90, 180, 270)),
    status       TEXT    NOT NULL CHECK(status IN ('FIXED','PLACED')),
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(run_id, component_id)   -- one placement per component per run
);

CREATE TABLE IF NOT EXISTS occupancy_grid (
    id           INTEGER PRIMARY KEY,
    run_id       INTEGER NOT NULL REFERENCES optimization_runs(id),
    cell_x       INTEGER NOT NULL,
    cell_y       INTEGER NOT NULL,
    component_id INTEGER NOT NULL REFERENCES components(id),
    UNIQUE(run_id, cell_x, cell_y)  -- one component per cell per run
);

CREATE TABLE IF NOT EXISTS score_history (
    id                INTEGER PRIMARY KEY,
    run_id            INTEGER NOT NULL REFERENCES optimization_runs(id),
    iteration         INTEGER NOT NULL,
    total_penalty     REAL    NOT NULL,
    constraint_penalty REAL   NOT NULL,
    overlap_penalty   REAL    NOT NULL,
    net_length_est    REAL    NOT NULL,
    created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(run_id, iteration)
);

-- ─────────────────────────────────────────────
-- LAYER 8: Scoring
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS violations (
    id              INTEGER PRIMARY KEY,
    run_id          INTEGER NOT NULL REFERENCES optimization_runs(id),
    constraint_id   INTEGER NOT NULL REFERENCES constraints(id),
    actual_dist_mm  REAL    NOT NULL,
    delta_mm        REAL    NOT NULL,   -- actual - required (negative = violation)
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS placement_score (
    id                   INTEGER PRIMARY KEY,
    run_id               INTEGER NOT NULL UNIQUE REFERENCES optimization_runs(id),
    final_penalty        REAL    NOT NULL,
    violation_count      INTEGER NOT NULL,
    hard_violation_count INTEGER NOT NULL,
    net_length_total     REAL    NOT NULL,
    created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ─────────────────────────────────────────────
-- LAYER 9: Review
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS review_notes (
    id         INTEGER PRIMARY KEY,
    run_id     INTEGER NOT NULL REFERENCES optimization_runs(id),
    note       TEXT    NOT NULL,
    action     TEXT    NOT NULL CHECK(action IN ('APPROVE','MODIFY','RERUN')),
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ─────────────────────────────────────────────
-- IMMUTABILITY TRIGGERS
-- Placed after all tables are defined so SQLite can resolve references.
-- ─────────────────────────────────────────────

-- Prevent inserting components into a LOCKED version.
CREATE TRIGGER IF NOT EXISTS trg_components_immutable
BEFORE INSERT ON components
WHEN (SELECT status FROM design_versions WHERE id = NEW.version_id) = 'LOCKED'
BEGIN
    SELECT RAISE(ABORT, 'design_version is LOCKED. Create a new version to modify components.');
END;

-- Prevent inserting constraints into a LOCKED version.
CREATE TRIGGER IF NOT EXISTS trg_constraints_immutable
BEFORE INSERT ON constraints
WHEN (SELECT status FROM design_versions WHERE id = NEW.version_id) = 'LOCKED'
BEGIN
    SELECT RAISE(ABORT, 'design_version is LOCKED. Create a new version to modify constraints.');
END;

-- ─────────────────────────────────────────────
-- LAYER 10: Render Artifacts
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS render_artifacts (
    id         INTEGER PRIMARY KEY,
    run_id     INTEGER NOT NULL REFERENCES optimization_runs(id),
    type       TEXT    NOT NULL CHECK(type IN ('SVG','PNG','HEATMAP','REPORT')),
    file_path  TEXT    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
