# Opt-in `staging_root` — Design Spec

**Date:** 2026-05-28
**Status:** Approved for planning
**Scope:** Make `orchestrator.staging_root` an optional, opt-in setting. A
device with a blank staging root simply cannot act as a staging PC. No
`/staging` directory is ever created unless the operator explicitly specifies a
path.

---

## 1. Summary

Today `orchestrator.staging_root` is a **required** setup field, tied together
with `orchestrator.label` in the `INCOMPLETE_NO_ORCHESTRATOR` setup gate
(GUI/Orchestrator Redesign §3.1, which made the staging pipeline "always
active"). In practice the pipeline already no-ops cleanly when the field is
blank — the quiescence poller, the staging query, and the validator all guard
on an empty `staging_root`. The only place a staging directory is ever created
is `--test` mode, under the sandbox.

This change makes `staging_root` genuinely optional:

- **Blank** → this device is **not** a staging PC. Nothing is created, nothing
  blocks setup.
- **Set** → the device opts in. The directory is created when Settings is
  saved, and the existing pipeline picks it up unchanged.

`orchestrator.label` **stays required** — it is stamped into every run's
`creation.json` as the workstation identity (`controller/creation.py:985`),
independent of whether the device stages anything. The two fields are
**decoupled**.

The app has **not been deployed**, so there is no backward-compatibility or
migration burden.

---

## 2. Goals & non-goals

### Goals
- `staging_root` is optional; blank is a valid, complete configuration.
- No `/staging` directory (or any staging directory) is ever auto-created
  without the operator specifying a path.
- Operators who want to stage can set the path in Settings at any time, guided
  by a sensible OS-appropriate suggestion.
- When a path **is** specified and saved, the directory is created so remote
  PCs can push into it.

### Non-goals
- A separate `staging_enabled` boolean (YAGNI — blank already means "off").
- A UI indicator for "this device is acting as a staging PC" (deferred; the
  filled/blank field already conveys it).
- Any change to the relay/sync data flow, transports, or NAS sync.
- Changing `--test` mode behavior.

---

## 3. Design

### 3.1 Setup-state gate — `paths.py`

`staging_root` leaves the required gate; `label` remains.

- `_orchestrator_identity_complete(config)` — return `bool(config.orchestrator.label)`
  only (drop the `and config.orchestrator.staging_root`). Update docstring.
- `_missing_orchestrator_fields(config)` — remove the `staging_root` rows in
  **both** the `config is None` branch and the live branch. Only the `label`
  row remains.

**Effect:** `SetupState.INCOMPLETE_NO_ORCHESTRATOR` now trips only on a missing
`label`. A device with a label and a blank staging root can reach
`SetupState.READY`. The `setup_state_next_action` mapping is unchanged
(`INCOMPLETE_NO_ORCHESTRATOR` still routes to `SET_PATHS`).

### 3.2 Suggestion helper — `paths.py`

Rename `default_orchestrator_staging_root()` → `suggested_staging_root()` and
update `__all__`. It becomes a **pure, side-effect-free** function used **only**
to populate the Settings placeholder — never written, never `ensure_dir`'d.

The bare `/staging` default is removed. The suggestion is OS-appropriate and
nested under the app name on every platform, mirroring config/state/cache:

| OS | Suggested path |
|----|----------------|
| macOS | `~/Library/Application Support/exlab-wizard/staging` |
| Linux | `$XDG_DATA_HOME/exlab-wizard/staging` → `~/.local/share/exlab-wizard/staging` |
| Windows | `%LOCALAPPDATA%\exlab-wizard\staging` (unchanged) |

Linux uses `XDG_DATA_HOME` (bulk user data, not transient cache) so a partially
relayed, un-synced run is never treated as discardable. The helper honors the
test-mode app-name suffix via `_app_name()`.

### 3.3 Settings UI — `ui/pages/settings.py`

The "Staging root" input (currently `settings.py:503`):

- Relabel to **"Staging root (optional)"**.
- Add `placeholder=str(suggested_staging_root())` — a greyed hint, **never** a
  prefilled value. The bound value stays `""` until the operator types.
- Binding to `draft.orchestrator.staging_root` is unchanged.

### 3.4 Create-on-save — `ui/mount.py` `_persist_config`

The save chain is `settings._do_save` → `finalize_settings_draft` → `on_save`
→ `mount._on_save` → `_persist_config`. After `_persist_config` successfully
writes the config:

- Read the validated, stripped `orchestrator.staging_root`.
- If **non-empty** → `ensure_dir(Path(staging_root))`.
- If **blank** → do nothing.
- Wrap the `ensure_dir` in `try/except OSError`. On failure, surface a
  notification ("Couldn't create staging directory: …") but **keep the saved
  config** — the path is recorded and the validator already flags inaccessible
  roots on the next audit.

This is the single place creation happens. "Specified by the operator and
saved" is the only trigger; blank never creates anything.

### 3.5 Verified, not modified

These already behave correctly for a blank `staging_root` and are confirmed,
not changed:

- **Quiescence poller** (`orchestrator/quiescence_poller.py:264`) — discovery
  is guarded by `if staging_root:`.
- **Staging query** (`orchestrator/staging_query.py:98-101`) — returns `[]`
  when `staging_root` is unset/missing.
- **Validator** (`validator/engine.py:479-480` + `tray/dependencies.py:194`) —
  `staging_root` is passed as `None` when the config field is empty, so the
  accessibility check is skipped.

Documentation-only touch-ups:

- `config/models.py` — no functional change (there is no pydantic enforcement
  of non-empty `staging_root`). Update the `OrchestratorConfig` docstring and
  the `_cross_field_invariants` comment to state that `staging_root` is optional
  and only `label` is required.
- `constants/enums.py:144` — update the comment that pairs `label` +
  `staging_root` as jointly required.

### 3.6 Test mode — unchanged

`--test` mode (`tray/main.py:289,329-332`) continues to set and `ensure_dir`
its sandboxed `…/exlab-wizard-test/staging`. This is an explicit developer
configuration under the sandbox; it is never `/staging` and is consistent with
"the operator specified it."

---

## 4. Data flow

Runtime behavior is unchanged; only the blank case is now first-class.

```
staging_root BLANK
  device is not a staging target → poller/query/validator skip it →
  nothing created, setup still READY (label present)

staging_root SET (saved in Settings)
  dir created on save → remote stage-mode PCs push runs in →
  quiescence poller discovers + syncs them onward to the NAS
```

Local `nas`-mode equipment continues to sync directly from
`local_root/<id>/…` to the NAS, bypassing staging entirely (unchanged).

---

## 5. Error handling & edge cases

- **Whitespace-only input** — `OrchestratorConfig` has
  `str_strip_whitespace=True`, so `"  "` validates to `""` and is treated as
  blank (no directory created).
- **`ensure_dir` failure on save** — non-fatal: notify the operator, keep the
  saved config; the validator surfaces the inaccessible root on the next audit.
- **Path set but folder later deleted/unmounted** — existing validator
  accessibility check applies (unchanged).

---

## 6. Testing

### `tests/unit/test_paths.py`
- Rename the `default_orchestrator_staging_root` tests → `suggested_staging_root`:
  - macOS → `~/Library/Application Support/exlab-wizard/staging`
  - Linux → `~/.local/share/exlab-wizard/staging` (fallback) and an
    `XDG_DATA_HOME` override case
  - Windows → `%LOCALAPPDATA%\exlab-wizard\staging` (unchanged)
  - test-mode suffix now applies on **all** platforms (not just Windows)
- Add `XDG_DATA_HOME` to the `fake_home` fixture's `delenv` list.
- Setup-state: a config with `label` set and `staging_root` blank is **not**
  `INCOMPLETE_NO_ORCHESTRATOR` and can reach `READY`; `setup_state_missing`
  for `INCOMPLETE_NO_ORCHESTRATOR` no longer lists `staging_root`.

### `tests/unit/ui/test_settings_page.py`
- The staging-root field is optional and exposes the suggested placeholder.

### `tests/unit/ui/test_mount.py` (new cases)
- Persisting a config with a non-empty `staging_root` creates the directory.
- Persisting with a blank `staging_root` creates nothing.
- `ensure_dir` raising `OSError` surfaces a notification and still persists the
  config.

### Regression sweep
- Tests that pass `staging_root="/staging"` purely as a config **value** are
  unaffected. Only tests asserting the setup **gate** (staging-root-empty →
  incomplete) are updated.

---

## 7. Affected files

| File | Change |
|------|--------|
| `src/exlab_wizard/paths.py` | Gate (`_orchestrator_identity_complete`, `_missing_orchestrator_fields`); rename + rewrite helper → `suggested_staging_root`; `__all__`. |
| `src/exlab_wizard/ui/pages/settings.py` | Optional label + placeholder on the staging-root input. |
| `src/exlab_wizard/ui/mount.py` | Create-on-save hook in `_persist_config`. |
| `src/exlab_wizard/config/models.py` | Docstring/comment: staging optional. |
| `src/exlab_wizard/constants/enums.py` | Comment: staging optional. |
| `tests/unit/test_paths.py` | Helper + gate tests; `XDG_DATA_HOME` delenv. |
| `tests/unit/ui/test_settings_page.py` | Optional-field + placeholder test. |
| `tests/unit/ui/test_mount.py` | Create-on-save tests. |
