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

The project virtualenv lives at `.venv/` relative to the repo root (i.e. relative to `pwd`).
**Always invoke Python scripts with `.venv/bin/python`** — never bare `python` or `python3`,
because the shell `PATH` may resolve to a different interpreter that lacks the required packages
(cairocffi, shapely, numpy, etc.).

```bash
# Correct — always use the project venv explicitly
.venv/bin/python scripts/db_write_session.py ...

# Wrong — PATH may resolve to a different venv or system Python
.venv/bin/python scripts/db_write_session.py ...
.venv/bin/python scripts/db_write_session.py ...
```

The database is created automatically on first use — no manual `db_init` call needed.
`.venv/bin/python db/db_init.py --force` (or `make db-init FORCE=1`) is available as an
administrative reset if you need to wipe the DB and start a new design from scratch.

## Workflow overview

Read the full step-by-step reference before starting:
→ `references/workflow.md` — each step: goal, inputs, processing, outputs (EE terminology)
→ `references/schema.md` — all DB tables, columns, constraints, integrity guarantees

## Execution pattern

For each LLM step:

1. Read required DB context (run `db_read_*.py` script, parse JSON output)
2. Reason — perform evidence-based web research and fact-check for major unknowns where indicated; do not make up facts
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
| 0.25 | Mechanical Architecture | LLM | `db_write_arch.py` (category: OTHER + notes) |
| 0.5 | Hardware Architecture | LLM | `db_write_arch.py` |
| 1 | Design Capture — BOM + Netlist | LLM | `db_write_bom.py` |
| 2 | Board Definition | LLM + Python | `db_write_board.py` + `db_check_edge_budget.py` |
| 3 | Component Geometry Resolution | LLM + Python | `db_write_geometry.py` |
| 4 | Constraint Derivation | LLM | `db_write_constraints.py` |
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
.venv/bin/python scripts/db_write_session.py \
  --prompt "Create a floorplan for a Raspberry Pi clone" \
  --model "claude-sonnet-4-5"
# → {"session_id": 1, "version_id": 1}

# Example: Step 5
.venv/bin/python scripts/db_lock_version.py --version_id 1
# → {"status": "LOCKED", "hash": "3fa2c1...", "components": 12, "constraints": 18}
# exit 1 if geometry missing or no constraints defined
```

## Wire-format schemas (exact JSON each script accepts)

These are the exact field names for each `--data` payload. The DB table columns differ
from the wire format in several scripts — always use these wire-format schemas, not the
schema.md table definitions.

### `db_write_session.py` (Step 0)

```bash
.venv/bin/python scripts/db_write_session.py \
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

**Keep-out zone positioning rule — always derive from mount hole coordinates, never from board corners:**

For each mount hole at `(hx, hy)` with a clearance zone of `(kw × kh)` mm:

```text
keep_out.x_mm = hx - kw / 2
keep_out.y_mm = hy - kh / 2
```

Example — RPi4B mount hole at (61.5, 3.5) with 7×7 mm keep-out:

```text
x_mm = 61.5 - 3.5 = 58.0   ← NOT board_width - keep_out_width (85 - 7 = 78)
y_mm =  3.5 - 3.5 =  0.0
```

`db_write_board.py` enforces this: it will raise a `ValueError` if any mount hole does not
fall inside at least one `is_mount_clearance` keep-out zone. The pipeline will not proceed
to Step 3 until every hole is covered.

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

### `db_check_edge_budget.py` (Step 2 gate)

```bash
.venv/bin/python scripts/db_check_edge_budget.py --version_id 1
# → {
#     "version_id": 1,
#     "board_mm": "85.0x56.0",
#     "edges": {
#       "top":    {"usable_mm": 71.0, "committed_mm": 52.0, "components": ["J8"], "ok": true},
#       "left":   {"usable_mm": 42.0, "committed_mm": 31.0, "components": ["J1","J2","J3","J4"], "ok": true},
#       ...
#     },
#     "corner_conflicts": [],
#     "feasible": true
#   }
# exit 1 + errors[] if feasible=false
```

Requires geometry to be written first (`db_write_geometry.py`). Components with an `edge`
requirement but no geometry are reported as errors.

### `optimizer_annealing.py` (Step 7)

```bash
.venv/bin/python scripts/optimizer_annealing.py --run_id 1 [--iterations 5000] [--seed 42]

# Re-running on the same run_id (e.g. more iterations, different seed):
.venv/bin/python scripts/optimizer_annealing.py --run_id 1 --iterations 20000 --seed 99 --overwrite
```

**`--overwrite` flag:** required when re-running SA on an existing `run_id`. Clears
`score_history`, `violations`, and `placement_score` before starting. Without it the
INSERT into `score_history` hits a UNIQUE constraint and the script aborts.

### `write_violations.py` (Step 8)

```bash
.venv/bin/python scripts/write_violations.py --run_id 1
# → {"violations": N, "hard_violations": N, "final_penalty": F, "net_length_mm": F}
```

### `db_write_review.py` (Step 10)

```bash
.venv/bin/python scripts/db_write_review.py \
  --run_id 1 \
  --action APPROVE \
  --note "VISUAL: all connectors at edge. SCORES: 5 soft violations, 0 hard. DECISION: APPROVE."
# valid actions: APPROVE, MODIFY, RERUN
```

## LLM step guidelines

### Step 0.25 — Mechanical Architecture

This step runs **before any IC is selected**. Its sole purpose is to establish the physical
reality of the enclosure and derive hard mechanical constraints that all later steps must
respect. Skipping it is the single most common cause of boards where connectors end up
inaccessible from outside the box.

**Goal:** Answer four questions before touching the BOM:

1. **What is the enclosure?** (desktop tower, mini-ITX case, custom ABS box, DIN rail,
   open-frame rack, Eurorack, handheld, wall-mount, etc.)
2. **Which PCB edges face which physical surfaces?** Map every board edge to a face:
   - face accessible to the user (rear panel, front panel, top lid, open side)
   - face blocked by case wall (no connectors possible)
   - face resting on standoffs (mount hole zone, cable routing)
3. **What must be reachable from outside?** For each user-accessible face, list every
   port, slot, button, LED, or ventilation feature that needs to break through the enclosure
   wall. This drives FIXED constraints in Step 4.
4. **What mechanical features constrain the PCB?** Standoff grid, screw hole positions,
   height-limited zones (low-clearance lid), airflow corridors, cable routing channels,
   connector mating envelopes (ISA/PCI cards need a cutout + guide rail).

**DIY and common design patterns to reason about:**

- **Desktop AT/ATX-style board:** ISA/PCI slots always at the rear panel, perpendicular
  to the board. I/O bracket connectors (USB, audio, LAN) also at the rear. Front panel
  (power button, reset, HDD LED, power LED) at the front edge via ribbon header.
  Power connector near the right edge (closest to the PSU).
- **SBC / embedded box:** All user I/O on one or two faces. Opposite face is typically
  mounting/power only. Tall connectors (USB-A, RJ45) constrain the PCB-to-lid clearance.
- **Eurorack module:** Fixed panel width in HP units. Front panel is the left or right PCB
  edge; all pots, jacks, and LEDs route to it. Power header at the rear.
- **Handheld / portable:** Screen and buttons on the front face. Battery connector internal.
  USB/charging port on one side edge. Board often portrait orientation.
- **DIN rail / industrial:** Screw terminals always accessible from the front. Status LEDs
  on the top. Power input on the side. Conformal coating may block some keep-out zones.

**Processing:**

1. Identify enclosure type from the prompt. If unspecified, assume the most common DIY
   form for the device class (e.g. desktop tower for a PC mainboard, custom ABS box for
   an MCU project).

2. Draw a text diagram mapping enclosure faces to PCB edges, e.g.:

   ```text
   Enclosure: AT desktop tower, board horizontal
   TOP edge    = rear panel  (accessible: I/O connectors, ISA slots)
   BOTTOM edge = front panel (accessible: power button, reset, HDD LED)
   LEFT edge   = left side wall (blocked, no connectors)
   RIGHT edge  = PSU bay (accessible: AT power connector)
   Board rests on standoffs at corners + centre
   ```

3. For each accessible face, list what must be there and whether it needs a cutout,
   a bracket slot, or just a header (for an internal cable).

4. Identify height-limited zones (e.g. below the PSU bay, below a GPU card) and mark
   them as keep-out zones for tall components.

5. Identify the standoff grid and convert to mount hole positions + corner keep-outs.

   1. **Check the edge width budget for every accessible face.** For each board edge, sum
      the widths of all connectors and slots assigned to that edge. If the total exceeds
      the board dimension for that edge, the design is infeasible before placement even
      starts. Reduce the connector count, widen the board, or redistribute to another edge.

      ```text
      Example — TOP edge (200 mm wide):
        2x ISA-16 slot  2 x 98 mm = 196 mm
        COM1 DB9              32 mm
        LPT1 DB25             57 mm
        Keyboard DIN-5        20 mm
        Total:               305 mm  >  200 mm  ← INFEASIBLE, must redesign
      ```

   2. **Determine the arrangement axis for each edge.** Multiple connectors or slots on
      the same edge are arranged side-by-side along the edge axis (X-axis for top/bottom
      edges, Y-axis for left/right edges). They are **never stacked perpendicular to the
      edge** — that would make one row inaccessible from outside.

      For connectors that come as a mated pair (e.g. AT power P8+P9, dual USB-A stacked
      vertically on a bracket), document the pairing explicitly: the two connectors must
      sit flush against each other, not separated by other components.

   3. Output a **Mechanical Constraints Summary** — a numbered list of hard rules every
      downstream step must follow. Example:

   ```text
   MECH-1: ISA expansion slots at TOP edge (rear panel). Card fingers toward y=0.
           Two slots arranged side-by-side along X. Total slot width: 2x98=196mm —
           leaves only 4mm for other top-edge connectors; move COM/LPT/KBD to rear
           bracket or use a single ISA slot.
   MECH-2: AT power connectors P8/P9 at RIGHT edge. Mated pair: flush adjacent along Y,
           no gap between them.
   MECH-3: Front panel header at BOTTOM edge (reset, HDD LED, power LED, speaker).
   MECH-4: No component taller than 15 mm in zone x=0..30, y=0..148 (PSU shadow).
   MECH-5: Four M3 standoff holes at corners, 5 mm inset. Keep-out 7x7 mm around each.
   ```

**Output:** Write the Mechanical Constraints Summary as `notes` on a functional block
named `MECHANICAL_ARCH` with category `OTHER` using `db_write_arch.py`. This makes the
constraints visible to every downstream LLM step that reads `functional_blocks`.

The summary is also the primary input to Step 0.5 — the hardware architect must treat
each MECH-N rule as a non-negotiable requirement, not a suggestion. In particular:

- Edge connector counts must fit within the edge width budget calculated in step 6.
- Paired connectors (e.g. power P8/P9, dual-port USB) must be noted as requiring
  flush adjacency with no gap.
- Multiple slots or connectors on the same edge are side-by-side along the edge axis,
  never stacked perpendicular to it.
- **Classify every connector as through-edge or on-board before assigning FIXED:**
  - **Through-edge** — body protrudes through or flush with the PCB edge; mating cable/plug
    exits horizontally through the enclosure wall. Examples: USB-A, USB-C, HDMI, RJ45,
    DE-9, barrel jack, audio jack. → assign `edge:` requirement → becomes FIXED constraint.
  - **On-board (ZIF/FFC/FPC)** — body sits flat on the PCB surface; ribbon cable exits
    vertically (upward) off the board. Examples: MIPI CSI, MIPI DSI, eDP, microSD push-pull
    slot. These live *near* an edge for cable routing clearance but must **not** be FIXED.
    → assign `near: <edge-reference-component>` requirement, no `edge:` key.

### Step 0.5 — Hardware Architecture

- **Read `MECHANICAL_ARCH` notes from `functional_blocks` before selecting any IC.**
  Every connector placement decision must satisfy the MECH-N rules from Step 0.25.
- **Respect the edge width budget.** If the MECH summary flags an infeasible edge, adjust
  the connector selection here — fewer slots, narrower connectors, or redistribution to
  another edge — before committing to the BOM.
- Decompose into: Compute, Memory, Power, IO, Clocking, Debug, RF blocks
- For each block: name the preferred IC family + rationale (cost, ecosystem, thermal)
- Flag critical interfaces: bus type + speed (e.g. LPDDR4X @ 3200 MT/s, PCIe Gen2)
- **For any high-speed interface, state the maximum allowable distance between the two
  endpoints.** This becomes a hard NEAR constraint in Step 4. Examples:
  - CPU ↔ memory controller: ≤15 mm at frequencies above 20 MHz
  - MCU ↔ crystal: ≤5 mm to minimise stray capacitance on load pins
  - CPU ↔ co-processor (FPU, DSP, GPU): ≤10 mm for direct shared-bus interfaces
  - PMIC ↔ SoC: ≤20 mm to minimise VDD_CORE IR drop
- **For any component group that must form a physical bank** (parallel memory modules,
  relay banks, ADC channels, identical slot rows), note this explicitly. Bank members
  must be co-located in a contiguous strip with uniform orientation. This becomes an
  ALIGN + chained NEAR group in Step 4.
- Document each major decision as: decision / rationale / alternatives / risk
- Produce an ASCII block diagram showing signal flow between blocks
- **Web research:** for every candidate SoC or major IC, perform an evidence-based web research
  and fact-check — confirm the part exists, its package, bus interfaces, and typical reference
  design topology. Do not invent IC families or interface speeds.

### Step 1 — BOM + Netlist

- **Read `MECHANICAL_ARCH` notes from `functional_blocks`.** Every `edge:` requirement
  in `requirements` must match the face mapping established in Step 0.25. If the mechanical
  plan says "ISA slots at TOP edge", every ISA slot gets `edge: top` — not `edge: bottom`.
- **Re-verify the edge width budget before finalising the BOM.** Sum connector body widths
  per edge using the geometry from Step 3 (or estimated widths if Step 3 has not run yet).
  If any edge is oversubscribed, reduce the connector count or flag for board resizing at
  Step 2 — do not proceed with an infeasible layout.
- **For paired connectors** (connectors that form a single functional unit and must be
  inserted together, e.g. AT power P8+P9, dual stacked USB-A), add a `near:` requirement
  between the pair with a very small value (≤ connector_width + 1 mm). This ensures the
  constraint is already captured before Step 4 derives the ALIGN+NEAR pair.
- Use `functional_blocks` as the IC selection guide, not free invention
- Net types: PWR (rails), GND, SIG (single-ended), DIFF (differential pairs)
- Requirements key-value pairs to always consider:
  - `near: <ref>` — must be placed close (decoupling caps, crystals, DDR)
  - `far: <ref>` — must be separated (switching reg from ADC, RF from digital)
  - `edge: <side>` — **through-edge connectors only** (body exits through the PCB edge):
    `top` (y≈0), `bottom` (y≈H), `left` (x≈0), `right` (x≈W).
    **Never assign `edge:` to on-board ZIF/FFC/FPC connectors** (MIPI CSI, MIPI DSI,
    microSD, eDP). Those sit flat on the board surface with the cable exiting upward —
    they are not edge-mount and must not be FIXED to the board perimeter.
    Use `near: <nearby-edge-connector>` for cable-routing proximity instead.
  - `max_temp_c: <N>` — thermal constraint
- **Web research:** for every component with an unknown or uncertain package, pin count, or
  power rail requirement, perform an evidence-based web research and fact-check before
  writing the BOM entry. Do not guess packages or pin names.

### Step 2 — Board Definition

**Coordinate system:** origin (0,0) is the **top-left** corner of the render.

- `y` increases **downward** (screen coordinates).
- `"top"` edge in `requirements` → low `y` values (≈ 0).
- `"bottom"` edge → high `y` values (≈ `height_mm`).
- This matches the render output: top of PNG = top of board = y=0.
- **Web research:** if a standard form factor applies (Raspberry Pi HAT, Arduino shield,
  Eurorack, etc.), perform an evidence-based web research and fact-check to confirm exact
  board dimensions, mounting hole positions, and edge-connector locations. Do not invent
  dimensions for established form factors.

**After writing board and geometry, run the edge-budget check (mandatory gate):**

```bash
.venv/bin/python scripts/db_check_edge_budget.py --version_id <id>
# → JSON with per-edge budget + corner conflicts
# exit 1 if feasible=false — DO NOT proceed to Step 3 until this passes
```

This check verifies two things deterministically before any placement runs:

1. **Edge budget:** for each edge, the sum of all connector body sizes + courtyard margins
   does not exceed the usable edge length (board dimension minus keep-out zones on that edge).
2. **Corner conflicts:** no two connectors on perpendicular edges both claim the same corner
   keep-out zone simultaneously (e.g. a wide GPIO header on the top edge and a tall connector
   on the left edge both reaching into the top-left corner).

**If `feasible=false`:** fix the design at Step 1 or Step 2 — do NOT proceed to Step 3.
Common remedies:

- Edge overcommitted → remove a connector, reduce its geometry, or widen the board
- Corner conflict → move one connector to a different edge, or shorten the body that claims the corner

This gate exists because violations caused by edge-budget failures **cannot be fixed by
placement or SA optimisation** — they are geometric infeasibilities that must be resolved
in the design data before locking.

**Keep-out zone anti-pattern — do NOT define keep-outs for edge connector zones:**

Keep-out zones block ALL components including FIXED edge connectors. If you define a
keep-out for "bottom edge connector zone", the placer will refuse to place connectors there.

**Correct pattern:** use keep-outs only for areas where NO component should ever go:

- Mount hole corners (mechanical clearance around screws) ✓
- RF antenna area (no copper, no components) ✓
- Full-edge connector zone ✗ — this blocks the connectors themselves

**When `db_write_board.py` writes a keep-out that spans a full edge, it will emit a
`WARNING` to stderr.** If you see that warning, remove the full-edge keep-out.

### Step 3 — Component Geometry Resolution

- **Web research:** for every component, perform an evidence-based web research and
  fact-check to determine the exact body dimensions (width × height in mm) for the assigned
  package. Use manufacturer datasheet land pattern or IPC-7351 courtyard values where
  available. Do not estimate or copy dimensions from a different package family.

### Step 4 — Constraint Derivation

- FAR constraints: switching regulator >10 mm from ADC; RF >10 mm from digital logic
- FIXED: all edge connectors (USB, HDMI, Ethernet, power jack)
- Hard constraints (hard=1): FIXED positions, zero-overlap rule
- Soft constraints (hard=0): NEAR/FAR distances with tunable weight
- Always include a reason string — used verbatim in violation reports
- **Web research:** for any constraint distance that depends on a specific standard or
  datasheet rule (e.g. DDR4 trace-length matching tolerance, USB differential pair spacing,
  crystal load capacitance proximity), perform an evidence-based web research and fact-check
  before setting `max_dist_mm` or `min_dist_mm`. Do not invent clearance values.

**Mandatory constraint patterns — apply these whenever the BOM contains the relevant topology:**

1. **Multiple connectors/slots on the same edge — ALIGN + FAR (arrangement axis).**
   Connectors on the same edge must be aligned to that edge (same centroid coordinate
   on the perpendicular axis) and spaced apart along the edge axis. Add:

   - `ALIGN` between every adjacent pair (enforces same Y centroid for top/bottom edges,
     same X centroid for left/right edges).
   - `FAR` between every adjacent pair with `min_dist_mm` = half the width of the wider
     connector + half the width of the narrower connector + 2 mm clearance.
     This prevents overlap along the edge axis.

   Example — two ISA slots on the top edge, each 98 mm wide:
   `FAR J1 J2  min_dist_mm=100  reason="ISA slots side-by-side on top edge, no overlap"`

2. **Paired connectors that form a single functional unit — ALIGN + tight NEAR.**
   Connectors inserted as a mated set (AT power P8/P9, stacked USB-A, Molex pairs)
   must be flush against each other. Add:

   - `ALIGN` between the pair.
   - `NEAR` with `max_dist_mm` = body_width_of_one_connector + 1 mm, `hard=1`.

   Example — AT power P8 (14 mm wide) and P9 (14 mm wide):
   `NEAR J_P8 J_P9  max_dist_mm=15  hard=1  reason="P8/P9 mated pair, must be flush adjacent"`

3. **High-speed IC pairs — hard NEAR.**
   Any two ICs that share a direct high-speed bus (CPU↔memory controller,
   MCU↔co-processor, SoC↔DDR, processor↔FPU) must have a hard NEAR constraint.
   Use `hard=1` and set `max_dist_mm` from the architecture notes (Step 0.5) or
   from the IC datasheet layout guidelines.

   Example:
   `NEAR U_CPU U_NB  max_dist_mm=20  hard=1  reason="386DX bus 40MHz, max trace length 20mm"`

4. **Memory/IO banks — ALIGN + chained NEAR.**
   Components that form a bank (SIMM sockets, DDR devices, relay channels, ADC inputs)
   must be co-located in a contiguous strip with uniform orientation. Add:

   - `ALIGN` between every adjacent bank member (all share the same centroid on one axis).
   - Chained `NEAR` between every adjacent pair: SIMM1↔SIMM2, SIMM2↔SIMM3, etc.
     `max_dist_mm` = body_width + 2 mm clearance, `hard=0` (soft but tight weight ≥ 2.0).
   - One `NEAR` from the entire bank to the memory controller with a larger
     `max_dist_mm` (e.g. 30 mm) to keep the bank close without over-constraining it.

### Step 9 — Render + Export

Run both render scripts and confirm output files exist and are non-zero bytes:

```bash
.venv/bin/python scripts/render_png.py --run_id <id>
# → {"floorplan": "output/floorplan.png", "heatmap": "output/heatmap.png"}
.venv/bin/python scripts/render_report.py --run_id <id>
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
.venv/bin/python scripts/db_read_violations.py --run_id <id>
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
.venv/bin/python scripts/db_write_session.py --prompt "<same prompt>" --model "<model>"
# Copy components/geometry/board from old version_id to new version_id
# (use db_copy_version.py if available, else manual INSERT SELECT)
# Adjust constraints, then lock again
.venv/bin/python scripts/db_lock_version.py --version_id <new_id>
```

## EE review — feedback entrypoints

After the floorplan is rendered, an electrical engineer will typically review and provide
feedback. Map their comment to the correct entrypoint and re-enter the pipeline from there.
Never unlock a LOCKED version — always create a new `design_version` for any post-lock change.

### Feedback type → entrypoint table

| EE feedback | Change at step | Re-enter at step |
|---|---|---|
| Mechanical rethink ("connectors on wrong face", "board won't fit in enclosure") | 0.25 — revise `MECHANICAL_ARCH` notes, update face mapping | 0.5 |
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
.venv/bin/python scripts/optimizer_annealing.py --run_id <id> --seed 99 --overwrite
.venv/bin/python scripts/write_violations.py --run_id <id>
.venv/bin/python scripts/render_png.py --run_id <id>
```

1. Re-enter at Step 9.5 (visual inspection).

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
