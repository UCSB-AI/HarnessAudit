#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "--check-tools" ]]; then
  for tool in latexmk tectonic pdflatex bibtex; do
    if command -v "$tool" >/dev/null 2>&1; then
      printf "%-10s %s\n" "$tool" "$(command -v "$tool")"
    else
      printf "%-10s missing\n" "$tool"
    fi
  done
  exit 0
fi

main="${1:-neurips_2026.tex}"
job="${main%.tex}"

missing_tools() {
  cat <<'MSG' >&2
No local LaTeX engine was found.

Recommended installs:
  Ubuntu/Debian:
    sudo apt-get install latexmk texlive-latex-extra texlive-fonts-recommended texlive-bibtex-extra
  macOS:
    brew install --cask mactex-no-gui
  Lightweight alternative:
    brew install tectonic

After installing, run:
  make pdf
  make watch
MSG
}

if command -v latexmk >/dev/null 2>&1; then
  latexmk -pdf -interaction=nonstopmode -file-line-error -synctex=1 "$main"
elif command -v tectonic >/dev/null 2>&1; then
  tectonic --keep-logs --synctex "$main"
elif command -v pdflatex >/dev/null 2>&1; then
  pdflatex -interaction=nonstopmode -file-line-error -synctex=1 "$main"
  if [[ -f "$job.aux" ]] && command -v bibtex >/dev/null 2>&1; then
    bibtex "$job" || true
  fi
  pdflatex -interaction=nonstopmode -file-line-error -synctex=1 "$main"
  pdflatex -interaction=nonstopmode -file-line-error -synctex=1 "$main"
else
  missing_tools
  exit 127
fi
