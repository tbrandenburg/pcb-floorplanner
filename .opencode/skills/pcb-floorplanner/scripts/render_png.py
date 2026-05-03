"""
render_png.py — Step 10
Render floorplan + heatmap to PNG using cairocffi.
Reads final placement from DB for given run_id.

Usage: python render_png.py --run_id 1 --out_dir output/
Produces: floorplan.png, heatmap.png
Writes paths to render_artifacts table.
"""

import argparse, json, math, sys
from pathlib import Path

_here = Path(__file__).resolve()
_db_dir = next(p / "db" for p in _here.parents if (p / "db" / "db_init.py").exists())
sys.path.insert(0, str(_db_dir))
from db_init import connect, DEFAULT_DB
from db_validate_placements import validate as validate_placements

import cairocffi as cairo
import numpy as np

SCALE = 8  # px per mm


def load_render_data(conn, run_id):
    board = conn.execute(
        """SELECT b.width_mm, b.height_mm, b.grid_resolution
           FROM board_outline b JOIN optimization_runs r ON r.version_id=b.version_id
           WHERE r.id=?""",
        (run_id,),
    ).fetchone()

    placements = conn.execute(
        """SELECT p.component_id, p.x_mm, p.y_mm, p.rotation, p.status,
                  g.width_mm, g.height_mm, g.courtyard_margin, c.name, c.type
           FROM placements p
           JOIN component_geometry g ON g.component_id=p.component_id
           JOIN components c ON c.id=p.component_id
           WHERE p.run_id=?""",
        (run_id,),
    ).fetchall()

    keep_outs = conn.execute(
        """SELECT k.x_mm, k.y_mm, k.width_mm, k.height_mm
           FROM keep_out_zones k JOIN optimization_runs r ON r.version_id=k.version_id
           WHERE r.id=?""",
        (run_id,),
    ).fetchall()

    mount_holes = conn.execute(
        """SELECT m.x_mm, m.y_mm, m.diameter_mm
           FROM mount_holes m JOIN optimization_runs r ON r.version_id=m.version_id
           WHERE r.id=?""",
        (run_id,),
    ).fetchall()

    violations = conn.execute("SELECT constraint_id, delta_mm FROM violations WHERE run_id=?", (run_id,)).fetchall()

    grid_cells = conn.execute("SELECT cell_x, cell_y FROM occupancy_grid WHERE run_id=?", (run_id,)).fetchall()

    return board, placements, keep_outs, mount_holes, violations, grid_cells


# ── colour palette ────────────────────────────────────────────────────────────
PCB_GREEN = (0.10, 0.28, 0.10)
BOARD_EDGE = (0.00, 1.00, 0.00)
KEEPOUT = (0.80, 0.10, 0.10, 0.35)
COPPER = (0.87, 0.71, 0.00)
SILK = (1.00, 1.00, 1.00)
FIXED_FILL = (0.20, 0.50, 0.80, 0.75)
FREE_FILL = (0.20, 0.70, 0.30, 0.65)
VIOLATION = (1.00, 0.20, 0.20, 0.50)
HOLE = (0.05, 0.05, 0.05)

# Keys are upper-cased at lookup time so "SoC", "soc", "SOC" all match.
# Colours chosen for maximum perceptual separation on a dark-green PCB background.
COMPONENT_COLORS = {
    # Processors / compute
    "SOC":        (0.95, 0.75, 0.00, 0.90),   # vivid amber
    "MCU":        (0.95, 0.60, 0.00, 0.90),   # orange-amber
    "FPGA":       (0.85, 0.85, 0.00, 0.88),   # yellow
    "CPU":        (0.95, 0.70, 0.05, 0.90),   # deep amber
    # Memory
    "SDRAM":      (0.20, 0.55, 1.00, 0.85),   # bright blue
    "FLASH":      (0.10, 0.70, 1.00, 0.85),   # sky blue
    "EEPROM":     (0.30, 0.80, 1.00, 0.82),   # light blue
    "SRAM":       (0.15, 0.45, 0.90, 0.85),   # medium blue
    # Power
    "PMIC":       (1.00, 0.25, 0.10, 0.88),   # vivid red
    "LDO":        (1.00, 0.45, 0.20, 0.85),   # red-orange
    "DCDC":       (1.00, 0.35, 0.15, 0.85),   # deep red-orange
    "VREG":       (0.95, 0.30, 0.20, 0.85),   # crimson
    "FUSE":       (1.00, 0.65, 0.00, 0.85),   # gold-orange
    # Connectivity
    "USB_HUB":    (0.65, 0.20, 0.90, 0.82),   # purple
    "USB_CTRL":   (0.75, 0.30, 0.95, 0.82),   # violet
    "USB_PD":     (0.55, 0.15, 0.80, 0.82),   # dark purple
    "ETH_PHY":    (0.10, 0.85, 0.55, 0.82),   # teal-green
    "WIFI_BT":    (0.00, 0.90, 0.70, 0.82),   # cyan-green
    "BLUETOOTH":  (0.10, 0.75, 0.90, 0.82),   # cyan
    # Passives
    "RESISTOR":   (0.70, 0.70, 0.70, 0.70),   # light grey
    "CAPACITOR":  (0.55, 0.55, 0.65, 0.70),   # blue-grey
    "CAP":        (0.55, 0.55, 0.65, 0.70),   # alias
    "INDUCTOR":   (0.60, 0.60, 0.50, 0.70),   # warm grey
    # Timing / analog
    "CRYSTAL":    (0.95, 0.95, 0.95, 0.88),   # near-white
    "OSCILLATOR": (0.90, 0.90, 0.80, 0.85),   # off-white
    # I/O
    "CONNECTOR":  (0.30, 0.50, 0.90, 0.85),   # cornflower blue
    "HDMI":       (0.80, 0.80, 0.10, 0.82),   # yellow-olive
    "DP":         (0.75, 0.75, 0.15, 0.82),   # olive-yellow
    # Misc / audio / sensors
    "CODEC":      (0.90, 0.30, 0.60, 0.82),   # pink-magenta
    "AUDIO":      (0.85, 0.25, 0.55, 0.82),   # magenta
    "SENSOR":     (0.40, 0.90, 0.40, 0.82),   # lime green
    "LED":        (0.95, 0.95, 0.20, 0.85),   # bright yellow
    "DIODE":      (0.90, 0.50, 0.50, 0.80),   # salmon
    "TRANSISTOR": (0.65, 0.85, 0.40, 0.80),   # yellow-green
    "IC":         (0.50, 0.75, 0.80, 0.80),   # steel blue
}


def _component_color(ctype: str) -> tuple:
    """Case-insensitive lookup; falls back to a deterministic hue so every
    distinct type gets a unique colour even if not in the table above."""
    key = (ctype or "").upper()
    if key in COMPONENT_COLORS:
        return COMPONENT_COLORS[key]
    # Deterministic hue from type name hash — stays consistent across renders
    import hashlib
    h = int(hashlib.md5(key.encode()).hexdigest(), 16)
    hue = (h % 360) / 360.0
    # Convert HSV(hue, 0.75, 0.85) → RGB
    import colorsys
    r, g, b = colorsys.hsv_to_rgb(hue, 0.75, 0.85)
    return (r, g, b, 0.80)


def mm(v):
    return v * SCALE


def render_floorplan(run_id, out_dir, db_path=DEFAULT_DB):
    # Gate: block render if any placement violations exist
    check = validate_placements(run_id, db_path)
    if not check["ok"]:
        msg = "RENDER BLOCKED — placement violations detected:\n" + "\n".join(f"  {v}" for v in check["violations"])
        print(msg, file=sys.stderr)
        sys.exit(1)

    conn = connect(db_path)
    board, placements, keep_outs, mount_holes, violations, _ = load_render_data(conn, run_id)
    conn.close()

    W_mm, H_mm, _ = board
    W, H = int(W_mm * SCALE), int(H_mm * SCALE)

    violated_comps = set()
    for con_id, delta in violations:
        if delta < 0:
            violated_comps.add(con_id)

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    ctx = cairo.Context(surface)

    # background + board fill
    ctx.set_source_rgb(*PCB_GREEN)
    ctx.paint()

    # board edge
    ctx.set_source_rgb(*BOARD_EDGE)
    ctx.set_line_width(2)
    ctx.rectangle(0, 0, W, H)
    ctx.stroke()

    # subtle grid
    ctx.set_source_rgba(0.15, 0.35, 0.15, 0.4)
    ctx.set_line_width(0.4)
    for gx in range(0, int(W_mm) + 1, 5):
        ctx.move_to(mm(gx), 0)
        ctx.line_to(mm(gx), H)
        ctx.stroke()
    for gy in range(0, int(H_mm) + 1, 5):
        ctx.move_to(0, mm(gy))
        ctx.line_to(W, mm(gy))
        ctx.stroke()

    # keep-out zones
    for kx, ky, kw, kh in keep_outs:
        ctx.set_source_rgba(*KEEPOUT)
        ctx.rectangle(mm(kx), mm(ky), mm(kw), mm(kh))
        ctx.fill()
        ctx.set_source_rgba(0.8, 0.1, 0.1, 0.8)
        ctx.set_line_width(1)
        ctx.rectangle(mm(kx), mm(ky), mm(kw), mm(kh))
        ctx.stroke()

    # mount holes
    for hx, hy, hd in mount_holes:
        ctx.set_source_rgb(*COPPER)
        ctx.arc(mm(hx), mm(hy), mm(hd / 2 + 0.5), 0, 2 * math.pi)
        ctx.fill()
        ctx.set_source_rgb(*HOLE)
        ctx.arc(mm(hx), mm(hy), mm(hd / 2), 0, 2 * math.pi)
        ctx.fill()

    # components
    for row in placements:
        comp_id, x, y, rot, status, w, h, cyd, name, ctype = row
        color = _component_color(ctype)

        # courtyard (dashed outline)
        ctx.set_source_rgba(0.7, 0.7, 0.7, 0.3)
        ctx.set_line_width(0.5)
        ctx.set_dash([mm(0.5), mm(0.5)])
        ctx.rectangle(mm(x - cyd), mm(y - cyd), mm(w + 2 * cyd), mm(h + 2 * cyd))
        ctx.stroke()
        ctx.set_dash([])

        # component body fill
        ctx.set_source_rgba(*color)
        ctx.rectangle(mm(x), mm(y), mm(w), mm(h))
        ctx.fill()

        # outline
        outline_color = (1.0, 0.3, 0.3, 1.0) if comp_id in violated_comps else (0.9, 0.9, 0.9, 0.9)
        ctx.set_source_rgba(*outline_color)
        ctx.set_line_width(1.2)
        ctx.rectangle(mm(x), mm(y), mm(w), mm(h))
        ctx.stroke()

        # label
        ctx.set_source_rgb(*SILK)
        font_size = max(6, min(mm(min(w, h)) * 0.35, 14))
        ctx.set_font_size(font_size)
        ctx.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        te = ctx.text_extents(name)  # tuple: (x_bearing, y_bearing, width, height, x_advance, y_advance)
        te_w, te_h = te[2], te[3]
        tx = mm(x) + mm(w) / 2 - te_w / 2
        ty = mm(y) + mm(h) / 2 + te_h / 2
        if te_w < mm(w) * 0.95:
            ctx.move_to(tx, ty)
            ctx.show_text(name)

    # title
    ctx.set_source_rgb(*SILK)
    ctx.set_font_size(14)
    ctx.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    ctx.move_to(4, 14)
    ctx.show_text(f"PCB Floorplan  run_id={run_id}  {W_mm:.0f}x{H_mm:.0f}mm")

    out_path = Path(out_dir) / f"floorplan_run_{run_id:04d}.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    surface.write_to_png(str(out_path))
    return str(out_path)


def render_heatmap(run_id, out_dir, db_path=DEFAULT_DB):
    conn = connect(db_path)
    board, _, _, _, _, grid_cells = load_render_data(conn, run_id)
    conn.close()

    W_mm, H_mm, RES = board
    cols = int(W_mm / RES)
    rows = int(H_mm / RES)

    grid = np.zeros((rows, cols), dtype=float)
    for cx, cy in grid_cells:
        if 0 <= cx < cols and 0 <= cy < rows:
            grid[cy, cx] += 1.0

    W, H = int(W_mm * SCALE), int(H_mm * SCALE)
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    ctx = cairo.Context(surface)
    ctx.set_source_rgb(0.05, 0.05, 0.05)
    ctx.paint()

    cell_w = mm(RES)
    cell_h = mm(RES)
    max_val = grid.max() if grid.max() > 0 else 1.0

    for cy in range(rows):
        for cx in range(cols):
            v = grid[cy, cx] / max_val
            if v > 0:
                r = min(1.0, v * 2)
                g = max(0.0, 1.0 - v * 1.5)
                b = 0.0
                ctx.set_source_rgba(r, g, b, 0.85)
                ctx.rectangle(cx * cell_w, cy * cell_h, cell_w, cell_h)
                ctx.fill()

    ctx.set_source_rgba(0.0, 1.0, 0.0, 0.6)
    ctx.set_line_width(1.5)
    ctx.rectangle(0, 0, W, H)
    ctx.stroke()

    ctx.set_source_rgb(1, 1, 1)
    ctx.set_font_size(12)
    ctx.move_to(4, 14)
    ctx.show_text(f"Occupancy Heatmap  run_id={run_id}  (red=dense, dark=empty)")

    out_path = Path(out_dir) / f"heatmap_run_{run_id:04d}.png"
    surface.write_to_png(str(out_path))
    return str(out_path)


def run(run_id, out_dir, db_path=DEFAULT_DB):
    fp = render_floorplan(run_id, out_dir, db_path)
    hm = render_heatmap(run_id, out_dir, db_path)

    conn = connect(db_path)
    for atype, path in [("PNG", fp), ("HEATMAP", hm)]:
        conn.execute(
            "INSERT INTO render_artifacts(run_id, type, file_path) VALUES (?,?,?)",
            (run_id, atype, path),
        )
    conn.commit()
    conn.close()
    return {"floorplan": fp, "heatmap": hm}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run_id", type=int, required=True)
    ap.add_argument("--out_dir", default="output")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    print(json.dumps(run(args.run_id, args.out_dir, args.db)))
