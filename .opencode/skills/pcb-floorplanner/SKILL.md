---
name: pcb-floorplanner
description: >
  Generate a PCB floorplan from a free-text hardware description using an
  LLM-guided, SQLite-backed pipeline. Steps alternate between LLM reasoning
  (architecture, BOM capture, constraint derivation, review) and deterministic
  Python (placement, optimisation, scoring, rendering). Use when asked to:
  create a PCB floorplan, lay out a board, place components for a hardware
  design, generate a floorplan for any embedded/SBC/IoT/power electronics
  project. Triggers: "create a floorplan for", "lay out a board for",
  "PCB placement for", "floorplan a", "generate a board layout".
---

# PCB Floorplanner

Generates a constraint-driven PCB floorplan from a single prompt.
Architecture: LLM steps reason + call helper scripts to write structured data
into a shared SQLite DB. Python steps run deterministic algorithms on that data.
No data is passed between steps as arguments — the DB is the contract.

## Prerequisites

```bash
# From project root (workspace/floorplan/)
python db/db_init.py          # create schema + DB
source .venv/bin/activate     # shapely, cairocffi, matplotlib installed
```

DB path: `db/floorplan.db` (default in all scripts)
All helper scripts: `skills/pcb-floorplanner/scripts/`

## Workflow overview

Read the full step-by-step reference before starting:
→ `references/workflow.md` — each step: goal, inputs, processing, outputs (EE terminology)
→ `references/schema.md` — all DB tables, columns, constraints, integrity guarantees

## Execution pattern

For each LLM step:

1. Read required DB context (run `db_read_*.py` script, parse JSON output)
2. Reason + optionally call `web_search.py` for datasheet/spec lookups
3. Produce structured JSON payload
4. Call `db_write_*.py` script with the payload
5. Verify output row count matches expectation
6. Proceed to next step

For each Python step:

- Run the named script directly, check exit code 0
- Parse JSON stdout for confirmation counts

## Step sequence

| Step | Name | Engine | Write script |
|---|---|---|---|
| 0 | User Prompt Intake | LLM | `db_write_session.py` |
| 0.5 | Hardware Architecture | LLM + Web | `db_write_arch.py` |
| 1 | Design Capture — BOM + Netlist | LLM + Web | `db_write_bom.py` |
| 2 | Board Definition | LLM + Web + Python | `db_write_board.py` |
| 3 | Component Geometry Resolution | LLM + Web + Python | `db_write_geometry.py` |
| 4 | Constraint Derivation | LLM + Web | `db_write_constraints.py` |
| 5 | Design Lock | Python | `db_lock_version.py` |
| 6 | Initial Placement | Python | `placer_greedy.py` |
| 7 | Optimization | Python | `optimizer_annealing.py` |
| 8 | Scoring + Violation Report | Python | `scorer.py` |
| 9 | LLM Review + Decision | LLM | `db_write_review.py` |
| 10 | Render + Export | Python | `render_png.py` + `render_report.py` |

## Helper script calling convention

All write scripts accept `--data '<JSON>'` or JSON on stdin, and `--db <path>`.
All print a JSON result to stdout. Non-zero exit = failure with error on stderr.

```bash
# Example: Step 0
python scripts/db_write_session.py \
  --prompt "Create a floorplan for a Raspberry Pi clone" \
  --model "claude-sonnet-4-5"
# → {"session_id": 1, "version_id": 1}

# Example: Step 5
python scripts/db_lock_version.py --version_id 1
# → {"status": "LOCKED", "hash": "3fa2c1...", "components": 12, "constraints": 18}
# exit 1 if geometry missing or no constraints defined
```

## LLM step guidelines

### Step 0.5 — Hardware Architecture

- Decompose into: Compute, Memory, Power, IO, Clocking, Debug, RF blocks
- For each block: name the preferred IC family + rationale (cost, ecosystem, thermal)
- Flag critical interfaces: bus type + speed (e.g. LPDDR4X @ 3200 MT/s, PCIe Gen2)
- Document each major decision as: decision / rationale / alternatives / risk
- Produce an ASCII block diagram showing signal flow between blocks
- Web search: `"{device} hardware design guide"`, `"{SoC} reference schematic"`

### Step 1 — BOM + Netlist

- Use `functional_blocks` as the IC selection guide, not free invention
- Net types: PWR (rails), GND, SIG (single-ended), DIFF (differential pairs)
- Requirements key-value pairs to always consider:
  - `near: <ref>` — must be placed close (decoupling caps, crystals, DDR)
  - `far: <ref>` — must be separated (switching reg from ADC, RF from digital)
  - `edge: <side>` — connector forced to board edge (top/bottom/left/right)
  - `max_temp_c: <N>` — thermal constraint

### Step 4 — Constraint Derivation

- NEAR constraints: decoupling caps ≤2 mm, crystal ≤5 mm, DDR topology matched
- FAR constraints: switching regulator >10 mm from ADC; RF >10 mm from digital logic
- FIXED: all edge connectors (USB, HDMI, Ethernet, power jack)
- Hard constraints (hard=1): FIXED positions, zero-overlap rule
- Soft constraints (hard=0): NEAR/FAR distances with tunable weight
- Always include a reason string — used verbatim in violation reports

### Step 9 — LLM Review

Read violations via:

```bash
python scripts/db_read_violations.py --run_id <id>
```

Categorise:

- `delta_mm < -5` AND `hard=true` → must fix, MODIFY constraints or board area
- `delta_mm < 0` AND `hard=false` → soft violation, check if acceptable tradeoff
- All soft violations within 20% of limit → APPROVE
Choose action: APPROVE / MODIFY / RERUN
- MODIFY: create new version, update constraint weights, re-run Steps 5–8
- RERUN: same version, new `optimization_runs` row, different random seed

## Modify cycle (Step 9 → Step 4)

Never unlock a LOCKED version. Instead:

```bash
# Create new version
python scripts/db_write_session.py --prompt "<same prompt>" --model "<model>"
# Copy components/geometry/board from old version_id to new version_id
# (use db_copy_version.py if available, else manual INSERT SELECT)
# Adjust constraints, then lock again
python scripts/db_lock_version.py --version_id <new_id>
```

## EE review — feedback entrypoints

After the floorplan is rendered, an electrical engineer will typically review and provide
feedback. Map their comment to the correct entrypoint and re-enter the pipeline from there.
Never unlock a LOCKED version — always create a new `design_version` for any post-lock change.

### Feedback type → entrypoint table

| EE feedback | Change at step | Re-enter at step |
|---|---|---|
| Position override ("move J3 to bottom edge") | 4 — add/update FIXED constraint | 5 |
| Proximity / separation tune ("C4 too far from U2", "keep switcher away from ADC") | 4 — adjust NEAR/FAR `max_dist_mm`, `min_dist_mm`, `weight`, `hard` | 5 |
| Board size or keep-out change ("board too narrow", "add RF keep-out") | 2 — new `board_outline` / `keep_out_zones` in new version | 5 |
| Component swap or BOM edit ("replace BCM2712 with RK3588", "add clock buffer") | 1 — update `components`, `nets`, `net_connections`, `requirements` | 3 |
| Architecture rethink ("use PMIC instead of discretes", "switch to PCIe Gen 3") | 0.5 — revise `functional_blocks`, `block_connections` | 1 |
| Same design, different SA outcome ("try another seed") | — no DB change needed | 6 — new `optimization_runs` row, different random seed |

### Position override (FIXED constraint)

EE says: "J3 must be on the bottom edge at x=40 mm."

1. Create new version (copy BOM + geometry + board from prior version).
2. In Step 4: add or replace the FIXED constraint for J3 with `x_target`, `y_target`, `hard=1`.
3. Re-enter at Step 5.

### Proximity / separation tuning (NEAR / FAR / ALIGN)

EE says: "C4 decoupling cap is 4 mm from U2 — tighten to ≤1.5 mm" or
"Switcher L1 is too close to ADC U5 — enforce ≥12 mm separation."

1. Create new version.
2. In Step 4: update the relevant constraint row (`max_dist_mm`, `min_dist_mm`, `weight`).
   Escalate `hard=0` to `hard=1` if the EE marks it as mandatory.
3. Re-enter at Step 5.

### Board geometry change (outline / keep-outs / mount holes)

EE says: "Board needs to be 90 mm wide, not 85 mm" or "Add a 10×10 mm keep-out for the RF antenna."

- **If no optimization runs exist yet** on the current version: use `db_patch_board.py` in-place.
- **If optimization has already run**: create a new version, re-run Step 2 with corrected values.
- Re-enter at Step 5.

### Component substitution or BOM change

EE says: "Replace the SoC with RK3588S" or "Add a dedicated LDO for the ADC supply."

1. Create new version.
2. In Step 1: update `components`, `nets`, `net_connections`, `requirements` for the change.
3. Re-enter at Step 3 (re-derive geometry for any new/changed parts), then 4 → 5 → …

### Architecture rework

EE says: "Use a single PMIC instead of three discrete regulators" or "Add a PCIe switch."

1. Create new version.
2. In Step 0.5: revise `functional_blocks` and `block_connections`.
3. Re-enter at Step 1 — full BOM re-capture required.

### Rerun with different SA seed (no design change)

EE says: "The placement looks locally stuck — try again."

1. No new version needed — same LOCKED version.
2. Clear `score_history`, `violations`, `placement_score` for the run (or create a fresh
   `optimization_runs` row with a different `random_seed` param).
3. Re-enter at Step 6.

### The golden rule for all modify cycles

```text
1. Create a new design_version (DRAFT) for the same session
2. Copy unchanged tables from the old version
3. Apply only the EE's change to the relevant table(s)
4. Lock the new version  →  Step 5
5. Re-run Steps 6–10
6. Compare versions: make db-status / make db-summary
```

Every iteration of EE feedback produces a separately scored, separately rendered version.
Use `make db-status` to list all versions and `make db-summary` to compare scores.

## Render output

Step 10 produces in `output/`:

- `floorplan.svg` — vector, layer-coloured, labelled with ref-des
- `floorplan.png` — cairocffi raster, PCB-green substrate, copper pads
- `heatmap.png` — occupancy density (highlights congested zones for the layout engineer)
- `report.html` — full design summary: BOM, constraints, violations, convergence plot
