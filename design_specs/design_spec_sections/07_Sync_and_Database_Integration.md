# 7. Sync and Database Integration

Parent: [[ExLab-Wizard_Design_Spec]]

---

## 7.1 NAS Sync (Internal Module)

The NAS sync subsystem is an **internal module of ExLab-Wizard**, not a separate process, daemon, service, or binary. It lives inside the same FastAPI app process as the rest of the backend; the previous standalone "NASSync" tool of the same name (now deprecated) is being replaced by this in-process module. The module is invoked by the `CreationController` at the `SYNC_QUEUED` state ([[04_Backend_Architecture#4.7 Creation-Session State Machine|§4.7]]) and runs background tasks for the duration of the server's lifetime.

In subsequent text the module is referred to as the **NAS sync module**, **NASSyncClient** (the public class name; see [[04_Backend_Architecture#4.4.6 NASSyncClient and LIMSClient|§4.4.6]]), or **the sync queue** depending on which aspect is in focus. None of these names imply a separate process.

### 7.1.1 Component model

All four components below are Python objects inside the FastAPI app process. They communicate via in-process method calls and asyncio queues; there is no IPC.

```
┌──────────────────────────────────────────────────────────────────┐
│  exlab-wizard FastAPI app process                                │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  NAS sync module (NASSyncClient + workers)                  │ │
│  │                                                             │ │
│  │  ┌──────────────────────────┐  ┌────────────────────┐       │ │
│  │  │ Durable Job Queue        │  │ Transport Drivers  │       │ │
│  │  │ (SQLite at               │  │  - rclone          │       │ │
│  │  │  {state_dir}/            │  │  - rsync-over-ssh  │       │ │
│  │  │  sync_queue.db)          │  └────────────────────┘       │ │
│  │  └──────────────────────────┘                               │ │
│  │  ┌──────────────────────────┐  ┌────────────────────┐       │ │
│  │  │ Verifier (SHA-256)       │  │ Cleanup Reaper     │       │ │
│  │  └──────────────────────────┘  └────────────────────┘       │ │
│  └─────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

The queue's SQLite file is durable so a server restart does not lose pending or in-flight syncs; the queue itself is just a table the in-process module reads and writes. The transport driver is selected per-equipment from `config.yaml` `equipment.transport.type` ([[09_Configuration_File|§9]]); valid values for v1 are `"rclone"` and `"rsync_ssh"`. The previous `"smb_mount"` and `"file_transfer"` transport types from the orchestrator topology ([[13_Equipment_to_Orchestrator_Data_Flow|§13]]) describe how data lands in *staging*; the NAS sync module's transport choice is independent and describes the staging-or-local-to-NAS hop.

When the rclone or rsync transport drivers shell out to the upstream binary, that subprocess is a child of the app process — also not a separate "service." It runs for the duration of one transfer and exits.

### 7.1.2 Job lifecycle

A NASSync job is one row in the SQLite queue with a stable `job_id` and the following state machine:

```
QUEUED → RUNNING → AWAITING_VERIFY → VERIFIED → CLEANUP_ELIGIBLE → CLEANED
   │         │            │              │              │             │
   └─ failed ┴─ failed ────┴─ failed ─────┴─ failed (re-verify) ──────-┘
```

| State | Meaning | Transition trigger |
|---|---|---|
| `QUEUED` | Enqueued by `CreationController.enqueue()`; not yet picked up. | The next free worker slot picks it up. |
| `RUNNING` | Transport is actively pushing files. | Transport reports completion (success or error). |
| `AWAITING_VERIFY` | Files transferred; not yet hash-verified. | Verifier scheduled. |
| `VERIFIED` | Local hash matches remote hash for every file in the job's manifest. | Cleanup interlock policy (§7.1.6). |
| `CLEANUP_ELIGIBLE` | All preconditions for local deletion are met. | Cleanup reaper deletes local copy. |
| `CLEANED` | Local copy removed; only NAS copy remains. | Terminal. |
| `FAILED` | Any prior step failed beyond retry policy. | Manual operator action via Problems-tab equivalent or restart. |

Job rows persist across restarts. On startup, NASSync requeues any `RUNNING` or `AWAITING_VERIFY` jobs (treating them as `QUEUED` and `VERIFIED → AWAITING_VERIFY` respectively, since transport may have completed but verification didn't run).

### 7.1.3 Transport drivers

Each driver is a thin wrapper around the upstream tool:

**rclone driver.** Shells out to `rclone copy --checksum <local_path> <remote>:<nas_path>` with `--transfers <N>` and `--bwlimit <K>` driven by config (§7.1.7). The remote name is configured per-equipment as `equipment.transport.rclone_remote` (e.g. `"lab-nas"`) and resolved against the operator's `rclone.conf` (default `~/.config/rclone/rclone.conf`). NASSync does not write `rclone.conf`; it expects IT to provision it once per machine. Credentials referenced by `rclone.conf` (S3 keys, SMB passwords, etc.) are managed by rclone and outside our keyring scope.

**rsync-over-SSH driver.** Shells out to `rsync -a --checksum --partial -e "ssh -i <key_path>" <local_path>/ <user>@<host>:<nas_path>/` with optional `--bwlimit=<K>`. The SSH key path is `equipment.transport.ssh_key_path` (default `~/.ssh/id_ed25519`); the `<user>@<host>` is `equipment.transport.ssh_target`. Authentication is **key-based only**; password authentication is rejected by config validation. The SSH key file lives under standard SSH file permissions; no app-managed credential is stored.

Both drivers stream stderr/stdout into the equipment-level `wizard.<hostname>.log` with structured prefixes so transfer failures are diagnosable from the cache log alone. Transfer progress (bytes-per-second, current file, ETA) is published to the controller's WebSocket via `phase: "queueing_nas_sync"` progress frames.

### 7.1.4 Hash verification

After the transport reports success the job moves to `AWAITING_VERIFY`. The verifier:

1. Walks the local subtree, computing SHA-256 for every file.
2. Walks the remote subtree (via the same transport: `rclone hashsum sha256` for rclone, `ssh <target> "find ... -exec sha256sum {} +"` for rsync). For rsync targets where invoking remote shell commands is restricted, the verifier falls back to `rclone`-style streaming download-and-hash bounded by `verify.max_stream_bytes` (configurable, default 1 GiB per file; oversize files emit a `verify_skipped_oversize` finding into the job log and require manual resolution).
3. Compares both hash sets pairwise. Mismatch on any file marks the job `FAILED` with the offending paths logged.
4. Writes the hash manifest into the run's `.exlab-wizard/checksums.sha256` (already referenced from `ingest.json` schema in §13.4) so a later out-of-band verification can re-confirm.

A successful verifier run transitions the job to `VERIFIED` and updates `creation.json` `sync_status` to `"synced"` via `CacheWriter.update_creation_atomic`. (No LIMS write per run in v1; the LIMS-side mirror returns in v1.x — see [[#7.2 LIMS Integration|§7.2.7]].)

### 7.1.5 Retry policy

| Failure mode | Retry behavior |
|---|---|
| Transport network error (timeout, ECONNRESET, transient SSH failure) | Exponential backoff: 30 s, 2 m, 8 m, 30 m, 2 h. After 5 failures, mark `FAILED`. |
| Transport authentication failure | No retry. Mark `FAILED` immediately; this is a configuration problem, not a transient one. |
| Hash verification mismatch | Single retry of transport phase (a partial transfer), then hash again. If the second pass also mismatches, mark `FAILED`. |
| Local file vanished between transport and verify | Mark `FAILED` with `local_file_vanished` reason; this is a different problem class than a hash mismatch. |

`FAILED` jobs are surfaced in the Problems tab as a new finding class (`nas_sync_failed`, soft tier — they do not gate sync because they ARE the sync result; they require operator attention but are not validation problems in the §8 sense). The Problems-tab row exposes a "Retry" action that re-enqueues the job at `QUEUED`.

### 7.1.6 Cleanup safety interlocks

The cleanup reaper deletes local files **only** when all of the following hold for a job (knob defaults in [[09_Configuration_File|§9]] `nas_cleanup` block):

1. `VERIFIED` status reached at least `nas_cleanup.min_verify_passes` times — i.e., the verifier has run that many times on different invocations and all confirmed the hash. Subsequent passes typically run on the next scheduled audit.
2. At least `nas_cleanup.min_age_hours` since the most recent `VERIFIED` transition. This is the "let it sit" buffer; it gives the operator time to notice unexpected loss before local copies disappear.
3. The remote NAS path is reachable at the moment of deletion (a final `stat` of the remote root succeeds). If it isn't, the job stays `CLEANUP_ELIGIBLE` and is retried on the next reaper pass.
4. No active `validation_overrides` revocation has been written for this run since the last `VERIFIED` transition (a revoked override re-blocks sync — we don't want to delete locally if the run is now blocked).

Cleanup is **always operator-overridable**: setting `nas_cleanup.enabled: false` ([[09_Configuration_File|§9]]) disables automatic cleanup entirely. With cleanup disabled, jobs accumulate in `CLEANUP_ELIGIBLE` and the operator deletes manually via the Problems-tab equivalent. This is the bias-toward-safety default for new installations; labs with confidence in their setup flip it on.

The cleanup reaper logs every deletion to the equipment-level `wizard.<hostname>.log` with file count, byte total, and the verification chain that authorized the deletion.

### 7.1.7 Bandwidth limiting

Optional, per-equipment, in `config.yaml`:

```yaml
equipment:
  - id: "CONFOCAL_01"
    transport:
      type: "rclone"
      rclone_remote: "lab-nas"
      bandwidth:
        upload_mbps: 50           # null or absent disables limiting
        schedule:                  # optional; if set, upload_mbps applies during the listed windows only
          - { days: ["mon", "tue", "wed", "thu", "fri"], from: "08:00", to: "18:00" }
```

`upload_mbps` translates to `--bwlimit <K>` for rclone (where K = `upload_mbps * 1024 / 8` KiB/s) and `--bwlimit=<K>` for rsync. Outside the configured schedule windows (or when no schedule is set and no `upload_mbps`), the transport runs unthrottled. The schedule is evaluated in the workstation's local time zone.

This addresses the acquisition-machine network-sharing concern: instrument control planes and acquisition data often share a NIC, and a saturated upstream during acquisition is a data-integrity risk. Bandwidth limiting + schedule lets the lab cap NASSync to off-peak hours.

### 7.1.8 Credential storage

NASSync credential storage is governed by §7.4 (the unified keyring model). Briefly: rsync uses SSH-key auth with no app-managed credential; rclone delegates to its own `rclone.conf`. The only NASSync-managed credential is the rare case of HTTP basic auth against an SMB-via-WebDAV endpoint, which is stored under keyring service `exlab-wizard` username `nas:<equipment_id>`.

### 7.1.9 What NASSync does not do

- It does not roll back local creation on sync failure. The local directory remains; the operator decides via the Problems tab.
- It does not handle the orchestrator staging-to-NAS hop *as a special case*; the staging directory is just the source path for the same NASSync queue. See [[13_Equipment_to_Orchestrator_Data_Flow|§13]] for staging semantics.
- It does not initiate a re-sync when a previously-synced run's local copy changes. Local mutations after `CLEANED` are operator-introduced and outside the app's contract; the spec assumes synced runs are read-only locally.
- It does not auto-discover the NAS topology. The remote root is set per-equipment in `config.yaml`.

### 7.1.10 Cache file syncing and metadata-only retention on cleanup

`.exlab-wizard` cache contents (including `wizard.<hostname>.log` and `creation.json`) are part of the synced subtree; see [[11_Cache_Folders#11.6 NAS Sync Behavior for Cache Files|§11.6]]. They reach the NAS as part of the same job as the data files.

**Cleanup retention policy (committed in v0.7): metadata-only retention by default.** When the cleanup reaper transitions a job to `CLEANED`, the data files inside the run directory are deleted but the `.exlab-wizard/` subtree is **kept on disk** (KB-to-MB scale). The local browse view continues to show the run with a `cleaned` badge; provenance, logs, and override history remain accessible without mounting the NAS. The full data is recoverable from the NAS copy.

Operators who prefer full reclaim can set `nas_cleanup.retain_cache: false` in `config.yaml` ([[09_Configuration_File|§9]]); with that flag the entire run directory is deleted on cleanup and the local browse view drops the run from its listing. The flag is per-installation, not per-run.

(Earlier drafts of this section called metadata-only retention "tombstone retention." The word "tombstone" is reserved in v0.7 for the `validation_overrides` revocation mechanism in [[11_Cache_Folders#11.3 `creation.json` Schema|§11.3]] — a different concept. The cache-cleanup behavior is "metadata-only retention.")

## 7.2 LIMS Integration

**v0.7 status: project mapping committed (Mapping B); ExLab-Wizard is read-only against LIMS in v1; run-level LIMS integration deferred to v1.x.**

**Source repository:** [`gitlab.com/mcnaughtonadm/exlab`](https://gitlab.com/mcnaughtonadm/exlab) (OCaml/Dream backend, PostgreSQL via Caqti, vanilla JS frontend).

### 7.2.0 Empirical facts about the LIMS

Verified against the LIMS source and docs (`README.md`, `docs/data_structure.md`, `docs/curl_commands.md`, `src/server/*_routes.ml`, `src/storage/migrate.ml`, `src/core/types.ml`).

- **Auth is session-cookie.** Login: `POST /api/v1/login` with body `{email, password}`. Response sets a Dream session cookie. All `/api/v1/*` endpoints below `/login` and `/logout` are cookie-authenticated. There is no API key, bearer token, or basic-auth path in v1.
- **Resources:** projects, samples, strains, plates, wells, result-categories, result-definitions, results, products, external-db-definitions, strain-external-links, users, settings. **No `runs` table**, no `equipment` table, no `operator` / `objective` / `run_kind` / `is_test` / `orchestrator_host` columns anywhere.
- **`projects.metadata` is a JSONB column** (migration V3). Free-form per-project metadata can land here.
- **No UNIQUE constraint on project name.** UNIQUE only on `uid` (UUID) and `short_id` (PROJ-NNNN). `POST /api/v1/projects` is non-idempotent.
- **HTTP status codes used:** `400 / 401 / 403 / 404 / 500 / 204`. No `409`, no `429`, no `Retry-After`.
- **No SDK** in the repo. Only `docs/curl_commands.md` recipes and OCaml tests.

### 7.2.1 Project mapping (Mapping B, committed)

**One LIMS project = one ExLab project.** ExLab-Wizard is **read-only** against LIMS projects in v1: it never `POST`s new projects, never `PATCH`es existing ones. The LIMS owns project identity; ExLab-Wizard consumes it.

The "New Project" wizard's first step is a **LIMS-project picker** populated from `GET /api/v1/projects` (filtered to the operator's memberships). The operator selects an existing LIMS project; ExLab-Wizard binds to it via the LIMS project's `uid` (UUID) and uses `short_id` (e.g. `PROJ-0042`) as the on-disk path segment. UX details: [[ExLab-Wizard_Frontend_Spec#4. New Project Wizard|Frontend Spec §4]].

Operators without a LIMS project for the work they want to start go to the LIMS web UI, create the project there, return to the wizard, and click "Refresh." A "+ New in LIMS" button in the picker deep-links to the LIMS create-project page in the OS browser.

**Why Mapping B + read-only:** simpler than the alternatives across every dimension that matters for v1. No idempotency dance, no LIMS-write auth scope, no race conditions on parallel project creation, no name-collision handling. The LIMS becomes a project-identity registry that ExLab-Wizard reads from; `creation.json` and the on-disk cache remain authoritative for everything else.

### 7.2.2 Run-level LIMS integration (deferred to v1.x)

Run creation in ExLab-Wizard is fully local in v1. Runs are not written to LIMS — they live on disk under `<equipment>/<lims_short_id>/Run_<DATE>/` (path-segment convention specified in §3.1) and in `creation.json`, with full provenance via §11. The Pre-Sync Gate (§7.3), validator audit (§11.8), and Problems tab all operate on the on-disk record without consulting LIMS.

The cleaner long-term model — a `runs` table in LIMS with first-class endpoints — is a v1.x ask of the LIMS team (§7.2.6). When that ships, ExLab-Wizard will gain `LIMSClient.register_run()` and the §4.7 state machine regains a `LIMS_REGISTER` state. Until then, the wizard's success card carries an informational note: *"Run logged locally; LIMS run-level tracking is not yet available."*

The "logical record fields" table previously embedded in this section described the LIMS record we *would* write per run; it is preserved in §7.2.7 as a v1.x specification target.

### 7.2.3 `LIMSClient` interface (v1)

```python
class LIMSClient:
    async def login(self, email: str, password: str) -> None: ...   # establishes the cookie session
    async def list_projects(self) -> list[LIMSProject]: ...          # cached; see §7.2.4
    async def get_project(self, uid_or_short_id: str) -> LIMSProject | None: ...
    async def get_me(self) -> LIMSUser: ...
    async def health_check(self) -> HealthStatus: ...                # used by Settings "Test connection"
```

**No write methods in v1.** The `register` and `update_sync_status` methods anticipated in earlier drafts are removed.

`LIMSProject` carries the relevant fields we read from LIMS:

```python
@dataclass(frozen=True)
class LIMSProject:
    uid: str                # UUID, stable identity
    short_id: str           # PROJ-NNNN, used as the on-disk path segment
    name: str               # human-readable, may be edited in LIMS over time
    description: str | None
    status: Literal["Pending", "Active", "Completed", "Archived"]
    contact_name: str | None
    owner: str              # free-text, set by LIMS at creation
    metadata: dict[str, Any]   # JSONB blob; ExLab-Wizard does not mutate
    fetched_at: datetime    # cache freshness
```

`LIMSUser` mirrors the upstream `safe_user` shape:

```python
class LIMSUser:
    uid: str
    email: str
    role: str
```

**Wire format.** Real upstream (`gitlab.com/mcnaughtonadm/exlab`) wraps the project list in `{"data": [...], "count": N}` and serves the safe_user shape `{id, uid, email, role, created_at, updated_at}` from `GET /api/v1/me`; extra fields are dropped by msgspec because `LIMSUser` and `LIMSProject` set `forbid_unknown_fields=False`. The local offline-catalogue format (§7.2.7) is independent of the wire envelope and keeps its `{"projects": [...]}` shape.

The wire envelope is verified two ways:
- `tests/integration/test_lims_contract.py` decodes vendored snapshots in `tests/fixtures/lims/exlab_v1/` on every PR.
- `.github/workflows/lims-live.yml` clones upstream at HEAD, boots the bundled `deploy/docker-compose.local.yml` stack, and runs the LIMS integration + contract suites against it weekly, on every merge to `main`, and on PRs that touch the LIMS surface.

### 7.2.4 Project cache and offline behavior

The LIMS project list is cached locally in a SQLite file (`{xdg_cache_home}/exlab-wizard/lims_cache.db`). On startup the LIMSClient refreshes the cache if older than `lims.cache_ttl_hours` (default declared in [[09_Configuration_File|§9]]). On LIMS unreachability:

- The picker uses the cached list with a *"(stale, last refreshed: <when>)"* badge.
- The "Refresh" button retries on demand; on success, the cache and badge clear.
- The operator can create new ExLab projects entirely offline as long as the desired LIMS project is in the cache.

The cache also stores per-project metadata (name, owner, contact, status, short_id) so the browse view renders rich context without a live LIMS call. `get_project` and `list_projects` both consult the cache first and fall back to the network on miss / stale.

There is **no offline write queue** in v1, because there are no writes. (The earlier `lims/offline_queue.py` module is removed; see §4.3.)

### 7.2.5 Authentication (cookie session)

Cookie-session auth is the only path the LIMS supports in v1. `LIMSClient`:

- Holds an `httpx.Cookies` instance for the lifetime of the FastAPI app process.
- Calls `POST /api/v1/login` with the credentials retrieved from the OS keyring (§7.4) on first use, on a `401` from any subsequent call, and from the Settings "Test connection" action.
- A second consecutive `401` after a fresh login surfaces as a configuration error in Settings (likely a stale or wrong password).
- Stores the operator's email in `config.yaml` (`lims.email`); the password lives in the OS keyring under service `exlab-wizard` username `lims`. See §7.4.

If the LIMS team adds a long-lived API token endpoint (§7.2.6 ask #1), the cookie path is replaced by token-in-`Authorization`-header. This is a small migration: the `LIMSClient.login` method is replaced with a `LIMSClient.authenticate_token`, and the keyring stores the token instead of `(email, password)`.

### 7.2.6 Asks for the LIMS team (deferred)

Improvements that would simplify the integration over time. None of these are v1 commitments; they are the conversation-starter list for the LIMS team.

1. **Long-lived API token auth path.** Removes the cookie-jar logic and the need to store the operator's password.
2. **First-class `runs` resource.** Gates v1.x run-level integration. See §7.2.7 for the proposed schema.
3. **Read-only service-account credentials.** Lets us avoid storing operator-personal credentials per workstation.
4. **`equipment` resource.** Removes the equipment-id-as-string convention.
5. **Idempotent project creation** (only relevant if v1.x re-introduces project writes).
6. **`Retry-After` header on `503`.** Lets the (future) offline write queue back off intelligently.

### 7.2.7 v1.x run-level record (specification target)

When the LIMS team ships ask #2 (a `runs` resource), ExLab-Wizard will start writing one record per run. The logical fields below describe what that record must carry to satisfy spec queries; the wire format will be filled in once the LIMS endpoint exists.

| Field | Source |
|---|---|
| `lims_project_uid` | The LIMS project the run was created under (Mapping B) |
| `equipment_id` | Operator selection at run-creation time |
| `label` | Mandatory core field (User Interaction Spec §2) |
| `operator` | Mandatory core field |
| `objective` | Mandatory core field |
| `run_date` | Auto-filled ISO 8601 timestamp |
| `template_name` | Selected template manifest name |
| `template_version` | From `_exlab_version` in `copier.yml` |
| `local_path` | Resolved absolute path |
| `nas_path` | Resolved NAS path |
| `created_by` | OS username; distinct from `operator` |
| `created_at` | UTC timestamp |
| `sync_status` | `pending` / `synced` / `failed` / `blocked_by_validation` |
| `readme_path` | Relative path to the generated README |
| `orchestrator_host` | Nullable; `<label>/<hostname>` in orchestrator mode |
| `ingest_state` | Nullable; mirrors §13.3 lifecycle |
| `staging_cleared_at` | Nullable UTC timestamp |
| `run_kind` | `"experimental"` or `"test"` (authoritative; `is_test` is derived) |

Notes:
- `run_kind` is authoritative; the prior draft's `is_test` boolean is a derived view (`is_test = (run_kind == "test")`) and not stored separately. Resolves the audit's Scanner #5 finding for the v1.x run record.
- `label`, `operator`, and `objective` should be indexed if the LIMS supports per-column indexes on the `runs` table.
- v1 stores the same fields locally in `creation.json` (§11.3) and in the README front matter (§10.7); v1.x mirrors them to LIMS.

### 7.2.8 What ExLab-Wizard does not write to LIMS in v1

For the avoidance of doubt:

- **Projects** — operators create via the LIMS web UI; ExLab-Wizard reads only.
- **Project metadata updates** — none. `objective` and other run-level fields stay local.
- **Runs** — deferred to v1.x.
- **Samples, plates, wells, results, strains, products** — outside the spec's scope. Operators interact with these via the LIMS web UI.

The full v1 LIMS write surface is the empty set.

### 7.2.9 Offline Catalogue (NAS-shared LIMS project list)

When `lims.offline_catalogue_path` (§9) is set, ExLab-Wizard treats a JSON file at that path as a fallback project source for workstations that cannot reach the LIMS API directly. This decouples LIMS reachability from the workstations that need project metadata, so an offline acquisition machine can still create projects against a known LIMS project list maintained by another connected workstation.

The catalogue is **opt-in**: workstations without `lims.offline_catalogue_path` set ignore the feature entirely. The catalogue does not replace the local SQLite cache (§7.2.4) — it is consulted as a strict fallback when the local cache is empty AND the LIMS API is unreachable.

#### 7.2.9.1 File location and format

`lims.offline_catalogue_path` is an absolute path to a JSON file on a shared NAS or any path reachable from the workstation. Typical placement: `<nas_root>/.exlab-wizard/lims-catalogue.json`.

The file is a single JSON document:

```json
{
  "schema_version": "1.0",
  "produced_by": "LAB_STATION_01",
  "produced_at": "2026-05-05T14:23:00Z",
  "lims_endpoint": "https://lims.lab.example/api/v1",
  "projects": [
    {
      "uid": "...",
      "short_id": "PROJ-0042",
      "name": "Cortex Q3 Pilot",
      "status": "active",
      "owner": "...",
      "contact": "...",
      "members": ["..."]
    }
  ]
}
```

The `projects` array carries the same per-project fields the local SQLite cache (§7.2.4) holds. The local cache is the source of truth for the producer; the catalogue is its on-disk JSON projection. `produced_by` is the producer workstation's `orchestrator.label` if set, otherwise its hostname. `lims_endpoint` records which LIMS instance the catalogue describes (used by the consumer to detect cross-lab misconfiguration; see §7.2.9.4).

#### 7.2.9.2 Producer behavior

A workstation with both a working LIMS connection AND `lims.offline_catalogue_path` set writes the catalogue on every successful LIMS refresh:

1. Serialize the current local cache to the JSON shape above.
2. Atomically write to the configured path: write to `<path>.tmp.<pid>`, fsync, then rename to `<path>` (atomic on POSIX; on Windows uses `MoveFileEx` with `MOVEFILE_REPLACE_EXISTING`).
3. On write failure (path not writable, disk full, NAS unreachable), log a warning at WARN and continue. Catalogue write is best-effort and never affects local cache state or LIMS request flow.

The atomic-write protocol guarantees readers always see either the previous catalogue or the new one — never a partial write. Two producers writing concurrently is benign: each rename is atomic; the last writer wins; no producer ever observes a corrupted catalogue.

A workstation that has the catalogue path set but no working LIMS connection (consumer-only) does not produce; it consumes only.

#### 7.2.9.3 Consumer behavior

A workstation reads the catalogue when ALL of:

1. `lims.offline_catalogue_path` is set.
2. The local SQLite cache contains zero project rows for the configured `lims.endpoint`.
3. The most recent attempt to reach the LIMS API failed (this attempt may be the current request or a recent one within `lims.cache_ttl_hours`).

Consumer steps:

1. Read the catalogue file. If parsing fails or `schema_version` is unrecognized, log a warning at WARN and treat the catalogue as absent.
2. Verify `lims_endpoint` matches the workstation's configured `lims.endpoint`. On mismatch, log at WARN and treat as absent (cross-lab misconfiguration guard; §7.2.9.4).
3. Project the `projects` array into the same in-memory shape `LIMSClient.list_projects()` would return.
4. Annotate each result with internal source metadata (`_source: "offline_catalogue"`, `_produced_by`, `_produced_at`) so the UI can render the catalogue badge (Frontend §4.1).

The catalogue is never written to the local SQLite cache. On the next successful LIMS reach, the local cache is populated from the live API and the catalogue is no longer consulted.

A workstation with BOTH a reachable LIMS connection AND `lims.offline_catalogue_path` set behaves as a producer (§7.2.9.2) AND consults the live LIMS API for reads. The catalogue is consulted on this workstation only when the live API later becomes unreachable AND the local cache is empty (e.g., after a cache wipe).

#### 7.2.9.4 Failure modes

| Condition | Behavior |
|---|---|
| Catalogue file does not exist | Treated as absent. No error surfaced. The picker's existing offline-with-empty-cache error renders (Frontend §4.1). |
| Catalogue unreadable (permissions) | Logged at WARN; treated as absent. |
| Catalogue parse error or unknown `schema_version` | Logged at WARN; treated as absent. |
| `lims_endpoint` mismatches `config.yaml` `lims.endpoint` | Logged at WARN; treated as absent. Guards against accidentally pointing at a catalogue from a different lab's LIMS. |
| Catalogue stale (`produced_at` arbitrarily old) | Consumed regardless. The UI surfaces `produced_at` in the picker badge so the operator can judge freshness. |
| Producer write failure (path read-only, NAS down) | Logged at WARN. Producer continues normally; consumers continue reading the previous catalogue version (or fall back to the offline-with-empty-cache error if no catalogue exists yet). |

#### 7.2.9.5 Security and trust

The catalogue contains project metadata (names, IDs, owners, member usernames). It carries no credentials and no secret content. It inherits whatever filesystem ACLs the NAS enforces on `lims.offline_catalogue_path`.

Signing or encryption of the catalogue is **out of scope for v1** and recorded as a future open question. If a lab requires cryptographic guarantees on catalogue authenticity (e.g., to defend against a compromised producer writing forged project metadata), file an enhancement request.

#### 7.2.9.6 Out of scope for v1

- Run-level catalogue support (deferred until v1.x adds run-level LIMS records; §7.2.7).
- Multiple catalogue paths per workstation (one path only).
- Conflict resolution between two producers writing different LIMS data; same-endpoint conflicts resolve last-writer-wins via the atomic rename, and the cross-endpoint case is handled by the `lims_endpoint` check (§7.2.9.4).
- Catalogue garbage collection or rotation; the catalogue is a single overwriting file, not an append log.

## 7.3 Pre-Sync Gate

The Pre-Sync Gate is the contract by which validator findings ([[08_Error_Handling_Principles#8.1 Path Validation Rules|§8.1]], [[11_Cache_Folders#11.8 Validator Engine and Problem Query Contract|§11.8]]) prevent NASSync from queueing a flagged run. It is the backend half of the user-facing Pre-Sync Gate contract (User Interaction Spec §7).

**Eligibility rule.** A run is eligible for NASSync queueing if and only if the validator engine reports zero hard-tier problems on that run, *or* every hard-tier problem on that run has a matching active entry in the run's `creation.json` `validation_overrides` array. NASSync MUST consult the validator before enqueueing. The check is local (no NAS round-trip) and runs against the run's `.exlab-wizard/creation.json` plus a directory walk of the run root.

**Hard-tier problems** are: unresolved-placeholder tokens ([[08_Error_Handling_Principles#8.1.1 Unresolved-placeholder rule (hard tier)|§8.1.1]]), illegal filesystem characters ([[08_Error_Handling_Principles#8.1.2 Illegal-filesystem-character rule (hard tier)|§8.1.2]]), and mode-prefix mismatches ([[08_Error_Handling_Principles#8.1.3 Mode-prefix mismatch rule (hard tier)|§8.1.3]]). **Soft-tier problems** (orphans, missing-required-field) do not gate sync.

**Override matching.** A `validation_overrides` entry matches a finding when its `problem_class` equals the finding's class. Overrides are scoped per run; an override on one run does not apply to another. Overrides are written by the override action (User Interaction Spec §7.3) and are append-only; revoking an override requires appending a **tombstone entry** that references the prior entry's `id` via `revokes`. Override entries may optionally carry an `expires_at` timestamp; once past, the override is no longer applied (no tombstone needed) and the gate re-engages automatically. The full matching algorithm — including how tombstones and expiry are applied — is specified in [[11_Cache_Folders#11.3 `creation.json` Schema|§11.3]] (see "Matching algorithm" under the `validation_overrides` description).

**Gate semantics on retroactive findings.** If the validator detects a hard-tier problem on a run whose `sync_status` is already `"synced"`, the gate does not initiate a re-sync or any kind of unsync. It records the finding in the Problems query results with a `synced_under_prior_policy: true` attribute. NASSync is not invoked. This keeps the gate forward-only: sync history is not rewritten by validator-policy changes.

**Sync status reporting.** When the NAS sync module rejects a run because of a hard-tier finding without an active override, it sets the run's `sync_status` in `creation.json` to `"blocked_by_validation"` (a value alongside `pending`/`synced`/`failed`). This lets the GUI distinguish "not yet attempted" from "attempted and gated"; blocked-run discovery is a cheap walk of `creation.json` files. (v1 has no per-run LIMS record; the LIMS-side mirror returns in v1.x — see [[#7.2 LIMS Integration|§7.2.7]].)

---

## 7.4 Credential Storage (OS Keyring)

Both NASSync (for the rare HTTP-basic-auth case) and `LIMSClient` (for the REST credential) store secrets in the **OS keyring** via the [`keyring`](https://pypi.org/project/keyring/) Python package. The keyring routes to:

| OS | Backend | What stores it |
|---|---|---|
| macOS | Keychain | `security` framework, encrypted at rest, scoped to OS user |
| Windows | Credential Manager | Win32 `CredRead/CredWrite`, encrypted at rest, scoped to OS user |
| Linux | Secret Service / libsecret | GNOME Keyring or KWallet daemon |

No secret material is ever written to `config.yaml`, the cache, or the wizard log. `config.yaml` holds **references** to keyring entries by service+username; the secrets themselves are retrieved at use time and held only in memory for the duration of the request.

### 7.4.1 Keyring entry naming convention

| Component | Service | Username | Secret value |
|---|---|---|---|
| LIMS | `exlab-wizard` | `lims` | The API credential as the LIMS expects it (token, password, etc.) |
| NASSync (per-equipment, only for HTTP-basic edge case) | `exlab-wizard` | `nas:<equipment_id>` | The HTTP-basic password |
| NASSync via rclone | (none) | (none) | rclone manages its own credentials in `rclone.conf`; we do not duplicate |
| NASSync via rsync-over-SSH | (none) | (none) | SSH key on disk under standard SSH file permissions |

Conventions:

- The `service` string is always the constant `"exlab-wizard"` so a single keyring search reveals every entry the app owns. (Useful for an operator wanting to clear all stored credentials when retiring a machine.)
- The `username` string is the namespaced identifier (`"lims"`, `"nas:<equipment_id>"`); this is not a user identity, just a key-within-service.

### 7.4.2 Settings UI for credentials

The Settings dialog manages credentials **without ever displaying stored values**. The full widget pattern (Not set / Set / Editing states; Set / Replace / Clear affordances; inline password input; Save-writes-immediately-to-keyring semantics) is specified in Frontend §7.4.1. The Test-connection feedback panel that accompanies most credential fields is specified in Frontend §7.4.2.

### 7.4.3 Credential lifecycle

- **Creation:** via Settings dialog, on first run or when the operator adds a new equipment.
- **Read:** at use time only; not cached in the config object. Each NASSync job and each LIMS write retrieves the credential from the keyring fresh.
- **Rotation:** the operator updates the keyring entry via Settings; in-flight jobs continue with the value they already retrieved (no mid-flight credential refresh).
- **Removal:** an explicit "Clear credential" action in Settings; also clearable via OS-native keyring tools.
- **Backup/restore:** out of scope. Credentials live in the keyring; the operator re-enters them on a new machine. This is the same model the rest of the OS uses for keychain entries.

### 7.4.4 Fallback when no keyring backend is available

OS keyring availability across our targets:

| OS | Backend | Status |
|---|---|---|
| Windows 10, 11, Server 2016+ | Windows Credential Manager (Win32 `CredRead`/`CredWrite`) | Always available; no configuration needed. |
| macOS 10.13+ | Keychain (`security` framework) | Always available; no configuration needed. |
| Linux (desktop) | Secret Service / libsecret (gnome-keyring or kwallet) | Available when the daemon is running (default on most desktop distros). |
| Linux (headless acquisition machine) | None by default | **Falls back to encrypted-at-rest, see below.** |

When `keyring.get_keyring()` returns a fail-class backend or `keyring.set_password` raises `NoKeyringError` at the startup smoke test, the app falls back to **encrypted-at-rest storage with a master passphrase**:

1. **Encrypted store location:** `{state_dir}/exlab-wizard/secrets.enc`. Format: a single Fernet-encrypted JSON object containing the same `{service, username} -> secret` map the keyring would hold.
2. **Encryption:** [`cryptography.fernet.Fernet`](https://cryptography.io/en/latest/fernet/) (AES-128-CBC + HMAC-SHA256) with a 32-byte key derived from the operator's master passphrase via Argon2id (memory-hard KDF; parameters: `time_cost=3`, `memory_cost=64 MiB`, `parallelism=4`). KDF implementation: [`argon2-cffi`](https://argon2-cffi.readthedocs.io/) — the conventional Python binding — which is committed in `pyproject.toml`. The Argon2 salt is stored in the same file alongside the ciphertext (the salt is not secret; the passphrase is).
3. **Passphrase prompt:** at launcher startup the app detects fallback mode and prints a passphrase prompt **before** starting uvicorn. The passphrase is read from the controlling terminal via `getpass.getpass()` and held in memory only for the server process's lifetime; restart re-prompts. There is no passphrase recovery — losing it means re-entering every credential through the Settings dialog.
4. **First-time setup:** the first run in fallback mode prompts the operator to create a new passphrase (with a confirmation re-entry) and writes an empty encrypted store. Subsequent runs prompt for the existing passphrase.
5. **Settings dialog UX:** identical to the keyring case — "Set/Not set" affordance, "Test connection" button. The only operator-visible difference is the launcher prompt at startup.
6. **No plaintext path.** Even in fallback mode, secrets are encrypted on disk. The launcher refuses to store credentials in plaintext under any flag.

The fallback is automatic: the operator does not pick keyring vs. encrypted-file; the app detects what works. The Settings dialog shows a banner when running in fallback mode (*"Storing credentials in encrypted file at `<path>`. OS keyring unavailable; install gnome-keyring or kwallet for transparent storage."*) so operators on accidentally-headless installs know they have an option to upgrade to native keyring.

A second fallback would be running with **no stored credentials at all** — viable only if every NAS transport on the machine uses SSH-key auth (no keyring needed; SSH key on disk is its own credential store) and the operator is willing to enter LIMS credentials interactively each session. This is a documented operator choice, not a separate code path.
