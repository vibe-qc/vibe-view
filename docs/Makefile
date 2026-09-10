# Minimal makefile for Sphinx documentation.
#
# `make html` renders into docs/_build/html. What CI publishes is
# `scripts/build_site.sh`, which wraps the same sphinx-build and adds
# robots.txt / sitemap.xml / .build-info -- use that to reproduce the live
# site exactly.
#
# `make strict` is the one to run before pushing a docs change: -W turns
# warnings into errors, which is how a broken cross-reference gets caught
# here rather than on the published site.

SPHINXOPTS    ?=
SPHINXBUILD   ?= sphinx-build
SOURCEDIR     = .
BUILDDIR      = _build

.PHONY: help html strict clean Makefile

help:
	@$(SPHINXBUILD) -M help "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

strict:
	@$(SPHINXBUILD) -b html -W --keep-going "$(SOURCEDIR)" "$(BUILDDIR)/html" $(SPHINXOPTS) $(O)

clean:
	@rm -rf "$(BUILDDIR)"

%: Makefile
	@$(SPHINXBUILD) -M $@ "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)
