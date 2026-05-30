"""Framed pane helper (GUI/Orchestrator Redesign §4.1).

The main-window refresh frames each content region (Explorer / Files /
metadata / footer) as an elevated card on the grey app canvas, topped by
an uppercase title strip with an optional right-aligned count pill -- the
approved "B + C hybrid" framing.

This module owns that shell as a single reusable context manager so the
card / title-strip / body styling lives in one place and every pane reads
identically. The style fragments are pure helpers so they can be
unit-tested without spinning up NiceGUI; the context manager itself is the
only NiceGUI-touching surface and ``yield``s the *body* element so children
of the ``with`` block nest inside the scrollable body, not the card root.

All colours / spacing / radii resolve to design tokens (``var(--…)``);
each carries a literal fallback because ``register_theme`` is not injected
on every route, so a bare ``var(--color-surface)`` would otherwise resolve
to empty and drop the whole declaration. Every fragment ends with ``;`` so
callers can safely concatenate an override (e.g. the metadata pane appends
``overflow: auto;`` to beat the card's ``overflow: hidden;``).
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from typing import Any

# Card shell: surface fill, hairline border, soft shadow, rounded corners,
# flex column so the title strip stays fixed and the body scrolls. Ends with
# ';' so an appended override (metadata pane) concatenates to valid CSS.
_CARD_STYLE = (
    "background: var(--color-surface, #ffffff); "
    "border: 1px solid var(--color-border, #dde3ed); "
    "border-radius: var(--radius-md, 10px); "
    "box-shadow: var(--shadow-sm, 0 1px 3px rgba(0,54,96,0.07)); "
    "display: flex; flex-direction: column; overflow: hidden; "
    "min-height: 0; height: 100%;"
)

# Title strip: subtle tint, bottom rule, flex row so title + count pill align.
_HEADER_STYLE = (
    "background: var(--color-pane-header, #f4f6f9); "
    "border-bottom: 1px solid var(--color-rule, #e8ecf2); "
    "padding: var(--sp-2, 0.5rem) var(--sp-3, 0.75rem); "
    "display: flex; align-items: center; gap: var(--sp-2, 0.5rem);"
)

_TITLE_STYLE = (
    "font-size: var(--text-xs, 0.6875rem); text-transform: uppercase; "
    "letter-spacing: 0.08em; color: var(--color-muted, #8892a4); font-weight: 600;"
)

# Right-aligned count pill (e.g. "7 items").
_COUNT_STYLE = (
    "margin-left: auto; background: var(--color-rule, #e8ecf2); "
    "color: var(--color-muted, #8892a4); border-radius: var(--radius-lg, 16px); "
    "padding: 0 var(--sp-2, 0.5rem); font-size: var(--text-xs, 0.6875rem);"
)

# Scrollable body.
_BODY_STYLE = "flex: 1 1 auto; min-height: 0; overflow: auto; padding: var(--sp-3, 0.75rem);"


def card_style() -> str:
    """Return the framed-pane card-shell style fragment (pure; testable).

    Always ends with ``;`` so callers can append an override (e.g. the
    metadata pane appends ``overflow: auto;`` to beat ``overflow: hidden;``).
    """
    return _CARD_STYLE


def header_style() -> str:
    """Return the title-strip style fragment (pure; testable)."""
    return _HEADER_STYLE


def body_style() -> str:
    """Return the scrollable-body style fragment (pure; testable)."""
    return _BODY_STYLE


@contextlib.contextmanager
def framed_pane(
    title: str,
    *,
    count: str | None = None,
    testid: str,
    header_extra: Callable[[], None] | None = None,
) -> Iterator[Any]:
    """Context manager rendering a framed, titled pane; yields the body element.

    Use as::

        with framed_pane("Explorer", testid="explorer-pane"):
            ...  # body content -- rendered INSIDE the scrollable card body

    ``title`` is shown uppercase in the strip; ``count`` (when given) is a
    right-aligned pill such as ``"7 items"``. ``testid`` is set on the card
    (``data-testid="<testid>"``); the title strip carries ``<testid>-header``
    and the count pill ``<testid>-count``.

    ``header_extra`` is an optional zero-arg callback invoked *inside* the
    title strip, after the title + count pill, so a pane can add header
    controls (e.g. the Files pane's per-folder refresh button) without this
    helper knowing their semantics. It is skipped outside a NiceGUI context.

    The manager opens the body div last and yields *it*, so children of the
    ``with`` block become DOM descendants of the body (not the card root or
    the header) -- they therefore sit below the title strip and inside the
    ``overflow: auto`` scroll region.

    Outside a NiceGUI app context (unit tests) the manager yields ``None``
    and renders nothing, mirroring the other components' NiceGUI-optional
    pattern; the style helpers above stay independently testable.
    """
    try:
        from nicegui import ui
    except Exception:
        yield None
        return

    with ui.element("div").props(f'data-testid="{testid}"').style(_CARD_STYLE):
        with ui.element("div").props(f'data-testid="{testid}-header"').style(_HEADER_STYLE):
            ui.label(title).style(_TITLE_STYLE)
            if count is not None:
                ui.label(count).props(f'data-testid="{testid}-count"').style(_COUNT_STYLE)
            if header_extra is not None:
                header_extra()
        # Open the body div last and yield it so `with framed_pane(...)`
        # children nest here, inside the scroll region.
        with ui.element("div").style(_BODY_STYLE) as body:
            yield body
