$pdf_mode = 1;
$interaction = 'nonstopmode';
$synctex = 1;
$max_repeat = 5;

$pdflatex = 'pdflatex -interaction=nonstopmode -file-line-error -synctex=1 %O %S';
$bibtex = 'bibtex %O %B';

@default_files = ('neurips_2026.tex');

$clean_ext = 'synctex.gz run.xml bcf nav snm vrb xdv';
$cleanup_includes_cusdep_generated = 1;
