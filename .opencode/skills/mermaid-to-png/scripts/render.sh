#!/usr/bin/env bash
# render.sh — Render a Mermaid diagram to PNG with built-in verification
# Usage: render.sh <input.mmd> <output.png> [--theme dark|default|neutral|forest] [--scale N] [--bg COLOR]
set -euo pipefail

INPUT="${1:-}"
OUTPUT="${2:-}"
THEME="dark"
SCALE="3"
BG="#0d1117"

# Parse optional flags (from remaining args only)
shift 2 2>/dev/null || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --theme=*) THEME="${1#*=}"; shift ;;
    --theme)   THEME="${2:-dark}"; shift 2 ;;
    --scale=*) SCALE="${1#*=}"; shift ;;
    --scale)   SCALE="${2:-3}"; shift 2 ;;
    --bg=*)    BG="${1#*=}"; shift ;;
    --bg)      BG="${2:-#0d1117}"; shift 2 ;;
    *) shift ;;
  esac
done

if [[ -z "$INPUT" ]]; then
  echo "Usage: render.sh <input.mmd> [output.png] [--theme dark|default|neutral|forest]" >&2
  exit 1
fi

if [[ ! -f "$INPUT" ]]; then
  echo "❌ Input file not found: $INPUT" >&2
  exit 1
fi

# Derive output path if not given
if [[ -z "$OUTPUT" ]]; then
  OUTPUT="${INPUT%.mmd}.png"
fi

# Ensure output directory exists
mkdir -p "$(dirname "$OUTPUT")"

echo "🔄 Rendering: $INPUT → $OUTPUT (theme: $THEME, scale: $SCALE, bg: $BG)"

# Write puppeteer config to allow sandbox-less rendering in Linux containers
PUPPETEER_CFG=$(mktemp /tmp/puppeteer-cfg.XXXXXX.json)
echo '{"args":["--no-sandbox","--disable-setuid-sandbox"]}' > "$PUPPETEER_CFG"
trap 'rm -f "$PUPPETEER_CFG"' EXIT

# Render via mermaid CLI
if ! mmdc -i "$INPUT" -o "$OUTPUT" --theme "$THEME" --backgroundColor "$BG" --scale "$SCALE" -p "$PUPPETEER_CFG" 2>&1; then
  echo "❌ mmdc failed — check Mermaid syntax in: $INPUT" >&2
  exit 2
fi

# Verification: file must exist and be non-empty
if [[ ! -f "$OUTPUT" ]]; then
  echo "❌ Verification failed: output file was not created: $OUTPUT" >&2
  exit 3
fi

SIZE=$(stat -c%s "$OUTPUT" 2>/dev/null || stat -f%z "$OUTPUT")
if [[ "$SIZE" -lt 100 ]]; then
  echo "❌ Verification failed: output file is suspiciously small (${SIZE} bytes): $OUTPUT" >&2
  exit 4
fi

echo "✅ Rendered successfully: $OUTPUT (${SIZE} bytes)"
