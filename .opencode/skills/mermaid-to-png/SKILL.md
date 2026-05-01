---
name: mermaid-to-png
description: Render Mermaid diagram source (.mmd) to PNG with integrated verification. Use when the user wants to convert a Mermaid diagram to an image — e.g. "render this diagram as PNG", "convert Mermaid to PNG", "create a diagram image", "visualize this flowchart/sequence/class/ER diagram". Supports all Mermaid diagram types (flowchart, sequence, class, ER, Gantt, pie, etc.) with GitHub Dark Theme by default. Verifies output existence and size after rendering.
---

# Mermaid to PNG

Render Mermaid diagram source to PNG using `mmdc` (Mermaid CLI) with built-in verification.

## Prerequisites

`mmdc` must be available:
```bash
which mmdc || npm install -g @mermaid-js/mermaid-cli
```

## Output path (situational)

Derive the output directory in this priority order:

1. **Explicit request** — if the user specifies a path, use it exactly.
2. **`workspace/output/mermaid/`** — if a `workspace/` directory exists in CWD.
3. **`output/mermaid/`** — if an `output/` directory exists in CWD.
4. **`/tmp/mermaid/`** — last-resort fallback.

Use the diagram's logical name as filename.

## Workflow

### 1. Determine output directory

```bash
if [ -d "workspace" ]; then
  OUTPUT_DIR="workspace/output/mermaid"
elif [ -d "output" ]; then
  OUTPUT_DIR="output/mermaid"
else
  OUTPUT_DIR="/tmp/mermaid"
fi
```

### 2. Write the .mmd file

Save the Mermaid source to `$OUTPUT_DIR/<name>.mmd`.

### 3. Render via script

```bash
bash scripts/render.sh \
  "$OUTPUT_DIR/<name>.mmd" \
  "$OUTPUT_DIR/<name>.png" \
  --theme dark \
  --scale 3
```

Available themes: `dark` (default), `default`, `neutral`, `forest`
Available scale: any positive integer, default `3` (≈3× resolution, ~300 DPI equivalent)

### 4. Verification (built-in)

The script automatically verifies:
- `mmdc` exit code = 0 (valid Mermaid syntax)
- Output file exists
- File size ≥ 100 bytes (non-trivial image)

On failure the script exits with a descriptive error code and message — report the exact error to the user.

### 5. Report result

Report the output path and file size. If the render failed, show the exact error message and suggest fixing the Mermaid syntax.

## Common Mermaid Syntax Errors

- Missing `graph`/`flowchart` direction keyword
- Unclosed quotes in node labels
- `---` separators inside subgraph blocks
- Invalid arrow types (use `-->`, `->`, `==>`, `-.->`)

## Theme Notes

`dark` theme renders white lines on transparent background — ideal for dark-mode docs/slides.
Use `default` for light backgrounds or print output.
