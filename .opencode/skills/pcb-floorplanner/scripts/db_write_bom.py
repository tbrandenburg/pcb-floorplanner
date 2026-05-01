"""
db_write_bom.py — Step 1
Write components, nets, net_connections, requirements.

Input JSON schema:
{
  "version_id": 1,
  "components": [
    {"name": "U1", "type": "SoC", "package": "BGA-485", "datasheet_url": "...", "notes": "BCM2712"}
  ],
  "nets": [
    {"name": "VDD_CORE", "type": "PWR"}
  ],
  "net_connections": [
    {"net_name": "VDD_CORE", "component_name": "U1", "pin_name": "VDD"}
  ],
  "requirements": [
    {"component_name": "U1", "key": "near", "value": "U2"}
  ]
}
Prints: {"components": N, "nets": N, "connections": N, "requirements": N}
"""

import argparse, json, sys
from pathlib import Path

# resolve db/ by walking up to repo root (db/db_init.py)
_here = Path(__file__).resolve()
_db_dir = next(p / "db" for p in _here.parents if (p / "db" / "db_init.py").exists())
sys.path.insert(0, str(_db_dir))
from db_init import connect, DEFAULT_DB


def write_bom(data: dict, db_path=DEFAULT_DB) -> dict:
    conn = connect(db_path)
    vid = data["version_id"]

    comp_map = {}
    for c in data.get("components", []):
        cid = conn.execute(
            "INSERT INTO components(version_id, name, type, package, datasheet_url, notes) VALUES (?,?,?,?,?,?)",
            (vid, c["name"], c["type"], c.get("package"), c.get("datasheet_url"), c.get("notes")),
        ).lastrowid
        comp_map[c["name"]] = cid

    net_map = {}
    for n in data.get("nets", []):
        nid = conn.execute(
            "INSERT INTO nets(version_id, name, type) VALUES (?,?,?)",
            (vid, n["name"], n["type"]),
        ).lastrowid
        net_map[n["name"]] = nid

    for nc in data.get("net_connections", []):
        conn.execute(
            "INSERT INTO net_connections(net_id, component_id, pin_name) VALUES (?,?,?)",
            (net_map[nc["net_name"]], comp_map[nc["component_name"]], nc["pin_name"]),
        )

    for r in data.get("requirements", []):
        conn.execute(
            "INSERT INTO requirements(component_id, key, value) VALUES (?,?,?)",
            (comp_map[r["component_name"]], r["key"], r["value"]),
        )

    conn.commit()
    conn.close()
    return {
        "components": len(comp_map),
        "nets": len(net_map),
        "connections": len(data.get("net_connections", [])),
        "requirements": len(data.get("requirements", [])),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", help="JSON string (else reads stdin)")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    payload = json.loads(args.data) if args.data else json.load(sys.stdin)
    print(json.dumps(write_bom(payload, args.db)))
