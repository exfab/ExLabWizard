# ExLab-Wizard Implementation Plan

## Context

ExLab-Wizard is a lightweight desktop application that creates standardized
directory structures on local disk, NAS, and a LIMS database from predefined
templates. It enforces the lab's
`<Equipment>/<Project>/Runs/Run_<ISO8601_DATE>` naming convention (and the parallel
`TestRuns/TestRun_<ISO8601_DATE>` for non-experimental runs), reduces human
error in directory creation, and provides an extensible plugin system for
transforming template file contents at creation time.

Distribution: a two-process desktop app (long-lived `exlab-wizard-tray`
hosting FastAPI + pystray; on-demand `exlab-wizard-window` running pywebview
that points at `http://127.0.0.1:<port>`). NiceGUI for UI, Copier for
templates, msgspec for cache I/O, Pydantic for HTTP body validation,
filelock for cross-platform file locks.

Authoritative spec sources:
- `DESIGN.md` -- frontend style guide (color palette, typography, components,
  absolute constraints).
- `design_specs/ExLab-Wizard_Design_Spec.md` -- backend index.
- `design_specs/design_spec_sections/02_User_Interaction.md` through
  `16_Logging.md` -- per-domain backend specs.
- `design_specs/ExLab-Wizard_Frontend_Spec.md` -- frontend spec (1441 lines).

The repository currently contains only specs, an empty `main.py`, an
`assets/` directory, and a `pyproject.toml`. We build from scratch on branch
`claude/implementation-plan-design-EWSF8`.

The implementation is partitioned into **16 sequential phases**, each with
its own definition-of-done QA loop (commit → simplify → coverage → design
adherence → run tests → fix → final commit + push). Each phase delegates
the bulk of independent work to subagents running in parallel; the main
agent coordinates, integrates, and gates phase exits.

---

## Goals

1. **Spec-faithful backend** -- every contract in §2-§13, §15, §16 implemented and
   covered by tests, with schema versions matching the §11 history tables
   verbatim (`creation.json` 1.8, `readme_fields.json` 1.1, `ingest.json`
   1.1, `equipment.json` 1.0).
2. **Spec-faithful frontend** -- NiceGUI + pywebview UI matching DESIGN.md
   tokens (color/typography/spacing/radius/shadows) and Frontend Spec page
   structures, with the IBM Plex Sans + system-monospace typography override
   from Frontend §2.1.
3. **Robust e2e testing** -- unit, integration, and Playwright e2e per
   Backend §4.10, with the e2e plan EXPANDED beyond §4.10.3 to cover all
   user-visible flows (15 flows enumerated below).
4. **Per-phase gating** -- no phase exits until simplify, coverage, design
   adherence, and tests all pass.

---

## First Action (before phase 1)

After plan approval and exit from plan mode, copy this plan to the repo as
`IMPLEMENTATION_PLAN.md` per the user's explicit request ("make a copy to the
repo for future reference"). The repo copy stays in sync with this planning
artifact for the duration of the work.

---

## Subagent Strategy (used at every phase)

| Stage | Subagent | Role |
|---|---|---|
| Implementation | `general-purpose` (×N parallel) | Each agent owns a leaf module or test target with NO shared mutable file. Main agent assigns disjoint trees so concurrent edits never collide. |
| Implementation | `Explore` (×1 lookup) | Targeted spec/code lookups when the main agent is blocked on a precise spec value. |
| Per-phase QA | `simplify` skill (×1) | Scans changed code for reuse, quality, efficiency; fixes findings. |
| Per-phase QA | `general-purpose` (named "test-coverage") (×1) | Runs `pytest --cov`, identifies uncovered code, writes targeted tests. |
| Per-phase QA | `general-purpose` (named "design-adherence") (×1) | Reads the phase's spec sections and the implemented files, lists per-section adherence (✅/⚠️/❌), proposes fixes for any gap. |
| Frontend e2e | `general-purpose` (named "playwright-e2e") (×1) | Drives Playwright against `tests/e2e/` -- separated because it requires a live server. |

**Concurrency rules:**
- Subagent prompts include the **exact module path(s) they own** and the
  explicit constraint that they may not edit anything outside that path.
- The main agent serializes any cross-cutting touchpoints (e.g.
  `constants/`, `pyproject.toml`, top-level test fixtures).
- After parallel implementation, the main agent runs the QA loop
  *sequentially* (simplify → coverage → adherence → tests → fix), because
  each step's input is the previous step's commit.

---

## Per-Phase Definition of Done

```
┌───────────────────────────────────────────────────────────────────────┐
│  Phase N implementation complete (N parallel agents merged)           │
│      │                                                                │
│      ▼                                                                │
│  COMMIT 1: "phase N: implement <subject>"                             │
│      │                                                                │
│      ▼                                                                │
│  Run /simplify on changed code -> apply fixes                         │
│      │                                                                │
│      ▼                                                                │
│  Spawn test-coverage subagent -> add tests until target met           │
│      │                                                                │
│      ▼                                                                │
│  Spawn design-adherence subagent -> spec gap report                   │
│      │  (any ❌ items must be addressed in main loop)                  │
│      ▼                                                                │
│  Run full test suite (pytest + Playwright if frontend touched)        │
│      │                                                                │
│      ▼                                                                │
│  Fix any failures, repeat tests until green                           │
│      │                                                                │
│      ▼                                                                │
│  COMMIT 2: "phase N: post-QA fixes"                                   │
│      │                                                                │
│      ▼                                                                │
│  git push -u origin claude/implementation-plan-design-EWSF8           │
│      │                                                                │
│      ▼                                                                │
│  Move to phase N+1                                                    │
└───────────────────────────────────────────────────────────────────────┘
```

**Coverage targets:**
- Public API methods on every component contract (§4.4): ≥1 happy-path +
  ≥1 failure-path test.
- Validator rules (§8.1): one fixture per finding shape, both creation-time
  and audit modes.
- Cache writers: concurrent-write fixture per §4.4.5.
- Branch coverage ≥85% per package after Phase 11.

---

## Test Plan (merged with spec §4.10 + §11.8 + Frontend §1)

The spec's §4.10 split (unit / integration / e2e) is preserved AND expanded
for robustness. The merged plan:

**Unit (`tests/unit/`)** -- mirrors package layout. Synchronous via pytest;
async via pytest-asyncio with function-scoped loops. No real FS where
possible (use `pathlib.PurePath`); `tmp_path` when FS behavior matters. No
external processes, no real network, no real keyring.

**Integration (`tests/integration/`)** -- FastAPI app started in-process via
`httpx.AsyncClient(app=app)`. Mocks:
- `tests/fixtures/mock_lims.py` -- a small FastAPI fixture app implementing
  `/api/v1/login`, `/me`, `/projects`, `/projects/{id}` with in-memory state.
- `tests/fixtures/stub_rclone.py`, `stub_rsync.py` -- Python stub binaries.
- `tests/fixtures/inmemory_keyring.py` -- registered via
  `keyring.set_keyring(...)`.
- `tmp_path` for `local_root`, templates dir, plugin dir.

Integration scenarios: full creation flow (project + run + test run),
`PluginInputRequired` suspend/resume, NAS sync queue lifecycle, validator
gate behavior, override + revoke, LIMS picker + cache, setup-incomplete
state transitions, schema-version migration, cache-file concurrent writes.

**End-to-end (`tests/e2e/`)** -- Playwright drives a real Chromium against
a real `uvicorn` process bound to a free port. The launcher's `--testing`
flag loads stub backends. Asserts both DOM state and side-effects on
filesystem / mock LIMS / mock NAS queue.

**E2E flows (the "robust and rigorous" expansion over spec §4.10.3):**
1. First-launch onboarding (`INCOMPLETE_NO_CONFIG` → `READY`) including the
   autostart prompt and welcome card dismissal flag in `app.storage.user`.
2. Project wizard end-to-end (7 steps: LIMS picker → Template → Equipment
   → Variables → README → Preview → Confirm & Create).
3. Experimental run wizard end-to-end (6 steps).
4. Test run wizard end-to-end (mode-invariant assertion: mode bound at
   construction, not changeable mid-session).
5. Browse view -- equipment/project/run hierarchy, mode badge, sync icon
   (six states: pending / retrying(N/M) / synced / failed /
   blocked_by_validation / override_active).
6. Problems tab -- snapshot + delta over WebSocket; override flow (10-500
   char reason, optional expiry quick-picks +30d/+90d/+1y); revoke flow.
7. `PluginInputRequired` suspend/resume mid-creation; cancel-during-escalation
   two-button choice (Keep partial / Discard everything).
8. Settings dialog -- paths, equipment add/remove (with equipment-id regex
   validation), LIMS test-connection, autostart toggle, all 9 sections.
9. Orchestrator staging panel -- five-state lifecycle (staging → complete
   → sync_queued → sync_verified → cleared), force-sync, clear-verified.
10. Schema-major-mismatch handling (write a future-version `creation.json`,
    confirm `code: schema_major_mismatch` and graceful degradation).
11. Crash-recovery scenarios from §4.8 (orphan surfacing in Problems tab).
12. Quit-coordinator graceful shutdown with in-flight sync (force-quit
    prompt; queue drains and resumes on next launch).
13. Keyboard navigation per Frontend §3.7 (every shortcut on macOS and
    Win/Linux variants, focus search via `/`, Cmd/Ctrl+Enter advance, Esc
    cancel with dirty-state confirmation).
14. Notification helpers per Frontend §2.2 (every variant: notify_success /
    notify_info / notify_warning / notify_error / notify_field_error /
    notify_form_error / show_banner with all 5 BannerId triggers).
15. WebSocket disconnect/reconnect banner during simulated server restart.

The e2e suite must run under 5 minutes total on a single GitHub Actions
runner.

---

## Phase Sequence

> **Implementation conventions enforced from Phase 1 onward:**
> - All schemas live in `constants/schema_versions.py`; versions match §11
>   history tables exactly: `creation.json` **1.8**, `readme_fields.json`
>   **1.1**, `ingest.json` **1.1**, `equipment.json` **1.0**.
> - All file names live in `constants/filenames.py`; no string literal of a
>   cache filename anywhere else.
> - All loggers come from `exlab_wizard.logging.get_logger()`; pre-commit
>   forbids direct `logging.getLogger`.
> - All `ui.notify` calls come through `ui/notifications.py`; pre-commit
>   forbids direct `ui.notify` in `exlab_wizard/ui/`.
> - Every public method on a §4.4 contract has a docstring linking to the
>   relevant spec subsection.
> - Em dashes are forbidden (DESIGN.md absolute constraint). Use `--`.

### Phase 1: Project scaffolding & constants foundations

**Spec sources:** §4.3, §4.3.1; §15.6; §16.10.

**Goal:** Stand up the Python package layout and the `constants/` single
source of truth so every later phase imports from a stable hard-constants
surface.

**Deliverables:**
- `exlab_wizard/__init__.py`, `__main__.py`.
- Full empty package skeleton matching §4.3 tree (every directory has
  `__init__.py`).
- `constants/{schema_versions,filenames,patterns,enums,keyring,limits}.py`
  with hard-coded values from §3.1, §11.3, §11.4, §11.5, §6.1.2, §7.4.
- `pyproject.toml` deps: `fastapi`, `uvicorn`, `nicegui`, `pywebview`,
  `pystray`, `plyer`, `keyring`, `httpx`, `aiosqlite`, `msgspec`,
  `pydantic`, `ruamel.yaml`, `pyyaml`, `copier`, `filelock`,
  `markdown-it-py`, `cryptography`, `argon2-cffi`. Dev: `pytest`,
  `pytest-asyncio`, `pytest-cov`, `playwright`, `pre-commit`, `ruff`,
  `mypy`.
- `.pre-commit-config.yaml` with ruff lints forbidding (a) direct
  `logging.getLogger` outside `exlab_wizard/logging/manager.py`, (b) direct
  `ui.notify` calls outside `exlab_wizard/ui/notifications.py`, (c) em
  dashes.
- `tests/{unit,integration,e2e,fixtures}/` skeleton.

**Subagent plan (3 parallel general-purpose agents):**
- Agent A: write `constants/` package + tests.
- Agent B: write `pyproject.toml`, `.pre-commit-config.yaml`, lint
  configs.
- Agent C: write empty package skeleton + `__init__.py` exports + the
  `exlab_wizard/logging/` package skeleton (since lint depends on its
  existence).

**Tests:** `tests/unit/constants/test_*.py` -- assert each constant matches
the spec verbatim (regex strings, max lengths, schema version strings).

---

### Phase 2: Configuration system & paths

**Spec sources:** `09_Configuration_File.md`; §4.9; §4.3 `paths.py`.

**Goal:** Load, validate, and round-trip `config.yaml` (preserving comments
and key order); compute setup state.

**Deliverables:**
- `exlab_wizard/config/models.py` -- Pydantic models matching §9 (top-level
  blocks: `paths`, `lims`, `readme`, `equipment`, `nas_cleanup`,
  `logging`, `operators`, `validator`, `plugins`, `sync`, `orchestrator`).
- `exlab_wizard/config/loader.py` -- `ruamel.yaml` round-trip loader.
  PyYAML reserved for read-only `copier.yml`, `manifest.yml`, README front
  matter (§4.3 docstring).
- `exlab_wizard/paths.py` -- OS-appropriate config/log/state directories,
  equipment-id canonicalization (regex `^[A-Z][A-Z0-9_]*$`, length 1-32),
  destination-path composition.
- Setup-state evaluator returning the §4.9.1 enum
  (`INCOMPLETE_NO_CONFIG`, `INCOMPLETE_MISSING_PATHS`,
  `INCOMPLETE_NO_EQUIPMENT`, `INCOMPLETE_NO_LIMS`,
  `INCOMPLETE_LIMS_UNREACHABLE`, `READY`).
- `tests/fixtures/configs/` -- `incomplete_no_paths.yaml`,
  `incomplete_no_equipment.yaml`, `incomplete_no_lims.yaml`,
  `complete.yaml`. (`INCOMPLETE_NO_CONFIG` is the absence-of-file state
  and is exercised by simply not loading a fixture; it has no
  corresponding YAML file. `tests/fixtures/configs/README.md` documents
  the rationale.)

**Subagent plan (3 parallel):**
- Agent A: models + Pydantic field validators.
- Agent B: loader + saver + comment-preservation tests.
- Agent C: paths + setup-state evaluator + tests.

**Tests:**
- Round-trip preservation of comments and key order on save.
- Every setup-state transition in §4.9.1.
- Equipment-id regex acceptance/rejection per §3.1.
- LIMS slot completeness via either keyring OR offline catalogue.

---

### Phase 3: Logging package + cache writers + schema models

**Spec sources:** `16_Logging.md` (full); `11_Cache_Folders.md` §11.3,
§11.4, §11.4.1, §11.4.2, §11.5, §11.9; §4.4.5.

**Goal:** Implement the canonical logger system (§16.2), then the
`CacheWriter` with atomic writes, per-file `filelock`, typed
`msgspec.Struct` schemas, and schema-version migration.

**Deliverables:**
- `exlab_wizard/logging/{__init__,manager,format,context,handlers}.py`
  -- full §16 package: `get_logger`, `configure_logging`,
  `set_run_context`, `EquipmentScopedFileHandler`, `RotatingFileHandler`
  for central app log, `QueueHandler`/`QueueListener` async pipeline.
  Format: `<UTC ISO 8601> [<LEVEL:5>] [host:][equip:][proj:][kind:][run:] <message>`.
- `cache/creation_writer.py` -- `creation.json` v1.8 schema; atomic
  read-mutate-write under `LOCK_EX`; tombstone-aware override matching;
  unknown-field preservation on mutation.
- `cache/log_writer.py` -- `wizard.<hostname>.log` append-only writer
  via `O_APPEND` / `FILE_APPEND_DATA | FILE_SHARE_WRITE` (§16.2.4).
- `cache/equipment.py` -- `equipment.json` v1.0.
- `cache/ingest_writer.py` -- `ingest.json` v1.1 (orchestrator-only).
- `api/schemas.py` (partial) -- the `msgspec.Struct` types for cache
  files: `CreationJson`, `ReadmeFieldsJson`, `EquipmentJson`,
  `IngestJson`, `OverrideEntry`, `Tombstone`, `LimsProjectBlock`.
- Schema-major-mismatch reader test (§11.9.2 → `code:
  schema_major_mismatch`).

**Subagent plan (4 parallel):**
- Agent A: full `exlab_wizard/logging/` package + tests.
- Agent B: `creation_writer.py` + schema + override matching + tests.
- Agent C: `log_writer.py` + `equipment.py` + their tests.
- Agent D: `ingest_writer.py` + schema-version migration + the
  `tests/integration/test_creation_json_concurrent_writes.py` fixture.

**Critical test:** concurrent-write test spawns N tasks mutating the same
`creation.json` simultaneously and asserts all N mutations are reflected.

---

### Phase 4: Validator engine & rules

**Spec sources:** `08_Error_Handling_Principles.md` §8.1 (all subrules);
§11.7, §11.8; UI-spec §3.8, §7.

**Goal:** Build the deterministic validator that powers both creation-time
and audit modes, plus the `query_problems(scope)` API.

**Deliverables:**
- `validator/rules.py` -- one function per §8.1 rule:
  - `unresolved_placeholder_token` (regex
    `<[A-Za-z_][A-Za-z0-9_]*>`) and `leftover_jinja_marker` (regexes
    `\{\{[^}]*\}\}` and `\{%[^%]*%\}`) (hard).
  - `illegal_filesystem_character` and `reserved_filesystem_name` (hard).
  - `mode_prefix_mismatch` (hard).
  - `orphan` (soft; project + run levels only, NOT equipment).
  - `missing_required_field` (soft).
  - `malformed_yaml_front_matter` (soft).
- `validator/findings.py` -- `Finding` dataclass; serialization per
  §11.8.
- `validator/engine.py` -- `Validator.validate_creation()`,
  `Validator.audit()`, `Validator.query_problems(scope)`. Uses
  `os.scandir` (§4.5). `msgspec.json.decode(..., type=CreationJson)` for
  cache reads. Stdlib `re` only for determinism.
- Content-scan bounds: `validator.content_scan_max_mib` (default 5),
  `validator.content_scan_extensions`. Markdown files: scan ONLY YAML
  front matter. Binary detection: read first 8 KiB; any `0x00` byte =
  binary.

**Subagent plan (3 parallel):**
- Agent A: `rules.py` -- one per rule, plus rule-level tests.
- Agent B: `findings.py` + `engine.py` (creation-time mode) + tests.
- Agent C: `engine.py` (audit mode) + `query_problems` + determinism test.

**Tests:** every finding shape from §8.1; determinism test (run audit
twice, assert byte equality); content-scan size cap behavior;
override-aware filtering using cache fixtures from Phase 3.

---

### Phase 5: Template engine (Copier)

**Spec sources:** `05_Template_Format.md` (all); §4.4.2.

**Goal:** Wrap `copier.run_copy()` with `unsafe=False`; resolve
`_exlab_*` metadata; integrate with the validator's pre-render placeholder
check.

**Deliverables:**
- `template/copier_driver.py` -- `TemplateEngine.resolve()`,
  `TemplateEngine.render()`. Calls `copier.run_copy(src_path=tpl.path,
  dst_path=dst, data=variables, overwrite=False, unsafe=False,
  quiet=True)`.
- Template-loading validation: `_exlab_version` required (`code:
  template_load_error`); `_exlab_type` ∈
  `{"project","equipment","run"}`; run templates require
  `_exlab_run_scope` ∈ `{"experimental","test","both"}`; question id
  warn-conform `^[a-z][a-z0-9_]*$`; core-field redeclaration in
  `_exlab_readme.fields` rejected with `code:
  template_core_field_redeclared`.
- Lint CLI exit codes 0/1/2/3.
- `tests/fixtures/templates/` -- minimal project, equipment, run
  templates; experimental + test scopes; one with `_tasks` (asserts
  silently ignored); one with intentional core-field redeclaration
  (asserts rejection).

**Tests:** render to tmpfs; assert `_tasks` ignored; `_exlab_run_scope`
honored; `_exlab_version` required; core-field redeclaration rejected.

---

### Phase 6: Plugin system (host + worker + isolation)

**Spec sources:** `06_Plugin_System.md` (all subsections); §4.4.3;
§16.8.

**Goal:** Implement the class-based `Plugin` contract, the host/worker
subprocess isolation model with JSON-over-stdio IPC, the
`manifest.yml`-driven registry, the `_exlab_plugins` ordering, and the
`PluginInputRequired` escape hatch.

**Deliverables:**
- `plugins/base.py` -- `Plugin` ABC, `PluginContext`, `FileChange`
  (frozen dataclass), `PluginError`, `PluginInputRequired(fields,
  reason)`.
- `plugins/registry.py` -- manifest scan (bundled `_internal/plugins/`
  + lab `paths.plugin_dir`, lab-wins on collision); `api_version` gating
  (`api_version: "1"` only); plugin name regex (letters / digits /
  underscore / hyphen).
- `plugins/host.py` -- `PluginHost` spawning workers via
  `asyncio.create_subprocess_exec` (NEVER `shell=True`) with
  `setrlimit`-driven isolation: `RLIMIT_AS` from `memory_mb` (default
  512, max 2048), `RLIMIT_CPU = timeout_seconds * 2` (default 30, max
  300), wall-clock = `timeout_seconds`, `RLIMIT_NOFILE=256`. Sanitized
  env: only `PATH`, `HOME`, `LANG`, `EXLAB_*` allowlist. Worker CWD =
  rendered destination directory. IPC frame cap 1 MiB. Validation
  worker has fixed lower limits (`RLIMIT_CPU=5s`, `RLIMIT_AS=256 MB`,
  wall-clock 10s).
- `plugins/_worker.py` -- the `python -m exlab_wizard.plugins._worker`
  entry. Worker exit codes: 0=success, 1=PluginError,
  2=PluginInputRequired, 3=uncaught, 124=timeout.
- `plugins/logger.py` -- `PluginLogger` shim. Worker stderr →
  `<central_log_dir>/plugins/<plugin>/<run_id>.stderr` (§16.8).
- Forbidden-write enforcement: `README.md*`, `.exlab-wizard/`,
  `.exlab-answers.yml` → `policy_violation` and snapshot revert.
- `tests/fixtures/plugins/hello_plugin/` -- canonical scaffold from §6.5.
- `tests/fixtures/plugins/xlsx_field_filler/` -- the worked §6.6 example.
- `tests/fixtures/plugins/_failures/` -- plugins exercising every
  failure surface.

**Subagent plan (4 parallel):**
- Agent A: `base.py` + `registry.py` + their tests.
- Agent B: `host.py` (subprocess management, IPC, isolation, timeouts).
- Agent C: `_worker.py` + `logger.py` + integration tests.
- Agent D: fixture plugins + per-failure tests.

**Critical tests:** subprocess crash containment (SIGSEGV in worker must
NOT abort creation unless `_exlab_plugins_fatal: true`); OOM containment;
timeout enforcement (SIGTERM then SIGKILL after 1s); `PluginInputRequired`
suspend/resume across the IPC boundary; forbidden-write policy violation
with snapshot revert.

---

### Phase 7: Creation controller & state machine

**Spec sources:** §4.4.1, §4.4.7, §4.7, §4.7.1, §4.8; UI-spec §2,
§3.1-§3.3, §4, §5.

**Goal:** Coordinate the §4.7 state machine end-to-end: validate →
render → plugin pass → cache write → post-validate → sync queue.

**Deliverables:**
- `controller/state_machine.py` -- `SessionState` enum (`PENDING`,
  `VALIDATING`, `RENDERING`, `PLUGIN_PASS`, `INPUT_REQUIRED`,
  `CACHE_WRITE`, `POST_VALIDATE`, `SYNC_QUEUED`, `DONE`, `FAILED`,
  `ABORTED`) + `Phase` enum + `state_to_phase` mapping per §4.7.1.
- `controller/session_store.py` -- in-memory `dict[str, Session]`; GC
  closes any `INPUT_REQUIRED` session with no client heartbeat for
  >1 hour (§4.4.7).
- `controller/creation.py` -- `CreationController` with
  `create_project`, `create_run`, `resume`, `cancel`, `status`,
  `subscribe`.
- Mandatory-core-field validation gate (UI §2): `label` ≤100 chars,
  `objective` ≤2000 chars after trim, `operator` non-empty (allowlist if
  `config.yaml operators.allowlist` non-empty).
- `POST_VALIDATE` second pass (catches plugin-introduced findings).
- Cancel cleanup hook (§4.7): `discard_files: bool` body field.

**Subagent plan (3 parallel):**
- Agent A: state machine + session store + GC + tests.
- Agent B: `create_project` + `create_run` + state-transition tests.
- Agent C: resume / cancel / status / subscribe + integration tests.

**Critical tests:** full happy-path creation; `PluginInputRequired`
suspend/resume; cancel-mid-creation cleanup with both `discard_files`
values; post-validate failure → `sync_status="blocked_by_validation"`;
session GC closes stale `INPUT_REQUIRED` sessions.

---

### Phase 8: README generator

**Spec sources:** `10_README_Generation.md`; UI-spec §3.5.

**Goal:** Merge field layers (core / template / config / system),
render YAML front matter + Markdown prose, write `README.md` and
`readme_fields.json` v1.1.

**Deliverables:**
- `readme/generator.py` -- merge logic, type validation (string / text
  / choice / date / boolean), pre-fill, system-field auto-population.
- `readme_fields.json` v1.1 schema (already partially in `api/schemas.py`
  from Phase 3).
- Core-field redeclaration check (`code:
  template_core_field_redeclared`).
- Custom-field order preservation; not persisted back to template.

**Tests:** every field type; layer-priority resolution (highest wins;
none can disable core); missing-required rejection; system-fields
auto-population at project-level vs run-level; YAML front matter
machine-queryability via `yaml.safe_load`.

---

### Phase 9: LIMS client & cache + keyring

**Spec sources:** `07_Sync_and_Database_Integration.md` §7.2, §7.2.4,
§7.2.9, §7.4.

**Goal:** Read-only LIMS client (Mapping B); SQLite-backed project-list
cache with TTL; offline-catalogue fallback; OS-keyring credential storage
with encrypted-at-rest fallback (Argon2id KDF, Fernet AEAD).

**Deliverables:**
- `lims/schemas.py` -- `LIMSProject` (`uid`, `short_id`, `name`,
  `description`, `status`, `contact_name`, `owner`, `metadata`,
  `fetched_at`), `LIMSUser` as `msgspec.Struct`.
- `lims/client.py` -- `LIMSClient` with `login`, `list_projects`,
  `get_project`, `get_me`, `health_check`. `httpx` cookie session.
- `lims/cache.py` -- `aiosqlite` cache at
  `{xdg_cache_home}/exlab-wizard/lims_cache.db`, TTL =
  `lims.cache_ttl_hours` (default 24), indexed on
  `(lims_endpoint, short_id, last_refreshed)`.
- Offline catalogue v1.0: `{schema_version, produced_by, produced_at,
  lims_endpoint, projects:[...]}`. Endpoint mismatch rejection.
- Keyring integration with service constant `"exlab-wizard"`;
  usernames `"lims"` and `"nas:<equipment_id>"`.
- Encrypted fallback at `{state_dir}/exlab-wizard/secrets.enc` using
  Fernet (AES-128-CBC + HMAC-SHA256), Argon2id (`time_cost=3`,
  `memory_cost=64 MiB`, `parallelism=4`), 32-byte key, 8 KiB salt.
- `tests/fixtures/mock_lims.py` -- fixture FastAPI app implementing the
  four endpoints with in-memory state.

**Subagent plan (3 parallel):**
- Agent A: schemas + `client.py` + login/list/get + tests against
  `mock_lims`.
- Agent B: `cache.py` + TTL + offline-catalogue fallback + tests.
- Agent C: `mock_lims.py` (fixture) + cache-coherency tests +
  keyring/encrypted-fallback round-trip tests.

---

### Phase 10: NAS sync client

**Spec sources:** §7.1 (all subsections), §7.3.

**Goal:** Durable NAS sync queue (per-equipment), Pre-Sync Gate
integration, rclone + rsync_ssh transports, hash verification, retry
backoff, cleanup interlocks.

**Deliverables:**
- `sync/nas_client.py` -- `NASSyncClient` with `enqueue`, `status`,
  `retry`, `force_verify`. Persistent SQLite queue at
  `{state_dir}/sync_queue.db`. State machine: `QUEUED` → `RUNNING` →
  `AWAITING_VERIFY` → `VERIFIED` → `CLEANUP_ELIGIBLE` → `CLEANED`,
  with `FAILED` branch.
- `sync/transports/rclone.py` -- thin wrapper for `rclone copy
  --checksum`, `--bwlimit` from `upload_mbps * 1024 / 8` KiB/s.
- `sync/transports/rsync_ssh.py` -- thin wrapper for `rsync -avz`,
  `--bwlimit=`. Key-based auth only (`password` rejected at config
  validation).
- Pre-Sync Gate: inline `validate_creation` before `enqueue`; if any
  hard-tier finding without active override, set
  `sync_status="blocked_by_validation"` and skip enqueue.
- Verifier: SHA-256, manifest at `.exlab-wizard/checksums.sha256`,
  fallback `verify.max_stream_bytes` 1 GiB.
- Retry backoff: `30s, 2m, 8m, 30m, 2h`. Auth fail = no retry. Hash
  mismatch = single retry. Local file vanished = `local_file_vanished`.
- Cleanup interlocks: `nas_cleanup.min_verify_passes` (default 2),
  `min_age_hours` (default 24), remote stat OK, no active overrides
  revocation, `retain_cache: true` default = metadata-only retention.
- Bandwidth schedule evaluator (workstation-local time).
- `tests/fixtures/stub_rclone.py`, `stub_rsync.py`.

**Subagent plan (3 parallel):**
- Agent A: queue persistence + state machine + tests.
- Agent B: transport drivers + verifier + tests.
- Agent C: Pre-Sync Gate + cleanup interlocks + bandwidth scheduler.

**Tests:** queue persistence across restarts; gate-blocked runs are not
enqueued; retry backoff sequence; per-equipment isolation; bandwidth
schedule; cleanup-interlock multi-condition gating.

---

### Phase 11: FastAPI app + routers + WebSocket + setup gate

**Spec sources:** §4.6 (all subsections), §4.9.

**Goal:** Wire all backend components into the HTTP+WebSocket surface;
gate creation/browse/problems endpoints behind setup state.

**Deliverables:**
- `api/app.py` -- FastAPI app + lifespan (load config, build plugin
  registry, refresh LIMS cache, start audit task).
- `api/routers/{sessions,problems,config,browse}.py` -- every endpoint
  from §4.6.1 (POST /sessions, GET /sessions/{id}, POST
  /sessions/{id}/resume, POST /sessions/{id}/cancel, GET /operations,
  GET /problems, POST /problems/{run_path}/override, POST
  /problems/{run_path}/override/revoke, POST /problems/refresh, GET
  /tree, GET /run/{path}, GET /config, PUT /config).
- `api/events.py` -- WebSocket envelope `msgspec.Struct` types
  (`PhaseEvent`, `ProgressEvent`, `InputRequiredEvent`, `WarningEvent`,
  `DoneEvent`, `FailedEvent`, `SnapshotEvent`, `DeltaEvent`).
- `api/health.py` -- `GET /api/v1/health` with §4.6.3 component rollup.
- `api/setup.py` -- `GET /setup/status`, `POST /setup/test-lims`,
  `POST /setup/test-equipment`, `POST /setup/autostart`.
- Setup-state gate middleware that returns 503 with `code:
  "setup_incomplete"` for creation/browse/problems endpoints in any
  `INCOMPLETE_*` state (except `INCOMPLETE_LIMS_UNREACHABLE`, which is
  a soft block per §4.9.4).
- Versioned route prefix `/api/v1/`.
- Error envelope per §4.6.3 with the full `code` enum.
- Background audit task running `Validator.audit("all")` every 30 s,
  publishes diff to a pub-sub channel.
- The `WS /api/v1/sessions/{id}/events` and `WS
  /api/v1/problems/events` channels.

**Subagent plan (4 parallel):**
- Agent A: lifespan + `sessions` router + WebSocket session events.
- Agent B: `problems` + `config` + `browse` routers.
- Agent C: setup gate + `/setup/*` endpoints.
- Agent D: `health` router + the §4.6.3 component rollup +
  `/operations` endpoint + audit pub-sub channel.

**Tests:** `httpx.AsyncClient(app=app)` integration tests for every
endpoint; WebSocket frame-by-frame tests for both channels; gate-503
behavior in every `INCOMPLETE_*` state; full happy-path
project-creation flow over HTTP.

---

### Phase 12: NiceGUI frontend (design tokens, pages, components)

**Spec sources:** `DESIGN.md` (all sections); `ExLab-Wizard_Frontend_Spec.md` (all sections); §4.3 `ui/`.

**Goal:** Frontend that mirrors DESIGN.md tokens and implements every
Frontend Spec page, with a Frontend §2.1 typography override.

**Deliverables:**
- `ui/design.py` -- design tokens mirroring DESIGN.md `:root` block.
  Constants: `COLOR_NAVY` (#003660), `COLOR_BLUE` (#1b75bc),
  `COLOR_GOLD` (#febc11), `COLOR_BG` (#f5f7fa), `COLOR_SURFACE`,
  `COLOR_BORDER`, `COLOR_RULE`, `COLOR_MUTED`, `COLOR_BODY`,
  `COLOR_HEADING` = `COLOR_NAVY`, all eight Okabe-Ito colors, semantic
  aliases. **Typography override:** `FONT_BODY = "'IBM Plex Sans',
  system-ui, ..., sans-serif"`; `FONT_MONO = "ui-monospace, 'SF Mono',
  ..., monospace"`. Type scale, spacing, radius, shadows verbatim from
  DESIGN.md.
- `ui/theme.py` -- NiceGUI/Quasar theme registration, writes the
  canonical `:root {…}` CSS block.
- `ui/notifications.py` -- the helper API: `notify_success`,
  `notify_info`, `notify_warning`, `notify_error`, `notify_field_error`,
  `notify_form_error`, `clear_field_error`, `clear_form_errors`,
  `show_banner`, `clear_banner`. `BannerId` enum with the 5 closed-set
  triggers; `Severity`, `ContainerId`, `ActionSpec` dataclass.
- `ui/keyboard.py` -- shortcut registry per Frontend §3.7 (Cmd/Ctrl+N,
  Cmd/Ctrl+Shift+N, Cmd/Ctrl+Shift+T, Cmd/Ctrl+`,`, Cmd/Ctrl+R,
  Cmd/Ctrl+Shift+P, `/` for tree search, Cmd/Ctrl+Enter advance, Esc
  cancel).
- `ui/components/{tree,mode_badge,session_progress,sync_status_icon,
  override_badge,test_run_badge,credential_field,test_connection_panel,
  filter_chips,status_bar_segment,banner_stack,operations_modal,
  bandwidth_schedule_editor,validation_summary}.py`.
- `ui/pages/main.py` -- main window: tree (left) + tabs (Details /
  Problems) (right) + toolbar + bottom status bar. Filter chips
  (Active default-on, Archived default-off, Test runs default-on).
  `.exlab-wizard/` hidden.
- `ui/pages/wizard_project.py` -- 7-step `ui.stepper`: LIMS Project
  → Template → Equipment → Variable Form → README Form → Preview →
  Confirm & Create. Validator gate on Preview; pre-flight (disk space
  ≥100 MB; `plugin_host.status != error`).
- `ui/pages/wizard_run.py` -- 6-step stepper. Mode bound at
  construction; immutable mid-flight. Title bar shows mode badge.
- `ui/pages/settings.py` -- 9 sections (Paths, LIMS, Equipment List,
  NAS Cleanup, Operators, Validator, Logging, Orchestrator Mode,
  Application). Setup-incomplete mode auto-selects first incomplete
  section.
- `ui/pages/problems.py` -- table with filter chips (Severity, Class,
  State, Scope), search, footer "Showing N of M findings · Last audit:
  HH:MM:SS · Next refresh in 23s"; override flow (10-500 char reason,
  optional expiry quick-picks); revoke flow.
- `ui/pages/welcome.py` -- first-launch welcome card with autostart
  toggle.

**Subagent plan (5 parallel):**
- Agent A: `design.py` + `theme.py` + `notifications.py` +
  `keyboard.py`.
- Agent B: `components/*` (all 14 components).
- Agent C: `pages/main.py` + `pages/welcome.py`.
- Agent D: `pages/wizard_project.py` + `pages/wizard_run.py`.
- Agent E: `pages/settings.py` + `pages/problems.py`.

**Critical DESIGN.md absolute constraints (enforced via lint + tests):**
- Okabe-Ito only for data viz, never buttons/nav/text.
- Fixed series order (navy, orange, sky, green, blue, purple,
  vermilion).
- DM Mono replaced by `FONT_MONO` per Frontend §2.1, but the rule that
  numeric data uses mono still holds.
- No `#F0E442` text on white.
- No em dashes anywhere.
- No `--shadow-lg` on inline cards.

**Tests:** Token-equality (every CSS custom prop in DESIGN.md is
reproduced in `design.py` with the documented Frontend §2.1 typography
override); component snapshot tests via Playwright; e2e flows from the
merged test plan.

---

### Phase 13: Tray + window processes

**Spec sources:** §4.1, §4.2, §4.3.2; §15.3, §15.7.

**Goal:** Two-process distribution model: long-lived `exlab-wizard-tray`
hosting FastAPI + pystray; on-demand `exlab-wizard-window` running
pywebview.

**Deliverables:**
- `tray/{main,icon,status,autostart,notifications,server_runner,
  window_launcher,quit_coordinator}.py`.
- `window/{main,pywebview_app}.py`.
- `<state_dir>/server.json` atomic write/read protocol (write `.tmp` →
  fsync → rename).
- Per-platform autostart: macOS LaunchAgent
  `~/Library/LaunchAgents/com.exlab-wizard.tray.plist`; Windows
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` value
  `ExLabWizard`; Linux systemd user unit
  `~/.config/systemd/user/exlab-wizard-tray.service` with
  `~/.config/autostart/exlab-wizard-tray.desktop` fallback.
- Graceful-shutdown protocol per §4.3.2: 30 s drain (5 s on `SIGTERM`),
  force-quit prompt with `[Force quit]` and `[Wait]`.
- OS notifications via `plyer` with macOS / Windows / Linux fallbacks.
  Coalesced (5 s window). Suppressed when window foregrounded.
- Linux no-tray fallback (§15.7.4).

**Subagent plan (4 parallel):**
- Agent A: `server_runner.py` + `window_launcher.py` +
  `quit_coordinator.py`.
- Agent B: `icon.py` + `status.py` + `notifications.py`.
- Agent C: `autostart.py` (one block per OS).
- Agent D: `window/` package.

**Tests:** integration test that spawns tray, opens/closes window N
times, asserts server survives; autostart register/unregister round-trip
on each OS via tempfile-based filesystem stubs.

---

### Phase 14: Orchestrator mode + ingest

**Spec sources:** `12_Orchestrator_Mode.md`;
`13_Equipment_to_Orchestrator_Data_Flow.md`.

**Goal:** Orchestrator-only features: staging directory monitoring,
five-state run lifecycle, `ingest.json` v1.1, staging cleanup, staging
state query.

**Deliverables:**
- Orchestrator-side staging watcher reading `creation.json` from staged
  runs and writing `ingest.json`.
- Five-state lifecycle: `staging` → `complete` → `sync_queued` →
  `sync_verified` → `cleared`. Append-only history.
- Run completeness: sentinel file OR manifest comparison.
- Staging cleanup: `manual` default; `scheduled` with `retain_hours`.
- Staging state query endpoint.
- `ui/pages/staging.py` -- bottom dock ~120 px, non-collapsible,
  per-row actions (Force sync, Clear, View log).

**Tests:** five-state lifecycle integration; concurrent equipment
pushes; cleanup-mode behavior; `force_sync` and `clear` actions.

---

### Phase 15: Distribution / PyInstaller bundling

**Spec sources:** `15_Distribution.md` (all subsections except §15.7
covered in Phase 13).

**Goal:** Build the distributable artifacts; verify launcher behavior.

**Deliverables:**
- `exlab_wizard.spec` -- PyInstaller `MERGE` producing
  `ExLab-Wizard-Tray`, `ExLab-Wizard-Window`, `ExLab-Wizard` entry
  points sharing `_internal/`.
- Hidden imports: `nicegui`, `pywebview`, `pystray`, `plyer`, `keyring`.
- `--add-data`: NiceGUI assets, `templates/`, `plugins/`.
- `--add-binary`: `rclone[.exe]`, WebView2 loader on Windows, GTK-WebKit
  hooks on Linux.
- Per-OS metadata (CFBundleIdentifier, app manifest, icon).
- CI workflow `.github/workflows/build.yml` -- Windows / macOS arm64 /
  macOS x64 / Linux x64 jobs.
- Versioning embedded from `pyproject.toml`.
- Bundled starter content under `_internal/templates/` and
  `_internal/plugins/`.

**Tests:** smoke-launch on each OS in CI; assert tray starts; assert
window spawns and reaches `/health`; assert `--testing` flag loads stub
backends.

---

### Phase 16: E2E hardening pass

**Goal:** Every flow listed in the merged test plan must be exercised
end-to-end and green. This phase patches gaps rather than introducing
new features. The Playwright suite must run in <5 min on a single CI
runner.

**Deliverables:**
- All 15 e2e flows green in `tests/e2e/`.
- `tests/e2e/README.md` describing how to run and debug e2e locally.
- Page-object pattern for stable selectors.

**Subagent plan (3 parallel):**
- Agent A: flows 1-5 (onboarding + wizards + browse).
- Agent B: flows 6-10 (problems + plugin input + settings + orchestrator
  + schema mismatch).
- Agent C: flows 11-15 (crash recovery + shutdown + keyboard +
  notifications + WebSocket reconnect).

---

## Critical Files (Reference)

| File | Phase | Purpose |
|---|---|---|
| `constants/{schema_versions,filenames,patterns,enums,keyring,limits}.py` | 1 | Hard-coded values single source of truth |
| `config/{models,loader}.py`, `paths.py` | 2 | Config + setup state |
| `logging/{__init__,manager,format,context,handlers}.py` | 3 | Canonical logger system |
| `cache/{creation_writer,log_writer,equipment,ingest_writer}.py` | 3 | All `.exlab-wizard/*` mutations |
| `validator/{rules,engine,findings}.py` | 4 | Both validator modes + Pre-Sync Gate input |
| `template/copier_driver.py` | 5 | Copier wrapper |
| `plugins/{base,registry,host,_worker,logger}.py` | 6 | Plugin contract + isolation |
| `controller/{state_machine,session_store,creation}.py` | 7 | §4.7 state machine |
| `readme/generator.py` | 8 | README + `readme_fields.json` |
| `lims/{client,cache,schemas}.py` | 9 | Read-only LIMS + keyring |
| `sync/nas_client.py`, `sync/transports/*` | 10 | NAS sync queue |
| `api/{app,routers/*,events,schemas,health,setup}.py` | 11 | HTTP+WS surface |
| `ui/{design,theme,notifications,keyboard}.py`, `ui/{pages,components}/*` | 12 | NiceGUI frontend |
| `tray/*`, `window/*` | 13 | Two-process model |
| `exlab_wizard.spec`, `.github/workflows/build.yml` | 15 | Distribution |
| `tests/{unit,integration,e2e,fixtures}/*` | All | Per-phase test additions |

---

## Verification (final state)

End-to-end happy-path verification once all 16 phases land:

1. `pytest tests/unit tests/integration -q` -- all green, ≥85% line
   coverage per package.
2. `pytest tests/e2e -q` -- all 15 flows green in <5 min.
3. `pre-commit run --all-files` -- lint clean, no `logging.getLogger`
   outside `logging/manager.py`, no direct `ui.notify` outside
   `ui/notifications.py`, no em dashes.
4. Spawn `exlab-wizard-tray`, open window, run the project + run
   wizards manually, confirm `creation.json` matches §11.3 schema 1.8
   verbatim.
5. Mutate the README YAML front matter, re-load, confirm round-trip.
6. Trigger a `PluginInputRequired` flow manually; confirm dialog
   appears, resume completes, run lands in tree.
7. Run `pyinstaller exlab_wizard.spec` on each target OS; smoke-test
   the resulting bundle.
8. `GET /api/v1/health` returns
   `{"status":"ok","schema_versions":{"creation_json":"1.8",...},"setup_state":"READY"}`.

---

## Risks & Mitigations

- **Concurrent edit collisions during parallel agent work.** Mitigated
  by each subagent owning a disjoint module path; main agent serializes
  cross-cutting touchpoints.
- **Spec drift between plan and implementation.** Mitigated by the
  per-phase design-adherence subagent that re-reads the spec sections
  and asserts adherence before phase exit.
- **Playwright flake.** Mitigated by stable `data-testid`-style
  selectors, retry-aware test helpers, and the page-object pattern in
  Phase 16.
- **PyInstaller hidden-import gaps.** Mitigated by Phase 15 CI smoke
  tests on each OS that drive the full happy path.
- **Cross-platform autostart edge cases.** Mitigated by Phase 13's
  per-OS register/unregister round-trip tests using filesystem stubs;
  manual verification on a real macOS / Windows / Linux machine in the
  Phase 13 phase exit.
