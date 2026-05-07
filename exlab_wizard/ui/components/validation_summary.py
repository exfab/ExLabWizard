"""Validation summary block (Frontend Spec §3.6.4, §11.4.1).

Renders the per-run validation snapshot in the detail pane:

* Header line *"⚠ N hard-tier findings"* in ``--color-warning`` (or
  the soft-only / override variants).
* First two findings as one-line excerpts (rule + matched-token).
* Optional *"+ N more in Problems"* link when more findings exist.
* Override summary line when an override is active.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from exlab_wizard.logging import get_logger

_log = get_logger(__name__)


@dataclass(frozen=True)
class FindingExcerpt:
    """A single finding row used in the summary."""

    rule: str
    matched_token: str


@dataclass(frozen=True)
class ValidationSummary:
    """Inputs to the summary block."""

    hard_count: int
    soft_count: int
    excerpts: tuple[FindingExcerpt, ...]
    override_active: bool = False
    override_reason_snippet: str | None = None
    override_operator: str | None = None
    override_set_at: str | None = None


def header_line(summary: ValidationSummary) -> tuple[str, str]:
    """Return ``(text, color_var)`` for the section header."""

    if summary.override_active:
        return ("Override active", "--color-info")
    if summary.hard_count > 0:
        return (
            f"{summary.hard_count} hard-tier findings",
            "--color-warning",
        )
    if summary.soft_count > 0:
        return (
            f"{summary.soft_count} soft-tier findings",
            "--color-muted",
        )
    return ("", "--color-muted")


def excerpt_line(finding: FindingExcerpt) -> str:
    """Compose one-line text for a finding excerpt (Frontend §3.6.4)."""

    return f"{finding.rule}  --  {finding.matched_token}"


def overflow_line(summary: ValidationSummary) -> str | None:
    """Return the *"+ N more in Problems"* line, or ``None`` if not needed.

    Frontend §3.6.4: at most two excerpts are rendered; remaining
    findings collapse into a single overflow line.
    """

    visible = min(len(summary.excerpts), 2)
    total = summary.hard_count + summary.soft_count
    overflow = max(0, total - visible)
    if overflow == 0:
        return None
    return f"+ {overflow} more in Problems"


def override_line(summary: ValidationSummary) -> str | None:
    """Return the override summary line if an override is active."""

    if not summary.override_active:
        return None
    snippet = summary.override_reason_snippet or "(no reason)"
    operator = summary.override_operator or "unknown"
    set_at = summary.override_set_at or "unknown"
    return f'Override active  --  "{snippet}" (set by {operator} on {set_at})'


def validation_summary(summary: ValidationSummary) -> Any:
    """Build the summary block."""

    header_text, header_color = header_line(summary)
    payload = {
        "header_text": header_text,
        "header_color": header_color,
        "excerpts": [excerpt_line(f) for f in summary.excerpts[:2]],
        "overflow": overflow_line(summary),
        "override_line": override_line(summary),
    }

    try:
        from nicegui import ui
    except Exception:
        return payload

    column = ui.column().classes("w-full").style("gap: 0.25rem;")
    with column:
        if header_text:
            ui.label(f"⚠ {header_text}").style(
                f"color: var({header_color}); "
                "font-family: var(--font-body); "
                "font-weight: 600; "
                "font-size: var(--text-sm);"
            )
        for excerpt in payload["excerpts"]:
            ui.label(f"⚠ {excerpt}").style(
                "font-family: var(--font-mono); "
                "font-size: var(--text-xs); "
                "color: var(--color-body);"
            )
        if payload["overflow"]:
            ui.label(payload["overflow"]).style(
                "font-family: var(--font-body); "
                "font-size: var(--text-xs); "
                "color: var(--color-info); "
                "cursor: pointer;"
            )
        if payload["override_line"]:
            ui.label(payload["override_line"]).style(
                "font-family: var(--font-body); "
                "font-size: var(--text-xs); "
                "color: var(--color-info);"
            )
    return column
