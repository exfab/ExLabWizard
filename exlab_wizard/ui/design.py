"""Design tokens. Mirrors DESIGN.md verbatim with the Frontend Spec §2.1
typography override (IBM Plex Sans body / system mono).

This module is the single Python source of truth for runtime design tokens.
Per Frontend Spec §2.1.1, every UI module imports tokens from here rather than
hard-coding hex / px / rem literals. The ``:root { ... }`` CSS block injected
by ``ui/theme.py`` is generated from these constants so component CSS can use
the canonical ``var(--color-*)`` names documented in DESIGN.md §07.

Update discipline: keep ``design.py`` and DESIGN.md in lock-step
(see ``tests/unit/ui/test_design.py``).
"""

from __future__ import annotations

from typing import Final

# Primary palette -- UI only (DESIGN.md §01).
# Never used for chart series; UI chrome only.
COLOR_NAVY: str = "#003660"
COLOR_BLUE: str = "#1b75bc"
COLOR_GOLD: str = "#febc11"
COLOR_BG: str = "#f5f7fa"
COLOR_SURFACE: str = "#ffffff"
COLOR_BORDER: str = "#dde3ed"
COLOR_RULE: str = "#e8ecf2"
COLOR_MUTED: str = "#8892a4"
COLOR_BODY: str = "#2e3a4e"
COLOR_HEADING: str = COLOR_NAVY

# Okabe-Ito palette -- data visualization only (DESIGN.md §01).
# Never used for buttons, navigation, headings, links, or input borders.
OI_ORANGE: str = "#E69F00"
OI_SKY: str = "#56B4E9"
OI_GREEN: str = "#009E73"
OI_VERMILION: str = "#D55E00"
OI_BLUE: str = "#0072B2"
OI_PURPLE: str = "#CC79A7"
OI_YELLOW: str = "#F0E442"
OI_GREY: str = "#BBBBBB"

# Semantic aliases (DESIGN.md §01).
# Warning is the canonical token for test mode, blocked sync, hard-tier
# validator stripe, etc. -- see Frontend Spec §2.1.4.
COLOR_SUCCESS: str = OI_GREEN
COLOR_INFO: str = OI_SKY
COLOR_WARNING: str = OI_ORANGE
COLOR_DANGER: str = OI_VERMILION

# Typography (Frontend Spec §2.1.3 override of DESIGN.md §02).
# DM Serif Display / DM Sans / DM Mono are replaced by IBM Plex Sans (body and
# headings) and the OS monospace stack (paths, hex, code).
FONT_BODY: str = "'IBM Plex Sans', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
FONT_DISPLAY: str = "'IBM Plex Sans', Georgia, serif"
FONT_MONO: str = "ui-monospace, 'SF Mono', 'Cascadia Code', 'Fira Code', Menlo, Consolas, monospace"

# Type scale (DESIGN.md §02).
TEXT_XS: str = "0.6875rem"
TEXT_SM: str = "0.8125rem"
TEXT_BASE: str = "0.9375rem"
TEXT_MD: str = "1.0625rem"
TEXT_LG: str = "1.25rem"
TEXT_XL: str = "1.5rem"
TEXT_2XL: str = "1.875rem"
TEXT_3XL: str = "2.5rem"
TEXT_4XL: str = "3.25rem"

# Spacing scale, 4px grid (DESIGN.md §03).
SPACING: dict[str, str] = {
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

# Border radius (DESIGN.md §04).
RADIUS_SM: str = "3px"
RADIUS: str = "6px"
RADIUS_MD: str = "10px"
RADIUS_LG: str = "16px"

# Shadows -- navy-tinted, never gray or black (DESIGN.md §04).
# `--shadow-lg` is reserved for modals / hero panels; never used on inline cards.
SHADOW_SM: str = "0 1px 3px rgba(0,54,96,0.07), 0 1px 2px rgba(0,54,96,0.04)"
SHADOW: str = "0 4px 12px rgba(0,54,96,0.08), 0 1px 3px rgba(0,54,96,0.05)"
SHADOW_MD: str = "0 8px 24px rgba(0,54,96,0.10), 0 2px 6px rgba(0,54,96,0.06)"
SHADOW_LG: str = "0 16px 40px rgba(0,54,96,0.12), 0 4px 12px rgba(0,54,96,0.07)"

# Motion (DESIGN.md §07).
EASE_OUT: str = "cubic-bezier(0.22, 1, 0.36, 1)"
TRANSITION: Final[str] = f"180ms {EASE_OUT}"

# Categorical series order (DESIGN.md §06).
# Fixed; never reorder. Vermilion is reserved for error/alert series.
OKABE_ITO_SERIES: tuple[str, ...] = (
    COLOR_NAVY,
    OI_ORANGE,
    OI_SKY,
    OI_GREEN,
    OI_BLUE,
    OI_PURPLE,
    OI_VERMILION,
)

# Badge darkened text variants -- WCAG AA compliant on white (DESIGN.md §05).
# Use these instead of raw Okabe-Ito hex for badge text on white surfaces.
BADGE_TEXT: dict[str, str] = {
    "navy": COLOR_NAVY,
    "blue": COLOR_BLUE,
    "orange": "#9A6B00",
    "sky": "#0B6E9E",
    "green": "#006B4F",
    "vermilion": OI_VERMILION,
    "purple": "#8B3D6E",
}

# Alert text variants -- WCAG AA compliant on tinted backgrounds (DESIGN.md §05).
ALERT_TEXT: dict[str, str] = {
    "info": "#0B5E87",
    "success": "#005C43",
    "warning": "#7A5500",
    "error": "#8A3C00",
}
