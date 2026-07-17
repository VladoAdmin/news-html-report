#!/usr/bin/env bash
# Find the newest news-YYYY-MM-DD.md in source_dir (sorted by the filename's
# date string, not mtime) and render it to target_dir.
#
# Usage: ./render-daily.sh <target_dir> [source_dir=samples/]
set -euo pipefail

TARGET_DIR="${1:?usage: render-daily.sh <target_dir> [source_dir=samples/]}"
SOURCE_DIR="${2:-samples/}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

newest=""
for f in "$SOURCE_DIR"/news-????-??-??.md; do
    [ -e "$f" ] || continue
    base="$(basename "$f")"
    if [ -z "$newest" ] || [ "$base" \> "$(basename "$newest")" ]; then
        newest="$f"
    fi
done

if [ -z "$newest" ]; then
    echo "render-daily.sh: no news-YYYY-MM-DD.md found in $SOURCE_DIR" >&2
    exit 1
fi

exec python3 "$SCRIPT_DIR/render_news_html.py" "$newest" --out "$TARGET_DIR"
