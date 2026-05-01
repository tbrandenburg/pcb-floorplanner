# PCB Floorplanner — Step-by-Step Workflow

This document defines each pipeline step in terms an electrical engineer can read, review,
and modify. Every step lists: goal, inputs, processing, outputs, and the execution engine.

Engine key:

- **LLM** — language model reasoning, web search, structured DB writes via helper scripts
- **Python** — deterministic scripts only, no LLM involvement
- **LLM + Web + Python** — LLM reasons, triggers web lookups, Python validates and persists

---

## Step 0 — User Prompt Intake

**Engine:** LLM

**Goal:** Capture the design intent from a free-text prompt and open a versioned design session in the database.

**Inputs:**

- Free-text user prompt (e.g. "Create a floorplan for a Raspberry Pi clone")

**Processing:**

- Parse the prompt for: target device/product, any explicit constraints (board size, connector positions, cost target, regulatory requirements)
- Create a `design_session` row to anchor all downstream work
- Create a `design_version` row in `DRAFT` status — all editable tables reference this version

**Outputs (DB writes):**

- `design_sessions(prompt, model, created_at)`
- `design_versions(session_id, status=DRAFT)`

**Helper scripts:**

- `db_init.py` — idempotent schema creation
- `db_write_session.py` — INSERT session + version rows

---

## Step 0.25 — Mechanical Architecture

**Engine:** LLM

**Goal:** Before any IC is selected, establish the physical reality of the enclosure and
derive hard mechanical constraints that all later pipeline steps must respect. This is the
step most often skipped in hobby/DIY projects — and its absence is the single most common
cause of boards where connectors are inaccessible from outside the box.

**Inputs:**

- `design_sessions.prompt` — enclosure type, board size, form factor (explicit or implied)
- Common DIY enclosure patterns (desktop tower, mini-ITX, custom ABS box, Eurorack,
  handheld, DIN rail, open-frame rack, wall-mount)

**Processing:**

1. **Identify the enclosure.** If unspecified in the prompt, infer from device class:
   - PC mainboard → AT/ATX desktop tower
   - SBC → custom ABS/acrylic box or open frame
   - Synthesiser module → Eurorack
   - Industrial controller → DIN rail enclosure
   - Handheld → custom moulded case

2. **Map PCB edges to enclosure faces.** Draw a text diagram, e.g.:

   ```text
   Enclosure: AT desktop tower, board horizontal (landscape)
   TOP edge    → rear panel  (user-accessible: I/O connectors, expansion slots)
   BOTTOM edge → front panel (user-accessible: power button, reset, drive LEDs)
   LEFT edge   → left side wall (blocked — no connectors allowed)
   RIGHT edge  → PSU bay (user-accessible: AT power connector P8/P9)
   Board sits on 4–6 standoffs; height above floor ≈ 20 mm
   ```

3. **List per-face requirements.** For each accessible face, enumerate every port, slot,
   button, LED, or ventilation feature that must break through the enclosure wall, including
   whether a bracket cutout, card guide rail, or internal cable header is needed.

4. **Identify mechanical constraints on the PCB itself:**
   - Standoff positions → mount hole coordinates
   - Corner clearance (standoff head diameter + washer) → keep-out zones
   - Height-limited zones (below PSU, under card guide rails) → keep-outs for tall components
   - Airflow corridors (fan inlet/outlet) → no tall components blocking flow
   - Connector mating envelopes (ISA/PCI cards need space to insert/remove perpendicular to board)

5. **Write a numbered Mechanical Constraints Summary (MECH-N rules).** Each rule is a
   one-sentence hard requirement, e.g.:

   ```text
   MECH-1: ISA expansion slots at TOP edge (rear panel). Card fingers toward y=0.
   MECH-2: Rear I/O bracket connectors (keyboard, serial, parallel, VGA) at TOP edge.
   MECH-3: AT power connectors P8/P9 at RIGHT edge (PSU bay).
   MECH-4: Front panel header at BOTTOM edge (power button, reset, LEDs, speaker).
   MECH-5: No component >15 mm tall in zone x=0..30 (PSU shadow zone).
   MECH-6: M3 mount holes at four corners, 5 mm inset. 7×7 mm keep-out around each.
   ```

3. **List per-face requirements.** For each accessible face, enumerate every port, slot,
   button, LED, or ventilation feature that must break through the enclosure wall, including
   whether a bracket cutout, card guide rail, or internal cable header is needed.

4. **Identify mechanical constraints on the PCB itself:**
   - Standoff positions → mount hole coordinates
   - Corner clearance (standoff head diameter + washer) → keep-out zones
   - Height-limited zones (below PSU, under card guide rails) → keep-outs for tall components
   - Airflow corridors (fan inlet/outlet) → no tall components blocking flow
   - Connector mating envelopes (ISA/PCI cards need space to insert/remove perpendicular to board)

5. **Write a numbered Mechanical Constraints Summary (MECH-N rules).** Each rule is a
   one-sentence hard requirement, e.g.:

   ```text
   MECH-1: ISA expansion slots at TOP edge (rear panel). Card fingers toward y=0.
   MECH-2: Rear I/O bracket connectors (keyboard, serial, parallel, VGA) at TOP edge.
   MECH-3: AT power connectors P8/P9 at RIGHT edge (PSU bay).
   MECH-4: Front panel header at BOTTOM edge (power button, reset, LEDs, speaker).
   MECH-5: No component >15 mm tall in zone x=0..30 (PSU shadow zone).
   MECH-6: M3 mount holes at four corners, 5 mm inset. 7×7 mm keep-out around each.
   ```

   Enclosure: AT desktop tower, board horizontal (landscape)
   TOP edge    → rear panel  (user-accessible: I/O connectors, expansion slots)
   BOTTOM edge → front panel (user-accessible: power button, reset, drive LEDs)
   LEFT edge   → left side wall (blocked — no connectors allowed)
   RIGHT edge  → PSU bay (user-accessible: AT power connector P8/P9)
   Board sits on 4–6 standoffs; height above floor ≈ 20 mm

   ```

6. **List per-face requirements.** For each accessible face, enumerate every port, slot,
   button, LED, or ventilation feature that must break through the enclosure wall, including
   whether a bracket cutout, card guide rail, or internal cable header is needed.

7. **Identify mechanical constraints on the PCB itself:**
   - Standoff positions → mount hole coordinates
   - Corner clearance (standoff head diameter + washer) → keep-out zones
   - Height-limited zones (below PSU, under card guide rails) → keep-outs for tall components
   - Airflow corridors (fan inlet/outlet) → no tall components blocking flow
   - Connector mating envelopes (ISA/PCI cards need space to insert/remove perpendicular to board)

8. **Write a numbered Mechanical Constraints Summary (MECH-N rules).** Each rule is a
   one-sentence hard requirement, e.g.:

   ```text
   MECH-1: ISA expansion slots at TOP edge (rear panel). Card fingers toward y=0.
   MECH-2: Rear I/O bracket connectors (keyboard, serial, parallel, VGA) at TOP edge.
   MECH-3: AT power connectors P8/P9 at RIGHT edge (PSU bay).
   MECH-4: Front panel header at BOTTOM edge (power button, reset, LEDs, speaker).
   MECH-5: No component >15 mm tall in zone x=0..30 (PSU shadow zone).
   MECH-6: M3 mount holes at four corners, 5 mm inset. 7×7 mm keep-out around each.
   ```

   MECH-1: ISA expansion slots at TOP edge (rear panel). Card fingers toward y=0.
   MECH-2: Rear I/O bracket connectors (keyboard, serial, parallel, VGA) at TOP edge.
   MECH-3: AT power connectors P8/P9 at RIGHT edge (PSU bay).
   MECH-4: Front panel header at BOTTOM edge (power button, reset, LEDs, speaker).
   MECH-5: No component >15 mm tall in zone x=0..30 (PSU shadow zone).
   MECH-6: M3 mount holes at four corners, 5 mm inset. 7×7 mm keep-out around each.

   ```

**Outputs (DB writes):**

- One `functional_blocks` row: `name=MECHANICAL_ARCH`, `category=OTHER`,
  `notes=<full Mechanical Constraints Summary text>`

This block is intentionally not connected to any other block. It exists solely as a
persistent, queryable record of the mechanical decisions that all downstream LLM steps
must read before writing their own outputs.

**Helper scripts:**

- `db_write_arch.py` — pass a single block with `category: "OTHER"` and the full
  Mechanical Constraints Summary in `notes`

**Downstream contract:**

- **Step 0.5** reads `MECHANICAL_ARCH.notes` before selecting any IC or connector.
  Every connector placement decision must satisfy the MECH-N rules.
- **Step 1** uses MECH-N rules to set `edge:` requirements for every connector in the BOM.
  If MECH-1 says ISA slots at TOP, every ISA slot gets `edge: top` in `requirements`.
- **Step 2** uses MECH-N rules for board dimensions and mount hole positions.
- **Step 4** promotes MECH-N rules to `FIXED` constraints with `hard=1`.

---

## Step 0.5 — Hardware Architecture

**Engine:** LLM + Web

**Goal:** Before selecting any specific IC, decompose the system into functional blocks, identify dominant IC families, and document architectural decisions. This is the senior hardware architect review that shapes everything downstream.

**Inputs:**

- `design_sessions.prompt`
- Web: reference schematics, design guides, application notes (e.g. "BCM2712 hardware design guide", "RPi 4 schematic")

**Processing:**

1. Decompose into functional blocks: Compute, Memory, Power, IO, Clocking, Debug, RF (as applicable)
2. For each block: identify 1–2 preferred IC families with rationale (cost, ecosystem, thermal, availability)
3. Identify critical interfaces: bus type, speed, width, termination requirements (e.g. LPDDR4X requires matched-length differential pairs)
4. Flag hard constraints early: thermal budget, power envelope, RF coexistence, regulatory (FCC Part 15, CE), BOM cost ceiling
5. Document architectural decisions in ADR (Architecture Decision Record) format: decision, rationale, alternatives considered, risk
6. Produce ASCII block diagram(s) showing logical block topology and signal flow
7. Render architecture document to Markdown file

**Outputs (DB writes):**

- `functional_blocks(version_id, name, category, notes)`
- `block_connections(version_id, from_block_id, to_block_id, interface_type, critical)`
- `architecture_decisions(version_id, decision, rationale, alternatives, risk)`
- `architecture_artifacts(version_id, file_path)` → points to `architecture.md`

**Helper scripts:**

- `web_search.py` — query search API, return ranked results
- `db_write_functional_blocks.py`
- `db_write_block_connections.py`
- `db_write_arch_decisions.py`
- `render_arch_doc.py` — write `architecture.md` with ASCII diagram

---

## Step 1 — Design Capture (BOM + Netlist)

**Engine:** LLM + Web

**Goal:** Translate the architecture into a concrete Bill of Materials and a logical netlist — the component list and how they connect electrically.

**Inputs:**

- `functional_blocks` (from Step 0.5) — block names and preferred IC families
- `architecture_decisions` — selected ICs and rationale
- Web: datasheets, reference schematics, IBIS models

**Processing:**

1. For each functional block: select a specific IC (manufacturer, part number, package)
2. Define all relevant nets grouped by type:
   - **PWR** — power rails (VDD_CORE, VDD_IO, 3V3, 5V0, VBUS)
   - **GND** — ground references
   - **SIG** — single-ended signals (GPIO, SPI, UART, I²C)
   - **DIFF** — differential pairs (USB, PCIe, HDMI, LPDDR DQ/DQS)
3. Assign each component's relevant pins to nets (logical connectivity)
4. Capture per-component layout requirements as key-value pairs:
   - `near: XTAL` — MCU must be close to crystal
   - `far: switching_reg` — ADC must be away from switching regulators
   - `max_temp_c: 85` — thermal requirement
   - `edge: USB_CONN` — connector must be at board edge

**Outputs (DB writes):**

- `components(version_id, name, type, package, datasheet_url, notes)`
- `nets(version_id, name, type)`
- `net_connections(net_id, component_id, pin_name)`
- `requirements(component_id, key, value)`

**Helper scripts:**

- `web_search.py`
- `db_write_components.py`
- `db_write_nets.py`
- `db_write_requirements.py`

---

## Step 2 — Board Definition

**Engine:** LLM + Web + Python

**Goal:** Define the physical PCB canvas: board outline, mechanical constraints, keep-out zones, and mounting provisions.

**Inputs:**

- `design_sessions.prompt` — any stated form factor or mechanical requirements
- `architecture_decisions` — connector types inform board edge positions
- Web: IPC-2221 standard, form factor specifications (Raspberry Pi HAT spec, Mini-ITX, etc.)

**Processing:**

1. Select or derive board outline dimensions (width × height in mm)
2. Set grid resolution (typically 0.5 mm or 1.0 mm) — defines occupancy grid cell size
3. Define keep-out zones:
   - RF antenna clearance (typically ≥5 mm ground-free zone)
   - High-voltage creepage/clearance (IPC-2221 Table 6-1)
   - Mechanical features (connector mating envelopes, heatsink footprints)
   - Board-edge component-free margin (typically 2–3 mm per IPC-7351)
4. Place mounting holes (M2.5 or M3, matching target enclosure or HAT spec)
5. Python validates: outline is non-zero, no keep-out zone exceeds board boundary

**Outputs (DB writes):**

- `board_outline(version_id, width_mm, height_mm, grid_resolution, layer_count)`
- `keep_out_zones(version_id, x_mm, y_mm, width_mm, height_mm, reason)`
- `mount_holes(version_id, x_mm, y_mm, diameter_mm)`

**Helper scripts:**

- `web_search.py`
- `db_write_board_outline.py`
- `db_write_keepouts.py`
- `db_write_mount_holes.py`
- `validate_board.py` — checks outline validity and zone bounds

---

## Step 3 — Component Geometry Resolution

**Engine:** LLM + Web + Python

**Goal:** Attach physical dimensions to every component so the placer knows how much board area each IC, connector, or passive occupies, including courtyard (assembly clearance envelope).

**Inputs:**

- `components` — all components needing geometry
- Web: manufacturer datasheets, JEDEC package standards (JESD30), IPC-7351 land pattern library

**Processing:**

1. Query each component's datasheet for:
   - Package body dimensions (width × height in mm)
   - Courtyard margin (IPC-7351 default: 0.25–0.5 mm per side)
   - Allowed placement rotations (0°/90°/180°/270°)
2. Extract key pin locations relative to component origin (bottom-left corner):
   - For connectors: mating-direction pin row
   - For ICs: power, ground, and critical signal pins
3. If datasheet unavailable: use JEDEC package standard dimensions as fallback; flag in notes
4. Python validates: all components have geometry before proceeding to Step 4

**Outputs (DB writes):**

- `component_geometry(component_id, width_mm, height_mm, courtyard_margin, allowed_rotations)`
- `pins(component_id, pin_name, rel_x_mm, rel_y_mm)`

**Helper scripts:**

- `db_read_components.py` — SELECT components missing geometry
- `web_search.py`
- `db_write_geometry.py`
- `db_write_pins.py`
- `validate_geometry.py` — assert 100% geometry coverage before lock

---

## Step 4 — Constraint Derivation

**Engine:** LLM + Web

**Goal:** Translate electrical and mechanical requirements into placement constraints the optimizer can score against. This is the PCB engineering domain knowledge step.

**Inputs:**

- `nets` + `net_connections` — electrical topology (which components share which nets)
- `requirements` — per-component layout requirements from Step 1
- Web: IC datasheet layout guidelines, application notes (e.g. "BCM2712 layout recommendations")

**Processing:**
Derive constraints of four types:

- **NEAR** — components that must be placed close together:
  - Decoupling capacitors to their IC power pins (max 1–2 mm)
  - Crystal oscillator to MCU (max 5 mm, minimise stray capacitance)
  - DDR memory to processor (matched-length topology)
  - PMIC to SoC (short VDD_CORE path = lower IR drop)
- **FAR** — components that must be separated:
  - Switching regulators from ADC inputs (EMI coupling)
  - RF sections from digital logic (isolation >10 mm typical)
  - High-current paths from sensitive analog
- **FIXED** — components pinned to specific board locations:
  - Edge connectors (USB, HDMI, Ethernet) — must be at board perimeter
  - Mounting holes — already placed in Step 2
  - Status LEDs — human-accessible face
- **ALIGN** — components that must share an axis:
  - Connectors on the same edge → aligned to board edge
  - DDR devices in a row (parallel termination topology)

Each constraint records: type, comp_a, comp_b (if applicable), min/max distance, weight (soft penalty multiplier), hard flag (hard=1 means violation = reject, hard=0 = penalty).

**Outputs (DB writes):**

- `constraints(version_id, type, comp_a_id, comp_b_id, min_dist_mm, max_dist_mm, weight, hard, reason)`

**Helper scripts:**

- `db_read_nets.py`
- `db_read_requirements.py`
- `web_search.py`
- `db_write_constraints.py`

---

## Step 5 — Design Lock

**Engine:** Python only

**Goal:** Freeze the design input. Once locked, no component, net, or constraint can be added to this version. All downstream steps operate on an immutable snapshot.

**Inputs:**

- All tables from Steps 0–4 for this `version_id`

**Processing:**

1. Validate all foreign keys (no orphaned placements, constraints referencing missing components)
2. Assert 100% geometry coverage (every component in `components` has a row in `component_geometry`)
3. Compute SHA-256 hash of `components` + `component_geometry` + `constraints` table contents
4. Set `design_versions.status = 'LOCKED'` and store the hash
5. DB trigger `trg_version_no_unlock` prevents future DRAFT reversion; `trg_components_immutable` blocks new inserts into this version

**Outputs (DB writes):**

- `design_versions(status=LOCKED, hash, locked_at)`

**Helper scripts:**

- `validate_fk.py`
- `validate_geometry.py`
- `hash_design.py`
- `db_lock_version.py`

---

## Step 6 — Initial Placement

**Engine:** Python only

**Goal:** Produce a legal starting placement for the optimizer — all components on the board, no overlaps, respecting FIXED constraints.

**Inputs:**

- `design_versions` (LOCKED)
- `component_geometry` — bounding boxes for all components
- `constraints` — FIXED constraints first, then NEAR groups
- `board_outline` + `keep_out_zones`

**Processing:**

1. Create a new `optimization_runs` row (run_id anchors all placement + score data)
2. Place FIXED components first (edge connectors, mounting-adjacent parts)
3. Cluster components by NEAR constraint groups — place each cluster as a unit
4. Fill remaining components greedily into available board area (row-by-row, respecting courtyard margins)
5. Write occupancy grid: each 1 mm² cell records which component_id occupies it

**Outputs (DB writes):**

- `optimization_runs(version_id, algorithm, params)`
- `placements(run_id, component_id, x_mm, y_mm, rotation, status=PLACED|FIXED)`
- `occupancy_grid(run_id, cell_x, cell_y, component_id)`

**Helper scripts:**

- `db_read_locked.py`
- `placer_greedy.py`
- `db_write_placements.py`
- `db_write_grid.py`

---

## Step 7 — Optimization (Simulated Annealing)

**Engine:** Python only

**Goal:** Iteratively improve placement quality by minimising a weighted penalty function using simulated annealing — the industry-standard metaheuristic for PCB placement.

**Inputs:**

- `placements` + `occupancy_grid` (from Step 6)
- `constraints` — penalty weights and hard flags
- `net_connections` — for net length estimation (half-perimeter bounding box, HPWL)

**Processing:**
Each iteration:

1. Propose a random move: translate component / rotate 90° / swap two components
2. Compute new penalty score:
   - `constraint_penalty` = Σ weight × distance_violation for NEAR/FAR constraints
   - `overlap_penalty` = large constant × number of overlapping component pairs
   - `net_length_est` = HPWL across all nets (proxy for routing difficulty)
   - `total_penalty` = constraint_penalty + overlap_penalty + net_length_est
3. Accept if score improves; accept with probability e^(−ΔE/T) if score worsens (Metropolis criterion)
4. Decrease temperature T at each iteration: T = T × cooling_rate
5. Record score per iteration in `score_history`

Terminate when T < T_min or max_iterations reached.

**Outputs (DB writes):**

- `score_history(run_id, iteration, total_penalty, constraint_penalty, overlap_penalty, net_length_est)`
- `placements` — UPDATE x_mm, y_mm, rotation on each accepted move
- `occupancy_grid` — UPDATE cell ownership on each accepted move

**Helper scripts:**

- `db_read_placements.py`
- `optimizer_annealing.py`
- `scorer.py`
- `db_write_score_history.py`

---

## Step 8 — Scoring + Violation Report

**Engine:** Python only

**Goal:** Evaluate the final placement against all constraints and produce a complete violation report — the equivalent of a DRC (Design Rule Check) for placement.

**Inputs:**

- Best iteration from `score_history` (lowest `total_penalty`)
- `placements` at that iteration
- `constraints` — all hard and soft rules

**Processing:**

1. Select best-scoring iteration as the final placement
2. Re-score against every constraint:
   - For NEAR: actual Euclidean distance between component centroids vs. `max_dist_mm`
   - For FAR: actual distance vs. `min_dist_mm`
   - For FIXED: deviation from required position
   - `delta_mm` = actual_dist − required_dist (negative = violation)
3. Count hard violations (hard=1 constraints with delta_mm < 0) — these block approval
4. Summarise: `final_penalty`, `violation_count`, `hard_violation_count`, `net_length_total`

**Outputs (DB writes):**

- `violations(run_id, constraint_id, actual_dist_mm, delta_mm)`
- `placement_score(run_id, final_penalty, violation_count, hard_violation_count, net_length_total)`

**Helper scripts:**

- `db_read_final_placement.py`
- `scorer.py`
- `db_write_violations.py`
- `db_write_score.py`

---

## Step 9 — LLM Review + Decision

**Engine:** LLM

**Goal:** Interpret the violation report with PCB engineering judgment. Either approve the floorplan or modify constraints and trigger a new optimisation cycle.

**Inputs:**

- `violations` JOIN `constraints` — human-readable violation list with reasons
- `score_history` — convergence curve (did the optimizer converge or plateau?)
- `placement_score` — summary metrics

**Processing:**

1. Categorise violations by severity:
   - Hard violations with large delta → must fix (e.g. FIXED connector not at board edge)
   - Soft violations with small delta → acceptable tradeoff (e.g. decoupling cap 2.1 mm instead of 2.0 mm)
2. Diagnose root cause: Is it a constraint weight issue? A board area problem? Conflicting constraints?
3. Choose action:
   - **APPROVE** — acceptable placement, proceed to render
   - **MODIFY** — adjust constraint weights or distances, create new `design_version`, re-run Steps 5–8
   - **RERUN** — same constraints, re-run optimizer from Step 6 (different random seed may escape local minimum)

**Outputs (DB writes):**

- `review_notes(run_id, note, action)`
- `constraints` — updated weight/dist values if action=MODIFY
- `design_versions(status=DRAFT)` — new version row if action=MODIFY

**Helper scripts:**

- `db_read_violations.py`
- `db_read_score_history.py`
- `db_write_review.py`
- `db_write_constraints.py` — only if action=MODIFY
- `db_new_version.py` — only if action=MODIFY

---

## Step 10 — Render + Export

**Engine:** Python only

**Goal:** Produce publication-quality visual artifacts and a human-readable report from the final approved placement.

**Inputs:**

- `placements` — final component positions and rotations
- `occupancy_grid` — for density heatmap
- `board_outline` + `keep_out_zones` + `mount_holes`
- `violations` + `placement_score` — for report
- `score_history` — for convergence plot

**Processing:**

1. **SVG** (via shapely + svgwrite): layer-coloured vector floorplan — board outline, keep-outs, component bounding boxes labelled with ref-des, net zones
2. **PNG** (via cairocffi): raster render at 150 DPI with:
   - PCB-green substrate
   - Copper-coloured component outlines
   - Semi-transparent zone fills (GND pour, power pour)
   - Pad markers at pin locations
   - Silkscreen labels
3. **Heatmap PNG** (via cairocffi + numpy): occupancy density grid — highlights congested areas a layout engineer should examine
4. **Report HTML** (via Jinja2): full design summary — architecture decisions, BOM table, constraint list, violation table, convergence plot, floorplan image

**Outputs (DB writes):**

- `render_artifacts(run_id, type, file_path, created_at)` — one row per artifact

**Helper scripts:**

- `db_read_final_state.py`
- `render_svg.py`
- `render_png.py`
- `render_heatmap.py`
- `render_report.py`
- `db_write_artifacts.py`
