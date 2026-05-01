PYTHON     := python3
DB         := db/floorplan.db
SCHEMA     := db/schema.sql
SCRIPTS    := .opencode/skills/pcb-floorplanner/scripts
VENV_PYTHON := $(shell command -v python3)

.PHONY: help db-init db-verify db-status db-summary lint test

help:
	@echo ""
	@echo "  PCB Floorplanner — available targets"
	@echo ""
	@echo "  db-init      Initialise $(DB) from schema (asks before overwriting)"
	@echo "  db-verify    Run schema integrity tests against live DB"
	@echo "  db-status    Show design versions and optimization runs in live DB"
	@echo "  db-summary   Show component count, violations, and latest score"
	@echo "  lint         Run ruff linter over all Python sources"
	@echo "  test         Run full test suite (unit + integration)"
	@echo ""

# ── db-init ───────────────────────────────────────────────────────────────────
db-init:
	@if [ -f "$(DB)" ]; then \
		printf "$(DB) already exists. Remove and re-initialise? [y/N] "; \
		read ans; \
		case "$$ans" in \
			[yY]*) echo "Removing $(DB)..."; rm -f "$(DB)" ;; \
			*)     echo "Aborted."; exit 0 ;; \
		esac; \
	fi
	$(PYTHON) db/db_init.py
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

# ── test ──────────────────────────────────────────────────────────────────────
test:
	$(PYTHON) -m pytest tests/ -v --tb=short
