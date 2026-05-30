"""Unit tests for :mod:`exlab_wizard.ui.theme`.

The theme module renders the ``:root { ... }`` CSS block from the design
constants. We assert the block contains every documented token verbatim
so DESIGN.md and the rendered CSS cannot drift.
"""

from __future__ import annotations

from exlab_wizard.ui import design, theme


def test_root_css_includes_compact_density_rule() -> None:
    """Phase 5 / §4.8: the compact-density rule scopes file-row padding to
    the .exlab-density-compact card class (sp-1 vs the default sp-2)."""

    css = theme.build_root_css()
    assert ".exlab-density-compact td" in css
    assert "var(--sp-1)" in css


def test_root_css_includes_tree_selection_rule() -> None:
    """Phase 5 / OQ-6: the selected tree node gets the same fill + accent bar
    as a selected file row, keyed on Quasar's .q-tree__node--selected."""

    css = theme.build_root_css()
    assert ".q-tree__node--selected" in css
    assert "var(--color-row-selected)" in css
    assert "var(--color-row-selected-bar)" in css


def test_root_css_contains_primary_palette() -> None:
    css = theme.build_root_css()
    for value in (
        design.COLOR_NAVY,
        design.COLOR_BLUE,
        design.COLOR_GOLD,
        design.COLOR_BG,
        design.COLOR_BORDER,
        design.COLOR_RULE,
        design.COLOR_MUTED,
        design.COLOR_BODY,
    ):
        assert value in css, value


def test_root_css_contains_okabe_ito_palette() -> None:
    css = theme.build_root_css()
    for value in (
        design.OI_ORANGE,
        design.OI_SKY,
        design.OI_GREEN,
        design.OI_VERMILION,
        design.OI_BLUE,
        design.OI_PURPLE,
        design.OI_YELLOW,
        design.OI_GREY,
    ):
        assert value in css, value


def test_root_css_contains_ui_refresh_surface_tokens() -> None:
    """The main-window refresh tokens are emitted into the :root block."""

    css = theme.build_root_css()
    for token in (
        "--color-pane-header",
        "--color-zebra",
        "--color-row-selected",
        "--color-row-selected-bar",
    ):
        assert token in css, token


def test_root_css_emits_previously_undefined_referenced_vars() -> None:
    """Regression guard: every var(--…) referenced by a UI component is emitted.

    Three (later four) variables were referenced by shipping components but
    never declared in the :root block, so their declarations silently dropped
    (e.g. the new-file highlight was invisible). This test pins the specific
    vars and, more broadly, scans the component tree so a future component
    cannot reintroduce the latent-dead-styling bug class.
    """

    css = theme.build_root_css()
    # The four that were undefined before the 2026-05-29 refresh.
    for token in (
        "--color-highlight",
        "--color-link",
        "--color-bg-subtle",
        "--color-on-info",
    ):
        assert f"{token}:" in css, f"{token} must be declared in :root"

    # Broad guard: collect literal var(--name) references under ui/ and assert
    # each is declared. Dynamic f-string refs (var(--color-{...})) and the
    # spacing loop (--sp-N) are excluded — they resolve at format time.
    import re
    from pathlib import Path

    ui_dir = Path(__file__).resolve().parents[3] / "src" / "exlab_wizard" / "ui"
    referenced: set[str] = set()
    pattern = re.compile(r"var\((--[a-z][a-z0-9-]*)\)")
    for py in ui_dir.rglob("*.py"):
        for match in pattern.finditer(py.read_text(encoding="utf-8")):
            referenced.add(match.group(1))

    declared = set(re.findall(r"(--[a-z][a-z0-9-]+):", css))
    # --sp-1..16 are emitted by the spacing loop; ensure they count as declared.
    missing = {v for v in referenced if v not in declared}
    assert not missing, f"referenced but undeclared CSS vars: {sorted(missing)}"


def test_root_css_contains_semantic_aliases() -> None:
    css = theme.build_root_css()
    assert "--color-success" in css
    assert "--color-info" in css
    assert "--color-warning" in css
    assert "--color-danger" in css


def test_root_css_contains_typography_tokens() -> None:
    css = theme.build_root_css()
    assert "IBM Plex Sans" in css
    assert "ui-monospace" in css
    assert "--font-body" in css
    assert "--font-mono" in css


def test_root_css_contains_spacing_scale() -> None:
    css = theme.build_root_css()
    for key in design.SPACING:
        assert f"--sp-{key}" in css


def test_root_css_contains_radius_tokens() -> None:
    css = theme.build_root_css()
    assert "--radius-sm" in css
    assert "--radius-md" in css
    assert "--radius-lg" in css


def test_root_css_contains_shadow_tokens() -> None:
    css = theme.build_root_css()
    assert "--shadow-sm" in css
    assert "--shadow-md" in css
    # Shadow-lg is reserved for modals / hero panels but the token must
    # still be declared so component CSS can reference it where allowed.
    assert "--shadow-lg" in css


def test_root_css_navy_tinted_shadows() -> None:
    """All shadows are navy-tinted (DESIGN.md §04)."""

    css = theme.build_root_css()
    assert "rgba(0,54,96" in css


def test_root_css_motion_tokens() -> None:
    css = theme.build_root_css()
    assert "--ease-out" in css
    assert "--transition" in css


def test_root_css_starts_with_root_block() -> None:
    """The block opens with ``:root {`` per DESIGN.md §07."""

    css = theme.build_root_css()
    assert css.lstrip().startswith(":root {")


def test_root_css_includes_body_resets() -> None:
    """A body / heading / mono reset block follows the ``:root {}``."""

    css = theme.build_root_css()
    assert "body {" in css
    assert "var(--color-body)" in css
    assert "var(--font-body)" in css


def test_resolve_assets_dir_points_at_repo_assets_in_source_layout() -> None:
    """In source layout, the resolver returns ``<repo>/assets/``."""

    assets_dir = theme.resolve_assets_dir()
    assert assets_dir.is_dir()
    assert (assets_dir / "sync_local.svg").is_file()
    assert (assets_dir / "sync_cloud.svg").is_file()
