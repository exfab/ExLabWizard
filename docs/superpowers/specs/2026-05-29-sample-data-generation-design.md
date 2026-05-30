# List-driven sample data generation — Design Spec

**Date:** 2026-05-29
**Status:** Approved for planning
**Scope:** Replace the single hardcoded `--add-test-samples` equipment entry with
a list-driven generator that seeds a full demo tree — equipment, project folders,
and run folders — where **every folder carries the same metadata the real wizard
writes** (`README.md`, `creation.json`, `readme_fields.json`, plus `equipment.json`
and the `test_runs.json` marker). The data is declared once as a typed,
code-embedded Pydantic list. The generator runs from a shared module behind two
thin entry points and regenerates a pristine dataset on every run.

---

## 1. Summary

Today `exlab-wizard-tray --test --add-test-samples` seeds exactly **one**
`EquipmentConfig` literal (`tray/main.py:311-323`) and nothing else: no projects,
no run folders, no per-folder metadata. Anyone wanting a realistic dataset to
exercise the browse / validate / sync UIs has to drive the wizard by hand.

This change makes sample data **declarative and complete**:

- A single code-embedded list, `SAMPLES`, describes equipment → projects → runs.
- A generator expands that list into a real on-disk tree under the `-test`
  sandbox, writing each folder's metadata through the **same producers the
  production creation pipeline uses** — so the metadata is correct by
  construction and cannot drift from the real format.
- The generator lives in one shared module with two callers: the existing
  `--add-test-samples` tray flag and a new standalone dev command.
- The dev command **wipes** the seeded subtrees and regenerates from scratch
  (using an injected fixed clock, so the dataset is deterministic); the tray flag
  seeds only on a fresh sandbox and never wipes a tree the operator added to
  between boots.
- The dataset deliberately spans the sync-status badge states (`SYNCED` /
  `PENDING` / `BLOCKED_BY_VALIDATION`) and drops small payload files into each run,
  so the validate and sync UIs have real input to act on.

The app has **not been deployed**, so there is no backward-compatibility or
migration burden. This is a developer/test fixture, not a user-facing feature.

### Key decisions (resolved during brainstorming)

| Decision | Choice |
|---|---|
| Seed depth | Full tree: equipment + projects + runs, every folder with metadata |
| List format | Code-embedded **Pydantic** models (house style: `config/models.py`) |
| Trigger | Shared core module + two entry points (tray flag + dev command) |
| Re-run behavior | Dev command **wipes + regenerates**; tray seeds **first launch only** (non-destructive on repeat boots) |
| Metadata mechanism | Reuse the real file producers + extract shared assembly helpers |
| Field validation | Reuse `validate_project_short_id` / `canonicalize_equipment_id` |
| Dataset shape | 2 equipment, 3 projects, ~7 runs (NAS-only); states + payload files spread for UI coverage |

---

## 2. Goals & non-goals

### Goals
- Adding/changing sample data is a one-place edit to a typed `SAMPLES` list.
- Every seeded project and run folder contains byte-correct `README.md`,
  `creation.json`, and `readme_fields.json`, identical in structure to what the
  wizard writes; equipment roots carry `equipment.json`; test runs carry the
  `test_runs.json` marker.
- The metadata-assembly logic is **shared** with `CreationController` so the two
  can never diverge.
- Generation is deterministic (fixed clock); the dev command is safe to re-run
  (wipe + rebuild always yields the same tree), and the tray flag is
  non-destructive on repeat boots.
- The dataset exercises every sync-status badge and contains payload files, so the
  browse / validate / sync UIs are driven by sample data rather than hand-built
  runs.
- The destructive wipe is impossible to fire outside the `-test` sandbox.

### Non-goals
- Seeding the LIMS offline catalogue / project picker (YAGNI — the browse UI
  reads folders from disk; the on-disk tree is sufficient. Noted as a future
  extension in §8).
- Driving the full async `CreationController` state machine (Copier template
  render, plugin pass, NAS enqueue) — see §3 rejected Approach A.
- A user-editable external data file or CLI-supplied path (rejected in favour of
  the code-embedded list).
- Triggering real NAS sync for seeded runs. Seeded `sync_status` values (including
  `SYNCED`) are **chosen UI scenarios** written straight into `creation.json`; no
  run is actually verified or transferred. The metadata stays structurally
  correct — only the value is a deliberate fixture choice.
- Fabricating validation findings / `validation_overrides` for the
  `BLOCKED_BY_VALIDATION` run (deferred). The blocked **status** is seeded; the
  problems-pane findings are not. A seeded payload file carrying a content-scan
  trigger string makes the block plausible, but populating real findings is its
  own future task.
- An `stage`-mode equipment in the dataset (deferred; NAS-only for v1).
- Any change to production creation/validation/sync behavior beyond the
  metadata-assembly extraction in §5.

---

## 3. Approaches considered

**Approach A — Drive the real `CreationController` pipeline.** Build a `Config`,
`apply_config`, then call `create_project` / `create_run` per node. Maximum
fidelity, but the pipeline (`creation.py:627`) requires a resolved Copier
template, a plugin host, LIMS identity resolution, and NAS enqueue; it is fully
async, rejects same-minute path collisions as a hard error, and stamps
timestamps from `now()`. Heavy and brittle for a fixture generator, and it fights
the wipe-and-regenerate model. **Rejected.**

**Approach B — Lightweight seeder reusing the real file producers (chosen).** A
new module composes a `ReadmeContext` + `CreationJson` per folder and calls the
real `ReadmeGenerator`, `CacheWriter`, and `EquipmentCacheWriter`. No
Copier/plugin/LIMS/state-machine baggage; trivially deterministic, wipeable, and
sync-friendly. The one gap — re-implementing the *value-assembly* layer — is
closed by the §5 shared-helper extraction. **Chosen.**

**Approach C — Hand-written metadata strings.** Write `README.md` /
`creation.json` as literals in the seeder. Guarantees drift the moment any schema
changes; contradicts the core goal. **Rejected.**

---

## 4. Sample list data model (Pydantic)

New package `src/exlab_wizard/sample_data/`, file `spec.py`. Models follow the
`config/models.py` style (`extra="forbid"`, `str_strip_whitespace=True`,
`frozen=True` since `SAMPLES` is a static constant). Reused enums `RunKind`,
`SyncMode`, and `SyncStatus` keep the data type-checked; field validators reuse the
real id rules so malformed sample data raises `ValidationError` **at import time**,
before any folder is touched.

```python
class SampleFile(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    relpath: str = Field(min_length=1)     # path under the run dir, e.g. "data/acq_001.csv"
    content: str = ""                      # deterministic UTF-8 content (kept small)

class SampleRun(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    kind: RunKind
    label: str = Field(min_length=1)
    operator: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    readme_extra: dict[str, Any] = Field(default_factory=dict)   # config-default + custom values
    sync_status: SyncStatus = SyncStatus.PENDING   # chosen badge scenario for this run
    files: list[SampleFile] | None = None          # payload files; None → a default pair
    minutes_offset: int | None = None              # deterministic run-date offset; auto if None

class SampleProject(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    short_id: str                          # LIMS short id (validated below)
    name: str = Field(min_length=1)        # human-readable <project>/ folder segment
    label: str = Field(min_length=1)
    operator: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    runs: list[SampleRun] = Field(min_length=1)

    @field_validator("short_id")
    @classmethod
    def _check_short_id(cls, v: str) -> str:
        from exlab_wizard.paths import validate_project_short_id
        return validate_project_short_id(v)

class SampleEquipment(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)

    id: str                                # raw id; TEST_ prefix applied by the loader
    label: str = Field(min_length=1)
    sync_mode: SyncMode = SyncMode.NAS
    projects: list[SampleProject] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        from exlab_wizard.paths import canonicalize_equipment_id
        return canonicalize_equipment_id(v)

SAMPLES: list[SampleEquipment] = [
    # Equipment A — one project, three runs, covers SYNCED + PENDING.
    SampleEquipment(
        id="TESTRIG", label="Test Rig", sync_mode=SyncMode.NAS,
        projects=[
            SampleProject(
                short_id="PROJ-0001", name="Demo Project", label="Demo Project",
                operator="asmith", objective="Exercise the browse / validate / sync UIs.",
                runs=[
                    SampleRun(kind=RunKind.EXPERIMENTAL, label="Baseline run",
                              operator="asmith", objective="Baseline acquisition.",
                              sync_status=SyncStatus.SYNCED),
                    SampleRun(kind=RunKind.EXPERIMENTAL, label="Repeat run",
                              operator="asmith", objective="Repeat for variance."),
                    SampleRun(kind=RunKind.TEST, label="Smoke test run",
                              operator="asmith", objective="Test-mode dry run."),
                ],
            ),
        ],
    ),
    # Equipment B — two projects. PROJ-0002 exercises config-default + custom
    # README fields; PROJ-0003 carries the BLOCKED_BY_VALIDATION scenario.
    SampleEquipment(
        id="ALTRIG", label="Alt Rig", sync_mode=SyncMode.NAS,
        projects=[
            SampleProject(
                short_id="PROJ-0002", name="Calibration Study", label="Calibration Study",
                operator="bjones", objective="Calibration sweep.",
                runs=[
                    SampleRun(kind=RunKind.EXPERIMENTAL, label="Calibration A",
                              operator="bjones", objective="Primary calibration.",
                              sync_status=SyncStatus.SYNCED,
                              # "sample_type" matches the seeded config default → config_fields;
                              # "reviewer" matches nothing → custom_fields.
                              readme_extra={"sample_type": "control", "reviewer": "asmith"}),
                    SampleRun(kind=RunKind.TEST, label="Calibration dry run",
                              operator="bjones", objective="Dry-run the calibration."),
                ],
            ),
            SampleProject(
                short_id="PROJ-0003", name="Failure Modes", label="Failure Modes",
                operator="bjones", objective="Reproduce failure modes.",
                runs=[
                    SampleRun(kind=RunKind.EXPERIMENTAL, label="Bad acquisition",
                              operator="bjones", objective="Trips the content scanner.",
                              sync_status=SyncStatus.BLOCKED_BY_VALIDATION,
                              files=[SampleFile(relpath="data/leak.txt",
                                                content="api_key=DEMO_SCAN_TRIGGER\n")]),
                    SampleRun(kind=RunKind.TEST, label="Edge case test",
                              operator="bjones", objective="Edge-case dry run."),
                ],
            ),
        ],
    ),
]   # the one place a developer edits
```

Notes:
- `id` is the **raw** equipment id (e.g. `TESTRIG`). The `TEST_` prefix is applied
  by `apply_test_mode_prefix` at config load (see §6 step 1).
- Local/NAS roots are **not** in the model — they are derived from the sandbox at
  generation time, so the list never hardcodes paths.
- `sync_status` and `files` default to `PENDING` / a built-in pair, so most runs
  stay one line; only the runs that need a specific scenario set them.
- `readme_extra` keys are partitioned by the README generator: a key matching a
  seeded `config.readme.defaults` id lands in `config_fields`, anything else lands
  in `custom_fields` (see §6 step 1 for the one seeded config default).
- The two reused validators (`validate_project_short_id`,
  `canonicalize_equipment_id`) are the same rules `config/models.py` and the LIMS
  layer enforce, so sample ids cannot drift from the real grammar.

---

## 5. Shared-helper refactor of `creation.py`

To eliminate assembly-layer drift between the controller and the seeder, extract
two pure, dependency-light functions into a new `controller/metadata_assembly.py`:

- `build_readme_context(...) -> ReadmeContext` — the body of today's
  `CreationController._build_readme_context` (`creation.py:898-961`).
- `build_creation_json(...) -> CreationJson` — the payload-assembly body of
  `CreationController._write_cache` (`creation.py:1047-1068`).

Both take explicit parameters instead of reading `self`: `config`,
`equipment_id`, `level`, core fields, `readme_extra`, a small `TemplateDesc`
(name / version / source_path / run_scope / extra_readme_fields / plugin_order),
`run_kind`, `short_id`, `lims_block`, `variables`, `plugins_applied`, a
`sync_status` (default `PENDING` — the controller keeps passing `PENDING`; the
seeder passes the per-run scenario), and an injected `clock`/timestamp (replacing
the direct `utc_now()` / `utc_now_iso()` calls so output is deterministic).

`CreationController` keeps its methods but delegates to these helpers, adapting its
`ResolvedTemplate` into a `TemplateDesc`. The seeder calls the same helpers with a
**sentinel `TemplateDesc`** (`name="seed"`, `version="0"`, `source_path=""`,
`run_scope="both"`, no extra fields, no plugins) and an **empty
`plugins_applied`** list. Both paths then produce byte-identical metadata
structure.

This is a focused, justified refactor of code we are already touching — not
unrelated churn. A parity unit test (controller vs. seeder, identical inputs →
identical output) guards the extraction.

---

## 6. Generation flow

`SampleDataGenerator.generate()` is a synchronous wrapper (`asyncio.run`) around an
async core, since the real producers (`ReadmeGenerator`, `CacheWriter`,
`EquipmentCacheWriter`) are async.

1. **Build & reload config.** Convert `SAMPLES` into `EquipmentConfig` entries
   (raw ids, `local_root`/`nas_root` derived as `sandbox/local/<id>` and
   `sandbox/nas/<id>`); merge into the starter `Config` (paths + orchestrator
   under the sandbox, as `_bootstrap_test_config` does today). Seed exactly one
   **non-required** `readme.defaults` field — `sample_type` (`type=choice`,
   options e.g. `["control", "treatment"]`) — so a run's `readme_extra` can
   populate the `config_fields` layer; leave `operators.allowlist` **empty** (the
   seeder bypasses the allowlist, and an empty list keeps the wizard frictionless
   for hand-created runs). `save_config()`, then **`load_config()` it back** so
   `apply_test_mode_prefix` stamps the `TEST_` prefix. The generator uses the
   **loaded (prefixed) equipment ids** for every folder, guaranteeing they match
   what the running app sees.
2. **Wipe** (dev command only; see §7). For each seeded equipment, remove
   `local_root/<PREFIXED_ID>/` after the guardrail check. The **tray flag does not
   reach this step** — it seeds only when the sandbox config is absent (§8), so a
   repeat `--test` boot never wipes.
3. **Create the tree.** Per project: `compose_project_path(local_root,
   equipment_id, project_name)` + `ensure_dir`. Per run:
   `compose_run_path(..., run_kind, run_date)` + `ensure_dir` →
   `Runs/Run_<DATE>/` (experimental) or `TestRuns/TestRun_<DATE>/` (test). Then
   write each run's payload files (`SampleRun.files`, or a default
   `data/acq_001.csv` + `notes.txt` pair when `None`) under the run dir.
4. **Write metadata** via the real producers, using the §5 helpers to assemble
   inputs:
   - `ReadmeGenerator.generate(dst, ctx)` → `README.md` + `readme_fields.json`
   - `CacheWriter.write_creation(creation_json_path(dst), payload)` →
     `creation.json`, with `sync_status` taken from the run's scenario (default
     `PENDING`). No real NAS sync is triggered regardless of the value.
   - `EquipmentCacheWriter` → `equipment.json` at each equipment root, and the
     `test_runs.json` marker the first time a `TestRuns/` run is written
   - Both project folders and run folders get the README + creation.json pair
     (the controller writes metadata at both levels). Project-level runs default
     to `PENDING`; the per-run `sync_status` applies to run folders.
5. **Determinism.** An injected `clock` provides a fixed base instant (e.g.
   `2026-01-01T09:00:00Z`). Each run's `run_date = base + (minutes_offset or
   auto_index)` minutes, where `auto_index` increments per project so
   minute-precision run paths are unique and reproducible. Every `created` /
   `created_at` field flows from the same clock; payload file contents are fixed
   bytes, so two generations are byte-identical.

The generator subsumes today's inline equipment seeding — `_bootstrap_test_config`
will call `generate_samples(...)` instead of constructing one `EquipmentConfig`.

---

## 7. Safety guardrails (destructive wipe)

The wipe is the only dangerous operation; it is fenced on every side:

- **Test-mode gate.** Refuse to wipe unless `EXLAB_WIZARD_TEST_MODE` is truthy
  **and** the resolved sandbox `app_name()` ends in `-test`. Raise loudly
  otherwise — a real `local_root` must never be deletable by this code path.
- **Scoped targets only.** Only ever `rmtree` `local_root/<PREFIXED_ID>/` for ids
  present in `SAMPLES`. Never `local_root` itself; never a path resolving outside
  the sandbox; never a non-seeded id.
- **Path containment check.** Resolve each target and assert it is a strict
  subpath of the sandbox before removal.
- **Visibility.** The dev command prints the exact list of directories it will
  remove before removing them.

---

## 8. Module layout & entry points

```
src/exlab_wizard/sample_data/
    __init__.py     # exports generate_samples(config_path, *, wipe, clock=...)
    spec.py         # SAMPLES + Pydantic models (§4)
    generator.py    # SampleDataGenerator (§6) + guardrails (§7)

src/exlab_wizard/controller/
    metadata_assembly.py   # build_readme_context / build_creation_json (§5)

src/exlab_wizard/dev/
    seed.py         # `python -m exlab_wizard.dev.seed` standalone command
```

The shared facade is `generate_samples(config_path, *, wipe, clock=...)`. `wipe` is
**not** a user-facing flag — it is set by the caller:

- **Tray flag.** `tray/main.py._bootstrap_test_config(..., include_samples=True)`
  calls `generate_samples(..., wipe=False)` and **only when the sandbox config is
  absent** (today's "never clobber an existing sandbox" behavior is preserved).
  Today's hardcoded `TESTRIG` literal is deleted. The flag's contract is unchanged
  (`--add-test-samples` still requires `--test`).
- **Dev command.** `dev/seed.py` resolves/forces the `-test` sandbox, then calls
  `generate_samples(..., wipe=True)` — **always** wipe + regenerate (the only mode;
  no `--reset` flag). It prints the exact directories it will remove first.
  Decoupled from tray boot, so the async work runs cleanly on its own event loop.

**Future extension (out of scope):** add an opt-in step that writes the LIMS
offline catalogue from `SAMPLES` so the project *picker* (not just the browse
tree) is populated.

---

## 9. Error handling & testing

### Error handling
- Invalid `SAMPLES` fails fast at **import time** via Pydantic `model_validate`
  (bad equipment id, bad `short_id`, empty required field).
- Config/merge errors surface through the existing `ConfigError` shape;
  per-folder failures abort that equipment with a clear message rather than
  leaving a half-built tree.
- The wipe guardrail raises (never silently no-ops) when its preconditions fail.

### Testing
- **Assembly parity:** `build_readme_context` / `build_creation_json` produce
  identical structure whether called by the controller or the seeder for the same
  inputs.
- **Generator end-to-end (tmp sandbox):** full tree exists (2 equipment, 3
  projects, 7 runs); every project/run folder has valid `README.md` +
  `creation.json` + `readme_fields.json` (decoded via the real msgspec Structs and
  the README front-matter parser); `equipment.json` present at each equipment root;
  `test_runs.json` present for test runs.
- **Sync-status spread:** the seeded runs collectively cover `SYNCED`, `PENDING`,
  and `BLOCKED_BY_VALIDATION`; each run's `creation.json` `sync_status` matches its
  `SampleRun` scenario.
- **Payload files:** every run dir contains its `files` (or the default pair);
  the seeded `.csv`/`.txt` extensions fall within the validator's
  `content_scan_extensions`; the `BLOCKED_BY_VALIDATION` run carries the
  trigger-string file.
- **README layers:** the `PROJ-0002 / Calibration A` run populates both
  `config_fields` (`sample_type`) and `custom_fields` (`reviewer`) in
  `readme_fields.json`; the seeded `readme.defaults` `sample_type` is present and
  non-required.
- **Determinism:** two consecutive generations produce identical paths, identical
  metadata bytes, and identical payload-file bytes (fixed clock).
- **Guardrails:** wipe refuses when `EXLAB_WIZARD_TEST_MODE` is unset, when
  `app_name()` does not end in `-test`, and when a target escapes the sandbox.
- **Trigger split:** the dev command (`wipe=True`) rebuilds a pre-existing tree;
  the tray path (`wipe=False`) seeds a fresh sandbox but is a no-op — and never
  deletes — when a config already exists.
- **Validation:** malformed `short_id` and malformed equipment `id` are rejected
  at model construction.
- **Regression:** existing `_bootstrap_test_config` / `--add-test-samples` tests
  updated to assert the new shared path (config still written; equipment present;
  no overwrite of an existing config).

---

## 10. File-level change summary

| File | Change |
|---|---|
| `src/exlab_wizard/sample_data/spec.py` | **New** — Pydantic models + `SAMPLES` (§4) |
| `src/exlab_wizard/sample_data/generator.py` | **New** — `SampleDataGenerator`, guardrails (§6–§7) |
| `src/exlab_wizard/sample_data/__init__.py` | **New** — `generate_samples(...)` facade |
| `src/exlab_wizard/controller/metadata_assembly.py` | **New** — extracted assembly helpers (§5) |
| `src/exlab_wizard/controller/creation.py` | Delegate `_build_readme_context` / `_write_cache` to the new helpers |
| `src/exlab_wizard/dev/seed.py` | **New** — standalone `python -m exlab_wizard.dev.seed` |
| `src/exlab_wizard/tray/main.py` | `_bootstrap_test_config` calls `generate_samples(..., wipe=False)` on a fresh sandbox only; delete inline `TESTRIG` literal |
| `tests/...` | New tests per §9; update existing bootstrap tests |
