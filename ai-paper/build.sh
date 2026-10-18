#!/usr/bin/env bash
# Build ai-paper/paper.pdf from sigconf.tex (run from anywhere).
#   ./build.sh
# Needs pdflatex + bibtex and the acmart dependencies (libertine, inconsolata, newtx, fontaxes, kastrup/binhex,
# totpages, hyperxmp, comment). Without sudo they were installed under ~/texmf (see README-ai-paper.md).
set -euo pipefail
cd "$(dirname "$0")"
python3 -B make_figures.py            # regenerates fig_where.png from ../results (no hand-typed numbers)
pdflatex -interaction=nonstopmode -halt-on-error sigconf.tex > build.log 2>&1
bibtex sigconf >> build.log 2>&1
pdflatex -interaction=nonstopmode -halt-on-error sigconf.tex >> build.log 2>&1
pdflatex -interaction=nonstopmode -halt-on-error sigconf.tex >> build.log 2>&1
cp sigconf.pdf paper.pdf
echo "paper.pdf: $(pdfinfo paper.pdf | grep Pages)"
grep -n "undefined\|Overfull \\\\hbox\|^!" sigconf.log || true
