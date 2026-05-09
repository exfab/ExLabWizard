"""Problems tab (Frontend Spec §11).

Filter chips (Severity, Class, State, Scope), table of findings, and a
footer status bar (*"Showing N of M findings · Last audit: HH:MM:SS ·
Next refresh in 23s"*).

The override-and-allow-sync dialog (§11.5) is implemented here as well:
the operator picks a reason (10--500 chars after trim), an optional
expiry, ticks the acknowledgement checkbox, and submits.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from exlab_wizard.constants import Tier
from exlab_wizard.logging import get_logger
from exlab_wizard.ui.components import filter_chips

_log = get_logger(__name__)

# Problem classes per Backend §8.1.1-§8.1.5.
PROBLEM_CLASSES: tuple[str, ...] = (
    "Placeholder",
    "Illegal char",
    "Mode mismatch",
    "Orphan",
    "Missing field",
)

# Override reason length policy (Frontend §11.5).
OVERRIDE_REASON_MIN = 10
OVERRIDE_REASON_MAX = 500


@dataclass(frozen=True)
class Finding:
    """One row in the Problems table."""

    finding_id: str
    severity: str  # "hard" | "soft"
    rule_class: str
    path: str
    matched_token: str
    run_label: str | None
    equipment: str
    detected_at: str
    state: str  # "Active" | "Override active" | "Marked known" | "Synced under prior policy"


@dataclass
class ProblemsPageState:
    """Mutable filter state."""

    severity_chips: filter_chips.ChipState = field(
        default_factory=lambda: filter_chips.ChipState(active={"hard"})
    )
    class_chips: filter_chips.ChipState = field(
        default_factory=lambda: filter_chips.ChipState(active=set(PROBLEM_CLASSES))
    )
    state_chips: filter_chips.ChipState = field(
        default_factory=lambda: filter_chips.ChipState(active={"Active"})
    )
    scope: str = "all"  # "all" | "<equipment_id>" | "staging"
    search: str = ""


def severity_chip_definitions() -> tuple[filter_chips.ChipDefinition, ...]:
    """Severity chips per Frontend §11.1."""

    return (
        filter_chips.ChipDefinition(chip_id="hard", label="Hard", default_on=True),
        filter_chips.ChipDefinition(chip_id="soft", label="Soft", default_on=False),
    )


def class_chip_definitions() -> tuple[filter_chips.ChipDefinition, ...]:
    """One chip per problem class (Frontend §11.1)."""

    return tuple(
        filter_chips.ChipDefinition(chip_id=name, label=name, default_on=True)
        for name in PROBLEM_CLASSES
    )


def state_chip_definitions() -> tuple[filter_chips.ChipDefinition, ...]:
    """State chips (Frontend §11.1)."""

    return (
        filter_chips.ChipDefinition(chip_id="Active", label="Active", default_on=True),
        filter_chips.ChipDefinition(
            chip_id="Override active", label="Override active", default_on=False
        ),
        filter_chips.ChipDefinition(chip_id="Marked known", label="Marked known", default_on=False),
        filter_chips.ChipDefinition(
            chip_id="Synced under prior policy",
            label="Synced under prior policy",
            default_on=False,
        ),
    )


def filter_findings(
    findings: list[Finding],
    state: ProblemsPageState,
) -> list[Finding]:
    """Filter ``findings`` against the active chip / search state."""

    out: list[Finding] = []
    for finding in findings:
        if not filter_chips.is_active(state.severity_chips, finding.severity):
            continue
        if not filter_chips.is_active(state.class_chips, finding.rule_class):
            continue
        if not filter_chips.is_active(state.state_chips, finding.state):
            continue
        if state.scope != "all" and state.scope != "staging" and finding.equipment != state.scope:
            continue
        if state.search and state.search.lower() not in finding.path.lower():
            continue
        out.append(finding)
    return out


def empty_state_text(
    state: ProblemsPageState,
    *,
    soft_findings_hidden_count: int = 0,
) -> str:
    """Compute the empty-state copy per Frontend §11.4."""

    if not filter_chips.is_active(state.severity_chips, "soft") and soft_findings_hidden_count > 0:
        return (
            f"No active problems. ({soft_findings_hidden_count} soft-tier findings "
            "hidden by filter.)"
        )
    if state.search or state.scope != "all":
        return "No findings match the current filters."
    return "No active problems."


def validate_override_reason(reason: str) -> tuple[bool, str | None]:
    """Validate an override reason per Frontend §11.5.

    Returns ``(ok, error_message)``. ``ok=False`` when the reason is too
    short or too long after trimming; the message names the failed bound.
    """

    trimmed = reason.strip()
    if len(trimmed) < OVERRIDE_REASON_MIN:
        return False, (f"Reason must be at least {OVERRIDE_REASON_MIN} characters.")
    if len(trimmed) > OVERRIDE_REASON_MAX:
        return False, (f"Reason must be at most {OVERRIDE_REASON_MAX} characters.")
    return True, None


def near_limit(reason: str) -> bool:
    """Return ``True`` when the counter should turn warning-tier (last 10)."""

    trimmed = reason.strip()
    return len(trimmed) >= OVERRIDE_REASON_MAX - 10


def render_problems_page(
    *,
    findings: list[Finding],
    state: ProblemsPageState | None = None,
    on_override: Callable[[str], None] | None = None,
    on_revoke_override: Callable[[str], None] | None = None,
) -> Any:
    """Render the Problems tab content."""

    s = state or ProblemsPageState()
    visible = filter_findings(findings, s)
    payload = {
        "visible": visible,
        "total": len(findings),
        "empty_text": empty_state_text(s),
        "filter_chips": {
            "severity": severity_chip_definitions(),
            "class": class_chip_definitions(),
            "state": state_chip_definitions(),
        },
    }

    try:
        from nicegui import ui
    except Exception:
        return payload

    container = (
        ui.column().classes("w-full").props('data-testid="problems-table"').style("gap: 0.5rem;")
    )
    with container:
        with ui.row().classes("items-center w-full").style("gap: 0.5rem;"):
            ui.label("Severity").style(
                "font-family: var(--font-mono); "
                "font-size: var(--text-xs); "
                "color: var(--color-muted); "
                "letter-spacing: 0.08em; "
                "text-transform: uppercase;"
            )
            filter_chips.filter_chips(
                severity_chip_definitions(),
                state=s.severity_chips,
            )
        with ui.row().classes("items-center w-full").style("gap: 0.5rem;"):
            ui.label("Class").style(
                "font-family: var(--font-mono); "
                "font-size: var(--text-xs); "
                "color: var(--color-muted); "
                "letter-spacing: 0.08em; "
                "text-transform: uppercase;"
            )
            filter_chips.filter_chips(
                class_chip_definitions(),
                state=s.class_chips,
            )
        with ui.row().classes("items-center w-full").style("gap: 0.5rem;"):
            ui.label("State").style(
                "font-family: var(--font-mono); "
                "font-size: var(--text-xs); "
                "color: var(--color-muted); "
                "letter-spacing: 0.08em; "
                "text-transform: uppercase;"
            )
            filter_chips.filter_chips(
                state_chip_definitions(),
                state=s.state_chips,
            )

        if not visible:
            ui.label(empty_state_text(s)).props('data-testid="problems-empty"').style(
                "color: var(--color-muted); font-family: var(--font-body); padding: 1rem 0;"
            )
        else:
            for idx, finding in enumerate(visible):
                color_var = "--color-warning" if finding.severity == Tier.HARD else "--color-muted"
                with (
                    ui.row()
                    .classes("items-center w-full")
                    .props(f'data-testid="problems-row-{idx}"')
                    .style(
                        f"border-left: 4px solid var({color_var}); "
                        "padding: 0.5rem 0.75rem; "
                        "border-bottom: 1px solid var(--color-rule); "
                        "gap: 0.5rem;"
                    )
                ):
                    ui.label(finding.rule_class).style(
                        "font-family: var(--font-mono); font-size: var(--text-xs);"
                    )
                    ui.label(finding.path).style(
                        "font-family: var(--font-mono); "
                        "font-size: var(--text-xs); "
                        "color: var(--color-body);"
                    )
                    ui.label(finding.state).props(f'data-testid="problems-row-{idx}-state"').style(
                        "font-family: var(--font-mono); font-size: var(--text-xs); "
                        "color: var(--color-muted);"
                    )
                    if (
                        finding.severity == Tier.HARD
                        and finding.state == "Active"
                        and on_override is not None
                    ):
                        ui.button(
                            "Override and allow sync",
                            on_click=lambda _evt, fid=finding.finding_id: on_override(fid),
                        ).props(f'flat data-testid="problems-row-{idx}-override"')
                    if finding.state == "Override active" and on_revoke_override is not None:
                        ui.button(
                            "Revoke override",
                            on_click=lambda _evt, fid=finding.finding_id: on_revoke_override(fid),
                        ).props(f'flat data-testid="problems-row-{idx}-revoke"')

        ui.label(
            f"Showing {len(visible)} of {len(findings)} findings  ·  Last audit: --",
        ).style(
            "font-family: var(--font-mono); "
            "font-size: var(--text-xs); "
            "color: var(--color-muted); "
            "padding: 0.5rem 0;"
        )
    return container
