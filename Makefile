PYTHON     := python3
DB         := db/floorplan.db
SCHEMA     := db/schema.sql
SCRIPTS    := .opencode/skills/pcb-floorplanner/scripts
MD_FILES   := AGENTS.md README.md $(wildcard .opencode/skills/pcb-floorplanner/*.md) \
              $(wildcard .opencode/skills/pcb-floorplanner/references/*.md)
VENV_PYTHON := $(shell command -v python3)

.PHONY: help db-init db-verify db-status db-summary format lint test qa example-386 example-uno example-rpi4

help:
	@echo ""
	@echo "  PCB Floorplanner — available targets"
	@echo ""
	@echo "  db-init        Wipe and reinitialise $(DB) from schema (asks before overwriting)"
	@echo "  db-init FORCE=1  Wipe and reinitialise without prompting (for automation)"
	@echo "  db-verify      Run schema integrity tests against live DB"
	@echo "  db-status      Show design versions and optimization runs in live DB"
	@echo "  db-summary     Show component count, violations, and latest score"
	@echo "  format         Auto-format Python (ruff) and Markdown (markdownlint --fix)"
	@echo "  lint           Check Python (ruff) and Markdown (markdownlint)"
	@echo "  test           Run full test suite (unit + integration)"
	@echo "  qa             Run format, lint, and test"
	@echo "  example-386    Run the 386 mainboard example via opencode"
	@echo "  example-uno    Run the Arduino Uno Rev3 example via opencode"
	@echo "  example-rpi4   Run the Raspberry Pi 4 Model B example via opencode"
	@echo ""

# ── db-init ───────────────────────────────────────────────────────────────────
db-init:
ifeq ($(FORCE),1)
	$(PYTHON) db/db_init.py --force --db $(DB)
else
	@if [ -f "$(DB)" ]; then \
		printf "$(DB) already exists. Remove and re-initialise? [y/N] "; \
		read ans; \
		case "$$ans" in \
			[yY]*) echo "Removing $(DB)..."; rm -f "$(DB)" ;; \
			*)     echo "Aborted."; exit 0 ;; \
		esac; \
	fi
	$(PYTHON) db/db_init.py --db $(DB)
endif
	@echo "✓ $(DB) initialised"

# ── db-verify ─────────────────────────────────────────────────────────────────
db-verify:
	@echo "Running schema integrity tests against live DB..."
	$(PYTHON) -m pytest tests/unit/test_schema.py -v

# ── db-status ─────────────────────────────────────────────────────────────────
db-status:
	$(PYTHON) db/db_status.py $(DB)

# ── db-summary ────────────────────────────────────────────────────────────────
db-summary:
	$(PYTHON) db/db_summary.py $(DB)

# ── lint ──────────────────────────────────────────────────────────────────────
lint:
	$(PYTHON) -m ruff check db/ $(SCRIPTS)/ tests/
	markdownlint --config .markdownlint.json $(MD_FILES)

# ── format ────────────────────────────────────────────────────────────────────
format:
	$(PYTHON) -m ruff format db/ $(SCRIPTS)/ tests/
	markdownlint --config .markdownlint.json --fix $(MD_FILES)

# ── test ──────────────────────────────────────────────────────────────────────
test:
	$(PYTHON) -m pytest tests/ -v --tb=short

# ── qa ────────────────────────────────────────────────────────────────────────
qa: format lint test

# ── example-386 ───────────────────────────────────────────────────────────────
example-386:
	opencode run \
		"Create a floorplan of a 386 mainboard which fits onto an A5 format box. If you have questions - assume the answer with a diy enthusiast mindset. Finally send the floorplan and the list of components and their task to me via telegram."

# ── example-uno ───────────────────────────────────────────────────────────────
example-uno:
	opencode run \
		"Create a floorplan of an Arduino Uno Rev3 board. The board is 68.6 x 53.4 mm. Key components: ATmega328P MCU, CH340G USB-serial bridge, 16 MHz crystal, 7805 5V regulator, USB-B connector, ICSP header, power barrel jack, and the standard digital/analog/power pin headers. If you have questions - assume the answer with a diy enthusiast mindset. Finally send the floorplan and the list of components and their task to me via telegram."

# ── example-rpi4 ──────────────────────────────────────────────────────────────
example-rpi4:
	opencode run \
		"Create a floorplan of a Raspberry Pi 4 Model B. Base the component placement on the official Raspberry Pi 4 Model B layout. If you have questions - assume the answer with a diy enthusiast mindset."
