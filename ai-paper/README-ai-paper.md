# ai-paper

A 3-page build of the paper (pages 1-2: title, body, figures; page 3: references only), made from `../paper/` (which
is untouched). Format: ACM `acmart`, `11pt,sigconf,nonacm`, two columns, as in `../paper/sigconf.tex`.

- `paper.pdf`: the result.  `sigconf.tex`: source.  `sample-base.bib`: the existing entries plus five new ones
  (`smith1988`, `heiser_benchmarking_crimes`, `perf_wiki`, `linux_man_strace`, `linux_ftrace_docs`).
- `make_figures.py`: draws `latency_throughput.png` (dual y-axis), `fig_breakdown.png` and `fig_wakeup.png` from
  `../results` (`latency_run*.csv`, `throughput_run*.csv`, and the newest `results/trace/{latency,throughput}/*`);
  it needs `../scripts/make_paper_assets.py` and `analyze_*.py` for reading the summaries. No number is typed in.
- `build.sh`: regenerates the figures, runs `pdflatex`, `bibtex`, `pdflatex` twice, and copies the result to `paper.pdf`.

Numbers not taken from a table or figure come from `../results/clockres-run.log` (timers), the
`analyze_latency.py` / `analyze_throughput.py` output, and the `summary.txt` and `wakeup/` files of the two trace runs.

## Dependencies

`pdflatex`, `bibtex` and the `acmart` requirements. Without sudo, these were unpacked from the Fedora packages into
`~/texmf` and registered with `updmap-user`: `texlive-libertine`, `texlive-inconsolata`, `texlive-newtx`,
`texlive-fontaxes`, `texlive-kastrup` (binhex), `texlive-totpages`, `texlive-hyperxmp`, `texlive-comment`.
With sudo the equivalent is `sudo dnf install texlive-libertine texlive-inconsolata texlive-newtx texlive-fontaxes
texlive-kastrup texlive-totpages texlive-hyperxmp texlive-comment`.

## Changes to the preamble

`\include{caption}` was removed (there is no `caption.tex`, and `\include` forces a page break). Everything else above
`\maketitle` is unchanged.
