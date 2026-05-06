# ExLab-Wizard: Frontend Design Specification

**Scope:** Frontend surfaces for the ExLab-Wizard application: main window, wizard flows, settings dialog, orchestrator-mode panels, and widget mappings. Backend behavior, data flows, schemas, and integrations are **out of scope** and specified in `ExLab-Wizard_Design_Spec.md` and the numbered backend section files under `design_spec_sections/`. The user-visible capability contract (triggers, inputs, validation order, mode invariants) lives in the User Interaction Spec at `design_spec_sections/02_User_Interaction.md`.

**Relationship to the other specs:** The user capabilities surfaced by this document are catalogued in the User Interaction Spec (`design_spec_sections/02_User_Interaction.md`) Section 3. Backend schemas and behavior live in `ExLab-Wizard_Design_Spec.md` and `design_spec_sections/`. Where this doc uses a backend term (e.g. `run_kind`, `_exlab_run_scope`, `ingest.json`), the definition lives in the Design Spec; for capability contracts (e.g. validation order, mode binding), the User Interaction Spec is authoritative. Follow the reference rather than duplicating it.

---

## Table of Contents

1. [Purpose and Scope](#1-purpose-and-scope)
2. [Framework Choice](#2-framework-choice)
3. [Main Window](#3-main-window)
4. [New Project Wizard](#4-new-project-wizard)
5. [New Run Wizard (Experimental and Test Modes)](#5-new-run-wizard-experimental-and-test-modes)
6. [README Authoring Step](#6-readme-authoring-step)
7. [Settings Dialog](#7-settings-dialog)
8. [Orchestrator Mode Surfaces](#8-orchestrator-mode-surfaces)
9. [Plugin Input Escalation](#9-plugin-input-escalation)
10. [Error, Progress, and Summary Presentation](#10-error-progress-and-summary-presentation)
11. [Problems Tab](#11-problems-tab)
12. [Widget Mappings](#12-widget-mappings)
13. [Open Questions](#13-open-questions)

---

## 1. Purpose and Scope

This document specifies how the ExLab-Wizard backend's user capabilities are presented to a desktop user. It covers window layouts, multi-step wizard flows, widget choices, affordance design, and interaction patterns. It does not specify data structures, persistence, network behavior, or file formats.

Guiding principles:

- **Mode-safety first.** The experimental/test distinction is a correctness boundary, not a convenience. Every surface that involves creating a run must make the active mode visible at all times and hard to misclick.
- **Non-blocking long operations.** Directory creation, NAS sync, and DB writes can be slow. Surfaces must either show determinate progress or step out of the user's way.
- **Read-only cache visibility.** The `.exlab-wizard/` folders are backend state. The user may benefit from seeing they exist but should never be prompted to edit them.

---

## 2. Framework Choice

**Committed: FastAPI + NiceGUI rendered in a native desktop window via pywebview, with a system-tray icon hosting a persistent server process.** The FastAPI app exposes `/api/v1/*` and mounts NiceGUI on the same app; both bind to `127.0.0.1:<random free port>` only. The native window (the `exlab-wizard-window` subprocess) opens pywebview pointed at that localhost server, and the tray-icon process (`exlab-wizard-tray`) is the long-lived parent that keeps the server alive across window opens and closes. The full deployment rationale, process model, and bundling are in `ExLab-Wizard_Design_Spec.md` §4.1, §4.2, and §15.

| Framework family | Disposition | Rationale |
|---|---|---|
| `tkinter` | Rejected | Limited widget set forced third-party additions for the tree, stepper, and tabs we need; no clean way to expose the WebSocket-driven progress bar and Problems-tab refresh without a separate event loop. |
| `PySide6` | Rejected | Capable but heavier (LGPL compliance, larger install, Qt learning curve) and would still leave us building our own backend↔GUI IPC. |
| FastAPI + NiceGUI **served as a browser-tab web app** | Reconsidered and rejected | The natural shape for a lab tool is "step away during a long sync, come back later" — closing the browser tab orphans the running server, and closing the entire app loses any wizard state in flight. Multi-tab edge cases (concurrent Settings edits, divergent wizard state, conflicting saves) compound the problem. |
| **FastAPI + NiceGUI in a native pywebview window with a pystray tray icon** | **Selected** | Same FastAPI + NiceGUI core as the rejected option — same component vocabulary, same Playwright-driven e2e test path — but rendered in a native window, with a persistent server hosted by a tray icon that survives window opens and closes. Single-instance window eliminates the multi-tab class of bugs. OS notifications (plyer) for events that need attention. |
| FastAPI + HTMX | Considered | Lighter than NiceGUI, but the live-updating Problems tab and the stepper-with-mode-binding wizards are more code without a component library. Reconsider if NiceGUI proves a poor fit for the orchestrator staging panel. |
| FastAPI + React/Svelte SPA | Considered | Most flexible, most code, separate frontend toolchain. Out of proportion for a single-user lab tool. |

**Implications for the rest of this document.** Widget mappings in §12 use NiceGUI component names. Modal dialogs (`ui.dialog`) are non-blocking but session-scoped on the server side. Wizard flows use `ui.stepper` with explicit step validation in the controller's `VALIDATING` state — frontend validation is for UX immediacy, the backend is authoritative. Long-running operations are server-side: closing the native window does not interrupt them; reopening reflects the latest state.

**Implications for testing.** Playwright drives a Chromium instance against `http://127.0.0.1:<port>` for e2e tests -- same NiceGUI surface that pywebview renders in production. Backend integration tests hit `httpx.AsyncClient(app=app)` directly without spawning the server. The Problems-tab WebSocket and the per-session events WebSocket are exercised in both layers. Tray and window subprocess behavior are exercised by separate cross-platform smoke tests in CI (skipped on the Linux runner where headless tray testing is brittle).

### 2.1 Visual tokens and the design system

ExLab-Wizard inherits its visual design system from the lab's authoritative style guide at [`DESIGN.md`](../DESIGN.md) (*"Scientific Analysis Dashboard Design System v1.1"*). DESIGN.md is the single source of truth for color palette, type scale, spacing tokens, border radius, shadows, and component styling rules across all lab applications (PhenoTypic, ExLab-Wizard, future tools). This subsection describes how ExLab-Wizard consumes those tokens, the small set of ExLab-Wizard-specific overrides, and the Python module that exposes the tokens to runtime code.

#### 2.1.1 The `design.py` module

Backend §4.3 includes `exlab_wizard/ui/design.py` as the single Python source of truth for design tokens at runtime. Its constants mirror DESIGN.md verbatim and are the **only** acceptable source of color hex values, font-family stacks, spacing values, radius values, and shadow definitions inside the codebase. Any UI code that needs a color, font, or spacing value imports it from `design.py`; no inline hex / px / rem literals.

Module shape (illustrative):

```python
# exlab_wizard/ui/design.py — mirrors DESIGN.md; update both together.

# Primary palette (UI only) — DESIGN.md §01
COLOR_NAVY    = "#003660"
COLOR_BLUE    = "#1b75bc"
COLOR_GOLD    = "#febc11"
COLOR_BG      = "#f5f7fa"
COLOR_SURFACE = "#ffffff"
COLOR_BORDER  = "#dde3ed"
COLOR_RULE    = "#e8ecf2"
COLOR_MUTED   = "#8892a4"
COLOR_BODY    = "#2e3a4e"
COLOR_HEADING = COLOR_NAVY

# Data palette (Okabe-Ito, visualization only) — DESIGN.md §01
OI_ORANGE    = "#E69F00"
OI_SKY       = "#56B4E9"
OI_GREEN     = "#009E73"
OI_VERMILION = "#D55E00"
OI_BLUE      = "#0072B2"
OI_PURPLE    = "#CC79A7"
OI_YELLOW    = "#F0E442"
OI_GREY      = "#BBBBBB"

# Semantic aliases
COLOR_SUCCESS = OI_GREEN
COLOR_INFO    = OI_SKY
COLOR_WARNING = OI_ORANGE          # test mode, blocked sync, hard-tier validator stripe — see §2.1.4
COLOR_DANGER  = OI_VERMILION

# Typography (ExLab-Wizard override of DESIGN.md §02 — see §2.1.3)
FONT_BODY = "'IBM Plex Sans', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"
FONT_MONO = "ui-monospace, 'SF Mono', 'Cascadia Code', 'Fira Code', Menlo, Consolas, monospace"

# Spacing (4px grid) — DESIGN.md §03
SPACING = { "1": "0.25rem", "2": "0.5rem", "3": "0.75rem", "4": "1rem",
            "5": "1.25rem", "6": "1.5rem", "8": "2rem", "10": "2.5rem",
            "12": "3rem",   "16": "4rem" }

# Border radius — DESIGN.md §04
RADIUS_SM = "3px"
RADIUS    = "6px"
RADIUS_MD = "10px"
RADIUS_LG = "16px"

# Shadows (navy-tinted) — DESIGN.md §04
SHADOW_SM = "0 1px 3px rgba(0,54,96,0.07), 0 1px 2px rgba(0,54,96,0.04)"
SHADOW    = "0 4px 12px rgba(0,54,96,0.08), 0 1px 3px rgba(0,54,96,0.05)"
SHADOW_MD = "0 8px 24px rgba(0,54,96,0.10), 0 2px 6px rgba(0,54,96,0.06)"
SHADOW_LG = "0 16px 40px rgba(0,54,96,0.12), 0 4px 12px rgba(0,54,96,0.07)"
```

`exlab_wizard/ui/theme.py` consumes these constants and registers them with NiceGUI/Quasar's theme API at app startup; the same values are also written to a `:root { ... }` CSS block so component CSS uses the standard `var(--color-warning)` / `var(--sp-4)` references documented in DESIGN.md §07.

**Update discipline.** `design.py` and DESIGN.md are kept in sync as a single change. The PR description must either confirm both were updated or explain why one diverged. A simple unit test asserts the constants in `design.py` match a small fixture extracted from DESIGN.md (the seven primary-palette hexes plus the eight Okabe-Ito hexes plus the spacing scale).

#### 2.1.2 What lives in DESIGN.md vs in this spec

- **DESIGN.md (authoritative):** color palette and tokens, typography scale and rules, spacing tokens, radius and shadow tokens, component styling (stat cards, progress bars, buttons, badges, alerts, tables, form inputs, tabs), data-visualization rules (Okabe-Ito series order, chart styling), code integration snippets (matplotlib rcParams, napari label colors, the canonical `:root` CSS block), and the absolute-constraint list (no em dashes, no Okabe-Ito for buttons, no `#F0E442` as text on white, etc.).
- **This Frontend Spec:** ExLab-Wizard-specific surfaces (main window, wizards, Settings dialog, Problems tab, tray UX), interaction patterns (validation order, mode binding, override flows), and any **overrides** to DESIGN.md called out in §2.1.3. Where a section here describes a styled element, it references DESIGN.md tokens by name (e.g. *"warning-tier border per `--color-warning`"*) rather than re-declaring hex values.

When this spec and DESIGN.md disagree, DESIGN.md wins for visual tokens; this spec wins for ExLab-Wizard-specific behavior. The override list (§2.1.3) is the only set of ExLab-Wizard-side deviations.

#### 2.1.3 ExLab-Wizard typography overrides

DESIGN.md §02 specifies a three-font stack (DM Serif Display / DM Sans / DM Mono). ExLab-Wizard overrides this for two reasons: (1) ExLab-Wizard is a tool / wizard, not a data dashboard with editorial display headings; (2) bundling DM Mono adds licensing friction that we sidestep by using the OS monospace stack.

| Role | DESIGN.md says | ExLab-Wizard uses |
|---|---|---|
| Prose / body / UI / headings | DM Sans (body/UI), DM Serif Display (h1–h3) | **IBM Plex Sans** for everything sans-serif (body, UI, headings). Bundled into the PyInstaller artifact (Backend §15.1) at three weights: 400 (body), 500 (UI emphasis, table numerics), 600 (headings, button text). |
| Monospace (paths, hex values, code) | DM Mono | **`ui-monospace, 'SF Mono', 'Cascadia Code', 'Fira Code', Menlo, Consolas, monospace`** — system stack, no bundled font. |

DESIGN.md's **type scale** (`--text-xs` through `--text-4xl`) is unchanged; we just substitute the families per the table above.

The DESIGN.md absolute constraint *"NEVER render numeric data, axis labels, badge text, captions, or code outside `font-family: 'DM Mono'`"* is satisfied by reading "DM Mono" as the monospace family — for ExLab-Wizard that's the `ui-monospace` stack defined above. The constraint is about monospace correctness, not the specific font.

#### 2.1.4 Warning-tier color (resolves OQ #3)

Per DESIGN.md §01 semantic assignments, warning is **`--oi-orange`** = `#E69F00`. This is the single token referenced from every ExLab-Wizard surface that previously asserted "warning-tier color":

- Test-mode badge in wizard title bar (§5.3)
- Highlighted `TestRuns/` segment and `TestRun_` leaf prefix in path previews (§5.2, §5.3)
- `blocked_by_validation` sync-status icon (§3.2)
- Sync-blocked banner on creation-success card (§10.4)
- Problems-tab hard-tier accent stripe (§11.2)
- Problems-tab severity icon (§11.1)
- Override-reason character-count "near limit" indicator (§11.5)
- Setup-incomplete banner on the main window (§3.1.4)

When the visual treatment is "fill" (badge, banner background), follow DESIGN.md badge / alert rules: tinted background at 7–10% opacity, darkened text variant `#9A6B00` for WCAG AA contrast (DESIGN.md §05 badges and alerts tables). When the treatment is "stroke or icon" (1px stripe, glyph fill, single-line border), the raw `#E69F00` is acceptable — DESIGN.md flags only thin lines / text on white as the contrast risk.

Resolves Frontend Open Question #3 (test-mode color).

#### 2.1.5 CSS styling reference rule

Every component-level CSS rule in this spec, in `design.py`, in NiceGUI custom-CSS overrides, and in any future component module **MUST** reference DESIGN.md tokens by `--color-*` / `--sp-*` / `--text-*` / `--radius-*` / `--shadow-*` variable name. Inline hex / px / rem literals are forbidden in component CSS. Two exceptions:

1. The `:root { ... }` block (declared once in `ui/theme.py`) is the only acceptable place for raw token values.
2. Vendor-pinned hex values that DESIGN.md itself lists with a specific hex (e.g. the badge darkened-text variants `#9A6B00`, `#0B6E9E`, `#006B4F`, `#8B3D6E`) may appear in component CSS where DESIGN.md sources them; these are themselves pre-tokenized and don't need a layer of indirection.

Anti-patterns to avoid (these are violations the dedup audit flagged):

- *"Use the same color as the test-mode badge"* — ambiguous string reference. **Use `var(--color-warning)` instead.**
- Inline `color: #E69F00` — hard-coded hex. **Use `var(--color-warning)` instead.**
- Repeating the warning-tier hex in narrative prose. **Reference DESIGN.md §01 or this section's §2.1.4.**

#### 2.1.6 DESIGN.md absolute constraints (binding)

The "Absolute Constraints" list at the top of DESIGN.md is binding for ExLab-Wizard. Notable items relevant to this spec:

- **No em dashes.** Use double hyphens (`--`) or restructure the sentence. Affects all generated UI strings, error messages, helper text, banner copy, and spec prose. Existing em-dash usage in this spec set is recorded as a follow-up cleanup task (no functional impact, mechanical replacement).
- **Okabe-Ito colors are data-only.** UI chrome (buttons, navigation, headings, links, input borders) uses the primary palette only. The semantic-alert exceptions (warning, error, success, info) where Okabe-Ito hues map to UI states are explicitly carved out in DESIGN.md §01 and apply here.
- **No `#F0E442` (yellow) as text or thin lines on light backgrounds.** Reserved for large filled chart elements only; not used in the wizard chrome.
- **No raw Okabe-Ito hex as badge text on white.** Use the darkened variants from DESIGN.md badges table.
- **No red-green colormaps.** Heatmaps in any future ExLab-Wizard surface follow DESIGN.md's navy-to-blue ramp with vermilion for failed cells.

### 2.2 Notification taxonomy

ExLab-Wizard uses six distinct surfaces for communicating with the operator: modals, banners, toasts, inline messages, status-bar segments, and OS notifications. Without a written rule for when each applies, the surfaces drift into ad-hoc usage and the operator can't predict where to look for a given kind of message. This subsection commits the canonical taxonomy and the per-surface specifics. Like §2.1, this is a design-system concern referenced from every section that emits user-facing messages.

#### 2.2.1 Pattern selection rule

The surface for any given message is determined by three properties of the underlying state:

| Underlying state | Persistent? | Needs ack? | Surface |
|---|---|---|---|
| Action result, success or info | one-shot | no | **Toast** (§2.2.2) |
| Action result, recoverable error | one-shot | sometimes (with action) | **Toast with one action** (§2.2.2) |
| Action result, fatal or requires decision | one-shot | yes | **Modal** (per-section spec) |
| App / page state, ongoing | persistent | sometimes | **Banner** (§2.2.3) |
| App / page state, one-shot | one-shot | no | **Toast** |
| Field-level validation | bound to field | yes | **Inline below field** (§2.2.4) |
| Form-level validation (multi-field rule) | bound to form | yes | **Inline above form** (§2.2.4) |
| Background / system state | persistent | no | **Status bar** (§3.5.5) |
| Operator-attention required while window closed | persistent | yes | **OS notification + tray status** (§3.4.5, §15.7.3) |

The rule is enforced through a small notification-helper API (§2.2.5) rather than per-call-site discipline. Every UI module imports `notify_action_result(...)`, `notify_field_error(...)`, etc. from `ui/notifications.py` instead of calling `ui.notify()` directly with arbitrary parameters.

#### 2.2.2 Toast specifications

Toasts are ephemeral, non-blocking surfaces for action results and one-shot state changes.

| Property | Value |
|---|---|
| Position | Bottom-right of the native window. |
| Duration | 4 s for `info` / `success`. 8 s for `warning` / `error`. Hover over the toast pauses the timer; mouseout resumes from the paused value. |
| Stacking | Up to 3 simultaneous toasts. A 4th arrival evicts the oldest. |
| Action affordances | At most ONE action button per toast (e.g. `[Retry]`, `[Undo]`, `[View]`). Multi-action requirements escalate to a modal. |
| Dismiss | Manual close `×` always available. Auto-dismiss never blocks while hovered. |
| Color and icon | Per DESIGN.md alerts table (§05): `var(--color-success)` (green) for success, `var(--color-info)` (sky) for info, `var(--color-warning)` (orange) for warning, `var(--color-danger)` (vermilion) for error. Each carries a leading icon glyph in the same color. |
| Typography | Headline in `var(--font-body)` weight 500 13 px; optional one-line detail below in `var(--font-mono)` 11 px `var(--color-muted)`. |
| Width | Bounded to 360 px; long content wraps. Content longer than ~3 lines belongs in a modal, not a toast. |

**Toast-with-action semantics.** When a toast carries an action button, the timer is extended to 12 s (giving the operator time to read and react). Clicking the action runs its callback and dismisses the toast immediately. Examples:

- *"Equipment removed."* with `[Undo]` (8 s extended to 12 s on action presence).
- *"Sync failed for `<run>`."* with `[Retry]`.
- *"LIMS cache invalidated."* with `[Refresh now]`.

**What NOT to put in a toast.** Anything that would be lost if the operator misses it within 8 s: failed sync gates that block creation, validation findings that prevent NAS sync, fatal errors during a wizard. Those are modals, banners, or persistent status (§3.5.5 / §11).

#### 2.2.3 Banner specifications

Banners are persistent, page-scoped surfaces for ongoing state that would surprise an operator at the next action.

**The five v1 banner triggers** (no other ongoing state qualifies):

1. **Setup-incomplete** -- main window, top of the toolbar (§3.1.4). `var(--color-warning)`.
2. **Sync-blocked-on-success-card** -- wizard's Confirm & Create step (§10.4). `var(--color-warning)`.
3. **LIMS-unreachable while a wizard is open** -- top of the wizard. `var(--color-danger)` (because the LIMS picker won't have live data and the operator about to use it should know). Banner clears automatically when the operator dismisses the wizard or LIMS reconnects mid-flight.
4. **NAS-unreachable across all configured equipment** -- main window, top of the toolbar. Above the setup-incomplete banner if both apply. `var(--color-danger)`.
5. **Reconnecting after server restart** -- main window, top of the toolbar. `var(--color-info)`. Auto-clears on reconnect (§7.1).

**Banner placement and stacking.** Banners stack vertically at the top of their container (main window, wizard, or settings dialog as scoped). Maximum 2 simultaneous; a 3rd collapses the oldest into a one-line *"... and N more issues"* link that opens a small dialog listing all active banners.

**Banner styling per DESIGN.md alerts table (§05):**

- 4 px left border in the tier color.
- Background tint at 7-10% opacity against the surface.
- Title in `var(--font-body)` weight 600 13 px in tier-color.
- Body in `var(--font-body)` 13 px `var(--color-body)` at 0.85 opacity.
- Optional `[CTA]` button on the right (e.g. `[Open Settings]` for setup-incomplete; `[Test connection]` for LIMS-unreachable).

**Banner content -- the four-part shape.** Headline (what's wrong), one-line body (what it affects), CTA (most-likely fix), and dismiss `×` only when the underlying state is genuinely transient (e.g. reconnecting). Setup-incomplete and the gating banners have no `×` -- the only way to clear them is to fix the state.

#### 2.2.4 Inline message specifications

Inline messages are non-blocking, in-flow surfaces for validation feedback.

**Two scopes:**

- **Field-level.** Rendered immediately below the offending input. Triggered on blur and on attempted form submission. Visual treatment per DESIGN.md form-input table (§05): `1.5px solid var(--color-danger)` border on the field, `0 0 0 3px rgba(213,94,0,0.12)` focus ring, error text 11 px `var(--color-danger)` in `var(--font-mono)` below.

- **Form-level.** Rendered as a colored block at the top of the form, used when a multi-field rule fails (e.g. *"Run date must be after project creation date"* -- can't be attributed to a single field). Visual treatment per DESIGN.md alerts table (§05): warning-tier styling for non-fatal rules, error-tier for fatal. Disappears on next valid submit attempt or when the form is reset.

**No section-level inline messages.** When the spec says "section X has an error", the section label gains a small error icon next to the section header, and the field(s) within the section render with their field-level treatment. Section-level chrome would add a third reading layer that operators don't need.

**Server-side validation feedback.** When the backend returns a `422` with field errors, the response's `error.field` and `error.message` (Backend §4.6.3) are rendered using the field-level pattern above. When the backend returns a `422` without a `field` (form-level rule violation), the form-level pattern is used.

**Successful validation.** No inline confirmation message -- a toast confirms the action result (per the §2.2.1 mapping rule). Form fields that pass validation simply stop showing the error treatment.

#### 2.2.5 The `notify()` helper API

`exlab_wizard/ui/notifications.py` exposes a small set of typed helpers; all UI code uses these instead of calling NiceGUI's `ui.notify` directly:

```python
# Action results -> toasts (§2.2.2)
notify_success(message: str, *, action: ActionSpec | None = None) -> None
notify_info(message: str, *, action: ActionSpec | None = None) -> None
notify_warning(message: str, *, action: ActionSpec | None = None) -> None
notify_error(message: str, *, action: ActionSpec | None = None) -> None

# Validation -> inline (§2.2.4)
notify_field_error(field_id: str, message: str) -> None
notify_form_error(form_id: str, message: str) -> None
clear_field_error(field_id: str) -> None
clear_form_errors(form_id: str) -> None

# State -> banner (§2.2.3)
show_banner(banner_id: BannerId, *, container: ContainerId, severity: Severity, ...) -> None
clear_banner(banner_id: BannerId) -> None

# Background state -> status bar (§3.5.5) is published by individual server components,
# not by this helper module.

# Modals are not part of this API -- they are per-section components that
# call into the per-section dialog state (Settings, override, escalation, etc.).

@dataclass(frozen=True)
class ActionSpec:
    label: str          # button text
    on_click: Callable  # callback invoked when the action is clicked
```

`BannerId` is a closed enum mirroring the five §2.2.3 triggers; `show_banner` rejects unknown ids at runtime so the banner set stays disciplined. Adding a new banner trigger is a deliberate spec change (update §2.2.3 + add the enum value), not an ad-hoc choice.

**ESLint-equivalent enforcement.** A small lint rule (added to the project's pre-commit suite) forbids direct calls to `ui.notify` from any module under `exlab_wizard/ui/` other than `notifications.py` itself. Bypass requires a `# noqa: notify` comment with a justification, which surfaces during code review.

---

## 3. Main Window

The main window is the persistent shell. It shows the existing project hierarchy and surfaces entry points to every creation flow. Before this shell is reached, however, the application boots through a lifecycle (welcome card, setup-incomplete handling) specified in §3.1; once the workstation is fully configured, the layout and refresh semantics in §3.2 and §3.3 govern.

### 3.1 Application Lifecycle and First-Launch State

The application has three distinguishable runtime states, derived from `config.yaml` and the OS keyring contents:

- **Uninitialized.** No `config.yaml` exists. First-ever launch on this workstation.
- **Setup-incomplete.** `config.yaml` exists but is missing required configuration (per the *setup-complete* definition in §3.1.1).
- **Ready.** `config.yaml` is complete; the main window operates normally.

#### 3.1.1 Setup-complete definition

Configuration is **complete** when ALL of:

- `paths.templates_dir`, `paths.plugin_dir`, `paths.local_root` are set to existing readable directories (`local_root` additionally writable). Backend §9.
- `equipment[]` contains at least one valid entry that passes equipment-ID validation (Backend §3.1).
- The **LIMS slot** is satisfied — EITHER (`lims.endpoint` + `lims.email` are set AND a password exists in the OS keyring under `(exlab-wizard, lims)`) OR `lims.offline_catalogue_path` points at a readable JSON file (Backend §7.2.9).

Optional sections (operators allowlist, validator overrides, logging tweaks, orchestrator) do not gate readiness.

#### 3.1.2 Boot flow

The application boots through a tray-mediated path (§3.4). The first launch differs from steady-state because the tray hasn't been registered to autostart yet and the welcome card needs to run.

**First launch (no `config.yaml`):**

1. The operator double-clicks the `ExLab-Wizard` icon. This invokes the CLI alias (Backend §15.3.3), which detects no running tray and spawns `ExLab-Wizard-Tray` as a detached background process.
2. The tray reads `config.yaml`, finds it absent (Uninitialized), writes an empty config with the §3.1.5 defaults (Backend §4.9.1), starts the FastAPI server on a random localhost port, registers the system-tray icon, and **immediately spawns** `ExLab-Wizard-Window` (the welcome card needs to be visible).
3. The window opens pywebview pointed at the local server. The first served page is the Welcome Card (§3.1.3).
4. The welcome card collects the autostart-prompt response and dismisses to the Settings dialog in setup-incomplete mode (§7.14).
5. The operator completes setup; closing Settings returns to the Main Window in Ready state (§3.2).

**Subsequent launch with autostart enabled:**

1. The OS executes the registered autostart entry at user login: `ExLab-Wizard-Tray` starts in the background.
2. The tray reads `config.yaml`, starts the server, registers the system-tray icon, and **does NOT auto-spawn the window** (autostart should not surprise the operator with an unsolicited window every login).
3. The operator clicks the tray's **Open** to bring up the window. The window opens to the Main Window in whatever lifecycle state applies (Ready normally; Setup-incomplete renders the §3.1.4 banner).

**Subsequent launch with autostart disabled (manual launch):**

1. The operator double-clicks the `ExLab-Wizard` icon. The CLI alias detects no running tray, spawns one, and signals it to **Open** the window (because the operator's act of double-clicking the icon is itself a request to see the UI).
2. Same as the autostart path from there, except the window opens immediately.

**Lifecycle transitions while running.** Whenever the operator dismisses the Settings dialog (§7), the backend re-evaluates the lifecycle state. A transition from Setup-incomplete to Ready clears the banner immediately (no manual refresh required); a transition the other direction (e.g. operator deletes their only equipment in Settings) re-shows the banner. The tray icon's status submenu (§3.4.2) reflects the change as well.

**Closing the window vs quitting the app.** Closing the native window (window controls or File menu → Close) terminates only the window subprocess. The tray icon and server remain alive; reopening from the tray brings the operator back to the same Main Window (NOT to a wizard or Settings dialog that was open before close — those are window-process state and are lost). To fully quit, the operator chooses Quit from the tray menu; this initiates graceful shutdown (Backend §4.3.2).

#### 3.1.3 Welcome Card (first launch only)

Modal card shown exactly once, on the first time the app is launched on a workstation.

**Layout (top to bottom, single screen):**

- **Headline:** *"Welcome to ExLab-Wizard"*.
- **Three bullets** describing what the app does:
  - *"Creates standardized run / project directories on disk and NAS."*
  - *"Integrates with your LIMS for project tracking."*
  - *"Validates outputs and gates NAS sync on hard-tier findings."*
- **Time estimate:** *"Setup takes about 5 minutes."*
- **Autostart toggle** (checkbox, default **on**): *"Start ExLab-Wizard automatically when I log in."* Helper text underneath: *"Recommended on lab workstations dedicated to acquisition. You can change this later in Settings → Application."* The toggle's state on dismissal is sent to the backend as `POST /api/v1/setup/autostart` with `{ "enabled": <bool> }`, which calls the platform-specific autostart helper (Backend §15.7).
- **Primary button: [Get started]** — applies the autostart choice and opens the Settings dialog in setup-incomplete mode (§7.14).
- **Secondary text link: Skip for now** — applies the autostart choice and closes the card; the Main Window opens with the setup-incomplete banner (§3.1.4) so the operator can explore and return to setup later.

After the first close (either button), the welcome card never appears again. The dismissal flag is persisted in the user's NiceGUI `app.storage.user` namespace (separate from `config.yaml`, so a config reset does not re-show the card).

**Why autostart defaults to on.** Acquisition workstations are typically dedicated to lab work and benefit from always-on (the persistent server lets sync, validation, and queued operations continue across window opens and closes — Frontend §3.4.4 explains the broader UX implications). Operators on shared or general-purpose machines uncheck the toggle.

#### 3.1.4 Setup-Incomplete state on the Main Window

When the Main Window renders in Setup-incomplete state, a sticky top banner appears above the toolbar:

- **Color:** `--color-warning` (§2.1.4) -- the canonical warning-tier token, shared with the test-mode badge, `blocked_by_validation` sync icon, hard-tier validator stripe, and Sync-blocked banner.
- **Headline:** *"Setup incomplete: <N> required section(s) need configuration."*
- **Sub-line listing what's missing**, e.g. *"Missing: equipment list, LIMS access."* Each missing slot is named in plain language (not raw config keys).
- **CTA:** **[Open Settings]** — opens the Settings dialog in setup-incomplete mode (§7.14).

While the banner is present:

- The toolbar's wizard buttons (**New Project**, **New Run**, **New Test Run**) are disabled with a tooltip *"Complete setup to enable creation flows."*
- The left tree displays whatever lives at `paths.local_root` (or an empty placeholder *"No data yet."* if `local_root` is unset).
- **Settings**, **Refresh**, and the **Problems** tab remain enabled — operators can configure, refresh the tree, and see any pre-existing problems.

The banner clears automatically the next time Settings is dismissed in Ready state. No manual refresh of the main window is required.

#### 3.1.5 First-launch defaults

When the backend writes the empty `config.yaml` on Uninitialized launch, it pre-populates the defaults documented in Backend §9 — values like `lims.cache_ttl_hours`, `nas_cleanup.*`, `validator.*`, `logging.*`, and `orchestrator.enabled` come straight from that single source of truth.

Two UX-relevant first-launch behaviors that are NOT in §9:

- **`paths.templates_dir` and `paths.plugin_dir`** default to OS-standard app-data locations (`~/Library/Application Support/exlab-wizard/{templates,plugins}` on macOS, `%APPDATA%\exlab-wizard\{templates,plugins}` on Windows, `$XDG_DATA_HOME/exlab-wizard/{templates,plugins}` on Linux). Both are auto-created as empty directories at first launch.
- **`paths.local_root`** is intentionally empty — research-data location must be a deliberate operator choice. The Settings field renders with placeholder text *"e.g. /data/lab or /Volumes/lab-share"*.

`equipment[]`, the LIMS connection, the offline-catalogue path, and the operators allowlist are not defaulted — they are workstation-specific choices.

#### 3.1.6 Bundled content discovery

ExLab-Wizard ships starter templates and plugins inside the application bundle's read-only `_internal/` directory (Backend §15, distribution). Both are discovered alongside the operator's writable `paths.templates_dir` and `paths.plugin_dir` at runtime; bundled content does not need to be configured and stays out of `config.yaml`.

To customize a bundled template or plugin (e.g. add a lab-specific README field), the operator copies the bundled subdirectory into `paths.templates_dir` (or `paths.plugin_dir`) and renames it (`lab-default-microscopy` → `lab-default-microscopy-mylab`). The two then coexist; bundled content updates with app upgrades while the lab copy is unaffected. Backend §5 (template format) and §6.2.1 (plugin discovery) define the dual-root resolution rules.

A practical implication for onboarding: a brand-new workstation has zero entries in its `templates_dir` and `plugin_dir` but still sees bundled templates and plugins available in the New Project Wizard's Template Selection step (§4) — the operator is not required to populate the writable directories before creating their first project.

#### 3.1.7 LIMS configuration: online and offline workstations

The LIMS slot is satisfied by either path enumerated in §3.1.1; this subsection covers the user-visible consequences. A workstation can be configured as **online-only** (`lims.endpoint` + `lims.email` + keyring password — typical connected case), **offline-only** (`lims.offline_catalogue_path` set — typical for an isolated acquisition machine), or **both** (online primary with the catalogue acting as a fallback when the API is unreachable; the workstation also writes back to the catalogue on each successful LIMS refresh — Backend §7.2.9.2). The picker behavior and badges across these modes are specified in §4.1.

The Settings dialog's LIMS section (§7.6) renders the fields for both paths; when an offline catalogue is configured the section shows an inline note above the live-LIMS fields: *"Offline catalogue is set; live LIMS connection is optional on this workstation."*

### 3.2 Layout

- **Left panel:** Tree or list view of the existing `<equipment>/<project>` hierarchy, read from the configured `local_root` (or NAS mount).
  - `TestRuns/` subfolders (and any leaf folder beginning with `TestRun_`) are shown with a distinct icon and dimmed styling so experimental and test runs are visually distinguishable at a glance.
  - `.exlab-wizard/` folders are hidden by default (see Open Question 2).
  - In orchestrator mode, an equipment selector (sidebar list or tab strip) switches the detail pane between equipment contexts. The project-first tree structure itself is unchanged; only the filter on the selected equipment changes.
- **Right panel:** Detail pane showing selected project or run metadata.
  - Test runs display a "Test run" badge.
  - Run detail includes template name and version, creation timestamp, operator, sync status, and (orchestrator only) current lifecycle state.
- **Toolbar actions:** "New Project", "New Run", "New Test Run", "Settings", "Refresh".
  - "New Run" and "New Test Run" are additionally surfaced as a split button to reinforce that they are distinct workflows with different downstream handling.
- **Tab strip in the right panel:** A tab bar at the top of the right (detail) panel switches between the **Details** view (selected project/run metadata, default) and the **Problems** view (always-on validator audit; Section 11). The Problems tab carries a count badge equal to the number of currently-active hard-tier findings across the managed tree (soft-tier counts shown as a secondary muted number, e.g. `3 + 12`). The badge updates on the same 30-second background refresh used for sync-status icons (Section 3.3) and is independent of which node is selected in the left tree.
- **Sync-status icon vocabulary (per-run, in the left tree and detail header):** Five states are rendered with distinct icons -- `pending` (queued), `synced` (verified at NAS), `failed` (NAS sync error), `blocked_by_validation` (hard-tier finding gates sync; new in v0.4), and `override_active` (sync allowed under operator override; new in v0.4). The `blocked_by_validation` state uses `--color-warning` (§2.1.4).
- **Staging panel (orchestrator mode only):** See Section 8.

### 3.3 Refresh Semantics

"Refresh" re-walks the filesystem and re-reads `.exlab-wizard/creation.json` for visible entries. It does not query the LIMS. A quiet background refresh fires every 30 seconds to keep sync-status icons current without user action.

### 3.4 Tray Icon and Window Lifecycle

The system-tray icon is the persistent surface that hosts the server process. The native window is on-demand: the operator opens it when they want to interact with the app and closes it when they don't. The two are decoupled — closing the window does not stop the server, and the server does not need a window open to do its work (NAS sync continues, validator audits run, plugin operations finish).

#### 3.4.1 Tray menu

A click on the tray icon opens a small native menu with three items:

| Item | Behavior |
|---|---|
| **Open** | Opens the native window. If a window is already alive, focuses it (single-instance — there is never more than one ExLab-Wizard window per workstation). If none, spawns one (Backend §15.3.2). |
| **Status** (submenu) | Renders live state from the server. See §3.4.2. |
| **Quit ExLab-Wizard** | Initiates graceful shutdown (drain in-flight ops up to 30 s, then terminate). See §3.4.3. |

The icon itself uses the app's standard glyph, sized for the platform's tray-icon conventions. Right-click and left-click both open the same menu (per-OS convention dictates which is "primary", but operators reach the menu either way).

#### 3.4.2 Status submenu

The submenu shows a single label that summarizes server state, derived from the live values of `SessionStore`, `NASSyncClient`, and `Validator` (Backend §4.5). Possible states:

| Server state | Submenu label |
|---|---|
| Idle (no active sessions, sync queue empty, no plugin escalations) | *"Idle"* |
| Sync queue active | *"Sync: N jobs"* (N = `queue_depth + in_flight`) |
| Plugin escalation pending | *"⚠ Plugin needs input"* (or *"⚠ N plugins need input"* if multiple) |
| Validator audit running (transient, sub-second) | *"Auditing…"* |
| Setup-incomplete | *"Setup incomplete — open the window to configure"* |

The submenu refreshes every 5 seconds. When the status would carry urgency (plugin escalation, sync failure with no retries), the operator also receives an OS notification (§3.4.5).

The submenu is informational only — clicking the label opens the window (same as **Open**).

#### 3.4.3 Window lifecycle

Closing the native window terminates only the window subprocess. The tray icon stays visible; the server continues running. To re-open, the operator clicks the tray's **Open** (or any other surface that calls back to **Open**, such as the operator double-clicking the desktop launcher icon — Backend §15.3.3).

Closing the window does NOT preserve in-window UI state. Specifically:
- An open Settings dialog with unsaved changes is lost.
- An in-progress wizard (any step) is lost.
- An open override-reason dialog is lost.

This matches the behavior the Settings dialog already specifies for window close (§7.1): the `beforeunload`-equivalent confirmation prompt fires when the operator initiates a window close with dirty state, asking *"Discard unsaved changes and close window?"* before allowing close.

In-flight server-side operations are unaffected by window close (Backend §4.5):
- A creation in progress continues; its result lands in the main-window tree on next reopen.
- NAS sync jobs continue.
- Validator background audits continue.
- A `PluginInputRequired` escalation that was waiting on a now-closed window remains suspended; reopening any window surfaces a notification (§3.4.5) and the resume dialog (§9).

#### 3.4.4 Why this lifecycle matters for the lab workflow

Concrete examples of operations that benefit from window-independent execution:

- **Long sync of large acquisition data.** The operator finishes a 4 hour confocal session, creates the run via the wizard, then closes the window and walks away. The NAS sync continues in the background under the tray; on next reopen the operator sees the run as `synced`.
- **Overnight validator audit.** The operator leaves the workstation; the background validator audits run on schedule (every 30 s) regardless of window state. Findings appear when the operator reopens the window the next morning.
- **Plugin escalation while away from the workstation.** A plugin pauses for input; the OS notification (§3.4.5) fires; the operator returns minutes or hours later, sees the notification or the tray status, and reopens the window to resume.

#### 3.4.5 OS notifications

Backend §15.7.3 is the canonical specification of when OS notifications fire (two triggers: `PluginInputRequired` escalation and sync failure with no retries left), how they coalesce, and how foreground suppression works. From the operator's perspective:

- Notifications are visible only when the window is closed or backgrounded.
- Click-action on an escalation notification opens the window and surfaces the resume dialog (§9).
- Click-action on a sync-failure notification opens the window's Problems tab.
- Routine successes (sync done, audit done, session done) never produce notifications — the in-window status indicators cover those.

#### 3.4.6 Quitting the app

**Quit ExLab-Wizard** in the tray menu initiates graceful shutdown. The full protocol (timing, predicate, force-quit consequences) is specified in Backend §4.3.2 (`quit_coordinator.py`). User-visible affordances:

- If shutdown completes within the wait window: the tray icon disappears with no further prompts.
- If the timeout expires with operations still in flight: the operator sees a prompt — *"ExLab-Wizard: 1 operation still running. Force quit anyway?"* — via the open window if alive, otherwise as an OS notification. The two responses are **[Force quit]** (server exits immediately; NAS-sync jobs resume on next launch) and **[Wait]** (timer resets; rechecks at the next idle moment).

#### 3.4.7 Linux fallback (no system tray)

When the tray fails to register on the current Linux desktop (Wayland-vanilla-GNOME without the AppIndicator extension, certain tiling WMs, headless+VNC sessions), ExLab-Wizard transparently falls back to **window-only mode**. Backend §15.7.4 specifies the procedure; the operator-visible consequences are:

- Closing the window quits the server (no persistent-server affordance available).
- The window's File menu provides an explicit **Quit ExLab-Wizard** (always present on all platforms; especially relevant here since the tray-Quit path is unavailable).
- The Settings dialog's `Application` section (§7.13) displays a note: *"System tray not available on this desktop. Closing the window will quit the app."*
- OS notifications still work (independent of the tray icon).

The fallback is automatic — operators don't choose between modes.

### 3.5 Tree details, filters, and status bar

§3.2 declares the layout shell; this subsection specifies how individual tree nodes render, how operators find runs in a busy tree, and the bottom status bar that summarizes server state at a glance.

#### 3.5.1 LIMS name resolution

The on-disk path segment for a project is its LIMS `short_id` (e.g. `PROJ-0042`); operators recognize projects by human name (e.g. *"Cortex Q3 Pilot"*). The tree resolves this gap with a snapshot-plus-refresh model.

**At creation time** the LIMS project's human name is captured into `creation.json` as `lims_project.name_at_creation` (Backend §11.3). The tree renders this snapshot at all times -- there is no live LIMS dependency for tree rendering, and a workstation that has lost LIMS connectivity still shows readable project names.

**On manual Refresh** (§3.3) the backend additionally re-fetches each visible project's current name from the local LIMS cache (Backend §7.2.4) or the offline catalogue (Backend §7.2.9) and updates the in-memory display only. `creation.json` is **not** rewritten -- the snapshot is preserved as historical truth, while the tree's rendered name reflects whatever LIMS most recently reported. If the cached name differs from the snapshot, the tree node carries a small *"(renamed in LIMS)"* annotation in `var(--color-muted)` until the next app restart, when it re-reads the snapshot fresh.

**Refresh failure** (LIMS unreachable, cache empty, no offline catalogue) leaves the tree showing the snapshot. No error banner; the existing offline indicators on per-row sync status already convey LIMS-side health.

#### 3.5.2 Tree node display format

| Element | Rendering |
|---|---|
| Equipment node | Equipment ID (e.g. `CONFOCAL_01`) in `var(--color-heading)`, body weight 600. Folder icon. |
| Project node | Human name (e.g. *"Cortex Q3 Pilot"*) on the primary line in `var(--color-body)`, body weight 500. Short_id (`PROJ-0042`) on the secondary line below in `var(--color-muted)`, monospace, 11px. Folder icon. |
| Run node (experimental) | `Run_<DATE>` segment in monospace; the operator's `label` from `readme_fields.json` shown to the right in `var(--color-body)` if present. Sync-status icon (§3.2) on the right edge. |
| Run node (test) | Same as experimental but with the test-mode dim treatment from §3.2 plus the `TestRun_` leaf prefix highlighted in `var(--color-warning)`. A small *"Test"* badge to the right. |

Hovering any truncated node reveals the full text plus the absolute on-disk path as a tooltip (`title` attribute -- native browser behavior, no custom popper).

#### 3.5.3 Archived and deleted LIMS-project handling

Three LIMS-side states the tree must render distinctly:

- **`active`** (default): rendered normally per §3.5.2.
- **`archived` in LIMS:** rendered with **strikethrough** on the project name AND short_id, plus a subtle *"(archived)"* badge in `var(--color-muted)` to the right. Children (runs) inherit the strikethrough cue but remain fully accessible -- archived projects still hold real data the operator may need.
- **Deleted from LIMS** (the `short_id` no longer resolves in the LIMS cache or offline catalogue): rendered with `var(--color-warning)` warning-icon prefix and a *"(LIMS project removed)"* tag. Operator can still drill in to access local data; the cache miss is permanent for this `short_id` until LIMS is re-checked.

**Filter interaction.** By default the tree HIDES `archived` projects (per §3.5.4 chip default-state); the strikethrough only becomes visible when the operator toggles the **Archived** chip on. **Deleted-from-LIMS** rows always render regardless of chips -- they are anomalies the operator should be aware of, not noise to filter.

A run created against a project that was later archived in LIMS does NOT itself become archived -- the strikethrough is on the project node only. Runs continue to render with their normal styling under an archived parent (just with the parent's strikethrough decoration).

#### 3.5.4 Search and filter chips

A single-row affordance pinned to the top of the left tree panel, above the equipment list:

```
[ search by name, short_id, or run label ... ]   [Active ✓] [Archived] [Test runs ✓]
```

**Search box.** `ui.input` with case-insensitive substring match against project human name, project short_id, and run label. Filter applies live as the operator types (debounced 150 ms). Match scope: project nodes match if the project name or short_id contains the query; run nodes match if the run label contains the query. When a child matches but its parent does not, the parent expands and renders normally so the match has visible ancestry.

Empty search resets to the unfiltered view.

**Chip strip.** Three toggleable chips:

- **Active** -- on by default. Toggle off to hide active projects (rare but supported, e.g. an operator focused only on a known-archived project).
- **Archived** -- off by default. Toggle on to show archived projects (with §3.5.3 strikethrough treatment).
- **Test runs** -- on by default. Toggle off to hide test runs (the dim-styled `TestRun_*` leaves) without hiding their parent projects -- useful for a clean view of just experimental runs.

Chip state persists per-tab in NiceGUI `app.storage.tab` (Backend §4.4.7); resets to defaults when the window is reopened from the tray.

Chips and search compose: a search query filters within whatever the chips have not hidden. When a search returns zero matches because of the active chip set, the empty state (§3.5.6) names the chips that may be hiding results.

#### 3.5.5 Status bar (bottom of the main window)

A persistent strip pinned to the bottom of the main window, ~24 px tall, in `var(--color-bg)` with a 1 px `var(--color-rule)` top border. Three segments left to right, each clickable:

| Segment | Content | Click action |
|---|---|---|
| **Sync** | *"All synced"* / *"Sync: N jobs"* / *"⚠ N sync failed"*. Reflects `NASSyncClient` aggregate state from Backend §4.5. The warning prefix appears only when at least one job is in `failed` with no retries left. | Opens the Problems tab filtered to sync-state findings; or, if no findings yet, opens a sync-detail view in the right panel listing in-flight jobs. |
| **Validator** | *"Last audit: HH:MM:SS"* with a relative-time tooltip on hover. Updates on every 30-second background refresh (§3.3). | Triggers an immediate audit via `POST /api/v1/problems/refresh` (Backend §4.6.1) and opens the Problems tab. |
| **LIMS** | *"LIMS: live"* / *"LIMS: cached (last fetched HH:MM)"* / *"LIMS: catalogue (produced by &lt;workstation&gt;)"* / *"LIMS: unreachable"*. Reflects the most recent `LIMSClient.health_check()` result. | Opens Settings -> LIMS section (§7.6) with the Test connection button highlighted. |

Each segment uses `var(--text-xs)` (11 px) DM-Mono-equivalent monospace per DESIGN.md §02 (resolved to ExLab-Wizard's `ui-monospace` stack per §2.1.3). Color: `var(--color-muted)` for normal states, `var(--color-warning)` when prefixed with the warning glyph, `var(--color-danger)` if the segment surfaces an error state (e.g. *"⚠ LIMS auth failed"*).

The status bar is hidden during the setup-incomplete state (§3.1.4) -- the setup banner takes its place at the top of the window, and the wizard buttons are disabled, so a status bar describing service health would be misleading.

#### 3.5.6 Empty states

| Context | Tree render | Right panel render |
|---|---|---|
| Setup-incomplete (§3.1.4) | Empty placeholder *"Configure equipment and paths to begin."* | Empty placeholder echoing the same; CTA to open Settings. |
| Setup complete, no projects yet | Equipment nodes visible but childless. Each shows *"No projects yet -- create one from the toolbar."* | When no equipment is selected: top-level message *"Welcome -- create your first project from the toolbar."* When an equipment is selected with zero projects: *"This equipment has no projects yet."* |
| Search returns nothing | *"No matches for '&lt;query&gt;'"* with a `[Clear search]` link. If filter chips may be hiding results, an additional line: *"Active filters: &lt;chip names&gt;. Try clearing chips or broadening your search."* | Right panel shows whatever was previously selected (does not blank on search); if nothing was selected, a generic empty state. |
| Equipment configured but local-root-side directory is empty or unreadable | Equipment node renders with a *"(empty)"* or *"(unreachable)"* annotation in `var(--color-muted)`. Children are not rendered. | Selected-equipment detail shows the configured `local_root` path and a *"Path is empty / not readable -- check Settings or filesystem permissions."* note. |
| LIMS is unreachable AND offline catalogue is unset | Tree renders normally from `creation.json` snapshots; project names appear as snapshots without "(renamed in LIMS)" annotations (since refresh has nothing to compare against). The status bar's LIMS segment shows *"LIMS: unreachable"*. No tree-level disruption. | Detail pane shows whatever metadata `creation.json` carries. Any *"View in LIMS"* deep link remains clickable but the LIMS web UI itself may not load -- that's the operator's signal. |

### 3.6 Detail pane (right panel)

When the operator selects a node in the tree (§3.5), the detail pane renders structured metadata for that node alongside any state-dependent actions. Layout is a vertical stack of **collapsible section blocks**: each block carries its own header, expand/collapse chevron, and optional per-section actions (§3.6.5). Section expand-state persists per-tab in `app.storage.tab` so an operator who keeps the README always-expanded sees that across selections within a session.

A persistent **title bar** sits above the section list -- always visible regardless of which sections are collapsed (§3.6.1).

#### 3.6.1 Title bar

| Element | Rendering |
|---|---|
| Title | The selected node's primary label: project human name (project pages) or run label from `readme_fields.json` (run pages, falling back to the `Run_<DATE>` directory name if no label). DM-Sans-equivalent (`var(--font-body)`), weight 600, `var(--text-lg)`. |
| Subtitle | Project: short_id (e.g. `PROJ-0042`) in monospace `var(--color-muted)`. Run: parent project's short_id + the absolute run-directory name (`PROJ-0042 / Run_2026-05-06T14-32-00`). |
| Sync-status icon | The same five-state icon vocabulary from §3.2, sized at 20 px and color-coded per `var(--color-success)` / `var(--color-warning)` / `var(--color-danger)` per state. Tooltip on hover with the underlying status string. |
| Test-run badge | When `run_kind == "test"` (run pages only): a small *"Test"* pill in `var(--color-warning)` with darkened-text variant per DESIGN.md badge rules. |
| Override badge | When the run has an active validation override: a small *"Override active"* pill in `var(--color-info)`. Click opens the §11.5 override dialog in revoke mode. |

The title bar plus a thin `1px solid var(--color-rule)` divider always sit at the top of the pane. Below the divider lives the action toolbar (§3.6.5) and then the section list.

#### 3.6.2 Project detail sections

| Section | Default state | Contents |
|---|---|---|
| **Identity** | expanded | Human name + short_id; equipment; LIMS-side status (`active`, `archived`, or `removed`); LIMS owner / contact (when in cache); a *"View in LIMS"* deep link (URL derived from `lims.endpoint` per §4.1's deep-link rule). |
| **Storage** | expanded | Local root path (`<local_root>/<equipment>/<short_id>/`) and NAS root path (display value from `equipment.nas_root`), both in monospace; total size on disk if cheaply available. |
| **Activity** | expanded | Run count (experimental + test, broken out); date of latest run; date of latest sync attempt. |
| **Description** | collapsed | LIMS project's `description` field (if available in cache or catalogue). When absent the section is hidden entirely (not collapsed) so projects without descriptions don't show empty chrome. |

A project page does NOT show README, Validation, or Plugin-output sections -- those are run-scoped concerns that exist only on run pages.

#### 3.6.3 Run detail sections

| Section | Default state | Contents |
|---|---|---|
| **Identity** | expanded | `label` (mandatory core field), `run_kind`, parent project (human name + short_id, click navigates to project page), equipment. |
| **Creation** | expanded | `created_at` timestamp (local time, ISO on hover), `operator` (mandatory core field), template name + version, OS username if different from `operator`, `objective` (mandatory core field; rendered as a multi-line block). |
| **Storage** | expanded | Local path, NAS path, both in monospace. |
| **Sync** | expanded | Current sync state (`pending` / `synced` / `failed` / `blocked_by_validation` / `override_active`); queue position if `pending`; last attempt timestamp; failure reason if `failed`; per-section actions per §3.6.5. |
| **Validation** | expanded only when active findings exist | Compact summary per §3.6.4. |
| **Plugin output** | collapsed | List of plugins that ran, with per-plugin status (`success` / `skipped` / `failed`) and any plugin-emitted warnings. Read-only. |
| **README** | collapsed | Per §3.6.5 — collapsed by default with *"Show README"* expansion. |
| **Files** | collapsed | A simple file listing of the run directory (filenames + sizes), bounded to the first ~200 entries; *"View all in file manager"* affordance opens the OS file browser at the directory. Useful for verifying what landed without leaving the app. |

#### 3.6.4 Validation and override summary (resolves Decision 3)

The Validation section shows a compact, glanceable view of per-run validation state and links to the Problems tab for canonical interactions.

**Header line.** When there are active findings: *"⚠ N hard-tier findings"* in `var(--color-warning)` (or *"N soft-tier findings"* in `var(--color-muted)` if only soft). When the run has an active override: *"Override active"* in `var(--color-info)`. When clean: section is hidden entirely (not even collapsed) per the default-state rule above.

**First-N excerpt.** When findings are present, the section shows the first **two** findings as one-line excerpts:

```
⚠ Unresolved placeholder token  ·  Run_<run_date>
⚠ Illegal character in filename  ·  /path/with:colon.txt
```

Each excerpt shows the rule name and the matched-token snippet, in `var(--font-mono)`. If more than two findings exist: *"+ N more in Problems"* link below the excerpts.

**Override summary.** When an override is active: a single line *"Override active — `<reason snippet first 80 chars>` (set by `<operator>` on `<date>`)"*. The full reason is available on hover or in the Problems-tab override dialog.

**Actions on the section** (per §3.6.5 inside-section pattern):

- `[View all in Problems →]` — switches the right panel to the Problems tab filtered to this run.
- `[Re-validate now]` — triggers an immediate audit pass for this run via `POST /api/v1/problems/refresh` scoped to the run path; updates the section and the Problems tab on completion.

#### 3.6.5 Action affordances

A small **global action toolbar** sits between the title bar and the section list. Always-relevant actions for any selected node:

| Action | Applies to | Behavior |
|---|---|---|
| `[Open in Finder]` | project, run | Opens the OS file manager at the node's local-root directory. Disabled (greyed with tooltip) when the path is unreadable. |
| `[Copy path]` | project, run | Copies the absolute local path to the OS clipboard. Toast confirmation on success. |
| `[View log]` | project, run | Opens the relevant `wizard.<hostname>.log` in a read-only viewer (project: equipment-level log; run: run-level log if present, else equipment-level). |
| `[Refresh]` | project, run | Re-walks the node's directory and re-reads `creation.json`. Same as the toolbar Refresh from §3.3 but scoped to the selection. |
| `[Reveal in tree]` | project, run | Visible only when the operator reached the detail pane via Problems-tab "Reveal in tree" or a deep link; scrolls and highlights the node in the left tree. |

**Per-section actions** for state-dependent operations:

- **Sync section** -- `[Retry sync]` (visible only when state is `failed` or `blocked_by_validation` with no override); `[Override and allow sync]` (visible only when state is `blocked_by_validation`; opens the §11.5 dialog).
- **Validation section** -- `[View all in Problems →]` and `[Re-validate now]` per §3.6.4.
- **README section** -- `[Show README]` / `[Hide README]` toggle; when expanded, a `[View raw front matter]` switch toggles between rendered and source view.
- **Files section** -- `[View all in file manager]` (same as toolbar's `[Open in Finder]` but scoped to the listed files, useful when the section has a paginated truncation indicator).
- **Plugin output section** -- per-plugin `[View log]` for plugins whose worker emitted detail logs.

State-dependent actions are hidden (not greyed) when they don't apply, so the section's chrome stays minimal. Greyed-with-tooltip is reserved for global toolbar actions whose target is temporarily unreachable (e.g. `[Open in Finder]` when the directory is offline).

#### 3.6.6 Empty selection

When the operator hasn't selected anything in the tree, the detail pane shows a centered illustration with copy:

- Setup-incomplete state: covered in §3.5.6.
- No selection: *"Select a project or run from the tree to see its details."* with a small *"or use the toolbar to create one"* link to the New Project / New Run wizards.
- Selection deleted in the background (rare; e.g. operator deleted a directory in another tool while ExLab-Wizard was open): the pane shows *"This item is no longer present on disk."* with a *"Refresh tree"* affordance.

### 3.7 Keyboard shortcuts

Beyond NiceGUI's component defaults (Tab / Shift-Tab navigation, Enter to submit a focused button, Esc to dismiss dialogs), ExLab-Wizard binds a small set of app-level shortcuts. The set is intentionally small -- new bindings require a spec change, not an ad-hoc addition. (Resolves Open Question §13.4.)

| Shortcut (macOS) | Shortcut (Windows / Linux) | Action |
|---|---|---|
| `Cmd+N` | `Ctrl+N` | Open the New Project Wizard |
| `Cmd+Shift+N` | `Ctrl+Shift+N` | Open the New Experimental Run Wizard |
| `Cmd+Shift+T` | `Ctrl+Shift+T` | Open the New Test Run Wizard |
| `Cmd+,` | `Ctrl+,` | Open the Settings dialog |
| `Cmd+R` | `Ctrl+R` | Refresh the tree (§3.3) |
| `Cmd+Shift+P` | `Ctrl+Shift+P` | Switch right panel to Problems tab |
| `/` | `/` | Focus the tree search box (§3.5.4); ignored when another text input is focused |
| `Arrow keys` | `Arrow keys` | Navigate within the tree (NiceGUI tree default) |
| `Cmd+Enter` (in wizards) | `Ctrl+Enter` (in wizards) | Advance to next step (equivalent to clicking the primary `Next` / `Submit` button) |
| `Esc` (in wizards) | `Esc` (in wizards) | Cancel; if the current step is dirty, presents the standard cancel-confirmation dialog (§9.4 for plugin escalation, otherwise the wizard's standard close-confirmation) |

**Disabled-context behavior.** All shortcuts are no-ops when the relevant action is disabled (e.g. wizard buttons are disabled in setup-incomplete state per §3.1.4 -- `Cmd+N` does nothing in that state).

**Discoverability.** A `[Keyboard shortcuts]` action in the Help menu (Backend §15.3.4) opens a cheatsheet dialog rendering the table above. This is the single source-of-truth surface operators consult when they forget a binding.

**Implementation.** Bindings live in `ui/keyboard.py` as a single registry; per-shortcut handlers dispatch to the same controller actions the toolbar buttons use. Adding a binding is a spec change to the table above plus a registry entry; bypassing the registry (e.g. binding directly to a NiceGUI element) is a code-review reject.

---

## 4. New Project Wizard

Modal, multi-step. User capability: "Create a New Project" (User Interaction Spec Section 3.1). Backed by the Mapping B LIMS integration ([Design Spec §7.2](../design_specs/design_spec_sections/07_Sync_and_Database_Integration.md#72-lims-integration)): ExLab-Wizard does not create LIMS projects, only consumes them.

| Step | Purpose | Widgets |
|---|---|---|
| 1. **LIMS Project** | Select which LIMS project this ExLab project will be tracked under. Populated from the cached LIMS project list (Design Spec §7.2.4). Searchable by name and `short_id`. Each row shows project name + `short_id` + `status` + `owner`. A "+ New in LIMS" button deep-links to the LIMS web UI's create-project page (opens in a new browser tab); after the operator creates the project there, a "Refresh" button on this step re-fetches the list and the new project appears. If the LIMS is unreachable, the picker uses the cache with a *"(stale, last refreshed: <when>)"* badge. | `ui.select` with filter, status pill, "Refresh" button, "+ New in LIMS" link |
| 2. Template Selection | List available project templates with name and description. User selects one. | Single-column list with description preview pane |
| 3. Equipment Selection | Dropdown or searchable list of known equipment IDs (from `config.yaml`). | Combobox with incremental filter |
| 4. Variable Form | Auto-generated form from the template's `copier.yml` questions. The `project_name` variable is pre-filled from the selected LIMS project's name and shown read-only with a "Defined in LIMS" annotation; templates that don't declare a `project_name` variable are unaffected. | See Section 12 (widget mappings) |
| 5. README Form | Always shown for project and run scopes. The mandatory core fields (`label`, `operator`, `objective`) are pinned at the top and cannot be skipped. `objective` is local-only — it is stored in `readme_fields.json` and the README front matter, not in LIMS (Design Spec §7.2.6). | See Section 6 |
| 6. Preview | Read-only tree showing the directory structure that will be created, with resolved variable values visible in filenames. The on-disk path segment for the project is the LIMS `short_id` (e.g. `PROJ-0042`), not the human-readable name. Read-only preview of README content. The validator (Design Spec §8.1) runs against the resolved destination path before this step renders; any unresolved placeholder tokens (`<...>` or `{{ ... }}`) or illegal-character findings appear as an inline error block above the tree, and the "Next" button is disabled until the operator goes back and fixes the upstream variable values. The error block names each offending segment and the matched token so the operator knows which variable to revisit. | Tree widget + scrollable Markdown preview + inline validator error block |
| 7. Confirm & Create | Progress bar during creation; error details on failure; success summary with path. If the run was created locally but a hard-tier finding gates sync (Section 10.4), the success card carries a "Sync blocked" banner with a deep link to the Problems tab. | Progress bar, collapsible error pane, final summary card |

Navigation: "Back" / "Next" at the footer; "Cancel" closes the wizard and aborts. Once Step 7 begins, "Cancel" becomes "Close" and only closes the dialog -- it does not roll back creation.

### 4.1 LIMS Picker Behavior

- **Filter scope.** The picker shows projects the logged-in operator is a member of (per `project_users` in the LIMS schema; the backend filters via `GET /api/v1/projects` scoped by the result of `GET /api/v1/me`). Operators with no project memberships see an empty list and a help message: *"You are not a member of any LIMS projects. Ask your PI to add you, or click '+ New in LIMS' to create one yourself."*
- **Refresh.** Clicking "Refresh" forces an immediate cache invalidation and re-fetch from LIMS. Failure shows a non-blocking toast (`ui.notify`) and leaves the existing list in place with the stale badge.
- **+ New in LIMS.** Opens the LIMS web UI's create-project page in a new browser tab. The URL is derived from `config.yaml` `lims.endpoint` rather than configured separately: the backend strips the trailing `/api/v1` (or `/api/v<N>`) path component and appends `/projects/new`. So an `endpoint` of `https://lims.lab.example/api/v1` resolves to `https://lims.lab.example/projects/new`. If the derivation rule needs to differ (a LIMS deployment that hosts the UI on a different host than the API), the LIMS team is asked to align them; v1 does not add a separate config knob. The operator creates the project in the LIMS UI, returns to the wizard, clicks "Refresh", and the new project appears.
- **Offline catalogue fallback.** When the consumer rules in Design Spec §7.2.9.3 trigger (catalogue path configured, local cache empty, LIMS unreachable), the picker reads from the offline catalogue and renders the rows normally. Each row carries an *"(via offline catalogue)"* badge in a muted treatment alongside the row's status pill; hovering the badge reveals a tooltip with the catalogue's producer workstation and timestamp (e.g. *"Produced by `LAB_STATION_01` on 2026-05-04 23:11"*). All other behavior (filter, search, "+ New in LIMS") is unchanged; only the source annotation differs. Catalogue read failures (file unreadable, parse error, `lims_endpoint` mismatch — see §7.2.9.4) are surfaced via the same blocking error described in the next bullet.
- **Empty cache + offline + no catalogue.** If the cache is empty, the LIMS is unreachable, AND no offline catalogue is available (path unset, file missing, or read failed for any reason), the picker shows a blocking error: *"No LIMS projects available. Connect to the LIMS network and click Refresh, configure an offline catalogue path in Settings (LIMS section), or copy the cache from a connected machine."* The wizard's "Next" button is disabled.
- **Status filter.** A small filter bar at the top of the picker offers `Active` (default), `Pending`, `Completed`, `Archived` chips. By default only `Active` and `Pending` projects are shown to reduce clutter.

---

## 5. New Run Wizard (Experimental and Test Modes)

Modal, multi-step. User capabilities: "Create a New Experimental Run" and "Create a New Test Run" (User Interaction Spec Sections 3.2, 3.3). Structurally similar to the Project wizard with the following mode-aware differences.

### 5.1 Mode Binding at Launch

- The wizard launches in **Experimental** mode (via "New Run") or **Test** mode (via "New Test Run").
- The mode is a single flag bound at wizard construction and cannot be changed mid-session. A misclicked mode is resolved by closing and reopening the wizard.
- The active mode is displayed in the wizard **title bar** at all times (e.g., "New Run -- Experimental" vs. "New Test Run").
- The active mode is **repeated on the Preview step**, above the destination path.

### 5.2 Steps

| Step | Purpose | Mode-specific behavior |
|---|---|---|
| 1. Project + Equipment | User selects parent project and equipment. Pre-selected if one is highlighted in the main window. | Same in both modes |
| 2. Template Selection | Lists run-scope templates filtered by `_exlab_run_scope`. | Experimental: scope `"experimental"` or `"both"`. Test: scope `"test"` or `"both"`. |
| 3. Variable Form | Auto-generated form from the template manifest. `run_date` is auto-filled to now; user may override. | Same in both modes |
| 4. README Form | Same as project wizard. | Same in both modes |
| 5. Preview | Destination path shown. The validator (Design Spec §8.1) runs against the resolved path before this step renders; any unresolved placeholder tokens (e.g. a literal `<run_date>` segment because the variable form left `run_date` empty) or illegal characters surface as an inline error block above the path, with the "Next" button disabled until the operator revisits the variable form. | Experimental: `<equipment>/<project>/Run_<DATE>/`. Test: `<equipment>/<project>/TestRuns/TestRun_<DATE>/`, with both the `TestRuns/` segment and the `TestRun_` leaf prefix **visually highlighted** and a short advisory underneath: *"This run will be excluded from automated analysis."* |
| 6. Confirm & Create | Same pattern as project wizard. | Test: the primary button is labeled **"Create test run"** and uses a differently colored button to reduce accidental creation in the wrong mode. Experimental: primary button is **"Create run"** in the default primary color. |

### 5.3 Visual Differentiation

- The wizard title bar badge is color-coded: experimental uses the app's primary accent color (`--color-navy`); test uses `--color-warning` (§2.1.4). Red is reserved for errors (`--color-danger`) and never used for test mode.
- The Preview step's highlighted `TestRuns/` segment and `TestRun_` leaf prefix both use `--color-warning`, matching the title bar badge so the operator sees a single consistent cue.
- On the main window, test runs in the left tree use the dimmed styling noted in Section 3.2 -- the same visual vocabulary is reused.

---

## 6. README Authoring Step

Always invoked for project-scope and run-scope creations. User capability: "Author a README at Creation Time" (User Interaction Spec Section 3.5). README generation is no longer optional for these scopes, because the mandatory core fields (`label`, `operator`, `objective`) provide the minimum recoverable context for every directory the app creates.

### 6.1 Layout

Form fields are grouped by source and rendered in a fixed vertical order, top to bottom:

1. **Mandatory core fields** (User Interaction Spec Section 2): `label`, `operator`, `objective`. Pinned at the top of the form with a visible "Required" section header. Each field is marked with the required-field indicator (Section 12.3). Empty values block advancement. `operator` pre-fills with the OS username; the operator may edit but cannot clear it.
2. **Template fields** declared in the selected template's `copier.yml`.
3. **Config-extended fields** declared in `config.yaml` `readme.defaults`.
4. **Custom fields** added by the operator via the "+ Add field" button (appends a blank label/value row; each operator-added row has a row-level delete affordance, a small "x" at the right).
5. **Auto-filled system fields** (timestamp, OS username, equipment, template, run kind), shown read-only and visually separated.

There is no "Skip" button on the README step. The step is only complete when all required fields (core + template-required + config-required) have non-empty values. Validation fires on blur and on attempted "Next".

### 6.2 Pre-fill Rules

- `operator` pre-fills with the OS username.
- `label` pre-fill behavior depends on scope:
  - **Project creation:** pre-fills with the LIMS project's `name` (e.g. `"Cortex Q3 Pilot"`) from the picker selection in §4 step 1. Editable; the operator can amend or replace. The on-disk path segment uses the LIMS `short_id` regardless of what the operator types here, so divergence between `label` and the on-disk segment is expected and harmless.
  - **Run creation:** no default; the operator must type a label appropriate to the run (e.g. `"calibration sweep, 488 nm"`).
- `objective` has no default in any scope; the operator must type it. The value is local-only (Design Spec §7.2.6) — it is written to `readme_fields.json` and the README front matter but is not synced to LIMS in v1.
- Editable template and config fields pre-fill from the template's or config's `default:` value only. There is no carry-forward from previous runs. (Backend spec Section 10.5.)
- Auto-filled system fields always show current values (timestamp, OS username, equipment, template, run kind).

### 6.3 Preview Behavior

The Preview step's README preview pane renders both the YAML front matter (as a syntax-highlighted code block) and the Markdown prose body (rendered). It is scrollable and read-only. Updates between step transitions; no live re-rendering on every keystroke.

### 6.4 Required-Field Error Messaging

When the operator tries to advance with an empty core field, the error message must name the specific field and the reason it is required (e.g. *"`objective` cannot be empty -- a one-paragraph description of this run is required on every creation."*). Generic "Please fill in all required fields" messages are not acceptable for the core set because they obscure which field is missing when multiple are empty.

---

## 7. Settings Dialog

The Settings dialog is the operator's surface for configuring everything in `config.yaml` and the OS-keyring credentials it references. User capability: "Configure Equipment, Paths, and Integrations" (User Interaction Spec Section 3.6); backend-side schemas live in Design Spec §9.

**Important: no plaintext credentials anywhere in this dialog.** All secrets (LIMS password, the rare per-equipment NAS HTTP-basic password) are managed via the OS keyring (Design Spec §7.4); the dialog never displays a stored secret.

### 7.1 Modality

Modal. The dialog blocks the main window while open. The dialog header carries a **"View main window"** affordance that closes the dialog. If any field in the working copy is dirty, closing the dialog (via the close button or "View main window") presents a confirmation with three options: **Save and close**, **Discard and close**, **Keep editing**.

Browser-refresh and tab-close concerns are mostly moot in the native-window distribution (§3.4): the pywebview window has no F5 key in shipping builds (debug-only) and is single-instance. The remaining disconnection scenario is **server restart while the window is open** — for example, the tray's Quit-then-relaunch flow or an upgrade. The window detects WebSocket disconnection, displays a non-blocking *"Reconnecting to ExLab-Wizard…"* banner for ~3 seconds, polls `/health`, and triggers a window-side reload as soon as the server is reachable. UI state is reset on reload (matching the §7.1 stateless-render policy); dirty Settings working copies are lost. The 3-button confirmation on close (above) and the `beforeunload`-equivalent on intentional window close (§3.4.3) protect the common loss paths.

### 7.2 Layout — sidebar navigation

A two-pane dialog:

- **Left:** vertical nav listing the nine sections (Paths, LIMS, Equipment List, NAS Cleanup, Operators, Validator, Logging, Orchestrator Mode, Application). The currently-selected section is highlighted; sections holding unsaved changes show a small "•" dot beside their name; sections with missing required configuration in setup-incomplete mode (§7.14) carry a warning icon.
- **Right:** content area for the active section.

A footer bar across the bottom of the dialog carries **[Discard all]** (left) and **[Save all changes]** (right). The Save button shows a count badge when the working copy is dirty (e.g., *"Save all (3 changes)"*).

Switching sections does NOT discard a section's edits — the working copy persists across section switches until the operator hits global Save or Discard.

### 7.3 Save and Discard model

**Global save.** The dialog holds a working copy of the entire `config.yaml` from the moment it opens. All field edits across all sections mutate that working copy. **[Save all changes]** writes the working copy to disk atomically (one `config.yaml` write); **[Discard all]** resets the working copy from disk and clears all dirty markers.

**Sub-dialogs stage into the working copy.** The Equipment Add/Edit sub-dialog (§7.7.2) carries a primary button labelled **Done** rather than "Save" to signal that nothing is persisted by clicking it — only by the parent dialog's **[Save all changes]**. The equipment list table updates immediately to reflect the working copy.

**Side-effect ordering on Save.** After `config.yaml` is written, the dialog triggers component re-initialization in this order: template re-discovery, plugin re-discovery, NASSync transport re-registration, LIMS cache invalidation. A toast (`ui.notify`) summarizes what changed (e.g., *"Saved. 2 equipment entries updated; LIMS cache invalidated."*).

**Credentials are independent of Save.** Set / Replace / Clear actions on credential fields (§7.4.1) write directly to the OS keyring at the moment they are clicked. Credentials are not part of the working copy, are not affected by **[Discard all]**, and don't contribute to the Save badge's pending-change count.

**Validation.** Per-field validation runs on blur and renders inline errors below the field. **[Save all changes]** is disabled while any required field is empty or has a validation error; the section nav highlights the offending section with an error icon so the operator can find it without scanning every section.

### 7.4 Reusable patterns

#### 7.4.1 Credential field

Used for the LIMS password (§7.6) and per-equipment HTTP-basic NAS passwords where the configured transport requires one (§7.7.2). Never displays a stored value.

A credential row has two resting states and one transient state:

- **Not set.** Displays `Status: Not set` with a `[Set]` button. Clicking expands the row to reveal an inline password input + Save / Cancel.
- **Set.** Displays `Status: Set ✓` with `[Replace]` and `[Clear]` buttons.
- **Editing** (transient). The inline password input is open. **Save** writes the typed value to the OS keyring under the appropriate `(service, username)` pair (Design Spec §7.4) and collapses the row to **Set**. **Cancel** discards the typed value and collapses the row without writing.

Clearing prompts a confirmation: *"Remove the stored password? You will be prompted to re-enter it on the next API call."* On confirm the keyring entry is removed immediately and the row returns to **Not set**.

#### 7.4.2 Test-connection feedback panel

Used by the LIMS section (§7.6) and the Equipment Add/Edit sub-dialog (§7.7.2).

A **[Test connection]** button below the relevant fields. Clicking probes the configured target with the working-copy values plus the credential currently in the keyring (or the value typed into an Editing-state credential field, when one is open — so the operator can validate before committing). The result renders in a persistent inline panel below the button:

- **Result icon + headline.** A green check + *"Connected"* or a red X + *"Connection failed"*.
- **Detail line.** For success: latency and any context returned by the target (e.g., LIMS: *"Authenticated as alex.nguyen@lab.example, round-trip 142 ms"*; rclone: *"Listed remote `lab-nas` in 318 ms"*). For failure: a one-line reason (*"401 Unauthorized — check the password"*, *"Connection refused at lims.lab.example:443"*, *"rclone remote `lab-nas` not found in rclone.conf"*).
- **Show details disclosure** (collapsed by default). Expanded, displays the full underlying response or stack message in a read-only monospaced block with a `[Copy details]` button.

The panel persists until the next Test or until any field in the same section is edited, at which point a *"(may be stale; re-test to confirm)"* tag is appended to the headline.

### 7.5 Paths section

Backs the `paths` block (Design Spec §9).

| Field | Backs | Notes |
|---|---|---|
| Templates directory | `paths.templates_dir` | Directory picker. Helper: *"Bundled starter templates ship under the app's `_internal/` directory and are read-only."* |
| Plugin directory | `paths.plugin_dir` | Directory picker. Helper: *"Bundled scaffolds are not configurable."* (Design Spec §6.2.1.) |
| Local data root | `paths.local_root` | Directory picker. Helper: *"All projects and runs live under `<local_root>/<equipment>/<project>/...`. Changing this affects new creations only; existing data is not moved."* |

All three paths must exist and be readable; `local_root` must additionally be writable. There is no global NAS root field — NAS targets are per-equipment (§7.7).

### 7.6 LIMS section

Backs the `lims` block (Design Spec §7.2, §9). The LIMS slot of the setup-complete check (§3.1.1) is satisfied by EITHER a live LIMS connection (Endpoint + Email + Password) OR an offline catalogue path; this section's fields are organized accordingly with a small inline note above the fields when the offline catalogue is configured: *"Offline catalogue is set; live LIMS connection is optional on this workstation."*

| Field | Backs | Notes |
|---|---|---|
| Endpoint URL | `lims.endpoint` | HTTPS URL. Helper: *"The LIMS web UI is derived from this URL by stripping the `/api/v1` suffix."* Optional if `Offline catalogue path` is set. |
| Operator email | `lims.email` | Optional if `Offline catalogue path` is set. |
| Password | OS keyring `(exlab-wizard, lims)` | Credential field (§7.4.1). Optional if `Offline catalogue path` is set. |
| Cache TTL (hours) | `lims.cache_ttl_hours` | Numeric, default 24, range 1–168. |
| Offline catalogue path | `lims.offline_catalogue_path` | Optional file path picker. Helper: *"Path to a shared JSON file written by another connected workstation. Used as a fallback when this machine can't reach the LIMS directly. See §7.2.9."* |

A **[Test connection]** button below the fields runs `LIMSClient.health_check()` (Design Spec §7.2.6) when an endpoint is configured, AND additionally reads the offline catalogue when the path is set. Result-panel composition (per §7.4.2):

- Live LIMS only configured: standard *"Connected"* / *"Connection failed"* result.
- Offline catalogue only configured: result reads *"Offline catalogue OK — produced by `<workstation>` on `<timestamp>`"* (or a corresponding error such as *"Catalogue not found at <path>"* / *"`lims_endpoint` mismatch"*).
- Both configured: a single combined result *"Connected (live) — catalogue produced by `<workstation>` on `<timestamp>`"*. Either path failing is reported individually with a per-path icon; the overall result is green only if at least one path is healthy.

### 7.7 Equipment List section

Backs the `equipment` array (Design Spec §9). The list-section view shows currently-configured equipment; Add and Edit operations open a dedicated sub-dialog (§7.7.2).

#### 7.7.1 List table

Columns:

| Column | Contents |
|---|---|
| ID | The equipment ID. |
| Label | Human-readable label. |
| Local root | The shared local-root path. |
| Transport | Badge: `rclone` or `rsync_ssh`, plus a small status dot (green / red / grey) reflecting the most recent connection-test result for that equipment. |
| Actions | `[Edit]` and `[Delete]`. |

Above the table: **[+ Add equipment]**.

**Reorder.** Rows are draggable; the order in `equipment[]` matches the table order. Drag-reorder mutates the working copy.

**Delete.** Clicking Delete prompts a confirmation: *"Remove `<ID>`? Existing data on disk is not affected; this only removes the equipment from `config.yaml`."* On confirm, the row is removed from the working copy and an undo toast (`ui.notify` with **Undo**, 8-second duration) appears; clicking Undo restores the row to its previous position. The disk-side delete only happens when the dialog's **[Save all changes]** fires.

#### 7.7.2 Add / Edit sub-dialog

Modal-on-modal sub-dialog. Scrollable single-column form. Primary button: **Done** (applies to working copy and closes); secondary: **Cancel** (closes without applying).

**Identity group**

- **ID.** Single-line input, validated against `^[A-Z][A-Z0-9_]*$` (max 32 chars; Design Spec §3.1). On Edit, this field is read-only with a help-link beside it: *"Need to rename? See [docs/equipment-rename.md] for the manual procedure."* The link opens the renaming-workaround documentation (delete + re-add with new ID + filesystem move + sync re-register). Equipment renames are rare in practice; an in-app guided migration is planned for v2 (Backend §15.8 OQ #5 tracks this as a v2 commitment). (Resolves Open Question §13.9.)
- **Label.** Single-line input, max 100 chars.
- **Completeness signal.** Radio: `sentinel_file` / `manifest`. Selecting a value reveals a sub-field:
  - `sentinel_file`: **Sentinel filename** input (default `acquisition_complete.flag`).
  - `manifest`: **Manifest filename** input (default `run_manifest.json`).

**Storage group**

- **Local root.** Directory picker; backs `local_root`.
- **NAS root** (display value). Text input; backs `nas_root`. Helper: *"Display path shown in the UI. The actual transport target is configured below."*

**Transport group**

- **Transport type.** Radio: `rclone` / `rsync_ssh`. Switching this resets the conditional fields below to their defaults and dirties the form.
- For `rclone`:
  - **Remote name.** Single-line input. Helper: *"Remote name from `rclone.conf`. To set up a new remote, run `rclone config` from a terminal — this app does not edit `rclone.conf`."*
  - **Remote path.** Single-line input.
- For `rsync_ssh`:
  - **SSH target.** Single-line input (e.g. `labuser@nas01.lab.example`).
  - **SSH key path.** File picker, default `~/.ssh/id_ed25519`. Helper: *"Password authentication is not supported. The key file must be present and have safe permissions."*
  - **Remote path.** Single-line input.
- **(Optional) NAS HTTP-basic password.** Credential field (§7.4.1), suppressed by default and shown only when the configured transport requires one.

**Bandwidth group.** See §7.7.3.

**[Test connection]** button at the bottom of the sub-dialog. For `rclone`, runs `rclone lsd <remote>:<path>` against the working-copy values. For `rsync_ssh`, opens an SSH connection and runs `ls <remote_path>`. Result panel per §7.4.2.

#### 7.7.3 Bandwidth schedule editor

Inside the equipment sub-dialog, in the Bandwidth group. Backs `transport.bandwidth` (Design Spec §9).

**Mode selector** (radio):

- **Unlimited** (default) — no cap, no schedule.
- **Limit upload bandwidth** — reveals the cap and schedule UI below.

**Cap field** (visible when mode = Limit):

- **Default upload (Mbps).** Numeric input. Applied outside any schedule window.

**Schedule windows** (visible when mode = Limit, optional):

A table with columns:

| Column | Widget |
|---|---|
| Days | Multi-select pills: `Mon Tue Wed Thu Fri Sat Sun`. |
| From | Time picker. |
| To | Time picker. |
| Upload (Mbps) | Numeric input; empty = unlimited within this window. |

**[+ Add window]** button below the table; per-row delete affordance.

**Validation.** Each row requires `From < To`. Rows whose Days overlap each other render a non-blocking warning beneath the table (*"Mon 08:00–18:00 overlaps Mon 09:00–12:00"*). For overnight windows (e.g., 22:00–06:00), the operator enters two rows.

### 7.8 NAS Cleanup section

Backs `nas_cleanup` (Design Spec §7.1.6, §9).

| Field | Backs | Notes |
|---|---|---|
| Cleanup enabled | `nas_cleanup.enabled` | Toggle. Helper: *"When disabled, all local data is retained until manually deleted."* |
| Minimum verify passes | `min_verify_passes` | Numeric, default 2, range 1–10. |
| Minimum age (hours) | `min_age_hours` | Numeric, default 24, range 1–720. |
| Retain `.exlab-wizard/` metadata | `retain_cache` | Toggle, default on. Helper: *"Keeps run metadata locally for audit and validation after the data files are deleted."* |

The lower three fields are interactable only when **Cleanup enabled** is on.

### 7.9 Operators section

Backs `operators.allowlist` (Design Spec §9).

A single chip-input field: each operator username is a chip, with a **[+ Add]** affordance and per-chip delete. Helper text above the field: *"If empty (default), the operator field accepts any value. If non-empty, the wizard renders a dropdown of these values and rejects free-text entries."*

### 7.10 Validator section

Backs the `validator` block (Design Spec §8.1.1, §11.8, §9).

| Field | Backs | Notes |
|---|---|---|
| Max content-scan size (MiB) | `validator.content_scan_max_mib` | Numeric, default 5, range 1–100. Helper: *"Files larger than this are skipped during placeholder-token scans."* |
| Scanned file extensions | `validator.content_scan_extensions` | Chip input pre-populated with the spec defaults. A **[Reset to defaults]** action restores the spec list. |

### 7.11 Logging section

Backs the `logging` block (Design Spec §11.5.1, §9).

| Field | Backs | Notes |
|---|---|---|
| Level | `logging.level` | Radio: `DEBUG` / `INFO` (default) / `WARN` / `ERROR`. |
| Central log size cap (MB) | `central_log_max_mb` | Numeric, default 10. |
| Rotated log copies kept | `central_log_keep` | Numeric, default 5. Helper: *"Per-equipment and per-run logs are not rotated by spec — they are bounded by the lab's run cadence."* |

### 7.12 Orchestrator Mode section

Backs the `orchestrator` block (Design Spec §9, §13).

| Field | Backs | Notes |
|---|---|---|
| Orchestrator mode enabled | `orchestrator.enabled` | Toggle. Toggling shows an inline banner: *"Orchestrator mode requires an app restart to take effect."* |
| Workstation label | `orchestrator.label` | Single-line input. |
| Staging root | `orchestrator.staging_root` | Directory picker. |
| Cleanup mode | `staging_cleanup.mode` | Radio: `manual` (default) / `scheduled`. |
| Retain hours | `staging_cleanup.retain_hours` | Numeric, default 24. Visible only when Cleanup mode is `scheduled`. |

All fields below the toggle are interactable only when **Orchestrator mode enabled** is on.

### 7.13 Application section

Settings that govern the app process itself rather than `config.yaml` data: autostart, tray-icon affordances, and platform-specific behavior. These do not roundtrip through `config.yaml`; the autostart toggle calls the platform-specific helper directly (Backend §4.3.2, §15.7), and the section is exempt from the working-copy / global-Save model (changes are applied immediately via dedicated affordances).

| Field | Mechanism | Notes |
|---|---|---|
| Start ExLab-Wizard at login | Toggle backed by `tray/autostart.is_registered()` / `register()` / `unregister()` | Toggling immediately registers or unregisters the platform autostart entry (LaunchAgent on macOS, registry Run-key on Windows, systemd user unit or XDG autostart on Linux). Helper text: *"Recommended on lab workstations dedicated to acquisition. Disabling means you'll need to launch ExLab-Wizard manually after each login."* |
| Show in system tray | Read-only status indicator | Reflects whether `pystray` successfully registered an icon on this desktop. On Linux without tray support, this displays *"Not available on this desktop — closing the window will quit the app."* (Frontend §3.4.7). Linked to a small `[Refresh]` button that re-attempts pystray registration (useful after operators install a tray-providing extension and want to enable it without restarting). |
| Window behavior on close | Read-only informational text | When the tray is available: *"Closing the window does not quit the app — the server keeps running in the tray. To fully quit, choose Quit from the tray menu."* When the tray is unavailable (Linux fallback, §3.4.7): *"Closing the window will quit the app on this desktop."* The text is always visible — its content tracks the current tray availability so the operator never has to wonder which mode they're in. |

A **[Quit ExLab-Wizard now]** button at the bottom of the section initiates graceful shutdown (§3.4.6) — this exposes the same action as the tray's Quit, useful when the operator can't find the tray icon on an unfamiliar desktop.

### 7.14 Setup-incomplete state

When the dialog is opened with required `config.yaml` sections missing (first-launch case; Backend §4.9, Frontend §3.1.1), the dialog enters a setup-incomplete mode. The first incomplete section is auto-selected; the sidebar nav decorates incomplete sections with a warning icon; a top-of-content banner reads *"Setup incomplete. Configure the highlighted sections to start using ExLab-Wizard."* In this mode the footer's **[Save all changes]** button is replaced by **[Save and continue]**, which advances to the next incomplete section after a successful save (or dismisses the dialog when all required configuration is valid).

**LIMS slot has two satisfying paths** (per the canonical setup-complete definition in §3.1.1). The setup-incomplete check warns the LIMS section only when neither path is configured; either alone is sufficient.

The full application lifecycle (welcome card, setup-incomplete banner on the main window, transitions between Uninitialized / Setup-incomplete / Ready states) is specified in §3.1.

---

## 8. Orchestrator Mode Surfaces

Only shown when `orchestrator.enabled: true` in `config.yaml`. User capability: "Monitor Orchestrator Staging" (User Interaction Spec Section 3.7); backend staging state query: Design Spec Section 13.8.

### 8.1 Equipment Selector

A sidebar list or tab strip in the main window header that switches which equipment context the left tree and detail pane display. Exactly one equipment is active at a time in the main view. Multiple wizard windows may be open simultaneously for different equipment; they are independent.

### 8.2 Staging Panel

**Bottom dock**, always visible when orchestrator mode is enabled. The dock occupies ~120 px at the bottom of the main window, beneath the left tree and right detail panel and above the status bar (§3.5.5). It is not collapsible -- orchestrator mode exists specifically to monitor multiple equipment, so the staging panel is the primary operator concern and stays persistent. (Resolves Open Question §13.2.) Shows all runs currently in staging with:

- Current lifecycle state (`staging`, `complete`, `sync_queued`, `sync_verified`, `cleared`)
- File count and total size
- Elapsed time since last activity
- A "Test" badge for staged runs whose `creation.json` sets `run_kind: "test"`
- Per-row actions:
  - **Force sync** (for runs stuck in `complete` but not yet `sync_queued`)
  - **Clear** (only for runs in `sync_verified`)
  - **View log** (opens the run's `wizard.<hostname>.log` in a scrollable read-only viewer)

### 8.3 Clear Verified Runs Action

When staging cleanup mode is `"manual"`, the main window exposes a **"Clear verified runs"** action listing all sync-verified staged runs with sizes. The operator initiates deletion explicitly. This is a toolbar action, separate from the per-row "Clear" above, for bulk cleanup.

---

## 9. Plugin Input Escalation

When a plugin worker raises `PluginInputRequired` mid-creation (Backend §6.4), the creation controller suspends and hands control back to the client. This section specifies the dialog UX, the handling of multiple consecutive escalations, the per-plugin progress affordance, the cancel-rollback behavior, the disconnect/reconnect path via the in-flight operations panel, and the concurrent-suspended-sessions policy.

### 9.1 Escalation dialog layout

A modal dialog rendered over whatever wizard step is currently active. **Click-outside-to-dismiss is disabled** -- accidental dismissal would abort an in-flight creation.

| Element | Content |
|---|---|
| Title bar | *"Additional input required"* + a small info pill on the right showing the plugin identity: *"Plugin: `xlsx_field_filler` v0.3"*. The pill is read-only; clicking opens the plugin manifest in a side popover (read-only excerpt: name, version, description, declared fields). |
| Reason line | The plugin's `reason` string from the `PluginInputRequired` event (Backend §4.6.2 `input_required` frame), e.g. *"The acquisition_metadata.xlsx template has 3 unresolved fields. Please fill them in."* |
| Form area | Fields generated from the plugin's `fields` definition. Widget mappings per §12 (string -> input, choice -> combobox, etc.). Required indicator per §12.3 on every field declared `required: true`. Inline validation on blur. |
| Footer | **[Submit]** (primary, disabled until all required fields are non-empty and pass per-field validation), **[Cancel]** (secondary; opens the cancel-confirmation dialog from §9.4). |

**Submit behavior.** Submitting sends `POST /api/v1/sessions/{id}/resume` (Backend §4.6.1) with the field values; the controller un-suspends and the plugin worker receives the values via its IPC channel (Backend §6.4). The dialog closes; the wizard's progress bar resumes ticking through the remaining phases.

**Plugin error during escalation.** If the worker crashes while suspended (timeout, exit-code, connection lost), the controller transitions to `FAILED`, the escalation dialog is force-closed, and the wizard's Confirm & Create step renders the standard error card (§10.2) with the plugin's name and reason. The cancel-rollback flow (§9.4) does NOT run -- the operator's previous-step state is preserved as orphan if any files were already written, matching the spec's "preserve partial files for review" default.

**Plugin worker timeout during escalation.** Per Backend §6.4, the worker's `isolation.timeout_seconds` runs continuously while suspended -- a plugin that escalates and waits 10 minutes for an answer has its timeout countdown going the whole time. Plugins SHOULD declare timeouts that account for human input latency (e.g. 600 seconds for a plugin that may need operator attention). If the timeout fires while the operator is filling the dialog, the controller transitions to `FAILED` and the dialog closes with a *"Plugin timed out waiting for input"* error.

### 9.2 Multiple consecutive escalations from one session (sequential)

A plugin processing N files may emit `PluginInputRequired` once per file. The controller protocol is sequential: each escalation dialog must be Submitted (or Cancelled) before the next opens. The wizard's progress bar shows phase = `running_plugins` throughout; each escalation is a brief modal-dialog interruption rather than a phase change.

A small toast (`ui.notify` per the notification taxonomy) appears between consecutive escalations (*"Resuming -- next prompt incoming"*) so the operator isn't surprised by a second dialog opening immediately after Submit.

There is no batched "1 of N" indicator. Each escalation is treated as an independent request because escalation N's questions often depend on escalation N-1's answers (e.g. *"You said the experiment uses cell line A; what passage number?"*); batching would force plugin authors to design for it, which most won't.

### 9.3 Mid-plugin-pass progress (per-plugin sub-progress)

The wizard's Confirm & Create step (§10.1) shows a phase progress bar. While the `running_plugins` phase is active, a sub-row appears beneath the phase row:

```
Running plugins                                                [////       ]
  └─  xlsx_field_filler  --  4 of 8 plugins                    [///////    ]
```

The sub-row's contents:

- **Current plugin name** in `var(--font-mono)`.
- **Position indicator** *"N of M plugins"* in `var(--color-muted)`.
- **Determinate sub-bar** showing N/M, OR an indeterminate bar if the plugin doesn't emit per-file progress.

The sub-row data comes from the backend's existing `progress` WebSocket frame (Backend §4.6.2): `{ kind: "progress", phase: "running_plugins", current: N, total: M }`. No new event shape is required; the frontend just renders an additional row when `phase: running_plugins` and `current/total` are present.

When a plugin escalates, the sub-bar pauses (visible but static) until Submit -- communicating to the operator that the lack of motion is expected, not a hang.

### 9.4 Cancel during escalation -- confirmation dialog

Clicking **[Cancel]** in the escalation dialog opens a small confirmation overlay (modal-on-modal):

- Headline: *"Cancel creation?"*
- Body: *"This run is partially created. The directory exists and some files have already been written. What should happen to them?"*
- Two affordances side by side:
  - **[Keep partial files for review]** *(default focus)*. The controller stops without further plugin invocations; partial files remain on disk; no `creation.json` is written. The validator's orphan rule (Backend §8.1.4) catches the directory on next audit and surfaces it on the Problems tab so the operator can salvage or delete from there.
  - **[Discard everything]**. The controller deletes the partially-created directory recursively and exits. No trace remains; no orphan to clean up later.
- Tertiary text link: **Keep editing** -- closes the confirmation dialog and returns to the escalation form (no cancellation).

The two-button choice is preferred over an OS-style "Cancel/Discard/Save" trio because both options here are forms of cancellation -- the choice is what to do with side effects, not whether to cancel.

After either choice, the wizard's Confirm & Create step renders a terminal state: **Discard** shows *"Creation cancelled; partial files removed."*; **Keep** shows *"Creation cancelled; partial files retained at &lt;path&gt;. See the Problems tab to clean up."* with a deep link to the orphan finding.

### 9.5 Disconnect and resume -- the in-flight operations panel

When the launching window disappears (operator closes the window, the workstation sleeps, etc.) while a session is suspended in `INPUT_REQUIRED`, the controller stays suspended indefinitely subject only to the plugin's worker timeout (§9.1). Reopening the window or any new client surfaces the suspended session through two paths.

**Fast path: OS notification.** Per §3.4.5, the tray fires a notification *"ExLab-Wizard: 1 plugin needs input"* (or *"N plugins need input"* if multiple are suspended). Click-action focuses or spawns the window and opens the resume dialog directly -- the same dialog from §9.1, populated with whatever fields the plugin previously declared.

**Discoverable path: Operations panel.** A new modal reachable from:

1. The bottom **status bar's Sync segment** (§3.5.5) gains a fourth state when any operation is suspended: *"⚠ N operations need input"*. Click opens the Operations panel.
2. A new toolbar action **[Operations…]** in the main window (visible only when at least one operation is in flight or suspended).

The Operations panel is backed by `GET /api/v1/operations` (Backend §4.6.1, new endpoint). Layout:

| Column | Content |
|---|---|
| State icon | `▶` running, `⏸` suspended (waiting for input), `✓` completed-pending-cleanup |
| Started at | Local time, sortable |
| Equipment | Equipment ID |
| Project | Project human name + short_id (per §3.5.2) |
| Run | Run label or `Run_<DATE>` |
| Plugin | The currently-running or currently-suspended plugin's name |
| Actions | **[Resume]** (only for suspended); **[Cancel]** (only for suspended; opens the §9.4 confirmation dialog); **[View log]** (always; opens the run's log in the same viewer as the Problems tab) |

Suspended-row default-sort is by Started-at (oldest first) so the operator clears the longest-pending input first.

The panel auto-refreshes on the same WebSocket events that drive per-session progress; closing the panel does not affect any operation.

### 9.6 Concurrent suspended sessions

Whether the operator can open a new wizard while another session is suspended is **mode-dependent**:

- **Single-equipment mode** (`orchestrator.enabled: false`): NO. The toolbar's wizard buttons (New Project / New Run / New Test Run) are disabled while any session is in `RUNNING` or `INPUT_REQUIRED`. Tooltip: *"An operation is in progress. Resume or cancel it from the Operations panel."* with a deep link to §9.5.
- **Orchestrator mode** (`orchestrator.enabled: true`): YES. Multiple equipment may need attention concurrently; locking out new wizards because one equipment is suspended would block the orchestrator-mode workflow. Wizard buttons remain enabled; the operator may start a new creation (against any equipment) while previous sessions remain suspended in the Operations panel. The single-window invariant (§3.4.1) still applies -- new wizards open in the same window, with the previously-suspended escalation dialog stashed (the operator returns to it from the Operations panel or via the next-pending notification).

This resolves Frontend Open Question #5 (concurrent wizard limit) for v1: single-equipment locks down to one creation at a time; orchestrator mode allows concurrent creations across equipment.

---

## 10. Error, Progress, and Summary Presentation

### 10.1 Progress

Step 6 (Confirm & Create) shows a progress bar with phase labels: "Validating inputs", "Rendering template", "Running plugins", "Writing cache", "Registering with LIMS", "Queueing NAS sync". The UI advances through each phase as the backend emits progress events.

### 10.2 Errors

A failure during any phase is surfaced as:

- A persistent error card on the final step (Confirm & Create) with the phase name, error message, and a "Copy details" affordance.
- A link to the relevant `wizard.<hostname>.log` path for deeper inspection.
- Retry is allowed only for transient failures flagged by the backend (e.g. NAS sync failure, DB unreachable). Validation or plugin failures require closing and restarting the wizard.

### 10.3 Success Summary

On completion, the final step displays:

- The created directory's absolute path (selectable for copy).
- A shortcut to open the directory in the OS file manager.
- Sync status: "Pending", "Synced", "Failed", "Sync blocked" (Pre-Sync Gate; Section 10.4), or "Override active" (sync allowed under operator override). Updates live for a short window before the operator closes the wizard.
- README path if generated.
- Any non-fatal warnings (e.g. a plugin skipped a file).

### 10.4 Sync-Blocked Banner

If the validator engine (Design Spec §8.1, §11.8) reported a hard-tier finding on the just-created run -- typically because a `.jinja` file produced a name still containing `<...>` or `{{ ... }}`, or because a post-copy plugin emitted an illegally-named file -- the success card carries a persistent **"Sync blocked"** banner above the path. The banner contents:

- Plain-language summary: *"This run was created locally but is blocked from NAS sync because <N> validation problems were detected."*
- The first finding's `rule` name and matched token (e.g. *"unresolved placeholder token `<run_date>` in directory name"*).
- Two affordances: **"View in Problems tab"** (deep link that switches the right panel to the Problems tab and selects this run's row) and **"Override and allow sync"** (opens the override dialog described in Section 11.5).

The banner uses `--color-warning` (§2.1.4). The banner is dismissible only by closing the wizard; the underlying gate state is unchanged by dismissal.

### 10.5 Recovery flows

This subsection covers the operator-facing UX for four classes of failure that have predictable recovery paths. Backend rules live elsewhere (§7.1.5 NAS retry policy, §4.8 crash recovery, §7.4.4 keyring fallback, §7.2.9 offline catalogue); this section closes the loop on what the operator sees and does.

#### 10.5.1 NAS sync failure recovery

NAS sync uses the retry policy in Backend §7.1.5 (exponential backoff with a configured retry budget). The operator sees the retry sequence in the run's sync-status icon (§3.2):

| Status | Visual | Tooltip / detail |
|---|---|---|
| `pending` | queued icon, `var(--color-muted)` | *"Queued for sync"* |
| `retrying (N/M)` | clock-with-arrow icon, `var(--color-info)` | *"Retry N of M, next attempt at HH:MM:SS"* |
| `failed` | error icon, `var(--color-danger)` | *"Sync failed: `<reason>`. Retry budget exhausted."* |

The retry counter appears as a small `(N/M)` adjacent to the icon in the left tree and the run's title bar in the detail pane (§3.6.1).

**Operator action affordances** in the run's Sync section (§3.6.3):

- During `pending` or `retrying`: `[Cancel pending sync]` — removes the run from the queue and sets status to `failed` with reason `cancelled by operator`.
- During `failed`: `[Retry now]` — resets the backoff timer, restarts the sequence from attempt 1. Multiple `[Retry now]` clicks are idempotent (the second is a no-op while a retry is in flight).
- During `failed` with `blocked_by_validation`: `[Override and allow sync]` — opens the §11.5 override dialog rather than retry.

**Aggregate sync state** is reflected in the status bar's Sync segment (§3.5.5) and the tray icon's status submenu (§3.4.2). When at least one job is `failed` with no retries left, the status bar's Sync segment renders in `var(--color-warning)` until the operator clears the failed state (via Retry now, Cancel, or successful sync).

**OS notification** fires once when a run reaches terminal `failed` status while the window is closed or backgrounded (§15.7.3).

#### 10.5.2 LIMS unreachability mid-wizard

When the LIMS becomes unreachable after the wizard opens, behavior depends on whether the workstation has an offline catalogue configured (Backend §7.2.9):

**Catalogue configured:** the wizard transparently falls back to the catalogue. The LIMS picker rows in §4.1 already carry the *"(via offline catalogue)"* badge for this case; no additional banner. The wizard proceeds to creation; the resulting `creation.json` records `lims_project.source: "offline_catalogue"` so the audit trail reflects what data was used.

**No catalogue configured:** a top-of-wizard banner appears with these properties:

- Tier: `var(--color-danger)` per §2.2.3 trigger #3.
- Headline: *"LIMS is unreachable."*
- Body: *"This wizard is using cached project data. Changes made in LIMS since you opened this wizard won't be reflected."*
- CTA: `[Test reconnect]` — runs `POST /api/v1/setup/test-lims`; on success the banner clears.
- Dismiss: not allowed; the banner clears only on reconnect or wizard dismissal.

The wizard does NOT abort — the operator can complete creation with the cached data they already saw on Step 1. The created run records `lims_project.source: "cache"` and `lims_project.cache_freshness_at_use: <timestamp>` for audit.

**Mid-wizard reconnect:** if the LIMS reconnects while the operator is in a later step, the banner clears and a one-line toast confirms: *"LIMS reconnected — current cached data is still being used for this wizard."* (Cached data continues — the wizard does not re-fetch mid-flight to avoid changing what the operator already saw.)

#### 10.5.3 Crash and orphan recovery

Backend §4.8 specifies that a crash mid-creation leaves a partially-rendered directory without `creation.json`. The validator's orphan rule (Backend §8.1.4) catches it on next audit; the Problems tab is the canonical surface for resolving orphans.

**Crash detection at launch.** On tray launch, if `<state_dir>/server.json` (per §15.3.1) exists with a `pid` that is not currently running, the previous launch ended without a clean shutdown. The tray:

1. Cleans up the stale state file.
2. Starts a fresh server normally.
3. Triggers an immediate validator audit (`POST /api/v1/problems/refresh`) on first window open.
4. If the audit surfaces any orphan findings, emits a one-time toast on the next window open: *"Recovered from previous session: N orphan finding(s) found. Open Problems tab to review."* with a `[Open Problems tab]` action.

The toast fires only once per crash — re-launching after a clean shutdown does not re-trigger it. If the operator dismisses the toast without opening the Problems tab, the orphans remain visible there as standard findings; no escalation, no second toast.

**No special bulk-action UI.** Each orphan is resolved through the standard Problems-tab actions (Reveal in tree, Open in file manager, View log, Mark as known, Override-and-allow-sync). The "discard the partial directory" path is reachable through the Reveal-in-tree → right-panel detail → file-manager affordance; v1 does not provide a one-click "discard orphan" because it would be too easy to delete real acquisition data.

**Crash during plugin escalation.** If the controller was suspended in `INPUT_REQUIRED` and the server crashed, the suspended session is lost (Backend §4.8 — session store is in-memory only). On next launch the orphan path applies as above. The plugin worker's stdin/stdout state is discarded; resume is not possible. The operator restarts the wizard from scratch.

#### 10.5.4 Pre-flight checks before creation

The wizard's Confirm & Create button runs two critical pre-flight checks on Preview-step entry. These run automatically — operators don't trigger them.

**Disk space.** Query `local_root`'s filesystem free space. If less than **100 MB** free, disable Confirm & Create with the tooltip *"Insufficient disk space at `<local_root>`"*. The operator dismisses the wizard, frees space, and reopens. There is no `[Try anyway]` override — disk-full mid-creation produces partial directories that are worse than aborting up front.

**Plugin host health.** Call `GET /api/v1/health` and check `components.plugin_host.status` (Backend §4.6.3). If `error`, render a form-level inline error at the top of the Preview step: *"Plugin host is unavailable. Try restarting the app from the tray menu."* Confirm & Create is disabled while this state holds.

Both checks are visible the moment the operator sees the Preview step — not after they click Confirm & Create. The validator's content-scan checks (§4 step 6, §5 step 5) run alongside; all three failure modes share the same form-level error block, listed by severity.

**Non-critical pre-flight conditions** (NAS unreachability, LIMS unreachability, low-but-not-zero disk space) do NOT block creation — they appear as banners or status-bar indicators per §2.2.3 and §3.5.5. The principle is: block when failure during creation would leave the operator worse off than not starting at all (disk full, plugin host down); warn otherwise. Sync can be retried; LIMS can be reconnected; only fatal-mid-flight failures justify pre-flight aborts.

---

## 11. Problems Tab

The Problems tab is the always-on surface that displays the validator audit (User Interaction Spec §3.8) and the per-run gate status (User Interaction Spec §7). It lives as a tab in the main window's right panel, alongside the Details tab (Section 3.2).

### 11.1 Layout

The tab is a single scrollable table with a fixed header strip and a footer status bar.

**Header strip (filter chips, top of tab):**

- A **"Severity"** chip group with two toggleable chips: **Hard** (selected by default) and **Soft** (unselected by default). Multiple chips may be active simultaneously; at least one must be active or the table is empty by design.
- A **"Class"** chip group with one chip per problem class enumerated in Design Spec §8.1 (`Placeholder`, `Illegal char`, `Mode mismatch`, `Orphan`, `Missing field`). All active by default.
- A **"State"** chip group: **Active** (default), **Override active**, **Marked known**, **Synced under prior policy**.
- A **"Scope"** dropdown: **All managed equipment** (default), or any single equipment ID, or **Staging only** (only meaningful in orchestrator mode).
- A search box that filters by path substring (case-insensitive).

**Table columns** (left to right):

1. **Severity icon.** Hard tier uses a filled glyph in `--color-warning` (§2.1.4); soft tier uses an outlined glyph in `--color-info`.
2. **Class.** The problem class name from the rule set (Design Spec §8.1.1-§8.1.5), rendered as a colored pill.
3. **Path.** The offending segment or file, with the matched token segment **highlighted** inline (e.g. `Run_<run_date>` with `<run_date>` underlined in `--color-warning`). Truncated from the left when long; full path on hover/tooltip.
4. **Run.** The run-level ancestor's friendly label from `creation.json` (`label` core field), or `--` for orphans at the project/equipment level.
5. **Equipment.** The equipment ID.
6. **Detected at.** The most recent audit timestamp where this finding appeared.
7. **State badge.** One of `Active`, `Override active`, `Marked known`, `Synced under prior policy`.
8. **Actions.** Per-row action menu (Section 11.3).

**Footer status bar:** displays *"Showing N of M findings · Last audit: HH:MM:SS · Next refresh in 23s"* with a manual **"Refresh now"** action.

### 11.2 Severity Tier Visual Treatment

Hard-tier rows use a left-edge accent stripe in `--color-warning` (§2.1.4) so a hard-tier row is recognizable at a glance even with the table scrolled. Soft-tier rows use a thinner stripe in `--color-muted`. The severity icon (column 1) uses the same color cue as the row stripe.

When a hard-tier row's run has an active override, the row is rendered with a strikethrough on the severity stripe and the State badge reads `Override active`. The row remains visible by default (not hidden) so the operator can still see what was overridden.

### 11.3 Per-Row Actions

Available from each row's action menu (a `...` button in the Actions column):

- **Reveal in tree.** Switches the right panel back to the Details tab and selects the run's node in the left tree.
- **Open in file manager.** Opens the run's directory in the OS file browser at the offending segment when possible.
- **View log.** Opens the run's `.exlab-wizard/wizard.<hostname>.log` (and the equipment-level log if the run-level one is missing) in a scrollable read-only viewer.
- **Mark as known issue.** Suppresses this finding from the default view (it remains visible when the `Marked known` State chip is active). Suppression is local to the workstation, persisted in app preferences, and does **not** clear the gate. A hard-tier finding suppressed this way still blocks sync.
- **Override and allow sync.** Available only on hard-tier findings whose run is currently `blocked_by_validation`. Opens the override dialog (Section 11.5). Greyed out for soft-tier rows and for rows that already have an active override.
- **Revoke override.** Available only on rows with `Override active`. Opens a confirmation dialog and writes a tombstone entry to `validation_overrides` (Design Spec §7.3). The gate re-engages immediately.

### 11.4 Empty State

When the filter set returns no rows, the tab shows a centered illustration with one of three messages, picked by the underlying state:

- *"No active problems."* — the unfiltered scope (all chips active) returned zero hard- and soft-tier findings.
- *"No active problems. (N soft-tier findings hidden by filter.)"* with a `[Show soft-tier findings]` link that toggles the Severity chip on — when the unfiltered scope has zero hard-tier findings AND at least one soft-tier finding AND the soft-tier chip is currently off (default). The conditional hidden-count line appears only when N > 0; when N = 0 the simple message above is shown.
- *"No findings match the current filters."* with a `[Clear filters]` button that resets to default chips — when filter chips are excluding all findings.

### 11.4.1 New-finding surfacing during the session

When the 30-second background audit (§3.3) detects a new hard-tier finding while the operator has the right panel on Details, the surfacing rule is **toast-first-then-badge**:

- **First hard-tier finding per session:** a toast (`var(--color-warning)`) appears with the rule name, the matched-token snippet, and a `[Open Problems tab]` action. Toast duration follows the warning-tier 8-second timer (§2.2.2). Operator's first-occurrence flag for the session is set after dismissal.
- **Subsequent hard-tier findings in the same session:** silent badge update only (the Problems-tab count badge in the right-panel tab strip increments). No toast.
- **Across sessions:** the first-occurrence flag resets when the window closes (it's `app.storage.tab` -- Backend §4.4.7), so reopening the window can fire one more first-occurrence toast.

Soft-tier findings never auto-surface; operators see them by enabling the Severity = Soft chip.

### 11.5 Override-and-Allow-Sync Dialog

A modal dialog opened from the `Override and allow sync` action or from the wizard's success-card banner (Section 10.4).

**Layout:**

- **Header:** *"Override sync gate -- this finding will be ignored for this run only"*.
- **Finding summary card** (read-only): rule name, severity, run path, matched token, full offending path. Mirrors the row's data so the operator sees exactly what they are overriding.
- **Operator confirmation:** read-only field showing the `operator` value that will be attributed to the override (pre-filled from the OS username, matching the README pre-fill rule in Section 6.2). Cannot be edited from this dialog; if the wrong operator is shown, the user-facing message instructs the operator to cancel and update their session identity in Settings.
- **Reason text area:** required, multi-line, **minimum 10 characters and maximum 500 characters** after trimming leading/trailing whitespace. A character counter (`123 / 500`) is shown beneath the text area; turning red when within 10 characters of the limit. Short reasons (e.g. *"Approved by PI"*) are accepted — the audit value comes from having any attributed reason; boilerplate detection is not enforced. The placeholder text reads *"Why is this override appropriate? (Required. Visible in the audit log and in `creation.json`.)"*.
- **Optional expiry:** a date picker with the label *"Expires (optional)"* and a small "Clear" button to reset to no-expiry. Default: empty (no expiry). Quick-pick chips beside the picker offer **+30 days**, **+90 days**, **+1 year** for common cases. The picker is bounded to dates strictly in the future (today + 1 day onward). When set, the value is stored as a UTC ISO 8601 timestamp at the end of the chosen day; the operator's local time zone is used for the picker's calendar but never persisted. Helper text below the picker reads *"Once expired, this override is automatically deactivated and the sync gate re-engages. The audit entry remains visible."*
- **Acknowledgement checkbox:** *"I understand this override will be appended to `creation.json` `validation_overrides` and to the equipment-level audit log."* Must be checked.
- **Footer buttons:** **Cancel** (closes without writing) and **Confirm override** (greyed out until the reason field has >= 10 characters and the checkbox is checked).

**Submit behavior.** On confirm, the dialog closes, the override entry is appended to the run's `creation.json` and audit log (Design Spec §7.3, §11.3), and NASSync is notified that the run is newly eligible. The Problems-tab row updates to `Override active` on the next refresh (or immediately if the dialog handler optimistically applies the change).

### 11.6 Cross-Surface Links

The Problems tab is reachable from:

- The right-panel tab strip (always visible).
- The wizard success card's "Sync blocked" banner (Section 10.4).
- A keyboard shortcut (Cmd/Ctrl+Shift+P; subject to Open Question 5 on keyboard-first navigation).
- A small "View N problems" link on the run's Details panel when that run has any active findings.

Conversely, the **Reveal in tree** action and the row's `Path` column hover (which becomes a clickable link) navigate from the Problems tab back to the Details tab on the relevant run.

---

## 12. Widget Mappings

Maps backend-declared types to concrete widgets.

### 12.1 Template Variable Form (from `copier.yml` questions)

| Question type | Widget | Notes |
|---|---|---|
| `str` | Single-line Entry | Default |
| `str` with `choices` | Combobox | Choices from `copier.yml` |
| `int`, `float` | Numeric spinner or Entry with validation | Framework-dependent |
| `bool` | Checkbox | |
| `str` with date hint | Date picker | Detected via `type: str` + `help` mentioning date, or explicit custom hint |

### 12.2 README Form Fields

| Field type (backend) | Widget |
|---|---|
| `string` | Single-line Entry |
| `text` | Multi-line text area, ~5 rows default, scrollable |
| `choice` | Combobox (requires `options: [...]` in declaration) |
| `date` | Date picker; defaults to today if not set |
| `boolean` | Checkbox, labeled "Yes" when checked |

### 12.3 Required-Field Indication

Required fields are marked with a leading asterisk on the label. Empty required fields block the "Next" button and surface an inline error message underneath on blur.

The mandatory core set (`label`, `operator`, `objective`; User Interaction Spec Section 2) is always required and additionally receives a "Required by lab policy" subtitle beneath the section header to distinguish backend-enforced fields from template- or config-declared requirements. Template- and config-declared required fields use the same asterisk indicator without the subtitle.

---

## 13. Open Questions

UI-only questions (migrated from backend spec v0.3 Section 10).

OQ #1 (GUI framework) was resolved in v0.5; see §2. Subsequent items renumbered.

1. ~~**`.exlab-wizard` tree visibility:**~~ **Resolved.** Hidden by default; not configurable. Operators access cache files via the file manager (`[Open in Finder]` in the detail-pane action toolbar, §3.6.5) when debugging is needed. Dotfile convention; cache contents are implementation detail.
2. ~~**Staging panel placement:**~~ **Resolved (in §8.2).** Bottom dock, always visible when orchestrator mode is enabled. Not collapsible; orchestrator mode exists specifically to monitor staging across equipment.
3. ~~**Test-mode color:**~~ **Resolved (in §2.1.4).** The warning-tier hue is `--color-warning` = `--oi-orange` = `#E69F00`, per DESIGN.md §01. Single token referenced from all surfaces that previously restated this color (test-mode badge, `TestRuns/` and `TestRun_` path highlight, `blocked_by_validation` sync icon, Sync-blocked banner, Problems-tab hard-tier stripe and severity icon, override-reason near-limit indicator, setup-incomplete banner).
4. ~~**Keyboard-first creation flow:**~~ **Resolved (in §3.7).** A small set of ~10 app-level shortcuts (New Project, New Run, New Test Run, Settings, Refresh, Problems, focus search, Cmd+Enter to advance, Esc to cancel-with-confirmation, etc.) bound centrally in `ui/keyboard.py`. Cheatsheet dialog reachable from the Help menu.
5. ~~**Concurrent wizard limit (orchestrator UI):**~~ **Resolved (in §9.6).** Mode-dependent: single-equipment workstations forbid concurrent suspended sessions (wizard buttons disable while any session is in `RUNNING` or `INPUT_REQUIRED`); orchestrator-mode workstations allow concurrent sessions across equipment, with the Operations panel (§9.5) as the surface for managing them.
6. ~~**Override-reason length policy:**~~ **Resolved (v0.7):** Min 10 chars, max 500 chars after whitespace trim. Short reasons accepted; no boilerplate detection. See Section 11.5.
7. ~~**Problems-tab default-open behavior:**~~ **Resolved (in §11.4.1).** Toast-first-then-badge: a single warning-tier toast on the first hard-tier finding per session (with `[Open Problems tab]` action); subsequent findings in the same session update the count badge silently. First-occurrence flag persists in `app.storage.tab` and resets when the window closes.
8. ~~**Hard-tier-finding scope at empty state:**~~ **Resolved (in §11.4).** Conditional empty-state copy: *"No active problems."* when no soft-tier findings exist; *"No active problems. (N soft-tier findings hidden by filter.)"* with a `[Show soft-tier findings]` link otherwise.
9. ~~**Equipment ID renaming:**~~ **Resolved (in §7.7.2).** v1 documents the manual workaround (delete + re-add with new ID, manually move data, sync re-register) via a help-link in the Equipment sub-dialog. An in-app guided migration that handles `paths.py` / NASSync / validator state / audit log atomically is a planned v2 feature.

