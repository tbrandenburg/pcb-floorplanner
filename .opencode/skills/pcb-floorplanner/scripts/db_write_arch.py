"""
db_write_arch.py — Step 0.5
Write architecture artefacts: functional blocks, block connections, decisions.
Reads JSON from stdin or --data argument.

Input JSON schema:
{
  "version_id": 1,
  "functional_blocks": [
    {"name": "Compute", "category": "COMPUTE", "notes": "BCM2712 SoC"}
  ],
  "block_connections": [
    {"from_name": "Compute", "to_name": "Memory", "interface_type": "LPDDR4X", "critical": 1}
  ],
  "decisions": [
    {"decision": "Use BCM2712", "rationale": "Best RPi ecosystem", "alternatives": "RK3588", "risk": "Supply chain"}
  ]
}
Prints: {"blocks_written": N, "connections_written": N, "decisions_written": N}
"""
import argparse, json, sys
from pathlib import Path
# resolve db/ by walking up to repo root (db/db_init.py)
_here = Path(__file__).resolve()
_db_dir = next(p / "db" for p in _here.parents if (p / "db" / "db_init.py").exists())
sys.path.insert(0, str(_db_dir))
from db_init import connect, DEFAULT_DB


def write_arch(data: dict, db_path=DEFAULT_DB) -> dict:
    conn = connect(db_path)
    vid = data["version_id"]

    # index block names → ids for connection linking
    name_to_id = {}
    for b in data.get("functional_blocks", []):
        bid = conn.execute(
            "INSERT INTO functional_blocks(version_id, name, category, notes) VALUES (?,?,?,?)",
            (vid, b["name"], b["category"], b.get("notes")),
        ).lastrowid
        name_to_id[b["name"]] = bid

    for c in data.get("block_connections", []):
        conn.execute(
            "INSERT INTO block_connections(version_id, from_block_id, to_block_id, interface_type, critical) VALUES (?,?,?,?,?)",
            (vid, name_to_id[c["from_name"]], name_to_id[c["to_name"]],
             c["interface_type"], c.get("critical", 0)),
        )

    for d in data.get("decisions", []):
        conn.execute(
            "INSERT INTO architecture_decisions(version_id, decision, rationale, alternatives, risk) VALUES (?,?,?,?,?)",
            (vid, d["decision"], d["rationale"], d.get("alternatives"), d.get("risk")),
        )

    conn.commit()
    conn.close()
    return {
        "blocks_written": len(data.get("functional_blocks", [])),
        "connections_written": len(data.get("block_connections", [])),
        "decisions_written": len(data.get("decisions", [])),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", help="JSON string (else reads stdin)")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    payload = json.loads(args.data) if args.data else json.load(sys.stdin)
    print(json.dumps(write_arch(payload, args.db)))
