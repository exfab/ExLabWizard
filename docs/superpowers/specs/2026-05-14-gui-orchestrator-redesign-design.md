# ExLab-Wizard GUI & System Redesign — Design Spec

**Date:** 2026-05-14
**Status:** Approved for planning
**Scope:** Collapse the single-equipment / orchestrator-mode split into a single
always-multi-equipment model, rebuild the main window as a file-explorer, and
make equipment addable at any time.

---

## 1. Summary

ExLab-Wizard today has two device modes — a single-equipment workstation and an
orchestrator — gated by `orchestrator.enabled` in `config.yaml`. This redesign
removes that toggle. Every device is always multi-equipment capable; the
"role" of a device is no longer a device-level mode but a per-equipment
property. The main window is rebuilt from a tree + Details/Problems tabs into a
three-region file explorer (tree | live file list | collapsible metadata pane)
that shows, for *this* device only, what files are landing where. Equipment can
be added at any time through a dedicated Add-Equipment wizard.

The app has **not been deployed**, so there is no backward-compatibility or
migration burden — the changes below are the v1 baseline.

---

## 2. Goals & non-goals

### Goals
- Remove the device-level mode toggle; one always-orchestrator model.
- Make equipment addable at any time via a guided wizard.
- Main window is a file explorer: the operator sees real files populating real
  folders, live.
- A device's view shows exactly the equipment it is involved with — equipment
  it acquires for (*owned*) and equipment relayed into its staging area
  (*received*) — and nothing else.
- Move experimental runs into a `Runs/` subdirectory, symmetric with
  `TestRuns/`.

### Non-goals (explicitly out of scope for this redesign)
- A NAS-wide browse view aggregating every lab machine's data.
- In-app file content preview.
- Drag-and-drop file operations.
- Changes to LIMS integration.
- Cross-device status queries (a `stage`-mode device asking an orchestrator for
  downstream NAS status).

---

## 3. System model changes

### 3.1 Always-orchestrator

`OrchestratorConfig.enabled` is **deleted**. The staging pipeline
(`StagingWatcher`, staging→NAS sync) is always active. Consequences:

- `orchestrator.label` and `orchestrator.staging_root` become **always
  required** config (today required only when `enabled` is true). They join the
  setup-incomplete gate.
- `creation.json` always carries the `orchestrator` block; DB records always
  carry `orchestrator_host`; logs always carry the `[equip:]` tag and
  per-`<equipment>/<project>` log files.
- The "single-equipment workstation" column of Design Spec §12.1 is deleted —
  there is only one mode.
- `MainPageState.orchestrator_enabled` is removed.

### 3.2 Per-equipment sync role

The device-level role is replaced by a per-equipment **sync mode**, stored on
each `EquipmentConfig` entry. New `SyncMode` `StrEnum` with **exactly one of**:

- **`nas`** — this device acquires the equipment's runs into `local_root` and
  syncs them **directly to the NAS** (`nas_root` + `transport`).
- **`stage`** — this device acquires the equipment's runs into `local_root` and
  **pushes them to a connected PC's staging area** (`orchestrator_staging_transport`
  — mount point + staging subpath). The connected PC owns the onward NAS sync.

An equipment is never both. `transport` is required when `sync_mode == nas`;
`orchestrator_staging_transport` is required when `sync_mode == stage`.

### 3.3 Owned vs. received equipment

A device's tree shows two kinds of equipment:

- **Owned equipment** — entries in this device's `config.yaml` `equipment:`
  registry. This device acquires runs for them. Sync mode is `nas` or `stage`.
- **Received equipment** — equipment that *another* machine relays into *this*
  device's `staging_root`. **Auto-discovered**: the always-on `StagingWatcher`
  reads the pushed `creation.json` (equipment id, label, run kind, and
  completeness-signal info all travel with the push) and surfaces the equipment
  as a tree node. It is **not** added to this device's config registry, and
  completeness-signal config is **not** duplicated onto the orchestrator. An
  operator who only ever touches the orchestrator can still watch relayed data
  sync `staging → NAS`.

The tree is still strictly **local** — it shows what this device acquires or
relays-onward, never a NAS-wide aggregate.

Each equipment node carries a **sync badge**:

| Equipment | Badge |
|---|---|
| Owned, `sync_mode == nas` | `→ NAS` |
| Owned, `sync_mode == stage` | `→ <target host>` (e.g. `→ labpc-04`) |
| Received (relayed into this PC) | `relay` (distinct badge styling) |

### 3.4 `Runs/` subdirectory

Experimental runs move from loose-at-project-level into a `Runs/` subdirectory,
symmetric with `TestRuns/`:

```
<equipment>/
  <project>/
    Runs/
      Run_<YYYY-MM-DDTHH-MM-SS>/
    TestRuns/
      TestRun_<YYYY-MM-DDTHH-MM-SS>/
```

Leaf-folder prefixes (`Run_` / `TestRun_`) are unchanged. Because the app is
undeployed there is no migration — this is simply the v1 convention. Affected
areas:

- Directory-convention spec (Design Spec §3).
- Path composition (`paths.py`, `controller/creation.py`).
- Validator mode/location checks (Design Spec §3.8 problem class 3) — a `Run_*`
  leaf **not** under `Runs/` is now a mismatch, alongside the existing
  `TestRun_*`-not-under-`TestRuns/` check.
- Orchestrator staging layout (Design Spec §13.2).
- Downstream "walk `Run_*`" contract → "walk `Runs/Run_*`".

---

## 4. Main window redesign

### 4.1 Layout

Three regions in a splitter, plus chrome:

```
┌─ Header toolbar: New Project · New Run · New Test Run · Add Equipment · Refresh · Settings ─┐
├─ Breadcrumb bar: CONFOCAL_01 / UCR-000-I-D_WHEELDON / Runs / Run_2026-05-14T09-22-00 ───────┤
│ ┌── Left ──┐ ┌──────── Centre ────────┐ ┌──── Right (collapsible) ────┐                    │
│ │ search   │ │ live file list of the  │ │ [Metadata] [Problems (n)]   │                    │
│ │ chips    │ │ selected folder        │ │ node-type-aware content     │                    │
│ │ tree     │ │                        │ │                             │                    │
│ └──────────┘ └────────────────────────┘ └─────────────────────────────┘                    │
├─ Footer status bar: Sync · Validator · LIMS · Staging ──────────────────────────────────────┤
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

The bottom staging dock (`pages/staging.py`) is **removed** — the unified
explorer absorbs it (see §4.6).

### 4.2 Left — tree

Reuses `components/filter_chips.py` and `components/tree.py`. The tree is
extended with:

- `Runs/` and `TestRuns/` grouping nodes under each project.
- Per-equipment sync badges (§3.3).
- Received-equipment styling (the `relay` badge + visually distinct node).
- The travelling problem badge (§4.5).

Hierarchy: `equipment → project → {Runs/, TestRuns/} → run → folders…`.

### 4.3 Centre — live file list

New `components/file_list.py`. Shows the immediate contents of the selected
folder: subfolders and files, with **name / size / modified / per-file sync
status** columns.

- Double-click a folder → navigate into it.
- Double-click a file → open in the OS default application.
- Single-click a file → selects it for the right-click context menu only; it
  does **not** change the right pane (decision 6A).
- No in-app content preview.
- Newly-appeared files briefly highlight (the "live" feel — see §5).
- Per-file sync status semantics depend on the owning equipment's sync mode
  (decision 5A, see §7).

### 4.4 Right — Metadata / Problems pane (collapsible)

Two tabs. Collapses via a chevron to give the file list more width.

- **Metadata tab** — new `components/metadata_pane.py`. A **node-type-aware**
  renderer keyed to the tree selection (`selected_node`):
  - *Equipment* node → id, label, sync mode, roots, transport, completeness
    signal; for received equipment, the relay source + lifecycle summary.
  - *Project* node → LIMS identity (name, `short_id`), objective, run counts.
  - *Run* node → run kind, label, operator, objective, template, created
    timestamp, LIMS project, sync status, validation summary; for received runs,
    the `ingest.json` lifecycle state and the relocated staging actions (§4.6).
- **Problems tab** — findings scoped to the selected node **+ its descendants**,
  via a path-prefix filter over the validator finding set. Reuses the
  `pages/problems.py` row rendering (jump-to, open-in-finder, override,
  mark-as-known). The tab label carries the hard+soft count badge. The footer
  Validator segment, when clicked, selects the device root so the Problems tab
  shows everything.

The right pane is **node-scoped only** — never driven by centre-pane file
selection (decision 6A).

### 4.5 Travelling problem badge

A pure function in `components/tree.py`: given the validator finding set and the
per-node expand state, the badge for each finding sits on the **shallowest
collapsed node on the path to that finding**, travelling inward as nodes expand.
If the path is fully expanded the badge sits on the finding's own node.

- Red = hard-tier, amber = soft-tier. If a node aggregates both, **red wins**
  and the count is the total.
- Clicking the badge selects that node and opens its Problems tab.

### 4.6 Footer status bar & relocated staging actions

Reuses `components/status_bar_segment.py`. Four segments: **Sync**,
**Validator**, **LIMS**, **Staging**.

The removed staging dock's functionality is redistributed:

- Received equipment → tree nodes (§3.3).
- Per-run lifecycle state → the run's Metadata pane.
- Per-run staging actions (force sync, clear verified, view log) → the run's
  Metadata pane + right-click context menu. These reuse the existing
  `pages/staging.py` handlers.
- Bulk "Clear verified runs" → an action in the footer **Staging** segment's
  popover.
- `pages/staging.py` is reduced to its reusable pure formatters
  (`format_bytes`, `format_elapsed`, state-pill props), which are kept.

### 4.7 Header toolbar & breadcrumb

- Toolbar: New Project / New Run / New Test Run / **Add Equipment** / Refresh /
  Settings.
- Breadcrumb bar shows the path of the selected node; **segments are clickable**
  to navigate to an ancestor (decision 7A).

---

## 5. Live file feed (Approach 1 — poll-only)

There is no per-folder filesystem watcher today; the tree relies on a quiet 30 s
background refresh. The redesign adds a scoped fast poll rather than a watcher
(an FS watcher is unreliable on the SMB/network mounts staging often uses, and
at acquisition timescales a 2–3 s poll is indistinguishable from instant).

- New endpoint **`GET /folder/{path}`** — immediate contents of one folder
  (files + subdirs) with name / size / modified / per-file sync status, walked
  via `os.scandir` (the iterator the validator and browse router already use).
- When a folder node is selected, the centre pane starts a **~2–3 s fast poll**
  of `GET /folder` for *just that folder*; it stops on selection change or pane
  close.
- The tree keeps its existing **30 s** `GET /tree` refresh. The fast poll and
  the tree refresh are coalesced so they don't both walk the FS at once.
- Both sit behind a small client-side **"folder feed" abstraction**
  (`start(path, onUpdate)` / `stop()`) so a WebSocket channel can replace the
  polling internals later without touching the UI.
- The centre pane **diffs successive responses** to drive the new-file
  highlight.
- Polling pauses when the window is backgrounded or closed (the server keeps
  working; the UI just stops asking).
- Per-file sync status: `nas` equipment → derived from the NASSync queue + run
  gate status; `stage` equipment → tops out at `relayed`; received equipment →
  derived from the run's `ingest.json` lifecycle state.

---

## 6. Add-Equipment wizard

New `pages/wizard_equipment.py`, a multi-step wizard launched from the toolbar.

1. **Identity** — equipment ID (validated against `^[A-Z][A-Z0-9_]*$`), label.
2. **Paths** — `local_root`.
3. **Sync mode** — pick `nas` or `stage`. The step then shows either the NAS
   transport sub-form (rclone / rsync_ssh) or the connected-PC staging transport
   sub-form (mount point + staging subpath). Transport sub-forms are reused from
   `pages/settings.py`.
4. **Completeness signal** — sentinel vs. manifest + filename.
5. **Review & confirm** — appends a validated `EquipmentConfig` to
   `config.yaml`; the tree picks it up on the next refresh.

Reuses `build_equipment_config()`, moved out of `pages/settings.py` into a
shared module so both the wizard and Settings use one assembler.

Edit and remove remain in **Settings → Equipment List** (decision 4A);
right-clicking an owned-equipment tree node offers "Edit equipment…" /
"Remove…" that deep-links into that Settings section.

---

## 7. Interaction decisions

| # | Decision | Choice |
|---|---|---|
| 1 | **New Run / New Project entry point** | The three creation buttons are always clickable. The wizard has an equipment+project picker step which is **pre-filled and skipped** when a valid *owned* node is selected in the tree, and shown otherwise. The buttons are **disabled when a received-equipment node is selected**. |
| 2 | **First launch / empty state** | Welcome card's "Get started" opens **Settings in setup-incomplete mode** (today's path). The Orchestrator section is dropped; `staging_root` + `label` fold into an early Settings section and join the setup-incomplete gate. The equipment section's "add" button launches the Add-Equipment wizard. Welcome bullets are reworded for the multi-equipment / file-explorer framing. |
| 3 | **Received-equipment boundary** | Hard UI boundary: received-equipment nodes disable the creation buttons; staging actions live in the Metadata pane + context menu; bulk "clear verified" in the footer Staging popover. |
| 4 | **Edit / remove equipment** | Add is the toolbar wizard; edit and remove stay in Settings → Equipment List; tree right-click deep-links there. |
| 5 | **Sync-status semantics by mode** | Mode-aware vocabulary. `nas`: `pending → synced → verified`. `stage`: `pending → relayed` (terminal on this device — it cannot see NAS verification). The Metadata pane explains the `stage` ceiling so a `stage` run never reaching `verified` locally is not confusing. |
| 6 | **File-list selection vs. right pane** | The right pane is node-scoped only. Clicking a file in the centre pane selects it for the context menu (open in OS, copy path) and nothing else. |
| 7 | **Breadcrumb + keyboard** | Breadcrumb segments are clickable. The three-pane layout extends Frontend Spec §3.7 keyboard nav (tab between panes, arrow nav in the tree). Full file-list keyboard navigation is deferred to v1.x. |

---

## 8. Configuration schema changes

- `OrchestratorConfig`: `enabled` field **removed**. `label` and `staging_root`
  become required (the top-level cross-field validator enforces them
  unconditionally instead of only when `enabled`).
- New `SyncMode` `StrEnum`: `nas` | `stage`.
- `EquipmentConfig`: gains `sync_mode: SyncMode`. `transport` becomes required
  iff `sync_mode == nas`; `orchestrator_staging_transport` becomes required iff
  `sync_mode == stage`. A `model_validator` enforces this.
- Existing dev/test `config.yaml` fixtures are updated to the new schema (no
  runtime migration — the app is undeployed).

---

## 9. Code impact map

| Area | Change |
|---|---|
| `config/models.py` | Remove `OrchestratorConfig.enabled`; `label`/`staging_root` always required; add `SyncMode` enum + `EquipmentConfig.sync_mode` + conditional-transport validator. |
| `constants/enums.py` | Add `SyncMode`. |
| `paths.py`, `controller/creation.py` | Add the `Runs/` segment to experimental-run path composition. |
| `validator/engine.py` | Update mode/location checks for `Runs/`; `creation.json` always carries the orchestrator block. |
| `api/routers/browse.py` | New `GET /folder/{path}`; extend `GET /tree` with sync-mode, received-equipment, and badge-finding data. |
| `orchestrator/staging_watcher.py` | Auto-discover received equipment from pushed `creation.json` and surface it into the tree feed. |
| `ui/pages/main.py` | Rebuilt to the three-region layout; `MainPageState` loses `orchestrator_enabled` / `staging_dock`, gains right-pane + folder-feed state. |
| `ui/components/` | New `file_list.py`, `breadcrumb.py`, `metadata_pane.py`; `tree.py` extended (travelling badge, `Runs/` grouping, sync badges, `relay` styling). |
| `ui/pages/staging.py` | Reduced to reusable pure formatters; dock renderer removed. |
| `ui/pages/wizard_equipment.py` | New Add-Equipment wizard. |
| `ui/pages/wizard_run.py`, `wizard_project.py` | Add the pre-fillable equipment+project picker step (decision 1). |
| `ui/pages/settings.py` | Drop the Orchestrator section; fold `staging_root`/`label` into an early section; equipment section gains `sync_mode`; `build_equipment_config()` moved to a shared module. |
| `ui/pages/welcome.py` | Reword bullets; `on_get_started` unchanged (still opens Settings in setup-incomplete mode). |
| Specs/docs | Update Design Spec §3, §12, §13 and Frontend Spec §3, §7, §8. |

---

## 10. Error handling

- **`GET /folder/{path}` on a vanished path** (folder deleted mid-poll) → 404;
  the centre pane shows "folder no longer exists"; the next tree refresh
  re-syncs the structure.
- **Transient folder-feed poll failure** → keep the last good state, retry on
  the next tick, no error spam.
- **Malformed / missing pushed `creation.json`** during `StagingWatcher`
  auto-discovery → the staged directory surfaces as a soft-tier validator
  finding (orphan-like), never a crash; the watcher continues.
- **Add-Equipment wizard** → per-step validation; full `EquipmentConfig`
  Pydantic validation at confirm; duplicate equipment ID rejected with a
  structured error naming the conflict.
- **`stage`-mode connected-PC staging mount unreachable** → the run shows
  `relay` status as pending/failed via the existing sync error path; no
  cross-device query is attempted.
- **Config schema** — loading a pre-redesign `config.yaml` (with `enabled` or
  without `sync_mode`) fails validation with a clear `ConfigError`; since the
  app is undeployed this only affects local fixtures, which are updated as part
  of the work.

---

## 11. Testing strategy

- **Unit**
  - `SyncMode` conditional-transport validation (`nas` requires `transport`,
    `stage` requires `orchestrator_staging_transport`, never both).
  - `Runs/` path composition for experimental runs.
  - The travelling-badge pure function across fold-state × finding-set
    combinations.
  - Folder-feed diff logic (additions / removals / modifications).
  - Metadata-pane node-type rendering (equipment / project / run / received).
- **Integration**
  - `GET /folder` contents + per-file sync status.
  - `GET /tree` with a mixed owned/received equipment set.
  - `StagingWatcher` auto-discovery surfacing received equipment.
- **E2E** — Playwright flows against the mounted NiceGUI app. E2E coverage is a
  first-class requirement for this redesign, not an afterthought: every
  user-facing flow below ships with a passing E2E test before the corresponding
  work is considered done.
  - **Onboarding (flow-01, updated)** — Welcome card → Settings in
    setup-incomplete mode → `staging_root` + `label` + first equipment added via
    the Add-Equipment wizard → main window renders with always-on staging.
  - **Add equipment + acquire (`nas` mode)** — add a `nas`-mode equipment via
    the wizard → create a project and an experimental run under it → the run
    lands under `Runs/` → files appear in the centre pane via the live folder
    feed → per-file sync status advances `pending → synced`.
  - **Add equipment + relay (`stage` mode)** — add a `stage`-mode equipment →
    create a run → the centre pane shows files → sync status tops out at
    `relayed` and the Metadata pane shows the `stage` ceiling note.
  - **Relay receive flow** — a run relayed into this device's `staging_root`
    is auto-discovered, appears as a received-equipment node carrying the
    `relay` badge, and its `ingest.json` lifecycle state shows in the Metadata
    pane.
  - **File-explorer navigation** — tree selection drives the centre file list
    and the right Metadata pane; double-click navigates folders; breadcrumb
    segments navigate to ancestors; the right pane collapses and restores.
  - **Problems tab + travelling badge** — a seeded hard-tier finding surfaces
    the red badge on the shallowest collapsed ancestor, travels inward on
    expand, and clicking it selects the node and opens its scoped Problems tab;
    the footer Validator segment selects the root for the full list.
  - **New Run picker step** — with a valid owned node selected the picker is
    pre-filled and skipped; with no selection the picker step is shown; the
    creation buttons are disabled when a received-equipment node is selected.
  - **Edit / remove equipment** — right-clicking an owned-equipment tree node
    deep-links into Settings → Equipment List.

---

## 12. Future (v1.x)

- WebSocket-backed live file feed replacing the poll (the "folder feed"
  abstraction is shaped for this).
- Full file-list keyboard navigation (arrow nav, enter, context-menu key).
- A unified Add/Edit equipment wizard (consolidating decision 4A's split).
- NAS-wide browse view.
