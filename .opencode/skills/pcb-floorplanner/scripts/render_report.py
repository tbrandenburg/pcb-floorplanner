"""
render_report.py — Step 10
Generate HTML report: BOM table, constraints, violations, convergence plot, floorplan image.

Usage: python render_report.py --run_id 1 --out_dir output/
Writes: output/report.html, writes path to render_artifacts.
"""
import argparse, base64, json, sys
from pathlib import Path

_here = Path(__file__).resolve()
_db_dir = next(p / "db" for p in _here.parents if (p / "db" / "db_init.py").exists())
sys.path.insert(0, str(_db_dir))
from db_init import connect, DEFAULT_DB


def img_b64(path):
    p = Path(path)
    if p.exists():
        return base64.b64encode(p.read_bytes()).decode()
    return ""


def render_report(run_id, out_dir, db_path=DEFAULT_DB):
    conn = connect(db_path)

    # board info
    board = conn.execute(
        """SELECT b.width_mm, b.height_mm, b.layer_count, dv.hash, ds.prompt
           FROM board_outline b
           JOIN optimization_runs r ON r.version_id=b.version_id
           JOIN design_versions dv ON dv.id=r.version_id
           JOIN design_sessions ds ON ds.id=dv.session_id
           WHERE r.id=?""", (run_id,)
    ).fetchone()

    # BOM
    bom = conn.execute(
        """SELECT c.name, c.type, c.package, c.notes
           FROM components c JOIN optimization_runs r ON r.version_id=c.version_id
           WHERE r.id=? ORDER BY c.type, c.name""", (run_id,)
    ).fetchall()

    # constraints
    constraints = conn.execute(
        """SELECT ct.type, ca.name, cb.name, ct.min_dist_mm, ct.max_dist_mm,
                  ct.weight, ct.hard, ct.reason
           FROM constraints ct
           JOIN components ca ON ct.comp_a_id=ca.id
           LEFT JOIN components cb ON ct.comp_b_id=cb.id
           JOIN optimization_runs r ON r.version_id=ct.version_id
           WHERE r.id=? ORDER BY ct.type""", (run_id,)
    ).fetchall()

    # violations
    violations = conn.execute(
        """SELECT ct.type, ca.name, cb.name, ct.reason,
                  v.actual_dist_mm, v.delta_mm, ct.hard
           FROM violations v
           JOIN constraints ct ON v.constraint_id=ct.id
           JOIN components ca ON ct.comp_a_id=ca.id
           LEFT JOIN components cb ON ct.comp_b_id=cb.id
           WHERE v.run_id=? ORDER BY v.delta_mm""", (run_id,)
    ).fetchall()

    # score
    score_row = conn.execute(
        "SELECT final_penalty, violation_count, hard_violation_count, net_length_total FROM placement_score WHERE run_id=?",
        (run_id,)
    ).fetchone()

    # convergence data
    history = conn.execute(
        "SELECT iteration, total_penalty FROM score_history WHERE run_id=? ORDER BY iteration",
        (run_id,)
    ).fetchall()

    # artifact paths
    artifacts = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT type, file_path FROM render_artifacts WHERE run_id=?", (run_id,)
        ).fetchall()
    }
    conn.close()

    # encode images
    fp_b64 = img_b64(artifacts.get("PNG", ""))
    hm_b64 = img_b64(artifacts.get("HEATMAP", ""))

    # convergence sparkline via inline SVG
    if history:
        max_p = max(r[1] for r in history) or 1
        spark_pts = " ".join(
            f"{int(r[0] / history[-1][0] * 400)},{int((1 - r[1] / max_p) * 80)}"
            for r in history if history[-1][0] > 0
        )
        spark = f'<svg width="400" height="80" style="background:#111"><polyline points="{spark_pts}" fill="none" stroke="#0f0" stroke-width="1.5"/></svg>'
    else:
        spark = "<em>no convergence data</em>"

    # HTML generation (no Jinja2 dependency)
    def bom_rows():
        return "\n".join(
            f"<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2] or '—'}</td><td>{r[3] or ''}</td></tr>"
            for r in bom
        )

    def constraint_rows():
        return "\n".join(
            f"<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2] or '—'}</td>"
            f"<td>{r[3] or '—'}</td><td>{r[4] or '—'}</td>"
            f"<td>{r[5]}</td><td>{'HARD' if r[6] else 'soft'}</td><td>{r[7]}</td></tr>"
            for r in constraints
        )

    def violation_rows():
        rows = []
        for v in violations:
            hard = "HARD" if v[6] else "soft"
            cls = 'class="hard"' if v[6] and v[5] < 0 else ('class="soft"' if v[5] < 0 else '')
            rows.append(
                f"<tr {cls}><td>{v[0]}</td><td>{v[1]}</td><td>{v[2] or '—'}</td>"
                f"<td>{v[3]}</td><td>{v[4]:.2f}</td><td>{v[5]:.2f}</td><td>{hard}</td></tr>"
            )
        return "\n".join(rows)

    score_summary = f"""
    <b>Final penalty:</b> {score_row[0]:.1f} &nbsp;|&nbsp;
    <b>Violations:</b> {score_row[1]} &nbsp;|&nbsp;
    <b>Hard violations:</b> {score_row[2]} &nbsp;|&nbsp;
    <b>Net length est:</b> {score_row[3]:.1f} mm
    """ if score_row else "No score computed"

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>PCB Floorplan Report — run {run_id}</title>
<style>
  body {{ font-family: monospace; background: #111; color: #ccc; padding: 20px; }}
  h1,h2 {{ color: #0f0; }} h3 {{ color: #8f8; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 20px; }}
  th {{ background: #1a3a1a; color: #0f0; padding: 6px 10px; text-align: left; }}
  td {{ padding: 4px 10px; border-bottom: 1px solid #222; }}
  tr:hover td {{ background: #1a1a1a; }}
  tr.hard td {{ background: #3a0a0a; color: #f88; }}
  tr.soft td {{ background: #2a1a0a; color: #fa8; }}
  .score-box {{ background: #1a2a1a; border: 1px solid #0f0; padding: 10px; margin: 10px 0; }}
  img {{ max-width: 100%; border: 1px solid #0f0; margin: 10px 0; }}
  .meta {{ color: #888; font-size: 0.9em; }}
</style></head><body>
<h1>PCB Floorplan Report</h1>
<p class="meta">run_id={run_id} &nbsp;|&nbsp; Board: {board[0]}×{board[1]}mm, {board[2]}-layer
&nbsp;|&nbsp; Hash: {(board[3] or '')[:12]}...</p>
<p class="meta"><b>Prompt:</b> {board[4]}</p>

<div class="score-box">{score_summary}</div>

<h2>Floorplan</h2>
{'<img src="data:image/png;base64,' + fp_b64 + '">' if fp_b64 else '<em>floorplan.png not found</em>'}

<h2>Occupancy Heatmap</h2>
{'<img src="data:image/png;base64,' + hm_b64 + '">' if hm_b64 else '<em>heatmap.png not found</em>'}

<h2>Score Convergence</h2>
{spark}

<h2>Bill of Materials ({len(bom)} components)</h2>
<table><tr><th>Ref</th><th>Type</th><th>Package</th><th>Notes</th></tr>
{bom_rows()}</table>

<h2>Constraints ({len(constraints)})</h2>
<table><tr><th>Type</th><th>Comp A</th><th>Comp B</th><th>Min mm</th><th>Max mm</th><th>Weight</th><th>Hard</th><th>Reason</th></tr>
{constraint_rows()}</table>

<h2>Violations ({len(violations)})</h2>
<table><tr><th>Type</th><th>Comp A</th><th>Comp B</th><th>Reason</th><th>Actual mm</th><th>Delta mm</th><th>Severity</th></tr>
{violation_rows() if violations else '<tr><td colspan="7">No violations</td></tr>'}</table>

</body></html>"""

    out_path = Path(out_dir) / "report.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html)

    conn2 = connect(db_path)
    conn2.execute(
        "INSERT INTO render_artifacts(run_id, type, file_path) VALUES (?,?,?)",
        (run_id, "REPORT", str(out_path)),
    )
    conn2.commit()
    conn2.close()

    return {"report": str(out_path)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run_id", type=int, required=True)
    ap.add_argument("--out_dir", default="output")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    args = ap.parse_args()
    print(json.dumps(render_report(args.run_id, args.out_dir, args.db)))
