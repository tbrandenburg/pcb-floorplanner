"""db_status.py — prints design versions and optimization runs from the live DB."""
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).parent / "floorplan.db"
if len(sys.argv) > 1:
    DB = Path(sys.argv[1])

conn = sqlite3.connect(str(DB))

print("=== Design versions ===")
rows = conn.execute(
    "SELECT id, status, hash, created_at FROM design_versions ORDER BY id"
).fetchall()
for r in rows:
    print(f"  v{r[0]:2d}  {r[1]:<8}  hash={str(r[2])[:12]:<12}  {r[3]}")
if not rows:
    print("  (none)")

print()
print("=== Optimization runs ===")
rows = conn.execute(
    "SELECT id, version_id, algorithm, created_at FROM optimization_runs ORDER BY id"
).fetchall()
for r in rows:
    print(f"  run {r[0]:2d}  v{r[1]}  algo={r[2]:<20}  {r[3]}")
if not rows:
    print("  (none)")
print()
