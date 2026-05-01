# PCB Floorplanner

**Describe your hardware in plain English. Get a PCB floorplan in seconds.**

No schematic tools. No layout expertise required. Just tell the AI what you're building.

```text
"Create a floorplan for a Raspberry Pi clone with LPDDR4X, USB-C power,
 HDMI output, M.2 SSD slot, and 40-pin GPIO header."
```

→ Outputs a fully placed, optimised PCB floorplan as a PNG — with components respected, keep-out zones enforced, and net lengths minimised.

---

## How it works

An LLM-guided pipeline of 11 steps alternates between AI reasoning and deterministic Python:

1. **Describe** your board in natural language
2. **Architecture** — the AI reasons about ICs, connectors, and power domains
3. **BOM** — components, packages, and net connections are captured
4. **Board** — outline, keep-out zones, and mount holes are defined
5. **Geometry** — footprint sizes and courtyard margins per component
6. **Constraints** — NEAR/FAR/FIXED/ALIGN rules between components
7. **Lock** — design version is frozen; no further mutations allowed
8. **Place** — greedy placer respects keep-outs and edge constraints
9. **Optimise** — simulated annealing minimises wire length and violations
10. **Score** — violations reported; hard constraints flagged
11. **Render** — PNG floorplan + HTML report generated

All state lives in a single SQLite database (`db/floorplan.db`). Every step reads and writes through it — no files passed between steps.

---

## Output

| File | Description |
|---|---|
| `output/floorplan.png` | Component placement render with keep-out zones |
| `output/heatmap.png` | Occupancy density heatmap |
| `output/report.html` | Full BOM, constraints, violations, and score |

---

## Quick start

```bash
# Install dependencies
pip install cairocffi

# Initialise a fresh database
python db/db_init.py

# Run the full pipeline (LLM steps guided interactively or via your AI agent)
# See .opencode/skills/pcb-floorplanner/SKILL.md for the step-by-step workflow
```

---

## Skills

The pipeline is packaged as an [OpenCode](https://opencode.ai) skill:

```text
.opencode/skills/pcb-floorplanner/
├── SKILL.md          # Entrypoint — load this in your AI agent
├── references/       # Workflow and schema documentation
└── scripts/          # All pipeline scripts
```

Load the skill in your AI session and say: *"Create a floorplan for…"*
