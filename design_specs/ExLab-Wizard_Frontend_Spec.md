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

**Implications for testing.** Playwright drives a Chromium instance against `http://127.0.0.1:<port>` for e2e tests — same NiceGUI surface that pywebview renders in production. Backend integration tests hit `httpx.AsyncClient(app=app)` directly without spawning the server. The Problems-tab WebSocket and the per-session events WebSocket are exercised in both layers. Tray and window subprocess behavior are exercised by separate cross-platform smoke tests in CI (skipped on the Linux runner where headless tray testing is brittle).

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

- **Color:** warning-tier (the same color used for the test-mode badge and `blocked_by_validation` sync icon, so the visual vocabulary is consistent across surfaces).
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
- **Sync-status icon vocabulary (per-run, in the left tree and detail header):** Five states are rendered with distinct icons -- `pending` (queued), `synced` (verified at NAS), `failed` (NAS sync error), `blocked_by_validation` (hard-tier finding gates sync; new in v0.4), and `override_active` (sync allowed under operator override; new in v0.4). The `blocked_by_validation` state uses the same warning-tier color the test-mode badge uses, so problem cues are consistent across surfaces.
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

- The wizard title bar badge is color-coded: experimental uses the app's primary accent color; test uses a distinct warning-tier color (not red; red is reserved for errors).
- The Preview step's highlighted `TestRuns/` segment and `TestRun_` leaf prefix both use the same test-mode color as the title bar badge, so the operator sees a single consistent cue.
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

- **ID.** Single-line input, validated against `^[A-Z][A-Z0-9_]*$` (max 32 chars; Design Spec §3.1). On Edit, this field is read-only — changing IDs is not supported in v1 (Open Question §13.9).
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

Attached to the main window (typically as a bottom dock or a dedicated tab). Shows all runs currently in staging with:

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

When a plugin raises `PluginInputRequired` mid-creation (backend spec Section 6.4), the creation controller suspends and hands control back to the client. The frontend surfaces this as:

- A modal dialog titled "Additional input required: *<plugin name>*".
- A form generated from the plugin's field definitions (same widget mappings as Section 12).
- Footer buttons: "Submit" (resumes creation) and "Cancel" (aborts the whole creation flow, with a confirmation dialog because partial state exists).

The escalation dialog must not close on click-outside: accidental dismissal would abort the creation.

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

The banner uses the same warning-tier color as the test-mode badge and the `blocked_by_validation` sync-status icon, so the visual vocabulary is consistent. The banner is dismissible only by closing the wizard; the underlying gate state is unchanged by dismissal.

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

1. **Severity icon.** Hard tier uses a filled warning-tier glyph; soft tier uses an outlined info glyph.
2. **Class.** The problem class name from the rule set (Design Spec §8.1.1-§8.1.5), rendered as a colored pill.
3. **Path.** The offending segment or file, with the matched token segment **highlighted** inline (e.g. `Run_<run_date>` with `<run_date>` underlined in warning-tier color). Truncated from the left when long; full path on hover/tooltip.
4. **Run.** The run-level ancestor's friendly label from `creation.json` (`label` core field), or `--` for orphans at the project/equipment level.
5. **Equipment.** The equipment ID.
6. **Detected at.** The most recent audit timestamp where this finding appeared.
7. **State badge.** One of `Active`, `Override active`, `Marked known`, `Synced under prior policy`.
8. **Actions.** Per-row action menu (Section 11.3).

**Footer status bar:** displays *"Showing N of M findings · Last audit: HH:MM:SS · Next refresh in 23s"* with a manual **"Refresh now"** action.

### 11.2 Severity Tier Visual Treatment

Hard-tier rows use a left-edge warning-tier accent stripe (the same color as the test-mode badge and the `blocked_by_validation` sync-status icon) so a hard-tier row is recognizable at a glance even with the table scrolled. Soft-tier rows use a thinner muted-color stripe. The severity icon (column 1) uses the same color cue.

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

When the filter set returns no rows, the tab shows a centered illustration with one of two messages:

- *"No active problems."* (the unfiltered scope returned zero hard- and soft-tier findings)
- *"No findings match the current filters."* (filters are excluding all findings; a "Clear filters" button resets to default chips)

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

1. **`.exlab-wizard` tree visibility:** Should `.exlab-wizard` folders be hidden from the main window's directory tree, or shown with a distinct icon to indicate wizard-managed directories? Current draft hides them by default.
2. **Staging panel placement:** Should the staging panel be a bottom dock (always visible) or a dedicated tab (hidden unless selected)? Preference TBD based on typical monitor sizes in the lab.
3. **Test-mode color:** Which specific hue for the test-mode badge and the `TestRuns/` / `TestRun_` path highlight? Must be distinguishable from the primary accent color and the error color, and legible against both light and dark themes if themes are supported. The same hue is reused for the `blocked_by_validation` sync icon and the Problems-tab hard-tier accent stripe (Section 11.2), so the choice is now load-bearing across surfaces.
4. **Keyboard-first creation flow:** Should the wizards support a keyboard-only path (tab through fields, Enter to advance, Esc to cancel)? Likely yes for operator efficiency but not specified in detail here. NiceGUI's `ui.stepper` and `ui.dialog` both support keyboard navigation; the question is which keys we bind beyond the defaults.
5. **Concurrent wizard limit (orchestrator UI):** The backend can handle multiple concurrent creation sessions (backend Open Question 9). Should the frontend enforce a visible cap, or just surface a warning when a threshold is exceeded? With the single-window model (§3.4.1), concurrent wizards share the one native window — open question is whether to allow stacked dialogs, a wizard-panel multiplexer, or hard-cap at one wizard at a time.
6. ~~**Override-reason length policy:**~~ **Resolved (v0.7):** Min 10 chars, max 500 chars after whitespace trim. Short reasons accepted; no boilerplate detection. See Section 11.5.
7. **Problems-tab default-open behavior:** When the right panel last had Details selected and the validator finds a new hard-tier problem in the background refresh, should the Problems tab auto-switch into focus, just badge the count, or surface a non-blocking toast (`ui.notify`)? Auto-switching is intrusive; pure badging risks operators not noticing a sync gate. A toast on first-occurrence-per-session is a candidate compromise.
8. **Hard-tier-finding scope at empty state:** When the operator has zero hard-tier findings but the soft-tier chip is unselected (default), the empty state reads *"No active problems."* even though soft findings may exist. Open question: should the empty-state copy mention the hidden soft findings (e.g. *"No active problems. (12 soft-tier findings hidden by filter.)"*) to avoid the false impression of a fully clean tree?
9. **Equipment ID renaming:** The Settings dialog's Equipment Add/Edit sub-dialog (§7.7.2) renders the ID field as read-only on Edit because renaming an equipment ID would require migrating on-disk data, NAS targets, validation overrides keyed on equipment, and the central audit log. v1 does not attempt this. Open question: do we add an explicit "Rename equipment" workflow in v1.x with a guided migration, or document the workaround (delete + re-add with the new ID, manually move data) and leave it at that?

