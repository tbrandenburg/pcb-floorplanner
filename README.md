# PCB Floorplanner

**Describe your hardware in plain English. Get a placed, optimised PCB floorplan.**

No schematic tools. No layout expertise required. Just tell the AI what you're building.

```text
"Create a floorplan for an 8-bit retro MIDI looper with USB-MIDI in,
 3.5mm AUX out, and control buttons — fits an Intel NUC-like case."
```

The pipeline reasons about ICs, places 40 components, runs simulated annealing,
scores violations, and renders a PNG — all in one session.

---

## How it works

An LLM-guided 11-step pipeline alternates between AI reasoning and deterministic Python.
All state lives in a single SQLite database (`db/floorplan.db`).
No data is passed between steps as arguments — the DB is the contract.

| Step | Name | Engine |
|---|---|---|
| 0 | User prompt intake | LLM |
| 0.5 | Hardware architecture — functional blocks, IC selection, ADRs | LLM + Web |
| 1 | BOM + netlist — components, packages, nets, requirements | LLM + Web |
| 2 | Board definition — outline, keep-outs, mount holes | LLM + Web + Python |
| 3 | Component geometry — footprint sizes, courtyard margins | LLM + Web + Python |
| 4 | Constraint derivation — NEAR/FAR/FIXED/ALIGN rules | LLM + Web |
| 5 | Design lock — version frozen, SHA-256 hash stored | Python |
| 6 | Initial placement — FIXED first, NEAR clusters, greedy fill | Python |
| 7 | Simulated annealing — minimises penalty across 5k–30k iterations | Python |
| 8 | Scoring + violation report — hard and soft violations flagged | Python |
| 9 | Render — PNG floorplan, occupancy heatmap, HTML report | Python |
| 9.5 | Visual inspection — adversarial checklist against rendered PNG | LLM |
| 10 | LLM review + decision — APPROVE / MODIFY / RERUN | LLM |

---

## Output

| File | Description |
|---|---|
| `output/floorplan.png` | PCB-green raster render — components, keep-outs, mount holes, labels |
| `output/heatmap.png` | Occupancy density heatmap — highlights congested zones |
| `output/report.html` | Full BOM table, constraint list, violation report, convergence plot |

---

## Quick start

```bash
# 1. Create a fresh database
python db/db_init.py
# Non-interactive (CI / automation):
python db/db_init.py --force
# Or via Makefile:
make db-init FORCE=1

# 2. Activate the virtualenv (cairocffi, shapely, matplotlib)
source .venv/bin/activate

# 3. Load the skill in your OpenCode session and say:
#    "Create a floorplan for …"
# The skill guides the full pipeline automatically.
```

---

## Make targets

| Target | Description |
|---|---|
| `make db-init` | Initialise `db/floorplan.db` (prompts before overwriting) |
| `make db-init FORCE=1` | Force-reinitialise without prompting |
| `make db-verify` | Run 17 schema integrity tests against live DB |
| `make db-status` | Show all design versions and optimisation runs |
| `make db-summary` | Component count, violations, and latest score |
| `make lint` | ruff over `db/`, `scripts/`, `tests/` |
| `make test` | Run all 86 tests (unit + integration) |
| `make qa` | format + lint + test |

---

## Key design rules

- **Immutability:** once a `design_version` is LOCKED, components and constraints cannot
  be added. Any post-lock change requires a new `design_versions` row.
- **FIXED connectors:** always set `hard=1` on FIXED edge-connector constraints. The scorer
  applies an extra `500 × delta_mm` penalty for hard FIXED violations, strongly incentivising
  SA to keep connectors at the board edge.
- **Mount hole keep-outs:** set `"is_mount_clearance": true` on corner keep-outs that
  intentionally surround a mount hole. This disables the annular-ring overlap check for
  that zone. The annular ring formula is `diameter_mm / 2 + 0.5 mm`.
- **SA reruns:** use `optimizer_annealing.py --overwrite` when re-running SA on an existing
  `run_id`. Without it the script aborts on a UNIQUE constraint in `score_history`.
- **Keep-out anti-pattern:** never define a keep-out that spans a full board edge — it blocks
  FIXED edge connectors. Use corner-only keep-outs for mount holes.

---

## Constraint types

| Type | Meaning | Key parameters |
|---|---|---|
| `NEAR` | Two components must be within `max_dist_mm` | `max_dist_mm`, `weight`, `hard` |
| `FAR` | Two components must be at least `min_dist_mm` apart | `min_dist_mm`, `weight`, `hard` |
| `FIXED` | Component must be at a board edge (connector, button) | `weight ≥ 10`, `hard=1` |
| `ALIGN` | Two components share a centroid axis | `weight` |

---

## Scoring

The penalty function used by simulated annealing:

```text
total_penalty = constraint_penalty + overlap_penalty + net_length_est + keep_out_penalty
```

- `constraint_penalty` — sum of weighted distance violations for NEAR/FAR/FIXED
- `overlap_penalty` — 100 × overlap area (mm²) per component pair (including courtyard)
- `net_length_est` — half-perimeter bounding box (HPWL) across all nets
- `keep_out_penalty` — 500 × overlap area (mm²) per component in a keep-out zone
- Hard FIXED violations add an extra `500 × delta_mm` on top of the soft penalty

---

## Tests

86 tests across unit and integration suites:

```text
tests/unit/
  test_schema.py          17 DB integrity tests (FK, UNIQUE, CHECK, immutability triggers)
  test_scorer.py          29 scorer unit tests (keep-out, overlap, NEAR/FAR/ALIGN/FIXED, HPWL,
                             hard flag propagation, hard=1 FIXED penalty amplification)
  test_placer.py          13 placer unit tests (cells_for, fits, snap, place_at)
  test_db_write_board.py   9 input validation tests (keep-out bounds, mount hole annular ring,
                             is_mount_clearance flag, full-edge keep-out warning)
  test_db_patch_board.py   4 trigger-bypass safety tests

tests/integration/
  test_placer_integration.py   4 tests — boundary, keep-out, overlap invariant, large component
  test_sa_optimizer.py         3 tests — improvement, keep-out elimination, no off-board placements
```

```bash
make test
# 86 passed
```

---

## Skill

The pipeline is packaged as an [OpenCode](https://opencode.ai) skill:

```text
.opencode/skills/pcb-floorplanner/
├── SKILL.md              Entrypoint — wire-format schemas, step guidance, pitfall warnings
├── references/
│   ├── workflow.md       Full step-by-step reference (EE terminology)
│   └── schema.md         All DB tables, columns, integrity guarantees
└── scripts/              All pipeline scripts (db_write_*, placer, optimizer, renderer)
```

Load the skill in your OpenCode session and say: *"Create a floorplan for…"*
