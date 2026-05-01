"""db_summary.py — prints a human-readable summary of the live floorplan DB."""

import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).parent / "floorplan.db"
if len(sys.argv) > 1:
    DB = Path(sys.argv[1])

conn = sqlite3.connect(str(DB))

n_comp = conn.execute("SELECT COUNT(*) FROM components").fetchone()[0]
n_net = conn.execute("SELECT COUNT(*) FROM nets").fetchone()[0]
n_const = conn.execute("SELECT COUNT(*) FROM constraints").fetchone()[0]
n_runs = conn.execute("SELECT COUNT(*) FROM optimization_runs").fetchone()[0]
score = conn.execute(
    "SELECT final_penalty, violation_count, net_length_total FROM placement_score ORDER BY run_id DESC LIMIT 1"
).fetchone()

print(f"  Components : {n_comp}")
print(f"  Nets       : {n_net}")
print(f"  Constraints: {n_const}")
print(f"  Opt runs   : {n_runs}")
print()
if score:
    print("  Latest score")
    print(f"    Penalty    : {score[0]:.2f}")
    print(f"    Violations : {score[1]}")
    print(f"    Net length : {score[2]:.1f} mm")
else:
    print("  No scores yet")
