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
python db/db_init.py          # create schema + DB (prompts if DB exists)
python db/db_init.py --force  # non-interactive reinitialise (automation / CI)
make db-init FORCE=1          # same via Makefile
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
| 9 | Render + Export | Python | `render_png.py` + `render_report.py` |
| 9.5 | Visual Inspection | LLM + PNG | adversarial checklist (see below) |
| 10 | LLM Review + Decision | LLM | `db_write_review.py` |

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

## Wire-format schemas (exact JSON each script accepts)

These are the exact field names for each `--data` payload. The DB table columns differ
from the wire format in several scripts — always use these wire-format schemas, not the
schema.md table definitions.

### `db_write_session.py` (Step 0)

```bash
python scripts/db_write_session.py \
  --prompt "Free text design intent" \
  --model "claude-sonnet-4-5"
# → {"session_id": N, "version_id": N}
```

### `db_write_arch.py` (Step 0.5)

Valid category values: `COMPUTE`, `MEMORY`, `POWER`, `IO`, `CLOCK`, `DEBUG`, `RF`, `OTHER`

```json
{
  "version_id": 1,
  "functional_blocks": [
    {"name": "MCU", "category": "COMPUTE", "notes": "ATmega32U4 — native USB, UART, SPI"}
  ],
  "block_connections": [
    {"from_name": "MCU", "to_name": "MEMORY", "interface_type": "SPI", "critical": 1}
  ]
}
```

### `db_write_bom.py` (Step 1)

```json
{
  "version_id": 1,
  "components": [
    {"name": "U1", "type": "MCU", "package": "TQFP-44", "notes": "ATmega32U4"}
  ],
  "nets": [
    {"name": "VCC", "type": "PWR"}
  ],
  "net_connections": [
    {"net_name": "VCC", "component_name": "U1", "pin_name": "VCC"}
  ],
  "requirements": [
    {"component_name": "U1", "key": "near", "value": "Y1"},
    {"component_name": "J1", "key": "edge", "value": "bottom"}
  ]
}
```

Valid net types: `PWR`, `GND`, `SIG`, `DIFF`

### `db_write_board.py` (Step 2)

```json
{
  "version_id": 1,
  "board": {"width_mm": 85.0, "height_mm": 56.0, "grid_resolution": 1.0, "layer_count": 2},
  "keep_out_zones": [
    {
      "x_mm": 0, "y_mm": 0, "width_mm": 7, "height_mm": 7,
      "reason": "corner clearance TL",
      "is_mount_clearance": true
    }
  ],
  "mount_holes": [
    {"x_mm": 3.5, "y_mm": 3.5, "diameter_mm": 3.2}
  ]
}
```

**Mount hole clearance rule:** annular ring = `diameter_mm / 2 + 0.5` mm.
Place the hole so the ring does not exit the board and does not overlap non-clearance keep-outs.
For a 3.2 mm drill, annular = 1.6 + 0.5 = 2.1 mm — the hole centre must be ≥ 2.1 mm from every
board edge and ≥ 2.1 mm from the edge of any keep-out zone that has `is_mount_clearance: false`.

**`is_mount_clearance` flag:** set this to `true` on corner keep-outs that intentionally
surround a mount hole. This disables the overlap check for that zone so the hole is allowed
to sit inside it. **Do NOT use the old workaround** of putting "mount hole" in the `reason`
string — that string match has been removed and will no longer suppress the check.

### `db_write_geometry.py` (Step 3)

```json
{
  "version_id": 1,
  "geometry": [
    {
      "component_name": "U1",
      "width_mm": 10.0,
      "height_mm": 10.0,
      "courtyard_margin": 0.5,
      "allowed_rotations": "0,90,180,270"
    }
  ]
}
```

### `db_write_constraints.py` (Step 4)

```json
{
  "version_id": 1,
  "constraints": [
    {
      "type": "NEAR",
      "comp_a": "U1",
      "comp_b": "C1",
      "max_dist_mm": 2.0,
      "weight": 2.0,
      "hard": 0,
      "reason": "Decoupling cap for U1 VCC"
    },
    {
      "type": "FAR",
      "comp_a": "U2",
      "comp_b": "U5",
      "min_dist_mm": 12.0,
      "weight": 1.5,
      "hard": 0,
      "reason": "Switcher away from ADC"
    },
    {
      "type": "FIXED",
      "comp_a": "J1",
      "comp_b": null,
      "max_dist_mm": null,
      "weight": 10.0,
      "hard": 1,
      "reason": "USB connector must be at bottom board edge"
    }
  ]
}
```

Valid constraint types: `NEAR`, `FAR`, `FIXED`, `ALIGN`

**FIXED + hard=1:** always use `hard=1` for FIXED edge connectors. The scorer adds a
`500 × delta` extra penalty for hard FIXED violations, giving SA a strong signal to
drive connectors to the board edge. With `hard=0` the penalty is only `weight × dist_from_edge`
which is too weak and connectors will drift inward.

### `optimizer_annealing.py` (Step 7)

```bash
python scripts/optimizer_annealing.py --run_id 1 [--iterations 5000] [--seed 42]

# Re-running on the same run_id (e.g. more iterations, different seed):
python scripts/optimizer_annealing.py --run_id 1 --iterations 20000 --seed 99 --overwrite
```

**`--overwrite` flag:** required when re-running SA on an existing `run_id`. Clears
`score_history`, `violations`, and `placement_score` before starting. Without it the
INSERT into `score_history` hits a UNIQUE constraint and the script aborts.

### `write_violations.py` (Step 8)

```bash
python scripts/write_violations.py --run_id 1
# → {"violations": N, "hard_violations": N, "final_penalty": F, "net_length_mm": F}
```

### `db_write_review.py` (Step 10)

```bash
python scripts/db_write_review.py \
  --run_id 1 \
  --action APPROVE \
  --note "VISUAL: all connectors at edge. SCORES: 5 soft violations, 0 hard. DECISION: APPROVE."
# valid actions: APPROVE, MODIFY, RERUN
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
  - `edge: <side>` — connector forced to board edge: `top` (y≈0), `bottom` (y≈H), `left` (x≈0), `right` (x≈W)
  - `max_temp_c: <N>` — thermal constraint

### Step 2 — Board Definition

**Coordinate system:** origin (0,0) is the **top-left** corner of the render.

- `y` increases **downward** (screen coordinates).
- `"top"` edge in `requirements` → low `y` values (≈ 0).
- `"bottom"` edge → high `y` values (≈ `height_mm`).
- This matches the render output: top of PNG = top of board = y=0.

**Keep-out zone anti-pattern — do NOT define keep-outs for edge connector zones:**

Keep-out zones block ALL components including FIXED edge connectors. If you define a
keep-out for "bottom edge connector zone", the placer will refuse to place connectors there.

**Correct pattern:** use keep-outs only for areas where NO component should ever go:

- Mount hole corners (mechanical clearance around screws) ✓
- RF antenna area (no copper, no components) ✓
- Full-edge connector zone ✗ — this blocks the connectors themselves

**When `db_write_board.py` writes a keep-out that spans a full edge, it will emit a
`WARNING` to stderr.** If you see that warning, remove the full-edge keep-out.

### Step 4 — Constraint Derivation

- FAR constraints: switching regulator >10 mm from ADC; RF >10 mm from digital logic
- FIXED: all edge connectors (USB, HDMI, Ethernet, power jack)
- Hard constraints (hard=1): FIXED positions, zero-overlap rule
- Soft constraints (hard=0): NEAR/FAR distances with tunable weight
- Always include a reason string — used verbatim in violation reports

### Step 9 — Render + Export

Run both render scripts and confirm output files exist and are non-zero bytes:

```bash
python scripts/render_png.py --run_id <id>
# → {"floorplan": "output/floorplan.png", "heatmap": "output/heatmap.png"}
python scripts/render_report.py --run_id <id>
# → {"report": "output/report.html"}
ls -lh output/floorplan.png output/heatmap.png
```

Proceed to Step 9.5 immediately after — do NOT write the review decision yet.

### Step 9.5 — Visual Inspection (adversarial)

**This step is mandatory. Do not skip it. Do not approve from scores alone.**

Visually inspect the rendered PNG and work through every
item on the checklist below. Assume the placement is broken until each item is
explicitly confirmed from the image.

#### Image orientation and colour legend

**Coordinate system:** `x=0, y=0` is the **top-left** corner of the board.
`x` increases to the right, `y` increases downward.
A connector at the bottom edge of the physical board will appear at the **bottom** of the image (high y).
A connector at the top edge will appear at the **top** of the image (low y).

| What you see | Meaning |
|---|---|
| Dark green fill | PCB substrate (board area) |
| Bright green border | Board outline |
| Faint green grid lines | 5 mm grid |
| Semi-transparent red rectangle | Keep-out zone — components must not enter here |
| Gold ring + dark hole | Mount hole (copper annular ring + drill) |
| Coloured filled rectangle | Component body — colour varies by type (see below) |
| Dashed grey outline around component | Courtyard / exclusion zone |
| Red/pink outline on component | Component has a constraint violation |
| White text inside component | Component name label |

**Component fill colours (approximate):**
SoC → gold; SDRAM → blue; PMIC → orange; Connector → blue-grey;
Crystal → near-white; everything else → mid-grey.

**Heatmap (separate `heatmap.png`):** dark = empty space; yellow-green → red = increasing
component density. A good placement has no isolated red hot-spot.

#### Adversarial Visual Checklist

Work through each item. For every FAIL, record it — do not stop at the first failure.

##### EDGE PLACEMENT

- [ ] All connectors (USB, audio jacks, power) are touching or within 2mm of the board edge
- [ ] No connector is floating in the middle of the board
- [ ] Buttons/switches labelled as front-panel controls are at the correct edge (top/bottom as specified)
- [ ] Connectors are on the correct edge (e.g. USB on bottom = high-y edge in render)

##### KEEP-OUT ZONES

- [ ] Red hatched areas (keep-outs) are only at corners / mechanical features, not blocking any connector
- [ ] Mount hole copper rings are visible at all four corners
- [ ] No component body overlaps a red keep-out zone (unless it is a FIXED connector that is allowed)

##### COMPONENT CLUSTERING

- [ ] Decoupling caps are visually adjacent to their IC (within ~2mm, not 10+ mm away)
- [ ] Crystal is visually close to the MCU, not isolated in a corner
- [ ] Power regulation components (LDO, PMIC) are not scattered far from their load

##### OVERLAP AND SPACING

- [ ] No two component bodies visually overlap each other
- [ ] No component is placed off-board (outside the green PCB area)
- [ ] Components do not cluster into one dense corner with half the board empty

##### LABEL READABILITY

- [ ] At least one FIXED component per expected edge has a label visible near that edge
- [ ] The board outline (green border) is clearly visible on all four sides

##### CROSS-CHECK AGAINST VIOLATIONS DATA

- [ ] For every NEAR violation with `delta_mm > 10`: confirm visually that the two components
  are at least in the same half of the board. If they are on opposite sides, flag as MODIFY.
- [ ] For every FIXED component in the violations list: confirm it is actually at an edge.
  If not → mandatory MODIFY, this is a placement error not a soft tradeoff.

#### Decision rules after visual inspection

| Visual finding | Decision |
|---|---|
| Any connector not at its required board edge | MODIFY — re-run from Step 4 |
| Any component off-board | MODIFY |
| Any two components visually overlapping | MODIFY |
| All edge connectors at edge, minor clustering issues | APPROVE with note |
| All checklist items pass | APPROVE |

Record findings verbatim in the `--note` argument of `db_write_review.py`.
Format: `"VISUAL: <pass/fail summary>. SCORES: <violation summary>. DECISION: <action>."`

### Step 10 — LLM Review + Decision

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

## Modify cycle (Step 10 → Step 4)

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
2. Re-run SA with `--overwrite` and a different seed to clear old score history:

```bash
python scripts/optimizer_annealing.py --run_id <id> --seed 99 --overwrite
python scripts/write_violations.py --run_id <id>
python scripts/render_png.py --run_id <id>
```

3. Re-enter at Step 9.5 (visual inspection).

### The golden rule for all modify cycles

```text
1. Create a new design_version (DRAFT) for the same session
2. Copy unchanged tables from the old version
3. Apply only the EE's change to the relevant table(s)
    4. Lock the new version  →  Step 5
    5. Re-run Steps 6–10 (including visual inspection at Step 9.5)
6. Compare versions: make db-status / make db-summary
```

Every iteration of EE feedback produces a separately scored, separately rendered version.
Use `make db-status` to list all versions and `make db-summary` to compare scores.

## Render output

Step 9 produces in `output/`:

- `floorplan.svg` — vector, layer-coloured, labelled with ref-des
- `floorplan.png` — cairocffi raster, PCB-green substrate, copper pads
- `heatmap.png` — occupancy density (highlights congested zones for the layout engineer)
- `report.html` — full design summary: BOM, constraints, violations, convergence plot
