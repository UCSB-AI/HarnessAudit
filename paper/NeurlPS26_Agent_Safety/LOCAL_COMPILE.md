# Local Compile

From this directory:

```bash
make pdf
```

To recompile automatically after edits:

```bash
make watch
```

The build scripts prefer `latexmk`, then fall back to `tectonic`, then
`pdflatex` plus `bibtex`.

If no TeX engine is installed, install one first:

```bash
# Ubuntu/Debian
sudo apt-get install latexmk texlive-latex-extra texlive-fonts-recommended texlive-bibtex-extra

# macOS
brew install --cask mactex-no-gui

# Lightweight alternative
brew install tectonic
```

Useful targets:

```bash
make check-tools
make clean
make distclean
```
