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

## Render output

Step 10 produces in `output/`:
- `floorplan.svg` — vector, layer-coloured, labelled with ref-des
- `floorplan.png` — cairocffi raster, PCB-green substrate, copper pads
- `heatmap.png` — occupancy density (highlights congested zones for the layout engineer)
- `report.html` — full design summary: BOM, constraints, violations, convergence plot
