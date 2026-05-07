MAIN ?= neurips_2026.tex

.PHONY: pdf watch clean distclean check-tools

pdf:
	@./scripts/latex-build.sh $(MAIN)

watch:
	@./scripts/latex-watch.sh $(MAIN)

clean:
	@if command -v latexmk >/dev/null 2>&1; then latexmk -c $(MAIN); fi
	@rm -f *.aux *.bbl *.bcf *.blg *.brf *.fdb_latexmk *.fls *.log *.out *.run.xml *.synctex.gz *.toc *.xdv

distclean:
	@if command -v latexmk >/dev/null 2>&1; then latexmk -C $(MAIN); fi
	@rm -f *.aux *.bbl *.bcf *.blg *.brf *.fdb_latexmk *.fls *.log *.out *.run.xml *.synctex.gz *.toc *.xdv *.pdf

check-tools:
	@./scripts/latex-build.sh --check-tools
