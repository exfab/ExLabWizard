# Hide orchestrator / staging from the UI — Design Spec

**Date:** 2026-05-29
**Status:** Draft for review
**Scope:** Make the orchestrator / staging-relay feature invisible to the
operator by gating its four GUI surfaces. The device model the operator sees
becomes: *one wizard instance hosts multiple equipment folders, each syncing
directly to the NAS.* No backend code is deleted — the staging pipeline stays
in the tree, dormant and still tested, so the feature is one small change away
from being re-enabled (or cleanly removed later under a separate spec).

---

## 1. Summary

Today every device is "always multi-equipment" and each equipment carries a
per-equipment `sync_mode` (GUI/Orchestrator Redesign §3.2):

- **`nas`** — acquire into `local_root`, sync **directly to the NAS** (the
  single `nas:` rclone remote). This is the default (`config/models.py:287`).
- **`stage`** — acquire into `local_root`, **push to a connected PC's staging
  area**; that PC relays onward to the NAS.

The `stage` path — plus the staging-PC side that receives, relays, monitors,
and cleans up staged runs — is the "orchestrator / staging feature." The
direct-to-NAS model the product now wants is **already the primary path**; this
spec retires the *secondary* path from view.

This is a **hide, not a remove**:

- The `nas` direct-sync path is untouched.
- The staging backend (`orchestrator/` package, `api/routers/staging.py`,
  the `nas_client` stage branch, the `staging_*` config fields, `SyncMode.STAGE`)
  stays in the tree, **dormant**. The 2026-05-28 `staging-root-opt-in` work
  already proved this code no-ops cleanly when no staging is configured: *"the
  quiescence poller, the staging query, and the validator all guard on an empty
  `staging_root`."* With every equipment in `nas` mode and `staging_root` blank,
  the device already behaves exactly like the target model.
- Only the **four operator-facing GUI surfaces** that expose `stage` /
  staging are changed.

The app has **not been deployed**, so there is no backward-compatibility or
migration burden.

### Why hide rather than a feature flag

The `staging-root-opt-in` spec already rejected a `staging_enabled` boolean as
YAGNI ("blank already means off"). Adding a flag now would repeat that mistake.
Hiding is a UI-only gate; re-enabling is restoring four small surfaces.

### Critical implementation note — do not delete the `orchestrator/` package

Despite its name, `orchestrator/quiescence_poller.py` is the **single auto-sync
engine for *both* modes** (its docstring: *"the single auto-sync trigger for
every run pending NAS sync — orchestrator-staged runs *and* runs acquired
directly on nas-mode equipment"*). The direct-NAS path **depends on it.** This
spec touches none of it.

---

## 2. Goals & non-goals

### Goals
- The operator never sees the words *stage*, *staging*, or *orchestrator* in
  the GUI, and never chooses a sync mode.
- The Add-Equipment wizard produces only `nas`-mode equipment.
- Settings no longer exposes a staging root; the workstation **label** (still
  required) is collected under a neutrally named section.
- No `/staging` route; no footer "Staging" segment.
- All staging backend code remains present, importable, and covered by its
  existing tests (reversibility + a clean base for a future removal spec).
- `CLAUDE.md` documents that the feature is intentionally hidden-but-present so
  future work does not "fix" the missing UI or assume the feature was deleted.

### Non-goals
- **No deletion** of the `orchestrator/` package, `api/routers/staging.py`,
  `ui/pages/staging.py`, the `nas_client` stage branch, or any staging-specific
  helper. (That is a separate "remove orchestrator/staging" spec.)
- **No config schema change.** `EquipmentConfig.sync_mode`, `SyncMode.STAGE`,
  and the `orchestrator.staging_*` fields all remain. No schema-version bump.
- **No feature flag** (see §1).
- **No change** to NAS sync data flow, transports, the quiescence poller, or
  the `nas:` remote.
- **No change** to the `--test` sandbox wiring (`tray/main.py`), which is
  dev-only; the e2e tests that drive `/staging` are simply skipped (§5).
- **No removal of `orchestrator.label`.** It is the workstation identity
  stamped into every run's `creation.json` (`controller/creation.py:1040`) and
  is independent of staging. It stays required.

---

## 3. Design

Four GUI surfaces change. Each subsection names exact files / lines.

### 3.1 Add-Equipment wizard — `ui/pages/wizard_equipment.py`

Drop the "Sync mode" step so the wizard is **identity → paths → review**, and
emit `nas` unconditionally.

- `EQUIPMENT_WIZARD_STEPS` (`:35`) → `("identity", "paths", "review")`.
- `EQUIPMENT_STEP_TITLES` (`:42`) → remove the `"sync_mode"` row.
- `_STEP_RENDERERS` (`:343`) → remove the `"sync_mode"` entry.
- `EquipmentWizardState.sync_mode` (`:66`) → **keep**, default stays `"nas"`.
  `assemble_equipment_config` (`:111`) continues to pass `sync_mode="nas"`, so
  `build_equipment_config` and the persisted `EquipmentConfig` are unchanged.
- `_render_sync_mode_step` (`:270`) → **keep the function defined but
  unreferenced** (dormant). Re-listing it in the two maps above restores the
  step verbatim — that is the reversibility hook.
- `can_advance` `"sync_mode"` case (`:87`) → keep (harmless; never reached).
- `_render_review_step` (`:310`) → drop the `Sync mode: …` summary line
  (`:326`); keep the nas hint (`:327`). The operator no longer sees a mode.

### 3.2 Settings section — `ui/pages/settings.py`

Keep the required **label** field; remove the staging-root input; rename the
section so "Orchestrator" disappears from the operator's view.

- `SECTION_TITLES["orchestrator"]` (`:57`) → `"Workstation"` (or
  `"Workstation Identity"`). **Keep the section *id* `"orchestrator"`** so the
  setup-state gate, `settings_sections_for`, and `first_incomplete_section`
  (which key on the id) are untouched.
- Render branch `elif section == "orchestrator":` (`:611`):
  - **Keep** the "Workstation label" input (`:618`) bound to
    `draft.orchestrator.label`.
  - **Remove** the "Staging root (optional)" input (`:621`–`:625`).
  - Remove the now-unused `from exlab_wizard.paths import suggested_staging_root`
    import (`:17`) to satisfy ruff.
- `staging_root` continues to default to `""` in the model and is simply never
  surfaced; nothing creates a staging directory.

### 3.3 Staging route — `ui/mount.py`

- Remove the `@ui.page("/staging")` block (`:447`–`:458`).
- Remove the bulk **clear-verified** wiring that fed the main page footer:
  the `on_clear_verified` handler and its `render_main(..., on_clear_verified=…)`
  argument, plus the `_build_staging_state` call that populated the footer.
- **Keep** `_run_staging_action` / `on_run_staging_action` (`:216`, `:1272`):
  these back the tree context-menu run actions (force-sync / clear), which
  apply to **nas-mode runs too** — they are *not* staging-dock-specific.
- `ui/pages/staging.py`, `_build_staging_state` (`:2085`), and the
  `staging_query` / `staging_clear` imports become unreferenced. Remove
  imports that ruff flags as unused; the standalone `staging.py` module stays
  in the tree (dormant).

### 3.4 Main-window footer — `ui/pages/main.py`

- Remove the footer **"Staging" segment** and the **"Clear verified runs"**
  button (`:474`–`:483`), including the `on_clear_verified` parameter on
  `render_main` / `render_main_page`.
- `MainPageState.staging_dock` (`:64`) and the legacy `orchestrator_enabled`
  (`:63`) are left inert (no operator-visible effect); they are removed in the
  future "remove" spec, not here. The `StagingDockState` import (`:27`) stays
  used by the inert field's annotation.
- The Sync / Validator / LIMS segments and the tree context-menu run actions
  are unchanged.

### 3.5 What stays dormant (explicitly untouched)

To keep the diff minimal and fully reversible, these are **not** changed:

| Surface | Why it can stay |
|---|---|
| `orchestrator/` package (poller, `_scan`, `staging_query`, `staging_clear`) | Poller serves nas-mode too; staging read-side simply isn't called. |
| `api/routers/staging.py` (still registered in `api/app.py`) | Internal REST plumbing, not operator-visible; no-ops with no staged runs; stays covered by `test_staging_router`. |
| `sync/nas_client.py` stage branch (`:655`–`:703`) | Never selected — no equipment is `stage` mode. |
| `config/models.py` `staging_*` fields, `SyncMode.STAGE` | No schema change; default `nas` means they're inert. |
| `validator/engine.py` staging-root branch (`:439`–`:506`) | Guards on empty `staging_root`; no-ops. |
| `tray/dependencies.py` poller boot (`:767`–`:784`) | Already boots on `has_staging_root OR has_nas_equipment`; with staging hidden it boots on nas equipment, which is correct. |
| `tray/main.py` `--test` sandbox `staging_root` (`:307`–`:342`) | Dev-only; out of scope. |
| `controller/creation.py` orchestrator block stamping (`:1040`) | Harmless metadata; unchanged. |

### 3.6 Reversibility contract

Re-enabling the feature is: (1) restore the `"sync_mode"` step in §3.1's two
maps, (2) restore the staging-root input in §3.2, (3) restore the `/staging`
route in §3.3, (4) restore the footer segment in §3.4. No backend work.

---

## 4. `CLAUDE.md` update

There is currently **no repo-root `CLAUDE.md`**. Create
`/Users/alex/Projects/ExLabWizard/CLAUDE.md` (or, if one exists by the time of
implementation, append a section) documenting the hidden-but-present state so a
future session does not misread the codebase. Required content:

```markdown
## Orchestrator / staging is intentionally hidden (not removed)

The per-equipment `stage` sync mode and the staging-PC relay
(orchestrator) are **hidden at the UI layer only**. The operator sees a
single model: one instance hosts multiple equipment folders, each syncing
**directly to the NAS** (`sync_mode = "nas"`).

- The staging **backend is still present and still tested** — `orchestrator/`,
  `api/routers/staging.py`, `ui/pages/staging.py`, the `nas_client` stage
  branch, `SyncMode.STAGE`, and the `orchestrator.staging_*` config fields all
  remain. They are dormant (no equipment is `stage` mode; `staging_root` is
  never surfaced).
- **Do not** "fix" the missing staging dock, the absent sync-mode wizard step,
  or the missing staging-root setting — their removal from the GUI is
  deliberate (see `docs/superpowers/specs/2026-05-29-hide-orchestrator-staging-design.md`).
- **Do not** assume the feature is gone: `orchestrator/quiescence_poller.py` is
  the auto-sync engine for **both** nas- and stage-mode runs — never delete it.
- `orchestrator.label` is still **required** (workstation identity in every
  `creation.json`); it is unrelated to staging.
- To re-enable staging, restore the four UI surfaces listed in the spec's §3.6.
  A full backend removal, if ever wanted, is a separate spec.
```

The implementer should also run a quick check for the `graphify claude` /
graphify integration footer convention already used in this repo's tooling and
keep this section above any auto-generated blocks.

---

## 5. Testing

### Tests that must change
- **Page objects:** `tests/e2e/page_objects/wizard_equipment_page.py` — drop
  the sync-mode interaction. `tests/e2e/page_objects/staging_page.py` — becomes
  unused (its driven tests are skipped below).
- **e2e — skip/remove** (drive hidden surfaces): `test_flow_09_orchestrator`,
  `test_flow_23_footer_staging`, `test_flow_18_relay_receive`,
  `test_flow_21_stage_ceiling`.
- **e2e — update:** `test_flow_16_add_equipment` (wizard now 3 steps, no
  sync-mode), `test_flow_08_settings` (section title `Workstation`, no
  staging-root field).
- **unit — update:** `tests/unit/ui/test_wizard_equipment.py` (steps tuple,
  renderer map, review summary), `tests/unit/ui/test_settings_page.py` (section
  title, staging-root input absent), `tests/unit/ui/test_staging_page.py`
  (skip — page unrouted; or keep, since `render_staging_dock` still exists).

### Tests that stay GREEN unchanged (by design)
`test_quiescence_poller`, `test_scan`, `test_staging_query`,
`test_orchestrator_lifecycle`, `test_nas_client`, `test_staging_router`,
`test_models`, `test_enums`. Keeping these green is a **feature**: it proves the
dormant backend still works and underpins the §3.6 reversibility contract.

### Tests to add
- Wizard has exactly the three steps `("identity", "paths", "review")` and
  `assemble_equipment_config` always yields `sync_mode == SyncMode.NAS`.
- `GET /staging` is not reachable from the GUI route table (the `@ui.page`
  is gone); the REST `api/routers/staging` endpoints are out of scope here.
- Settings render for a typical config exposes no input bound to
  `orchestrator.staging_root`.

### Verification
- `uv run ruff check` (catch the unused imports flagged in §3.1–§3.4).
- `uv run pytest` (unit + integration + e2e) green.

---

## 6. Risks / notes

- **Reversibility is the whole point.** Standalone dormant *modules* are fine to
  leave; within *edited* files, unused imports/params must be removed or ruff
  fails. Keep the cut at module boundaries where possible.
- **Don't conflate two action surfaces.** The tree context-menu run actions
  (force-sync / clear) serve nas-mode runs and **stay**; only the footer
  *Staging dock* segment and *bulk clear-verified* are removed.
- **`orchestrator.label` stays required.** Hiding staging does not relax the
  setup gate; the renamed "Workstation" section still collects it.
- **The staging REST router stays registered.** It is internal and not
  operator-visible, so it does not violate "hide." Flag it for the future
  removal spec.
- **Lint vs. dormancy tension.** If leaving `ui/pages/staging.py` in the tree
  triggers a coverage or dead-code gate, prefer a `# pragma: no cover` /
  per-file ignore over deleting it — deletion is the removal spec's job.
- **Future removal is clean.** Because nothing here changes the schema or data
  flow, the eventual "remove" spec starts from a known-dormant, fully tested
  baseline with **no migration burden** (app not deployed).
