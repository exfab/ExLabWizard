# 11. `.exlab-wizard` Cache Folders

Parent: [[ExLab-Wizard_Design_Spec]]

Every directory level created or managed by the app contains a `.exlab-wizard` cache folder. Its purpose is to make each directory self-describing: the template used, the inputs provided, and the creation context are all recoverable from the filesystem without querying the LIMS database.

## 11.1 Folder Placement

```
<equipment>/
  .exlab-wizard/
    equipment.json              # equipment metadata; one file per equipment root
    wizard.<hostname>.log       # shared log for all activity under this equipment
    templates/                  # per-equipment template cache (run templates + equipment-specific project templates)
      run_<name>/               # one Copier template directory per template
        copier.yml
        ...
      project_<name>/           # equipment-specific project templates (override global on name collision)
        copier.yml
        ...
  <project>/
    .exlab-wizard/
      creation.json             # project-level provenance
      readme_fields.json
      wizard.<hostname>.log
    Run_<DATE>/                 # experimental run
      .exlab-wizard/
        creation.json
        readme_fields.json
        wizard.<hostname>.log
        ingest.json              # orchestrator only; see Section 13.4
    TestRuns/
      .exlab-wizard/
        test_runs.json           # marks the subfolder as test-only; written once on first test run; filename retained for backward compatibility with v0.5 readers
        wizard.<hostname>.log    # shared log for all test-run activity under this project
      TestRun_<DATE>/            # test run (TestRun_ leaf prefix is the secondary marker)
        .exlab-wizard/
          creation.json          # creation.json sets run_kind: "test" (see Section 11.3)
          readme_fields.json
          wizard.<hostname>.log
          ingest.json             # orchestrator only
```

Equipment folders sit at the top level under the shared storage root and contain an `equipment.json` describing the equipment ID, label, and configured roots. Project folders are children of equipment folders; the same project label may appear under multiple equipment folders, with each `<equipment>/<project>/` pair being independently provenanced. The equipment registry in `config.yaml` remains the single source of truth for equipment IDs and labels.

## 11.2 File Inventory per Level

**Equipment level** -- written when the equipment root is first initialized (typically as a side-effect of the first project creation under this equipment, or via explicit equipment setup):

| File / Directory | Synced to NAS | Description |
|---|---|---|
| `equipment.json` | Yes | Equipment ID, label, configured NAS root path, first-seen timestamp |
| `wizard.<hostname>.log` | Yes | Append-only log of all activity (across all projects, experimental and test) under this equipment folder |
| `templates/` (directory) | Yes | Per-equipment template cache; holds run templates and equipment-specific project templates. See [[05_Template_Format#5.0 Template Locations (Global and Per-Equipment)|§5.0]]. Each child directory is a Copier template with its own `copier.yml`. |

**Test-runs folder level** -- written the first time a test run is created for a given `<equipment>/<project>/TestRuns/`:

| File | Synced to NAS | Description |
|---|---|---|
| `test_runs.json` | Yes | Marker declaring the subtree as test-only; contains `{ "run_kind": "test", "created_at": <UTC>, "project": <name>, "equipment": <id> }`. The filename `test_runs.json` is retained from v0.5 for backward compatibility, even though the parent folder name was renamed to `TestRuns/` in v0.6. |
| `wizard.<hostname>.log` | Yes | Append-only log of all test-run activity under this subfolder |

**Project level** -- written when project creation completes:

| File | Synced to NAS | Description |
|---|---|---|
| `creation.json` | Yes | Template provenance and resolved variables |
| `readme_fields.json` | Yes | Exact field values written into README, including user-added custom fields |
| `wizard.<hostname>.log` | Yes | Append-only log of events for this project |

**Run level** -- written when run creation completes:

| File | Synced to NAS | Description |
|---|---|---|
| `creation.json` | Yes | Template provenance and resolved variables |
| `readme_fields.json` | Yes | Exact field values written into README |
| `wizard.<hostname>.log` | Yes | Append-only log of events for this run |
| `ingest.json` | Yes | Orchestrator staging lifecycle history (orchestrator mode only) |

All `.exlab-wizard` contents sync to NAS as part of the directory mirror with SHA256 checksum verification.

## 11.3 `creation.json` Schema

```json
{
  "schema_version": "1.8",
  "created_at": "2026-04-17T14:32:00Z",
  "created_by": "asmith",
  "level": "run",
  "run_kind": "experimental",
  "lims_project": {
    "uid": "8c7e9d2f-1a4b-4e6c-9b3d-7f2a1e5d8c4b",
    "short_id": "PROJ-0042",
    "name_at_creation": "Cortex Q3 Pilot",
    "source": "live",
    "cache_freshness_at_use": null
  },
  "template": {
    "name": "confocal_run_v2",
    "version": "2.1",
    "source_path": "templates/confocal_run_v2",
    "run_scope": "both"
  },
  "variables": {
    "project_name": "Cortex Q3 Pilot",
    "operator": "asmith",
    "run_date": "2026-04-17T14:32:00Z"
  },
  "plugins_applied": [
    {
      "plugin": "xlsx_field_filler",
      "version": "0.3.1",
      "files_affected": ["metadata.xlsx"],
      "status": "success",
      "isolation": {
        "duration_ms": 412,
        "exit_code": 0,
        "peak_memory_mb": 38
      }
    }
  ],
  "paths": {
    "local": "/data/lab/CONFOCAL_01/PROJ-0042/Run_2026-04-17T14-32-00",
    "nas": "//nas01/lab/CONFOCAL_01/PROJ-0042/Run_2026-04-17T14-32-00"
  },
  "orchestrator": {
    "enabled": true,
    "host": "labpc-04",
    "label": "Lab Acquisition Station 01"
  },
  "sync_status": "pending",
  "validation_overrides": []
}
```

**Current schema version: `1.8`.** The 1.5 → 1.6 → 1.7 → 1.8 progression added (in order) the `lims_project` block (Mapping B; [[07_Sync_and_Database_Integration#7.2 LIMS Integration|§7.2]]), tombstone-by-reference for `validation_overrides`, optional `expires_at` on overrides, and the `lims_project.source` + `lims_project.cache_freshness_at_use` fields that record which LIMS data source the wizard used at creation time. See the "Schema-version history" table near the bottom of this section for the full progression. Notes specific to `lims_project` (introduced in 1.5):

- `lims_project.uid` — the LIMS project's stable UUID. Authoritative identity.
- `lims_project.short_id` — the LIMS project's `PROJ-NNNN` form. **Authoritative for the on-disk path segment** (see [[03_Directory_Structure_Convention|§3]]). Path examples in this section show `PROJ-0042`. Stable across LIMS-side renames of the project's `name`.
- `lims_project.name_at_creation` — the LIMS project's human-readable name at the time the run was created. Stored as a denormalized snapshot so a later LIMS-side rename does not silently mutate the audit trail. The current name is fetched live from LIMS when needed (or from the cache; §7.2.4).
- `lims_project.source` — closed enum `"live"` | `"cache"` | `"offline_catalogue"`. Records WHICH LIMS data source the wizard used at creation time. `"live"` means the wizard hit the LIMS API and got a fresh response; `"cache"` means it fell back to the local SQLite cache (§7.2.4) because the API was unreachable; `"offline_catalogue"` means it read from the NAS-shared catalogue (§7.2.9). Used by the Frontend recovery flows (Frontend §10.5.2) and as an audit signal for runs created during a LIMS outage.
- `lims_project.cache_freshness_at_use` — UTC timestamp string OR `null`. Set only when `source != "live"`. Records when the cached data was last refreshed from LIMS, so an auditor can see "the wizard used data from N hours before creation" without re-fetching anything.

The `lims_project` block is **absent** at the equipment level (`equipment.json` lives there instead; §11.1, §11.2 — equipment is registry-driven, not LIMS-bound). The `lims_project` block is **required** at the project and run levels in v0.7. (`source` and `cache_freshness_at_use` are required in 1.8 and later; on a 1.7 file they're absent and read as `"live"` and `null` respectively per the migration policy in §11.9.2.)

Readers expecting `1.0`–`1.4` ignore the `lims_project` block. The validator's audit-mode walk treats the **absence** of `lims_project` in a project- or run-level `creation.json` whose `schema_version` is ≥ 1.5 as a soft-tier `missing_required_field` finding ([[08_Error_Handling_Principles#8.1.5 Missing-required-field rule (soft tier)|§8.1.5]]), with `field: "lims_project"` and the run path. Files with `schema_version` ≤ 1.4 are exempt because the field did not exist when they were written.

The `orchestrator` block is absent (not null) when the app is running in single-equipment mode. `sync_status` starts as `"pending"` and is updated in-place to `"synced"`, `"failed"`, or `"blocked_by_validation"` by the NASSync component (the last value is set by the Pre-Sync Gate per [[07_Sync_and_Database_Integration#7.3 Pre-Sync Gate|§7.3]]). The `validation_overrides` array is also mutated in place when the operator records an override; all other fields are immutable after initial write.

`validation_overrides` is an append-only list of operator overrides recorded from the Problems tab (User Interaction Spec §7.3). The list is append-only for audit integrity: revocation is a new entry (a *tombstone*) that points at the entry it cancels, never an in-place edit. Two entry shapes:

**Override entry** (adding an override):

```json
{
  "id": "8c7e9d2f-1a4b-4e6c-9b3d-7f2a1e5d8c4b",
  "problem_class": "unresolved_placeholder_token",
  "operator": "asmith",
  "recorded_at": "2026-04-18T09:14:22Z",
  "expires_at": "2026-10-18T09:14:22Z",
  "reason": "Vendor template uses literal angle-bracket syntax for run codes; whitelisting per lab policy 2026-04-15.",
  "revoked": false
}
```

**Tombstone entry** (revoking a prior override):

```json
{
  "id": "f3a91e2c-5d6b-4a7e-8c9d-1e2f3a4b5c6d",
  "revokes": "8c7e9d2f-1a4b-4e6c-9b3d-7f2a1e5d8c4b",
  "operator": "asmith",
  "recorded_at": "2026-05-01T11:02:14Z",
  "reason": "Vendor fixed the template; override no longer needed.",
  "revoked": true
}
```

Field semantics:

- `id` (UUID v4) — generated server-side at write time. Stable for the entry's lifetime. Required on every entry. Tombstones reference it via `revokes`.
- `revokes` — present **only** on tombstone entries (those with `revoked: true`); points at the `id` of the entry being revoked. Absent (and ignored if present) on override entries.
- `problem_class` — required on override entries; absent on tombstones (the tombstone inherits its class from the entry it revokes). One of `unresolved_placeholder_token`, `leftover_jinja_marker`, `illegal_filesystem_character`, `reserved_filesystem_name`, `mode_prefix_mismatch`.
- `operator` / `recorded_at` / `reason` — required on every entry. Tombstones must give a reason for revocation.
- `expires_at` (UTC ISO 8601) — **optional**, override entries only. If present, the override is treated as inactive once the wall-clock UTC time passes this timestamp; the gate re-engages automatically without an explicit tombstone. Absent or `null` means the override does not expire. Resolves OQ #13. Tombstones do not carry `expires_at` (they are themselves immediate-effect markers, not time-bounded statements).
- `revoked` — `false` on override entries, `true` on tombstones. Required on every entry; controls the schema interpretation of `revokes` and `expires_at`.

**Matching algorithm** (used by [[07_Sync_and_Database_Integration#7.3 Pre-Sync Gate|§7.3]]):

1. Build a set `revoked_ids = { entry.revokes : entry ∈ validation_overrides ∧ entry.revoked == true }`.
2. Let `now = current UTC time`.
3. The set of *currently-active* overrides is:
   ```
   { entry ∈ validation_overrides
     : entry.revoked == false
     ∧ entry.id ∉ revoked_ids
     ∧ (entry.expires_at is None ∨ entry.expires_at > now) }
   ```
4. A finding with `rule == finding.rule_name` is allowed past the gate iff the active overrides contain at least one entry with `problem_class == finding.rule_name`.

Empty array (the default) means no overrides are active. Tombstones with no matching `revokes` target (e.g. the referenced `id` is missing from the array) are logged as a `WARN` but do not cause the gate to error — they just have no effect. **Expired overrides are not auto-removed from the array**, preserving the audit trail; the matching algorithm simply skips them. The Problems tab renders expired overrides with a distinct *"Override expired"* state badge so operators can decide whether to renew (write a fresh override) or accept the re-engaged gate.

(The `revoked: false` on override entries is technically redundant given the structural distinction, but it is required on every entry to make the field a primary discriminator for serialization and to keep the matching algorithm uniform across both shapes.)

`run_kind` is one of `"experimental"` or `"test"` and is set by the creation controller at creation time from the mode flag (User Interaction Spec 3.3). For test runs the `paths` block includes the `TestRuns/` segment and uses the `TestRun_` leaf prefix, for example:

```
"paths": {
  "local": "/data/lab/CONFOCAL_01/PROJ-0042/TestRuns/TestRun_2026-04-17T14-32-00",
  "nas":   "//nas01/lab/CONFOCAL_01/PROJ-0042/TestRuns/TestRun_2026-04-17T14-32-00"
}
```

### Schema-version history

| Version | Adds | Notes |
|---|---|---|
| `1.0` | initial | Pre-`run_kind` baseline. |
| `1.1` | `run_kind`, `template.run_scope` | Test/experimental separation introduced. |
| `1.2` | `validation_overrides`, `"blocked_by_validation"` value for `sync_status` | Pre-Sync Gate. |
| `1.3` | `plugins_applied[].isolation`, `"timeout"` value for `plugins_applied[].status` | Plugin subprocess isolation ([[06_Plugin_System#6.3 Subprocess Isolation|§6.3]]). |
| `1.4` | (skipped — briefly carried a `lims_status` field that v0.7 retracted, see [[04_Backend_Architecture#4.8 Crash Recovery|§4.8]]) | Not in production. **If a 1.4 file is encountered (e.g. from a pre-release test):** the reader treats it as 1.3 — the `lims_status` field is silently dropped from in-memory representation; the next mutation rewrites the file at the current schema version. No `schema_major_mismatch` error; same major. |
| `1.5` | `lims_project` block (`uid`, `short_id`, `name_at_creation`) at project and run levels | Mapping B; [[07_Sync_and_Database_Integration#7.2 LIMS Integration|§7.2]]. |
| `1.6` | `validation_overrides[].id` (UUID v4, required on every entry) and `validation_overrides[].revokes` (present on tombstones); `plugins_applied[].status` gains `"policy_violation"` value | Tombstone-by-reference for unambiguous revocation; plugins-must-not-touch enforcement ([[06_Plugin_System#6.1.5 What plugins must not touch|§6.1.5]]). |
| `1.7` | `validation_overrides[].expires_at` (optional, override entries only) | Time-bounded overrides; resolves OQ #13. The matching algorithm skips entries past `expires_at`. |
| `1.8` | `lims_project.source` (closed enum `live` / `cache` / `offline_catalogue`) and `lims_project.cache_freshness_at_use` (UTC timestamp string OR `null`) | Audit signal for runs created during a LIMS outage. See Frontend §10.5.2 for the recovery flow that drives non-`live` source values. |

Readers expecting earlier versions ignore unknown fields; `run_kind` defaults to `"experimental"`, `validation_overrides` defaults to `[]`, `plugins_applied[].isolation` is treated as absent when missing, `lims_project` is treated as absent, and `lims_project.source` defaults to `"live"` with `cache_freshness_at_use` defaulting to `null` — matching historical behavior before each respective addition.

## 11.4 `readme_fields.json` Schema

Schema version 1.1 separates the four field layers so downstream tools can reason about provenance: which fields came from backend core, template declaration, config extension, or ad-hoc user input.

```json
{
  "schema_version": "1.1",
  "generated_at": "2026-04-17T14:32:05Z",
  "core_fields": {
    "label": "Cortex Q3 calibration sweep",
    "operator": "asmith",
    "objective": "Characterize layer-specific synaptic density in fixed mouse cortex sections across three developmental timepoints. This run is a calibration sweep at 488 nm to validate laser power settings before the production acquisitions."
  },
  "template_fields": {
    "sample_type": "Fixed tissue",
    "protocol_reference": "SOP-CONF-2025-14"
  },
  "config_fields": {
    "irb_protocol": "IRB-2026-0042"
  },
  "custom_fields": [
    {"label": "Collaborator", "value": "Dr. J. Lee (Neurobiology)"},
    {"label": "Expected duration (hr)", "value": "3.5"}
  ],
  "system_fields": {
    "created": "2026-04-17T14:32:00Z",
    "created_by": "asmith",
    "equipment": {"id": "CONFOCAL_01", "label": "Confocal Microscope 1"},
    "template": {"name": "confocal_run_v2", "version": "2.1"},
    "project": "PROJ-0042",
    "run": "Run_2026-04-17T14-32-00",
    "run_kind": "experimental"
  }
}
```

`core_fields` is always present and always fully populated -- the creation controller guarantees this by validating before any filesystem writes. `template_fields`, `config_fields`, and `custom_fields` may be empty objects/arrays if no fields of that layer were declared or supplied. Readers that expect `"1.0"` can continue to parse the file by treating unknown keys as no-ops; the `template_fields` key preserves its 1.0 meaning.

**Field length limits.** `core_fields.label` is bounded to 100 characters and `core_fields.objective` to 2000 characters, both after whitespace trim. Both are enforced by the creation controller at validation time per User Interaction Spec §2; a writer must not produce a file violating these limits. Readers may surface a validation finding (Backend §8.1) when an out-of-bound value is encountered (e.g. a hand-edited file).

### 11.4.1 `equipment.json` Schema

```json
{
  "schema_version": "1.0",
  "id": "CONFOCAL_01",
  "label": "Confocal Microscope 1",
  "configured_local_root": "/data/lab",
  "configured_nas_root": "//nas01/lab",
  "first_seen_at": "2025-09-12T09:14:00Z",
  "last_modified_at": "2026-04-17T14:32:00Z"
}
```

One file per equipment, written at `<local_root>/<equipment_id>/.exlab-wizard/equipment.json`. Treated as a registry record (the equipment is a configured workstation peripheral, not an instance of a creation flow), distinct from `creation.json` which is provenance for a single creation event.

Field semantics:

- `id` mirrors the equipment's `config.yaml` `equipment[].id` and is validated against the same regex (`^[A-Z][A-Z0-9_]*$`, max 32 chars; Design Spec §3.1).
- `label` mirrors `config.yaml` `equipment[].label`. Operator-edited; may diverge from `config.yaml` if the operator renames the equipment in Settings without re-walking the file system.
- `configured_local_root` and `configured_nas_root` mirror `config.yaml` `equipment[].local_root` / `equipment[].nas_root` at the time the file was last written. Useful for diagnosing "this equipment used to live elsewhere" cases.
- `first_seen_at` is the UTC timestamp at which `equipment.json` was first written for this equipment. Never updated on subsequent writes.
- `last_modified_at` is updated on every `config.yaml`-driven re-sync of this equipment's metadata.

Schema version `1.0` is the only valid value in v1. The orphan rule (Backend §8.1.4) does NOT apply at the equipment level — equipment without `equipment.json` is a different problem class (Settings warning), not a Problems-tab finding.

### 11.4.2 `test_runs.json` Schema

```json
{
  "schema_version": "1.0",
  "run_kind": "test",
  "created_at": "2026-04-17T14:00:00Z",
  "project": "PROJ-0042",
  "equipment": "CONFOCAL_01"
}
```

Marker file written once on the first test run within `<equipment>/<project>/TestRuns/`. Filename retained from v0.5 for backward compatibility (the parent folder was renamed `TestRuns/` in v0.6 but the marker file kept its name to avoid breaking old readers). The marker declares the entire `TestRuns/` subtree as test-only; downstream consumers (validator, NAS sync) read it to apply mode-aware rules without inspecting individual run-level `creation.json` files.

Schema version `1.0` is the only valid value in v1. Subsequent test-run creations under the same project do NOT rewrite this file.

## 11.5 `wizard.<hostname>.log` Format

The on-disk format of `wizard.<hostname>.log` is canonical here; the runtime logger architecture that produces this file is specified in [[16_Logging|§16]].

Plain text, append-only, one structured event per line. Uses absolute local machine paths for full debugging traceability. Includes `[host:]` and `[equip:]` tags per entry for disambiguation when logs are aggregated or reviewed outside their original context.

Per-machine log files (`wizard.<hostname>.log`) avoid concurrent write conflicts without requiring file locking: each machine writes only to its own file.

```
2026-04-17T14:31:55Z [INFO ] [host:labpc-04] [equip:CONFOCAL_01] [proj:PROJ-0042] [kind:experimental] Creation started: new_run on CONFOCAL_01/PROJ-0042
2026-04-17T14:32:00Z [INFO ] [host:labpc-04] [equip:CONFOCAL_01] [proj:PROJ-0042] [kind:experimental] Template selected: confocal_run_v2 v2.1
2026-04-17T14:32:01Z [INFO ] [host:labpc-04] [equip:CONFOCAL_01] [proj:PROJ-0042] [kind:experimental] Variables resolved: 3 fields
2026-04-17T14:32:02Z [INFO ] [host:labpc-04] [equip:CONFOCAL_01] [proj:PROJ-0042] [kind:experimental] Directory created: /data/lab/CONFOCAL_01/PROJ-0042/Run_2026-04-17T14-32-00
2026-04-17T14:32:03Z [INFO ] [host:labpc-04] [equip:CONFOCAL_01] [proj:PROJ-0042] [kind:experimental] Plugin xlsx_field_filler applied to metadata.xlsx: success
2026-04-17T14:32:04Z [INFO ] [host:labpc-04] [equip:CONFOCAL_01] [proj:PROJ-0042] [kind:experimental] README written: README.md
2026-04-17T14:32:05Z [INFO ] [host:labpc-04] [equip:CONFOCAL_01] [proj:PROJ-0042] [kind:experimental] Cache written: .exlab-wizard/creation.json, readme_fields.json
2026-04-17T14:32:05Z [INFO ] [host:labpc-04] [equip:CONFOCAL_01] [proj:PROJ-0042] [kind:experimental] NAS sync queued
2026-04-17T14:32:11Z [INFO ] [host:labpc-04] [equip:CONFOCAL_01] [proj:PROJ-0042] [kind:experimental] NAS sync complete: //nas01/lab/CONFOCAL_01/PROJ-0042/Run_...
2026-04-17T14:32:11Z [INFO ] [host:labpc-04] [equip:CONFOCAL_01] [proj:PROJ-0042] [kind:experimental] creation.json sync_status updated: synced
```

Log entries add `[proj:]` and `[kind:]` tags so that when logs are aggregated across projects or across run kinds (experimental vs test), each entry is self-classifying. Test-run entries use `[kind:test]`.

### 11.5.1 Log Levels, Rotation, and the Central App Log

The per-event format above (§11.5) applies to the equipment-, project-, and run-level `wizard.<hostname>.log` files. There is also a **central app log** for events that are not scoped to any single equipment, project, or run. See [[16_Logging|§16]] for the full logger architecture (the `logging/` package, debugging recipes, plugin worker logging, where-to-look quick reference); this subsection covers only the on-disk rotation policy and central-log location.

**Log levels.** Standard Python `logging` levels: `DEBUG`, `INFO`, `WARN`, `ERROR`. Threshold is configurable in `config.yaml` `logging.level` (default in [[09_Configuration_File|§9]]). Default for stderr (visible in the launcher console) is `WARN` regardless of the file-log threshold. `DEBUG` is recommended for support and reproduction work; production runs should sit at `INFO`.

**Rotation policy.**

- Equipment-, project-, and run-level `wizard.<hostname>.log` files are not rotated. They are bounded in practice: a per-run log is written once at creation; a per-project log accumulates one entry per run plus a few setup events; a per-equipment log accumulates entries proportional to the number of runs that have ever touched that equipment. Even at very high cadence (hundreds of runs per equipment per year) the file stays under a few MB.
- The **central app log** uses Python's `logging.handlers.RotatingFileHandler`: rotate when the active file exceeds `logging.central_log_max_mb`, keep `logging.central_log_keep` rotated files. Defaults declared in [[09_Configuration_File|§9]].

**Central app log location** (OS-appropriate state directory):

| OS | Path |
|---|---|
| macOS | `~/Library/Logs/exlab-wizard/app.log` |
| Windows | `%LOCALAPPDATA%\exlab-wizard\Logs\app.log` |
| Linux | `${XDG_STATE_HOME:-~/.local/state}/exlab-wizard/app.log` |

**What lives in the central log** (and not in per-equipment / per-run logs):

- App startup and shutdown.
- Plugin registry build and reload events.
- Validator background-audit ticks (one entry per 30-s pass with the finding count).
- NAS sync queue events that don't belong to a specific run (queue startup, retry-batch summaries, cleanup reaper passes).
- LIMS cache refresh outcomes.
- Settings-dialog mutations.
- Any `ERROR`-level event that lacks an equipment/project/run scope (e.g. config load failure, keyring access failure).

Per-equipment and per-run logs continue to receive their scoped events as documented in §11.5; the central log is additive, not a replacement. An event with equipment/run scope is written to **both** the scoped file and the central log if its level is `WARN` or higher; `INFO`-level scoped events stay only in the scoped file to keep the central log readable.

## 11.6 NAS Sync Behavior for Cache Files

All `.exlab-wizard` contents sync to NAS as part of the directory mirror, including `wizard.<hostname>.log` files. This means the full audit trail -- provenance, README inputs, and log history -- travels with the data and is accessible from NAS without requiring the originating machine.

## 11.7 Discovery and Validation Use Cases

The self-describing cache enables secondary tooling without a live database connection:

- **Orphan detection:** Walk the filesystem, read `creation.json` at each level, and identify project- and run-level directories without a `creation.json` (e.g. created outside the app, or DB write failed). v0.7 narrows the orphan rule to project- and run-level directories; equipment-level directories use `equipment.json` (registry record) rather than `creation.json` (provenance record), so they are not orphan candidates. An equipment directory on disk that is not registered in `config.yaml` is a *different* problem class — surfaced as a Settings warning rather than a Problems-tab finding. See [[08_Error_Handling_Principles#8.1.4 Orphan rule (soft tier)|§8.1.4]].
- **Template audit:** Query which template version was used across all runs under a project by reading each `creation.json`.
- **README regeneration:** If a README is accidentally deleted, `readme_fields.json` contains all inputs needed to regenerate it without re-running the creation flow.
- **Sync recovery:** After a NAS outage, any `creation.json` with `sync_status: "pending"` or `"failed"` can be used by a recovery script to retry only affected directories.
- **Multi-machine log aggregation:** Glob `wizard.*.log` at the equipment level to see activity from all machines that have ever operated on that equipment folder.
- **Experimental-only analysis gates:** Analysis pipelines can assert that the runs they process are experimental by reading `creation.json` and rejecting any where `run_kind != "experimental"`. This is a belt-and-braces check on top of the folder-level and leaf-prefix separation: directories under `TestRuns/` are skipped by default, leaf folders matching `TestRun_*` are skipped as a secondary check, and any stray run whose parent folder is not `TestRuns/` but whose `creation.json` still says `run_kind: "test"` (or whose leaf prefix is `TestRun_`) is flagged for operator review rather than processed.
- **Always-on Problems audit:** The validator engine (§11.8) consumes the cache to power the always-on Problems tab (User Interaction Spec §3.8). Every directory in the managed tree is walked and `creation.json` is consulted to compute mode-prefix mismatches ([[08_Error_Handling_Principles#8.1.3 Mode-prefix mismatch rule (hard tier)|§8.1.3]]) and to identify orphans ([[08_Error_Handling_Principles#8.1.4 Orphan rule (soft tier)|§8.1.4]]) and missing-required-field findings ([[08_Error_Handling_Principles#8.1.5 Missing-required-field rule (soft tier)|§8.1.5]]). The engine's outputs gate NAS sync via the Pre-Sync Gate ([[07_Sync_and_Database_Integration#7.3 Pre-Sync Gate|§7.3]]).

## 11.8 Validator Engine and Problem Query Contract

The validator engine is the single component that implements the rules in [[08_Error_Handling_Principles#8.1 Path Validation Rules|§8.1]]. It runs in two modes against the same rule set, so a finding in one mode is reproducible in the other.

**Creation-time mode.** Inputs: a resolved destination path, a resolved variable map, a list of files about to be written (with their post-render content for text files). Output: `pass` or `fail` plus a list of findings. On `fail`, the controller raises a structured validation error and aborts before any filesystem writes ([[08_Error_Handling_Principles|§8]] bullet "Validation"). This mode does not touch the disk.

**Audit mode.** Inputs: a directory subtree under the managed `local_root` (and, when orchestrator mode is on, the `staging_root`). Output: a list of findings, one per problem instance. This mode walks the disk; it reads `.exlab-wizard/creation.json` for each directory it visits but does not read large data files. Text-file content scanning (for [[08_Error_Handling_Principles#8.1.1 Unresolved-placeholder rule (hard tier)|§8.1.1]]'s leftover Jinja markers) is bounded by `config.yaml` `validator.content_scan_max_mib` and `validator.content_scan_extensions` (defaults defined in [[09_Configuration_File|§9]] — the canonical source); files outside those limits are skipped. Binary files are always skipped.

**Finding shape.** Every finding emitted by either mode has the same JSON shape:

```json
{
  "rule": "unresolved_placeholder_token",
  "tier": "hard",
  "run_path": "/data/lab/CONFOCAL_01/PROJ-0042/Run_<run_date>",
  "offending_path": "/data/lab/CONFOCAL_01/PROJ-0042/Run_<run_date>",
  "offending_kind": "directory_segment",
  "matched_token": "<run_date>",
  "rule_detail": "Angle-bracket identifier token <run_date> survived templating; this segment was not rendered.",
  "synced_under_prior_policy": false,
  "override_active": false
}
```

`offending_kind` is one of `directory_segment`, `file_name`, or `file_content`. `run_path` is the run-level directory ancestor (or project/equipment level for orphans at those levels). `synced_under_prior_policy` is set to `true` when audit mode finds a hard-tier finding on a run whose `creation.json` `sync_status` is already `"synced"` ([[07_Sync_and_Database_Integration#7.3 Pre-Sync Gate|§7.3]]). `override_active` is set to `true` when the run's `validation_overrides` contains a non-revoked entry whose `problem_class` matches `rule`.

**Problem query contract for the GUI.** The frontend's always-on Problems tab calls a single backend method `query_problems(scope)` where `scope` is one of `equipment_id` (audit one equipment subtree), `project_path` (audit one `<equipment>/<project>` subtree), or `all` (audit every configured equipment plus staging). The return value is the list of findings as defined above, sorted by `tier` (hard first), then by `run_path`. The query is read-only: it does not mutate `creation.json`, does not write log entries, and does not initiate sync. The GUI's separate per-row actions (mark-as-known, override) call dedicated mutation endpoints.

**Background-refresh integration.** The frontend's 30-second background refresh (Frontend Spec §3.3) calls `query_problems("all")` and diffs against the previous result so the Problems-tab badge count stays current without re-walking the whole tree on every keystroke. The engine is responsible for being cheap enough to run at this cadence on a typical lab tree (tens of thousands of files); the audit-mode walk is bounded by `.exlab-wizard` cache reads, not by data-file content scans (only rendered text files under the size cap are scanned).

**Determinism.** Given identical inputs (path layout, file contents, `creation.json` payloads, **and** `config.yaml` `validator.*` settings), both modes produce byte-identical finding lists. This is a testability requirement: it is what lets the same fixture exercise the creation-time block and the audit-mode surface in unit tests. The dependency on `validator.*` settings is the trade-off for OQ #14's resolution in favor of operator-configurability; labs that change the size cap or extension list will see a one-time finding-set delta, but the algorithm itself remains deterministic at any given configuration.

## 11.9 Schema Versioning and Migration Policy

The on-disk schemas (`creation.json`, `readme_fields.json`, `ingest.json`, `equipment.json`, `test_runs.json`) carry an explicit `schema_version` field. The policy below is the contract every reader and writer follows.

### 11.9.1 Versioning rules

- Schemas use a `MAJOR.MINOR` versioning scheme.
- **Minor bumps** (e.g. `1.5 → 1.6`) are additive: new optional fields, new enum values, new optional sub-blocks. Every existing reader continues to work by ignoring unknown fields.
- **Major bumps** (e.g. `1.x → 2.0`) are breaking: removed fields, renamed fields, type changes, semantic changes. Major bumps will be rare and are explicitly out of scope for v1.

### 11.9.2 Reader policy

A reader at version `R` MUST:

1. Read any file at version `R.x` for any `x ≤ R.minor` (older minor) by treating the schema as the older one and using the documented defaults for fields that didn't exist in the older minor (see each schema's history table).
2. Read any file at version `R.y` for any `y > R.minor` (newer minor) by parsing only the fields known at `R.minor` and ignoring unknown fields. The reader MUST NOT fail because the writer wrote a newer minor.
3. **Refuse** any file at version `M.x` where `M ≠ R.major`. Surface a structured error (`code: "schema_major_mismatch"`, `field: "schema_version"`, `expected_major: R.major`, `found: "M.x"`). No silent partial-parse across major boundaries.

For the `creation.json` schema specifically, the documented defaults for backward compat are in [[#11.3 `creation.json` Schema|§11.3]]'s history table — e.g. `run_kind` defaults to `"experimental"` for files predating 1.1, `validation_overrides` defaults to `[]` for files predating 1.2, etc.

### 11.9.3 Writer policy

A writer at version `R` MUST:

1. Always write the **current** version (`R.minor`) for any new file. There is no "downgrade write" mode.
2. When mutating an existing file (e.g. updating `sync_status` in `creation.json`), preserve any unknown fields the reader didn't recognize. Treat the file as a JSON object: parse, mutate the known fields, re-serialize the entire object including unknowns. This protects forward-compat: a v0.7 writer mutating a file written by a v0.8 writer doesn't lose v0.8 fields.
3. When the schema version of a file on disk is older than the writer's version, the writer's mutation **bumps** the version to the writer's current version on the next write. Old files are silently upgraded as a side effect of routine activity. There is no batch migration tool in v1.

### 11.9.4 Cross-major migration (v1 → v2)

Out of scope for v1. When v2 lands and introduces a major schema change, the policy will be:

- v2 reader cannot read v1 files directly (per §11.9.2 rule 3).
- An explicit migration tool (`exlab-wizard migrate <root>`) walks the configured roots and rewrites old-major files to the new major.
- The migration tool is opt-in and one-shot; running v2 against unmigrated v1 data shows a Problems-tab finding directing the operator to run the migration.

### 11.9.5 Directory-layout migration (v0.5 → v0.6)

A separate concern from schema migration. The v0.5 layout `<project>/<equipment>/Run_<DATE>/` doesn't match the v0.6 equipment-first layout `<equipment>/<project>/Run_<DATE>/`. v1 ships with no migration tool for this; legacy v0.5 trees are read-only via the orphan rule (Open Question #12 in the parent design spec, deferred for v1). When migration tooling is built, it will live alongside the cross-major migration tool above.

### 11.9.6 What about `config.yaml`?

`config.yaml` doesn't carry a `schema_version` field today; it's a free-form Pydantic model. The same compatibility principles apply at the `pydantic.BaseModel` level: new optional fields are additive; new required fields constitute a breaking change requiring an explicit migration prompt at startup. The launcher refuses to start with an unmigrated config and prints the specific upgrade instruction. This will be revisited if `config.yaml` schema evolution proves frequent enough to need explicit versioning.
