"""Unit tests for the framed-pane helper (Redesign §4.1)."""

from __future__ import annotations

from exlab_wizard.ui.components.framed_pane import (
    body_style,
    card_style,
    framed_pane,
    header_style,
)


def test_card_style_uses_surface_border_shadow_radius_tokens() -> None:
    css = card_style()
    assert "var(--color-surface" in css
    assert "var(--color-border" in css
    assert "var(--shadow-sm" in css
    assert "var(--radius-md" in css
    # Flex column so the title strip stays fixed and the body scrolls.
    assert "flex-direction: column" in css
    assert "overflow: hidden" in css


def test_card_style_ends_with_semicolon_for_safe_concatenation() -> None:
    """The metadata pane appends 'overflow: auto;' -- card_style() must end
    with ';' so the concatenation is valid CSS (else both decls are dropped)."""
    assert card_style().rstrip().endswith(";")


def test_card_style_carries_literal_fallbacks() -> None:
    """Every token has a literal fallback (register_theme not on every route)."""
    css = card_style()
    assert "#ffffff" in css  # surface fallback
    assert "#dde3ed" in css  # border fallback


def test_header_style_is_tinted_strip_with_rule() -> None:
    css = header_style()
    assert "var(--color-pane-header" in css  # subtle tint background
    assert "border-bottom" in css  # rule under the strip
    assert "align-items: center" in css  # title + count pill share one line


def test_body_style_scrolls() -> None:
    css = body_style()
    assert "overflow: auto" in css
    assert "flex: 1 1 auto" in css


def test_framed_pane_yields_body_and_nests_children() -> None:
    """The manager yields the body element and children of the ``with`` block
    land inside it (not the card root or header) -- guards the yield target.

    NiceGUI is a hard runtime dependency, so the manager always takes its
    real render path here; the body is a live Element and a child created
    inside the block is one of its slot children.
    """
    from nicegui import ui

    with framed_pane("Files", count="2 items", testid="files-pane") as body:
        assert body is not None
        child = ui.label("inside")
        assert child in body.default_slot.children


def test_framed_pane_card_carries_testid() -> None:
    """The card root (the body's grandparent element) carries the testid."""
    with framed_pane("Explorer", count="3 items", testid="explorer-pane") as body:
        assert body is not None
    # body -> parent_slot.parent is the card root element (header + body are
    # the card's two children; body's parent slot belongs to the card).
    card = body.parent_slot.parent if body.parent_slot else None
    assert card is not None
    assert card._props.get("data-testid") == "explorer-pane"
