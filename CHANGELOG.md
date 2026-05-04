# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

---

## [v0.1.2] — 2026-05-04

### Changed

- Added `output/` to `.gitignore` and removed all generated output files from version control

---

## [v0.1.1] — 2026-05-04

---

## [v0.1.1] — upcoming

### Fixed

- Tightened `_is_corner_adjacent` tolerance and added pipeline iteration loop
- Used two keep-out sentinels in placer to prevent mount-hole overlap
- Enforced mount hole inside `is_mount_clearance` keep-out at write time
- Exempted FIXED components from mount hole overlap checks in validator
- Removed unused variable flagged by ruff in test_scorer

### Changed

- Updated README hero image with latest RPi 4B floorplan (run 10)
- Removed board-specific example from keep-out positioning rule in SKILL.md

---

## [v0.1.0] — 2026-05-03

---

## [0.1.0] — 2026-05-03

Initial release of the LLM-guided PCB floorplanner.

### Added

- Core 11-step pipeline: session → architecture → BOM → board → geometry →
  constraints → lock → greedy placement → SA optimiser → violations → render
- 24-table SQLite schema with FK enforcement, CHECK constraints, and
  immutability triggers on locked design versions
- Greedy placer with FIXED → NEAR-priority → fill ordering; FIXED components
  bypass keep-out cells so edge connectors seat correctly
- Simulated annealing optimiser with keep-out penalty (500×area), boundary
  safety for swap moves, and score-history convergence tracking
- Shared scorer: overlap, NEAR/FAR/ALIGN constraint, HPWL net-length, keep-out,
  and FIXED edge-distance penalties
- Step 0.25 mechanical architecture step — board-edge budgeting and corner
  conflict detection before BOM lock
- Edge-budget check gate (`db_check_edge_budget.py`) with corner-conflict
  reporting
- Pre-render placement validation gate (`db_validate_placements.py`) — blocks
  render on mount-hole / keep-out / overlap violations
- Violation persistence and LLM review decision (APPROVE / REVISE / REJECT)
- PNG render via cairocffi: per-type component colours, dashed courtyard,
  keep-out shading, mount holes, violation outlines, score-convergence SVG
- Heatmap render (occupancy density) per run
- HTML report: BOM table, constraints table, violations table, score SVG
- `render_artifacts` table records output file paths per run
- `db_patch_board.py` — safe geometry patch on locked versions (atomically
  drops/recreates immutability triggers)
- `db_status.py` / `db_summary.py` — quick DB inspection helpers
- Auto-init DB on first `connect()` — no manual `make db-init` required for
  new users
- Component colour rendering: case-insensitive type lookup, full palette for
  SOC / MCU / SDRAM / PMIC / USB\_HUB / ETH\_PHY / CRYSTAL / CONNECTOR etc.,
  deterministic md5-derived hue fallback for unknown types
- Make targets: `db-init`, `db-verify`, `db-status`, `db-summary`, `format`,
  `lint`, `test`, `qa`, `example-386`, `example-uno`, `example-rpi4`
- Pre-push QA hook: ruff format + lint + markdownlint + full test suite
- 131-test suite: unit (schema, scorer, placer, board write, patch, edge budget,
  validate placements, write violations) + integration (placer invariants,
  SA optimiser)
- OpenCode skill packaging (`pcb-floorplanner.skill`)
- Example prompts: 386 mainboard, Arduino Uno Rev3, Raspberry Pi 4 Model B

### Fixed

- FIXED connectors now slide along their edge axis on collision rather than
  being nudged inward
- Keep-out violations enforced for all components including FIXED on
  non-mount-clearance zones
- Unused imports and variables removed (ruff lint)
- Duplicate pipeline steps and bare fenced code blocks in SKILL.md / README
  cleaned up

[Unreleased]: https://github.com/tbrandenburg/pcb-floorplanner/compare/v0.1.2...HEAD
[v0.1.2]: https://github.com/tbrandenburg/pcb-floorplanner/compare/v0.1.1...v0.1.2
[v0.1.1]: https://github.com/tbrandenburg/pcb-floorplanner/compare/v0.1.0...v0.1.1
[v0.1.0]: https://github.com/tbrandenburg/pcb-floorplanner/compare/v0.0.0...v0.1.0
[0.1.0]: https://github.com/tbrandenburg/pcb-floorplanner/releases/tag/v0.1.0
