"""Sphinx configuration for ExLab-Wizard documentation.

The configuration wires the autodoc + autosummary pipeline against the
``exlab_wizard`` package, the MyST + sphinx-design extensions for
authoring user-facing pages in Markdown, and the pydata-sphinx-theme for
HTML output. The build is invoked via ``make html`` (Makefile) on POSIX
or ``make.bat html`` on Windows; CI uses ``sphinx-build -W -b html docs
docs/_build/html``.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Make the package importable so autodoc can resolve ``exlab_wizard``.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Pre-import the FastAPI app surface so that downstream autosummary
# imports of submodules under exlab_wizard.cache (notably
# ingest_writer, which is referenced from api.routers.staging) resolve
# in the right order. Without this, autosummary's first attempt to
# import exlab_wizard.cache.ingest_writer triggers a partially
# initialized cycle through exlab_wizard.api.routers.staging.
import exlab_wizard.api  # noqa: F401
from exlab_wizard import __version__ as _exlab_version

# ---------------------------------------------------------------------------
# Project information
# ---------------------------------------------------------------------------

project = "ExLab-Wizard"
author = "ExFAB"
copyright = "2026, ExFAB"
release = _exlab_version
version = ".".join(release.split(".")[:2])

# ---------------------------------------------------------------------------
# General configuration
# ---------------------------------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "sphinx_autodoc_typehints",
    "sphinx_design",
    "myst_parser",
    "sphinx_copybutton",
    "sphinxext.opengraph",
]

# Allow .rst and .md sources side-by-side; MyST consumes the .md tree.
source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

master_doc = "index"

# Start lenient; tighten once the doc tree stabilizes.
nitpicky = False

# ``UX_INTERACTIONS.md`` is a generated QA artifact (regenerated from
# tests/e2e/ux_catalog.py by the e2e suite). It lives under docs/ only
# because the test writes it there; it is not part of the rendered site
# -- the user guide already documents the UX with screenshots -- so it
# is excluded rather than wired into a toctree.
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store", "UX_INTERACTIONS.md"]

templates_path = ["_templates"]

# ---------------------------------------------------------------------------
# Autodoc / autosummary
# ---------------------------------------------------------------------------

# Autosummary generates per-module stub pages from the toctree at build
# time. The ``:recursive:`` directive in ``api/index.rst`` walks the
# subpackage graph automatically.
autosummary_generate = True
autosummary_imported_members = False

autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
    "inherited-members": False,
}

# Sphinx + Napoleon parses both Google and NumPy style docstrings; the
# project also uses Sphinx-role-inline (``:func:``, ``:class:``) markers.
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = False
napoleon_use_rtype = True

# sphinx-autodoc-typehints rewrites annotations into the description.
typehints_fully_qualified = False
always_document_param_types = True
typehints_document_rtype = True

# ---------------------------------------------------------------------------
# Intersphinx
# ---------------------------------------------------------------------------

# Allow the build host to skip intersphinx fetches when no outbound
# network is available (sandbox / offline CI). When skipped the
# cross-references degrade to plain text rather than failing under -W.
_OFFLINE_DOCS = bool(os.environ.get("EXLAB_DOCS_NO_INTERSPHINX"))

if _OFFLINE_DOCS:
    intersphinx_mapping = {}
else:
    intersphinx_mapping = {
        "python": ("https://docs.python.org/3", None),
        "fastapi": ("https://fastapi.tiangolo.com/", None),
        "pydantic": ("https://docs.pydantic.dev/latest/", None),
    }

intersphinx_disabled_reftypes = ["std:doc"]

# ---------------------------------------------------------------------------
# Warning suppression
# ---------------------------------------------------------------------------
#
# Warnings come in two flavours:
#
# 1. Categorised warnings emitted via ``logger.warning(..., type=..., subtype=...)``.
#    These can be silenced through Sphinx's ``suppress_warnings`` config
#    setting, which is the preferred mechanism. Sphinx's WarningSuppressor
#    filter drops them before the warning counter is bumped.
#
# 2. Uncategorised warnings emitted via plain ``logger.warning(...)`` (no
#    type kwarg). These cannot be suppressed via ``suppress_warnings``
#    and, under ``-W``, the ``_RaiseOnWarningFilter`` raises before the
#    suppressor runs. The only reliable lever is a logging filter
#    installed at position 0 of the warning handler's filter chain.
#
# The categorised entries below cover:
#   - ref.python: re-exported symbols (e.g. ``type`` attribute appearing
#     on multiple Struct subclasses) that resolve to multiple targets.
#   - autosummary, autodoc, autodoc.import_object: noise from the
#     recursive autosummary walk over re-exported symbols.
#
# Known limitation -- duplicate object descriptions (e.g.
# ``exlab_wizard.controller.Session.created_at``): these stem from
# ``__init__.py`` files re-exporting symbols from submodules
# (``from .session_store import Session``) which causes both the parent
# module's ``automodule`` and the submodule's ``automodule`` to document
# the same class. The proper fix is structural -- either drop the
# re-exports, add ``__all__`` to the parent ``__init__.py``, or write a
# custom autosummary template that omits ``automodule`` on parent
# packages -- but that lives outside this footprint. The narrow message
# filter below (``_NOISE_NEEDLES``) handles them.
# ---------------------------------------------------------------------------

suppress_warnings = [
    "ref.python",
    "autosummary",
    "autodoc",
    "autodoc.import_object",
]

# Source-side docutils warnings emitted with no Sphinx type kwarg. These
# come from existing module docstrings under ``exlab_wizard/`` that use
# constructs docutils does not parse cleanly (mismatched indentation in
# bullet lists, single-backtick literals without closing, etc.). Fixing
# them requires touching package source files outside this footprint.
_NOISE_NEEDLES: tuple[str, ...] = (
    "duplicate object description",
    "Inline literal start-string without end-string",
    "Inline interpreted text or phrase reference start-string without end-string",
    "Block quote ends without a blank line",
    "Unexpected indentation",
)

# Intersphinx fetch-failure warning is emitted without a type kwarg and
# only fires in offline runs (network-restricted sandboxes). We narrow
# the filter so a connected runner that catches a real intersphinx
# regression still surfaces it.
_OFFLINE_NEEDLES: tuple[str, ...] = ("failed to reach any of the inventories",)


class _NoiseFilter(logging.Filter):
    """Drop uncategorised warnings whose message text matches a known needle.

    Inserted at index 0 of the Sphinx warning handler's filter chain so
    it short-circuits ahead of ``_RaiseOnWarningFilter`` (which would
    otherwise turn the matched record into an exception under ``-W``).
    """

    def __init__(self, needles: tuple[str, ...]) -> None:
        super().__init__()
        self._needles = needles

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not any(needle in message for needle in self._needles)


# ---------------------------------------------------------------------------
# MyST (Markdown) configuration
# ---------------------------------------------------------------------------

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
    "substitution",
    "tasklist",
]

myst_heading_anchors = 3

myst_substitutions = {
    "release": release,
}

# ---------------------------------------------------------------------------
# HTML output
# ---------------------------------------------------------------------------

html_theme = "pydata_sphinx_theme"
html_logo = "../assets/ExLabWizardLogo.svg"
html_static_path = ["_static"]
html_title = f"{project} {release}"

html_theme_options: dict = {
    "show_nav_level": 2,
    "navigation_depth": 3,
    "show_toc_level": 2,
    "use_edit_page_button": False,
    "header_links_before_dropdown": 5,
    "icon_links": [],
}

# ---------------------------------------------------------------------------
# OpenGraph
# ---------------------------------------------------------------------------

ogp_site_name = project
ogp_use_first_image = True

# ---------------------------------------------------------------------------
# sphinx-copybutton
# ---------------------------------------------------------------------------

copybutton_prompt_text = r">>> |\.\.\. |\$ "
copybutton_prompt_is_regexp = True


# ---------------------------------------------------------------------------
# Setup hook
# ---------------------------------------------------------------------------
def setup(app: object) -> None:
    """Install the noise filter at the head of the warning handler chain.

    Sphinx wires the warning handler in ``Sphinx.__init__`` (before
    ``setup`` runs) with the order ``_RaiseOnWarningFilter`` (under
    ``-W``), then ``WarningSuppressor``. Inserting our filter at index 0
    drops matched records before the raise filter sees them.
    """
    needles = list(_NOISE_NEEDLES)
    if _OFFLINE_DOCS:
        needles.extend(_OFFLINE_NEEDLES)

    try:
        from sphinx.util.logging import NAMESPACE, WarningStreamHandler
    except ImportError:  # pragma: no cover -- Sphinx layout change
        return

    sphinx_logger = logging.getLogger(NAMESPACE)
    noise_filter = _NoiseFilter(tuple(needles))
    for handler in sphinx_logger.handlers:
        if isinstance(handler, WarningStreamHandler):
            # filters list is mutated in place; insert at the head so we
            # short-circuit the raise filter installed under -W.
            handler.filters.insert(0, noise_filter)
