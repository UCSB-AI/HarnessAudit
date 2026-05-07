#!/usr/bin/env bash
set -euo pipefail

main="${1:-neurips_2026.tex}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
build="$script_dir/latex-build.sh"

if command -v latexmk >/dev/null 2>&1; then
  latexmk -pdf -pvc -interaction=nonstopmode -file-line-error -synctex=1 "$main"
  exit 0
fi

if command -v inotifywait >/dev/null 2>&1; then
  "$build" "$main" || true
  while inotifywait -r -e close_write,create,delete,move \
      --include '.*\.(tex|bib|sty|pdf|png|jpg|jpeg)$' .; do
    "$build" "$main" || true
  done
  exit 0
fi

if command -v fswatch >/dev/null 2>&1; then
  "$build" "$main" || true
  fswatch -0 . | while read -r -d '' _event; do
    "$build" "$main" || true
  done
  exit 0
fi

snapshot() {
  find . -maxdepth 3 -type f \( \
    -name '*.tex' -o -name '*.bib' -o -name '*.sty' -o \
    -name '*.pdf' -o -name '*.png' -o -name '*.jpg' -o -name '*.jpeg' \
  \) -exec stat -c '%Y %n' {} \; 2>/dev/null | sort
}

echo "No latexmk/inotifywait/fswatch found; using 2s polling watcher."
"$build" "$main" || true
last="$(snapshot)"
while true; do
  sleep 2
  current="$(snapshot)"
  if [[ "$current" != "$last" ]]; then
    last="$current"
    "$build" "$main" || true
  fi
done
