"""Unit tests for :mod:`exlab_wizard.ui.theme`.

The theme module renders the ``:root { ... }`` CSS block from the design
constants. We assert the block contains every documented token verbatim
so DESIGN.md and the rendered CSS cannot drift.
"""

from __future__ import annotations

from exlab_wizard.ui import design, theme


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
