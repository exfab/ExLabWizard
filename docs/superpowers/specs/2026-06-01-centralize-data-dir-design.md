# Centralize the data / template / plugin directory under one Documents-based app root

- **Date:** 2026-06-01
- **Status:** Approved (design); pending implementation plan
- **Branch:** `worktree-refactor+centralize-data-dir`

## 1. Problem

Today the operator must independently configure three filesystem roots in
Settings — `paths.templates_dir`, `paths.plugin_dir`, and `paths.local_root`
— each defaulting to the empty string and each gated by the
`INCOMPLETE_MISSING_PATHS` setup state. There is no sensible default, so every
fresh install forces the operator to invent and type three paths.

Two concrete problems motivate the change:

1. **No defaults.** `PathsConfig` fields default to `""` and
   `evaluate_setup_state` blocks setup until all three are non-empty
   (`paths._paths_complete`, `paths.py`). Other desktop apps simply default a
   working folder under the user's *Documents* directory; we do not.

2. **Two divergent local roots (latent bug).** There is a global
   `paths.local_root` *and* a per-equipment `EquipmentConfig.local_root`
   (`models.py`, required, `min_length=1`). Run **creation** composes paths
   from the *global* root (`controller/creation.py`), but the auto-sync
   **quiescence poller** walks the *per-equipment* root
   (`orchestrator/quiescence_poller.py`). If an operator types different values
   in the two places, runs are created in one tree while the sync engine
   watches another — runs silently never sync.

## 2. Goal

A single operator-configurable **app root**, defaulting to the OS *Documents*
folder, from which every working directory is derived:

```
~/Documents/ExLabWizard/            <- the single configured "app root"
├── templates/                      (derived)
├── plugins/                        (derived)
└── data/                           (derived; the experiment data root)
    └── <EQUIPMENT_ID>/
        └── <project>/
            └── Runs/Run_<DATE>/
```

Each equipment's data lives at `<app_root>/data/<EQUIPMENT_ID>/…`, derived from
the single root — eliminating the divergence bug at its source.

## 3. Scope decisions (locked during brainstorming)

| Decision | Choice |
|----------|--------|
| Config shape | **Single app root**, with `templates`/`plugins`/`data` *derived*, not separately stored. |
| Per-equipment `local_root` | **Removed entirely.** Both creation and the poller derive `<app_root>/data/<EQUIPMENT_ID>`. |
| Migration | **Clean break (pre-release).** No compat shims, no filesystem migration. Old configs carrying the retired keys (`paths.templates_dir` / `paths.plugin_dir` / `paths.local_root`, or `equipment.local_root`) fail validation and route to the setup wizard. |
| App-root UX | **One editable "Data folder" input**, pre-filled with the Documents default. Power users can relocate the whole app folder. |
| Setup gate | **Repurposed** from "is it blank" to "is the resolved root creatable/writable." |

## 4. Design

### 4.1 Config model — `config/models.py`

`PathsConfig` collapses to a single stored field plus read-only derived
properties:

```python
class PathsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    app_root: str = Field(default_factory=lambda: str(default_app_root()))

    @property
    def data_root(self) -> str:     return str(Path(self.app_root) / "data")
    @property
    def local_root(self) -> str:    return self.data_root          # alias (see note)
    @property
    def templates_dir(self) -> str: return str(Path(self.app_root) / "templates")
    @property
    def plugin_dir(self) -> str:    return str(Path(self.app_root) / "plugins")
```

- Plain `@property` (NOT `computed_field`): the derived dirs are never written
  to YAML — **only `app_root` is serialized**. `save_config`'s `model_dump`
  therefore persists `paths: {app_root: …}` alone.
- The property names `local_root` / `templates_dir` / `plugin_dir` are **kept**
  so the existing read-only consumers need no change — `config.paths.local_root`
  now simply resolves to `<app_root>/data`.
  - **Naming note / known tradeoff:** retaining the name `local_root` for what
    is now `<app_root>/data` is slightly less honest than `data_root`. The
    decision is to keep `local_root` to avoid touching ~15 call sites; a later
    rename is a separate, mechanical change. `data_root` is provided as the
    honest name for new code.

**`EquipmentConfig`:** the `local_root` field is removed. Remaining fields:
`id`, `label`, `nas_root`, `sync_mode`. `build_equipment_config()` drops its
`local_root` parameter.

### 4.2 Documents resolver — `paths.py`

Two new pure, side-effect-free helpers alongside the existing `os_*_path()`
family:

```python
def os_documents_path() -> Path:
    # macOS:   ~/Documents
    # Windows: SHGetKnownFolderPath(FOLDERID_Documents) via ctypes;
    #          fallback %USERPROFILE%\Documents; then ~/Documents
    # Linux:   $XDG_DOCUMENTS_DIR if set; else ~/Documents

def default_app_root() -> Path:
    return os_documents_path() / _display_name()
```

- New constant `DISPLAY_NAME = "ExLabWizard"` in `constants/app.py` — the
  user-facing Documents subfolder, deliberately distinct from
  `APP_NAME = "exlab-wizard"` (which names the hidden OS config/state/cache
  dirs).
- `_display_name()` appends `-test` when `EXLAB_WIZARD_TEST_MODE=1`, mirroring
  the existing `_app_name()`, so tests and `--test` never write into the real
  Documents folder.
- Resolution is best-effort and **never raises**: a Windows ctypes failure
  falls through `%USERPROFILE%\Documents` → `~/Documents`.

### 4.3 Directory creation

New helper `ensure_app_dirs(config) -> None` runs `ensure_dir` over `app_root`,
`data`, `templates`, and `plugins` (empty scaffolds — no seed files). It is
invoked at the same two points `orchestrator.staging_root` is created today:

- tray bring-up (`tray/dependencies.py`), and
- the Settings save path (`ui/mount._persist_config`).

### 4.4 Run layout and the divergence-bug fix

`compose_run_path` is **unchanged** — it still accepts `local_root` and appends
`<equipment_id>/<project>/Runs/…`. Because `local_root` now resolves to
`<app_root>/data`, runs land at
`<app_root>/data/<EQUIPMENT_ID>/<project>/Runs/Run_<DATE>/` with no edit to the
composer or to `controller/creation.py`.

The single behavioural edit is in `orchestrator/quiescence_poller.py`: it stops
reading the deleted `equipment.local_root` and instead derives
`Path(config.paths.local_root) / equipment.id`, identical to what
`creation.py` composes. An integration regression test asserts the two agree.

Read-only consumers verified to keep working unchanged via the derived
properties: `controller/creation.py`, `api/routers/browse.py` (including the
path-confinement allowlist that reads `local_root`/`templates_dir`/`plugin_dir`),
`sample_data/generator.py`, `template/resolution.py`, and `ui/mount.py`.

### 4.5 Setup gate — `paths.py` + `constants/enums.py`

- Rename `SetupState.INCOMPLETE_MISSING_PATHS` → `INCOMPLETE_PATHS_UNWRITABLE`.
- `evaluate_setup_state` gains an injected `paths_writable: bool = True` flag
  (same pattern as `lims_reachable`), keeping the evaluator pure and testable.
  The caller computes it by attempting `ensure_app_dirs` and an
  `os.access(W_OK)` probe of `app_root` and `data`.
- The gate position in the first-failing-wins chain is unchanged (it still
  fires immediately after `config is None`).
- `setup_state_missing` emits `{"field": "paths.app_root", "reason": "unwritable"}`.
- `setup_state_next_action` still maps the state to `SetupNextAction.SET_PATHS`.

### 4.6 Settings UX — `ui/pages/settings.py`

- Paths section: the three inputs become **one** "Data folder" input bound to
  `draft.paths.app_root`; `templates/`, `plugins/`, and `data/` are shown as
  read-only derived labels beneath it.
- Equipment section: the per-equipment "Local root" input (`eq_local`) and its
  wiring into `build_equipment_config` are removed.

## 5. Error handling

- **Documents resolution:** never raises; fallback chain as in §4.2.
- **Legacy config** carrying any retired key — `paths.templates_dir` /
  `paths.plugin_dir` / `paths.local_root` (no longer model fields) or
  `equipment.local_root`: fails Pydantic validation (`extra="forbid"`) →
  `load_config` raises `ConfigError` → `tray/dependencies._try` logs a WARN and
  leaves `deps.config = None` → the setup wizard runs (`INCOMPLETE_NO_CONFIG`).
  Verified against the current bring-up path; no crash, no special-casing
  required.
- **Unwritable app root** (bad drive, permissions): surfaces
  `INCOMPLETE_PATHS_UNWRITABLE` as a setup banner rather than failing at
  run-creation time.

## 6. Testing

**Unit**

- `os_documents_path()` per platform — monkeypatch `sys.platform`, `Path.home`,
  and the relevant env vars (`XDG_DOCUMENTS_DIR`, `USERPROFILE`).
- `default_app_root()` test-mode suffix (`ExLabWizard` vs `ExLabWizard-test`).
- `PathsConfig` derived properties resolve to `app_root/{templates,plugins,data}`;
  only `app_root` serializes via `model_dump`.
- Writability gate: writable `tmp_path` passes; a read-only directory yields
  `INCOMPLETE_PATHS_UNWRITABLE`.
- `EquipmentConfig` validates without `local_root`; a YAML still carrying it is
  rejected.
- `compose_run_path` output now contains the `data/` segment.

**Integration / regression**

- Run creation and the quiescence poller compute the *same* equipment
  directory for a given config (guards the divergence bug from recurring).

**Fixtures**

- `config/test_bootstrap.py`: construct `PathsConfig(app_root=str(sandbox))` and
  `ensure_dir` the derived subdirs; drop the removed `local_root` arguments.
- Update existing `test_paths.py`, config-model, and setup-status tests for the
  renamed state and the dropped field.

## 7. Out of scope

- Renaming the `local_root` property to `data_root` across all call sites
  (mechanical follow-up if desired).
- Any filesystem migration of data created under the old
  `<local_root>/<EQUIPMENT_ID>` layout (clean break; pre-release).
- Per-equipment data-root overrides (explicitly rejected: equipment data is
  always derived).
- Re-enabling or relocating the hidden orchestrator/staging surfaces
  (`staging_root` keeps its existing, separate handling).

## 8. Affected files (anticipated)

| File | Change |
|------|--------|
| `constants/app.py` | Add `DISPLAY_NAME`; `_display_name()` test-mode helper. |
| `constants/enums.py` | Rename `INCOMPLETE_MISSING_PATHS` → `INCOMPLETE_PATHS_UNWRITABLE`. |
| `paths.py` | Add `os_documents_path`, `default_app_root`, `ensure_app_dirs`; rework `_paths_complete`/missing-fields into the writability gate; update `evaluate_setup_state` signature. |
| `config/models.py` | `PathsConfig` → `app_root` + derived properties; drop `EquipmentConfig.local_root`. |
| `config/test_bootstrap.py` | Construct with `app_root`; ensure derived subdirs. |
| `orchestrator/quiescence_poller.py` | Derive equipment dir from `config.paths.local_root`. |
| `ui/pages/settings.py` | Single "Data folder" input; remove per-equipment local-root input. |
| `ui/mount.py` | Call `ensure_app_dirs` on save; `build_equipment_config` signature. |
| `tray/dependencies.py` | Call `ensure_app_dirs` at bring-up; compute `paths_writable`. |
| Tests | New unit + integration coverage per §6. |
