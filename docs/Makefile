# Standard sphinx-build wrapper. Run `make help` for the full list of
# available targets; the most common are:
#
#   make html         -- build the HTML site under build/html
#   make clean        -- remove the build directory
#   make linkcheck    -- verify external links resolve
#
# CI uses `sphinx-build -W -b html docs/source docs/build/html` directly
# so warnings are treated as errors; this Makefile is for local
# convenience. Layout matches `sphinx-quickstart --sep`.

SPHINXOPTS    ?=
SPHINXBUILD   ?= sphinx-build
SOURCEDIR     = source
BUILDDIR      = build

.PHONY: help Makefile

help:
	@$(SPHINXBUILD) -M help "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

%: Makefile
	@$(SPHINXBUILD) -M $@ "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)
