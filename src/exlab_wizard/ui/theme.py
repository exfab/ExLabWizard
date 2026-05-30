"""Theme registration for NiceGUI / Quasar.

Per Frontend Spec §2.1.1, this module is the single place that injects the
canonical ``:root { ... }`` CSS block (DESIGN.md §07). Component CSS uses
``var(--color-*)`` / ``var(--sp-*)`` / ``var(--text-*)`` / ``var(--radius-*)``
/ ``var(--shadow-*)`` references instead of inline literals.

The block is generated from :mod:`exlab_wizard.ui.design` so DESIGN.md and
``design.py`` cannot drift.
"""

from __future__ import annotations

import sys
from pathlib import Path

from exlab_wizard.logging import get_logger
from exlab_wizard.ui import design

_log = get_logger(__name__)

_STATIC_ASSETS_MOUNTED = False


def build_root_css() -> str:
    """Render the canonical ``:root { ... }`` CSS block from design tokens.

    The string is intentionally derived from constants in
    :mod:`exlab_wizard.ui.design` so a token change automatically lands in
    the generated CSS without per-call-site updates.
    """

    spacing_lines = "\n".join(f"  --sp-{key}: {value};" for key, value in design.SPACING.items())

    return (
        ":root {\n"
        f"  --color-navy:    {design.COLOR_NAVY};\n"
        f"  --color-blue:    {design.COLOR_BLUE};\n"
        f"  --color-gold:    {design.COLOR_GOLD};\n"
        f"  --color-white:   {design.COLOR_SURFACE};\n"
        f"  --color-bg:      {design.COLOR_BG};\n"
        f"  --color-surface: {design.COLOR_SURFACE};\n"
        f"  --color-border:  {design.COLOR_BORDER};\n"
        f"  --color-rule:    {design.COLOR_RULE};\n"
        f"  --color-muted:   {design.COLOR_MUTED};\n"
        f"  --color-body:    {design.COLOR_BODY};\n"
        f"  --color-heading: {design.COLOR_HEADING};\n"
        # Main-window UI-refresh surface tokens + three vars components already
        # referenced but the block never emitted (latent dead styling): without
        # these, the new-file highlight, breadcrumb link/bar tint, and relay-
        # badge text colour silently resolved to empty and were dropped.
        f"  --color-bg-subtle:        {design.COLOR_BG_SUBTLE};\n"
        f"  --color-pane-header:      {design.COLOR_PANE_HEADER};\n"
        f"  --color-highlight:        {design.COLOR_HIGHLIGHT};\n"
        f"  --color-zebra:            {design.COLOR_ZEBRA};\n"
        f"  --color-row-selected:     {design.COLOR_ROW_SELECTED};\n"
        f"  --color-row-selected-bar: {design.COLOR_ROW_SELECTED_BAR};\n"
        f"  --color-link:             {design.COLOR_LINK};\n"
        f"  --color-on-info:          {design.COLOR_ON_INFO};\n"
        f"  --oi-orange:    {design.OI_ORANGE};\n"
        f"  --oi-sky:       {design.OI_SKY};\n"
        f"  --oi-green:     {design.OI_GREEN};\n"
        f"  --oi-vermilion: {design.OI_VERMILION};\n"
        f"  --oi-blue:      {design.OI_BLUE};\n"
        f"  --oi-purple:    {design.OI_PURPLE};\n"
        f"  --oi-yellow:    {design.OI_YELLOW};\n"
        f"  --oi-grey:      {design.OI_GREY};\n"
        f"  --color-success: {design.COLOR_SUCCESS};\n"
        f"  --color-info:    {design.COLOR_INFO};\n"
        f"  --color-warning: {design.COLOR_WARNING};\n"
        f"  --color-danger:  {design.COLOR_DANGER};\n"
        f"  --font-display: {design.FONT_DISPLAY};\n"
        f"  --font-body:    {design.FONT_BODY};\n"
        f"  --font-mono:    {design.FONT_MONO};\n"
        f"  --text-xs:   {design.TEXT_XS};\n"
        f"  --text-sm:   {design.TEXT_SM};\n"
        f"  --text-base: {design.TEXT_BASE};\n"
        f"  --text-md:   {design.TEXT_MD};\n"
        f"  --text-lg:   {design.TEXT_LG};\n"
        f"  --text-xl:   {design.TEXT_XL};\n"
        f"  --text-2xl:  {design.TEXT_2XL};\n"
        f"  --text-3xl:  {design.TEXT_3XL};\n"
        f"  --text-4xl:  {design.TEXT_4XL};\n"
        f"{spacing_lines}\n"
        f"  --radius-sm: {design.RADIUS_SM};\n"
        f"  --radius:    {design.RADIUS};\n"
        f"  --radius-md: {design.RADIUS_MD};\n"
        f"  --radius-lg: {design.RADIUS_LG};\n"
        f"  --shadow-sm: {design.SHADOW_SM};\n"
        f"  --shadow:    {design.SHADOW};\n"
        f"  --shadow-md: {design.SHADOW_MD};\n"
        f"  --shadow-lg: {design.SHADOW_LG};\n"
        f"  --ease-out:   {design.EASE_OUT};\n"
        f"  --transition: {design.TRANSITION};\n"
        "}\n"
        "body { "
        "background: var(--color-bg); "
        "color: var(--color-body); "
        "font-family: var(--font-body); "
        "font-size: var(--text-base); "
        "line-height: 1.65; "
        "}\n"
        "h1, h2, h3, h4, h5, h6 { "
        "font-family: var(--font-display); "
        "color: var(--color-heading); "
        "font-weight: 600; "
        "}\n"
        "code, kbd, samp, pre, .mono { font-family: var(--font-mono); }\n"
        # Compact file-list density (Phase 5 / §4.8): the Files card carries the
        # .exlab-density-compact class when ?density=compact; this tightens only
        # the file-row vertical padding (sp-1 vs the default sp-2 from Tailwind
        # .p-2). The descendant selector (0,1,1) outranks .p-2 (0,1,0) so no
        # !important is needed; left/right padding is left to .p-2.
        ".exlab-density-compact td { "
        "padding-top: var(--sp-1); padding-bottom: var(--sp-1); }\n"
        # Unified selection (Phase 5 / OQ-6): the selected tree node gets the
        # same fill + 3px inset accent bar as a selected file row
        # (file_list.row_background). Depends on Quasar tagging the selected
        # node header with .q-tree__node--selected (seeded from the URL's
        # ?selected= via build_tree).
        ".q-tree__node-header.q-tree__node--selected { "
        "background: var(--color-row-selected); "
        "box-shadow: inset 3px 0 0 var(--color-row-selected-bar); "
        "border-radius: var(--radius-sm); }\n"
    )


def mdi_font_head_html() -> str:
    """Return the ``<link>`` that loads the vendored MDI webfont from ``/assets``.

    Served by :func:`register_static_assets` (mounted before
    :func:`register_theme` in ``mount_ui``) and bundled offline under
    ``assets/fonts/mdi/`` so the frozen desktop app renders MDI glyphs with no
    network access. Quasar already understands ``mdi-*`` icon names once this
    stylesheet is present.
    """
    return (
        '<link rel="stylesheet" '
        'href="/assets/fonts/mdi/css/materialdesignicons.min.css">'
    )


def register_theme() -> str:
    """Register the design tokens with NiceGUI / Quasar at app start.

    Imports NiceGUI lazily so unit tests can exercise this module without
    NiceGUI's import side effects (the package opens browser channels at
    import time in some configurations).

    The ``:root`` block is injected with ``shared=True`` so it lands in the
    document head of *every* page rather than only the page being rendered
    at call time -- this is invoked once at app setup (from ``mount_ui``),
    before any client connects, so a per-client (``shared=False``) head
    injection would have nothing to attach to and the design tokens would
    never reach the browser.

    Returns the CSS string that was registered (handy for tests and for
    the static-asset bundler).
    """

    from nicegui import ui

    css = build_root_css()
    ui.add_head_html(f"<style>{css}</style>", shared=True)
    ui.add_head_html(mdi_font_head_html(), shared=True)
    _log.debug(
        "registered_theme_css",
        extra={"event": "ui.theme.registered", "bytes": len(css)},
    )
    return css


def resolve_assets_dir() -> Path:
    """Return the absolute path to the bundled ``assets/`` directory.

    Resolves both in source layout (``<repo>/assets/``) and in a
    PyInstaller-frozen layout (``sys._MEIPASS/assets/``).

    Source-layout walk: this file lives at
    ``<repo>/src/exlab_wizard/ui/theme.py`` so the four ``.parent`` hops
    are ``ui -> exlab_wizard -> src -> <repo>``.
    """
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "assets"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent.parent.parent / "assets"


def register_static_assets() -> Path:
    """Mount the project's ``assets/`` directory at ``/assets``.

    Idempotent: a module-level guard prevents double-mounting on import
    cycles. Returns the resolved assets directory.
    """
    global _STATIC_ASSETS_MOUNTED
    assets_dir = resolve_assets_dir()
    if _STATIC_ASSETS_MOUNTED:
        return assets_dir
    from nicegui import app as nicegui_app

    nicegui_app.add_static_files("/assets", str(assets_dir))
    _STATIC_ASSETS_MOUNTED = True
    _log.debug(
        "registered_static_assets",
        extra={"event": "ui.static.registered", "path": str(assets_dir)},
    )
    return assets_dir
