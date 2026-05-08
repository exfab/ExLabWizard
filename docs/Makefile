# Standard sphinx-build wrapper. Run `make help` for the full list of
# available targets; the most common are:
#
#   make html         -- build the HTML site under _build/html
#   make clean        -- remove the _build directory
#   make linkcheck    -- verify external links resolve
#
# CI uses `sphinx-build -W -b html docs docs/_build/html` directly so
# warnings are treated as errors; this Makefile is for local convenience.

SPHINXOPTS    ?=
SPHINXBUILD   ?= sphinx-build
SOURCEDIR     = .
BUILDDIR      = _build

.PHONY: help Makefile

help:
	@$(SPHINXBUILD) -M help "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

%: Makefile
	@$(SPHINXBUILD) -M $@ "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)
