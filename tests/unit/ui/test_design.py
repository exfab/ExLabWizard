"""Token-equality tests for :mod:`exlab_wizard.ui.design`.

The tests assert each constant matches the documented DESIGN.md value
verbatim, plus the Frontend Spec §2.1.3 typography overrides. A drift
between DESIGN.md and ``design.py`` is the single thing the design-system
discipline cares about; these tests are the canonical guard.
"""

from __future__ import annotations

from exlab_wizard.ui import design


def test_primary_palette_matches_designmd() -> None:
    """Primary palette hexes are verbatim from DESIGN.md §01."""

    assert design.COLOR_NAVY == "#003660"
    assert design.COLOR_BLUE == "#1b75bc"
    assert design.COLOR_GOLD == "#febc11"
    assert design.COLOR_BG == "#f5f7fa"
    assert design.COLOR_SURFACE == "#ffffff"
    assert design.COLOR_BORDER == "#dde3ed"
    assert design.COLOR_RULE == "#e8ecf2"
    assert design.COLOR_MUTED == "#8892a4"
    assert design.COLOR_BODY == "#2e3a4e"
    assert design.COLOR_HEADING == design.COLOR_NAVY


def test_okabe_ito_palette_matches_designmd() -> None:
    """Okabe-Ito hexes are verbatim from DESIGN.md §01."""

    assert design.OI_ORANGE == "#E69F00"
    assert design.OI_SKY == "#56B4E9"
    assert design.OI_GREEN == "#009E73"
    assert design.OI_VERMILION == "#D55E00"
    assert design.OI_BLUE == "#0072B2"
    assert design.OI_PURPLE == "#CC79A7"
    assert design.OI_YELLOW == "#F0E442"
    assert design.OI_GREY == "#BBBBBB"


def test_semantic_aliases_are_okabe_ito() -> None:
    """Semantic tokens alias the Okabe-Ito palette per DESIGN.md §01."""

    assert design.COLOR_SUCCESS == design.OI_GREEN
    assert design.COLOR_INFO == design.OI_SKY
    assert design.COLOR_WARNING == design.OI_ORANGE
    assert design.COLOR_DANGER == design.OI_VERMILION


def test_warning_token_is_oi_orange() -> None:
    """Frontend §2.1.4: warning is canonically ``--oi-orange``."""

    assert design.COLOR_WARNING == "#E69F00"


def test_typography_uses_frontend_override() -> None:
    """Frontend §2.1.3 overrides DM Sans / DM Mono with IBM Plex / system mono."""

    assert "IBM Plex Sans" in design.FONT_BODY
    assert "system-ui" in design.FONT_BODY
    assert "ui-monospace" in design.FONT_MONO
    assert "SF Mono" in design.FONT_MONO
    assert "Fira Code" in design.FONT_MONO


def test_type_scale_matches_designmd() -> None:
    """Type scale tokens are verbatim from DESIGN.md §02."""

    assert design.TEXT_XS == "0.6875rem"
    assert design.TEXT_SM == "0.8125rem"
    assert design.TEXT_BASE == "0.9375rem"
    assert design.TEXT_MD == "1.0625rem"
    assert design.TEXT_LG == "1.25rem"
    assert design.TEXT_XL == "1.5rem"
    assert design.TEXT_2XL == "1.875rem"
    assert design.TEXT_3XL == "2.5rem"
    assert design.TEXT_4XL == "3.25rem"


def test_spacing_scale_4px_grid() -> None:
    """Spacing tokens follow the 4px grid (DESIGN.md §03)."""

    assert design.SPACING == {
        "1": "0.25rem",
        "2": "0.5rem",
        "3": "0.75rem",
        "4": "1rem",
        "5": "1.25rem",
        "6": "1.5rem",
        "8": "2rem",
        "10": "2.5rem",
        "12": "3rem",
        "16": "4rem",
    }


def test_radius_tokens_match_designmd() -> None:
    """Border radius tokens (DESIGN.md §04)."""

    assert design.RADIUS_SM == "3px"
    assert design.RADIUS == "6px"
    assert design.RADIUS_MD == "10px"
    assert design.RADIUS_LG == "16px"


def test_shadows_are_navy_tinted() -> None:
    """All shadow definitions use ``rgba(0,54,96,...)`` (DESIGN.md §04)."""

    for shadow in (design.SHADOW_SM, design.SHADOW, design.SHADOW_MD, design.SHADOW_LG):
        assert "rgba(0,54,96" in shadow, shadow


def test_motion_tokens_match_designmd() -> None:
    """Motion tokens (DESIGN.md §07)."""

    assert design.EASE_OUT == "cubic-bezier(0.22, 1, 0.36, 1)"
    assert design.TRANSITION == "180ms cubic-bezier(0.22, 1, 0.36, 1)"


def test_okabe_ito_series_order_is_fixed() -> None:
    """Series order is fixed (DESIGN.md absolute constraint)."""

    assert design.OKABE_ITO_SERIES == (
        "#003660",  # navy
        "#E69F00",  # orange
        "#56B4E9",  # sky
        "#009E73",  # green
        "#0072B2",  # blue
        "#CC79A7",  # purple
        "#D55E00",  # vermilion (error / alert)
    )


def test_vermilion_is_last_in_series() -> None:
    """Vermilion is reserved for the error / alert series (DESIGN.md §06)."""

    assert design.OKABE_ITO_SERIES[-1] == design.OI_VERMILION


def test_no_blue_collision_in_series_first_six() -> None:
    """Frontend rules (DESIGN.md): series 5 is OI_BLUE but only after 4 prior series.

    Verify that ``--color-blue`` (the UI brand blue) is NOT in the first
    six categorical series, so the navy / orange / sky / green / OI_BLUE /
    purple sequence is preserved without colliding with the UI blue.
    """

    assert design.COLOR_BLUE not in design.OKABE_ITO_SERIES


def test_badge_text_variants_are_wcag_aa() -> None:
    """Badge text variants are the documented darkened hexes (DESIGN.md §05)."""

    assert design.BADGE_TEXT["orange"] == "#9A6B00"
    assert design.BADGE_TEXT["sky"] == "#0B6E9E"
    assert design.BADGE_TEXT["green"] == "#006B4F"
    assert design.BADGE_TEXT["purple"] == "#8B3D6E"
    assert design.BADGE_TEXT["navy"] == design.COLOR_NAVY
    assert design.BADGE_TEXT["blue"] == design.COLOR_BLUE
    assert design.BADGE_TEXT["vermilion"] == design.OI_VERMILION


def test_alert_text_variants_match_designmd() -> None:
    """Alert text variants are the documented hexes (DESIGN.md §05)."""

    assert design.ALERT_TEXT == {
        "info": "#0B5E87",
        "success": "#005C43",
        "warning": "#7A5500",
        "error": "#8A3C00",
    }


def test_yellow_is_present_but_only_for_fills() -> None:
    """``#F0E442`` is exposed as a token but never used for chrome.

    The chip strip / badges / buttons / inputs all draw from
    :data:`COLOR_*` and the UI-only palette; ``OI_YELLOW`` is reserved
    for chart fills. The constraint check here is that the token exists
    (so chart code can find it) and that the value is the documented
    hex.
    """

    assert design.OI_YELLOW == "#F0E442"
