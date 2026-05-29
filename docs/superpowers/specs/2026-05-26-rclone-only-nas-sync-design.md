# Rclone-only NAS sync with password auth (SFTP + SMB)

**Status:** design approved, awaiting implementation plan.
**Author:** alex.
**Date:** 2026-05-26.
**Predecessor:** `2026-05-21-operator-free-per-file-nas-sync-design.md` (the
quiescence-driven sync model this spec inherits and extends).

## Context

ExLabWizard's NAS sync subsystem today supports two transports:

- `RsyncSshTransport` — rsync-over-SSH, key-only. `BatchMode=yes`
  prevents any password fallback; `_reject_password_field` enforces the
  policy at config-validation time.
- `RcloneTransport` — references a remote already configured in
  `~/.config/rclone/rclone.conf`.

Neither carries an operator-typed password. Two pressures motivate a
rewrite:

1. **IT policy now forbids SSH keys.** Lab machines cannot ship private
   keys. Equipment needs to authenticate to the NAS by username +
   password.
2. **Heterogeneous lab fleet.** Mac and Linux equipment reach the NAS
   over SFTP (the SSH path) with password auth. Windows equipment reach
   the NAS over an SMB share with username + password.

Rather than tack passwords onto the existing transports (which means
keeping two divergent code paths, two binary dependencies — rclone and
rsync — and two error-classification surfaces), this spec collapses
everything onto **rclone as the sole transport binary**. SFTP and SMB
remotes are configured inline via `RCLONE_CONFIG_<remote>_*` environment
variables at subprocess time; the per-equipment password is injected
from the OS keyring (the same `KeyringStore` and `credential_field`
machinery that already serves the LIMS password). The existing operator-
free, per-file quiescence-driven sync model from the predecessor spec
stays intact.

Nothing is deployed against the legacy transports yet, so no migration
is required — they are deleted outright.

## Goals

1. **Single transport binary** — rclone for both supported backends. No
   rsync, no ssh, no key files.
2. **Operator-typed password per equipment**, stored in the OS keyring
   under `keyring_nas_username(equipment.id)`, parallel to the LIMS
   credential.
3. **Hybrid integrity model** — full SHA verification for files freshly
   synced in the current batch; size+modtime trust for steady-state
   already-verified files. Achieved via `rclone check --download
   --files-from`.
4. **Durable local audit trail** — every per-file `sync_state.json`
   record gains `verified_sha256`, captured once at sync time from a
   local-disk SHA pass. Recovers the offline-audit affordance lost by
   dropping `checksums.sha256`.
5. **Settings-only credential entry** — registration completes without
   a password; the setup gate flags `INCOMPLETE_NO_NAS_CREDENTIAL`; the
   operator sets the password in Settings → NAS credentials before the
   first sync.

## Non-goals

- **Scheduled drift audit** (Slot B in brainstorming) — orthogonal to
  the migration; designed in a follow-up spec once the rclone-only
  baseline ships.
- **Backends beyond SFTP and SMB.** The model accommodates S3 / B2 /
  WebDAV by adding new flat config models, but they are not part of
  this spec.
- **Backward compatibility.** No config-file migration; nothing is
  deployed against the legacy transports.
- **Backwards-compat shims for the checksum manifest file.** Nothing
  outside the sync subsystem reads `<run>/.exlab-wizard/checksums.sha256`
  today; it is deleted along with the Python SHA pipeline.

## Architecture

```
+-------------------------------+
| QuiescenceSyncPoller          |  (unchanged — pure Python)
|   os.scandir + (size, mtime)  |
|   in-memory _snapshots        |
|   eligibility vs sync_state   |
+-----------+-------------------+
            | nas_sync.enqueue(run_path, files=[...])
            v
+-------------------------------+
| SyncQueue (SQLite, unchanged) |
|   QUEUED -> RUNNING -> ...    |
+-----------+-------------------+
            | worker dequeues
            v
+-------------------------------+      env: RCLONE_CONFIG_<rmt>_*
| NASSyncClient._drive_job      |--+   (assembled from EquipmentConfig
|                               |  |    + keyring password +
|  1. compute local SHA for     |  |    `rclone obscure -`)
|     each file in `files`      |  |
|  2. rclone copy --files-from  |---->  rclone (subprocess)
|  3. rclone check --download   |---->
|     --files-from --combined   |  |
|  4. parse =/*/+/-/! lines     |  |
|  5. credit '=' files into     |  |
|     sync_state.json with      |  |
|     synced_signature +        |  |
|     verified_sha256 +         |  |
|     verified_at               |  |
|  6. cleanup gate              |  |
+-------------------------------+
```

The poller's eligibility logic, the queue's state machine and retry
classes, `SyncStateWriter`'s file-locked atomic writes, and the cleanup-
gate semantics are inherited from the predecessor spec unchanged. Only
the transport driver, the verifier, and the credential surface change.

## Components

### 1. Config models

**Delete:**

- `RsyncSshTransport` (and the `_reject_password_field` validator)
- the existing rclone.conf-based `RcloneTransport`

**Add** in `src/exlab_wizard/config/models.py`:

```python
class RcloneSftpTransport(BaseModel):
    type: Literal["rclone_sftp"] = "rclone_sftp"
    host: str = Field(min_length=1)
    port: int = Field(default=22, ge=1, le=65535)
    user: str = Field(min_length=1)
    remote_path: str = Field(min_length=1)
    bandwidth: BandwidthConfig = Field(default_factory=BandwidthConfig)

class RcloneSmbTransport(BaseModel):
    type: Literal["rclone_smb"] = "rclone_smb"
    host: str = Field(min_length=1)
    share: str = Field(min_length=1)
    user: str = Field(min_length=1)
    domain: str = ""
    remote_path: str = ""
    bandwidth: BandwidthConfig = Field(default_factory=BandwidthConfig)

EquipmentTransport = Annotated[
    RcloneSftpTransport | RcloneSmbTransport,
    Field(discriminator="type"),
]

def transport_requires_keyring_password(transport: EquipmentTransport) -> bool:
    return True  # all current transports need a password
```

The predicate exists so the gate / UI / probe code doesn't grow
`isinstance` branches when a future hash-bearing backend (S3, B2) is
added.

### 2. Keyring + dependencies + setup gate

**Keyring** (`src/exlab_wizard/constants/keyring.py`):

- No change. `KEYRING_USERNAME_NAS_TEMPLATE = "nas:{equipment_id}"` and
  `keyring_nas_username(equipment_id)` already exist.

**Tray dependencies** (`src/exlab_wizard/tray/dependencies.py`):

- `_check_nas_passwords_present(keyring_store, config) -> set[str]` at
  boot. Iterates nas-mode equipment whose transport returns True from
  `transport_requires_keyring_password`; returns the subset whose
  keyring slot is populated. Hydrated into
  `deps.nas_password_present: set[str]`.
- `NASSyncClient.__init__` gains a `keyring_store` argument so the
  worker can fetch passwords at push time.

**Shared accessor** (`src/exlab_wizard/api/_dependencies.py`):

- `nas_password_present(deps, equipment_id: str) -> bool` — the
  repo-wide reader. Parallel to `lims_password_present`.

**Setup state** (`src/exlab_wizard/constants/enums.py`,
`src/exlab_wizard/paths.py`, `src/exlab_wizard/api/setup.py`):

- New `SetupState.INCOMPLETE_NO_NAS_CREDENTIAL`.
- `_nas_slot_satisfied(config, *, nas_password_present_for)` — every
  password-requiring nas-mode equipment must have its keyring entry.
- `_missing_nas_fields` yields one
  `{"field": "equipment.<id>.nas_password", "reason": "missing_in_keyring"}`
  per missing entry.
- `evaluate_setup_state` slots the check between
  `INCOMPLETE_NO_EQUIPMENT` and `INCOMPLETE_NO_LIMS`.
- `setup_state_next_action` maps the new state to
  `"open_settings_nas_credentials"`.
- The new state is a **hard block** (gates creation); left out of
  `is_creation_blocked`'s non-block tuple.

### 3. Rclone driver

**Replace** `src/exlab_wizard/sync/transports/rclone.py` entirely; delete
`rsync_ssh.py`. The driver exposes three methods plus a module-level
helper:

```python
class RcloneDriver:
    async def push(
        self,
        local: Path,
        remote: str,
        *,
        files_from: Path | None,
        bwlimit_kibps: int | None,
        env: dict[str, str],
    ) -> TransportResult: ...

    async def check(
        self,
        local: Path,
        remote: str,
        *,
        files_from: Path,
        env: dict[str, str],
    ) -> CheckResult: ...
        # rclone check --download --combined <tmp> --files-from <list>
        # parses =/*/+/-/! lines into CheckResult.

    async def about(self, remote: str, *, env: dict[str, str]) -> AboutResult: ...
        # rclone about <remote> --json
        # used as the equipment probe (auth + reachability test).

async def obscure(password: str) -> str: ...
    # rclone obscure - on stdin; AUTH-classified TransportError on failure.
```

`CheckResult` carries `equal: tuple[str, ...]`, `differ: tuple[str, ...]`,
`missing_on_dst: tuple[str, ...]`, `extra_on_dst: tuple[str, ...]`,
`errors: tuple[str, ...]` — derived from the `--combined` output.

**Subprocess helper** (`src/exlab_wizard/sync/transports/_run.py`):

- Extend `run_subprocess(cmd, *, env=None, mask_for_log=())`. Any env
  key in `mask_for_log` is redacted in the debug log line. Required for
  `RCLONE_CONFIG_<remote>_PASS` injection.

**Env construction** — a small module-level helper:

```python
def build_rclone_env(
    *,
    transport: EquipmentTransport,
    password_obscured: str,
    remote_name: str,
) -> dict[str, str]:
    """Build the RCLONE_CONFIG_<REMOTE>_* env dict for an inline backend."""
```

`remote_name` is e.g. `exlab_<equipment_id>`. For SFTP, sets `_TYPE=sftp`,
`_HOST`, `_PORT`, `_USER`, `_PASS=<obscured>`. For SMB, sets
`_TYPE=smb`, `_HOST`, `_USER`, `_PASS=<obscured>`,
`_DOMAIN` (when set). The `_PASS` key is added to `mask_for_log`.

### 4. NAS sync client

**Refactor `_drive_job`** in `src/exlab_wizard/sync/nas_client.py`:

1. Validate the local run exists; if not, terminal FAILED with
   `local_file_vanished` (unchanged).
2. Transition QUEUED → RUNNING (unchanged).
3. **Compute local SHA-256 for each file in `job.files`** (the new
   Slot A pass). Cached as `{rel: sha}` on the call stack for step 7.
4. Write the run-relative paths to a `--files-from` tempfile (unchanged).
5. Build the rclone env (`build_rclone_env`) from the equipment config,
   keyring password lookup, and `rclone obscure` round-trip. A missing
   keyring entry yields `TransportError(AUTH)` → terminal FAILED.
6. Call `rclone copy <local> <remote> --files-from <tmp> --bwlimit=K`
   with the env. Map exit code + stderr to `TransportErrorKind` exactly
   like today.
7. On push success, transition RUNNING → AWAITING_VERIFY.
8. Call `rclone check --download <local> <remote> --files-from <tmp>
   --combined <out>` with the env. Parse `--combined` lines.
9. **Per-file reconciliation**: for every file in `files` whose status
   in the combined output is `=`, call
   `sync_state_writer.upsert_file(run_path, rel,
   synced_signature=(size, mtime_ns), verified_sha256=<step-3 sha>,
   verified_at=<iso>)`. Per the predecessor spec this happens even
   when the batch otherwise fails — one bad file doesn't block
   crediting the good ones.
10. If every file in `files` got an `=`, transition to VERIFIED and
    mark the run's `creation.json` `sync_status=synced`. Otherwise,
    route through the `§7.1.5` retry classes (AUTH terminal, NETWORK /
    UNKNOWN backoff, HASH_MISMATCH single-retry-then-terminal) exactly
    as today.
11. `_maybe_cleanup` gates on the whole-run `sync_state.json` rollup
    being `SYNCED` (unchanged from the predecessor).

The two-phase "push then probe" shape stays. The probe is now `rclone
check --download` instead of `rclone hashsum sha256` + dict-compare,
but the surrounding state machine is identical.

### 5. Verifier collapse

**Delete** from `src/exlab_wizard/sync/verifier.py`:

- `compute_local_manifest`
- `verify_against_local`
- `verify_against_remote`
- `format_manifest`, `parse_manifest`, `_iter_files`, `_compute_sha256`,
  `_is_inside_cache_dir`
- `CHECKSUMS_RELATIVE` and the `.exlab-wizard/checksums.sha256` write

**Add** (or replace the file):

```python
class Verifier:
    def __init__(self, driver: RcloneDriver) -> None: ...

    async def verify(
        self,
        run_path: Path,
        remote: str,
        files_from: Path,
        env: dict[str, str],
    ) -> VerifyResult:
        """One rclone check --download call; parse --combined output."""
```

`VerifyResult` shrinks to `(ok, mismatched, missing, extra, errors,
error_kind)` — purely the parse of `rclone check --combined`. The
`manifest` field that previously carried the local SHA dict is
**removed**; the verifier never sees local SHAs. `_drive_job` owns the
marriage of "files with status `=` in `verify_result` → local SHA from
its own step 3 dict → `upsert_file`". This keeps the verifier a thin
adapter over rclone and the SHA-capture logic local to the one place
that has access to the fresh local read.

`force_verify(run_path)` is still exposed on `NASSyncClient` for the
Settings "verify integrity" action; internally it calls
`Verifier.verify` against the equipment's remote with a `files_from`
listing every tracked file in `sync_state.json`. It **reports only** —
it does NOT update `verified_sha256` (there is no fresh local SHA in
this code path; the existing `verified_sha256` is left as-is). The
caller surfaces the rclone-check status to the GUI: which files
matched, which mismatched, which are missing.

### 6. Wizard + Settings UI

**Add-Equipment wizard** (`src/exlab_wizard/ui/pages/wizard_equipment.py`,
`src/exlab_wizard/ui/equipment_form.py`):

- Sync-mode step's transport radio: two options.
  - "SFTP (password)" → `rclone_sftp`. Fields: host, port (default 22),
    user, remote_path.
  - "SMB share (password)" → `rclone_smb`. Fields: host, share, user,
    domain (optional), remote_path (optional).
- **No password field** in the wizard. Review step shows a small
  banner: "you'll set the NAS password in Settings before the first
  sync runs."
- `build_equipment_config` collapses to two branches matching the
  radio.
- `can_advance` validates the new required fields per branch.
- The wizard's old "Test connection" affordance is **removed** — the
  probe requires a keyring entry that doesn't exist yet at this point.

**Settings** (`src/exlab_wizard/ui/pages/settings.py`,
`src/exlab_wizard/ui/mount.py`):

- New "NAS credentials" subsection rendered below the LIMS credential
  drawer. For each nas-mode equipment whose transport requires a
  password, render:
  - A `credential_field(label=f"NAS password — {equipment.id}",
    data_testid=f"settings-nas-password-{equipment.id}", ...)` row.
  - A small "Test connection" button next to it that calls
    `POST /setup/test-equipment/{equipment_id}` (uses
    `rclone about` against the equipment's resolved transport + keyring
    password). **The existing body-based `POST /setup/test-equipment`
    endpoint** (which accepted a candidate `EquipmentConfig` for the
    wizard's pre-save probe) **is deleted** — no pre-save probe path
    exists anymore because credentials are post-registration.
- Hidden entirely when no equipment requires a password (keeps the
  section clean for hypothetical future password-less backends).
- New section in the left-rail nav (`settings-nav-nas-credentials`).
- `_nas_credential_handlers(deps, equipment_id)` in `mount.py` mirrors
  `_lims_credential_handlers` — saves to keyring, updates
  `deps.nas_password_present` set in place, shows a toast.

**Setup banner** (`src/exlab_wizard/ui/components/setup_banner.py`):

- New copy line for `INCOMPLETE_NO_NAS_CREDENTIAL` pointing the
  operator at Settings → NAS credentials.

### 7. Sync state schema

`src/exlab_wizard/cache/sync_state_schema.py` `FileSyncRecord` gains
one field:

```python
class FileSyncRecord(Struct):
    synced_signature: tuple[int, int] | None = None
    verified_at: str | None = None
    keep_local: bool = False
    verified_sha256: str | None = None  # Slot A: durable local-audit hash
```

`SyncStateWriter.upsert_file` gains a `verified_sha256: str | None = None`
kwarg. Backwards-compat reads tolerate the field's absence (default
`None`) so a partially-migrated dev state file still loads.

### 8. Error classification

| Kind | Trigger |
|---|---|
| `AUTH` | rclone stderr contains "authentication failed", "permission denied", "auth_error", "username/password", "not authorized", "NT_STATUS_LOGON_FAILURE", "NT_STATUS_ACCESS_DENIED"; OR `obscure` shell-out fails |
| `NETWORK` | rclone exit 5 / 6; stderr contains "i/o timeout", "connection refused", "no route to host", "network is unreachable" |
| `LOCAL_FILE_VANISHED` | stderr contains "no such file" with a path under the local run dir |
| `HASH_MISMATCH` | `*` line in `rclone check --combined` output |
| `UNKNOWN` | everything else; routed as NETWORK (existing behaviour) |

## Data flow (one sync cycle)

1. Operator drops a file into `<local_root>/<equipment_id>/<project>/Run_<date>/`.
2. `QuiescenceSyncPoller` observes the file's `(size, mtime_ns)` across
   two sweeps spanning ≥ `quiescence_minutes`; file is quiet. Its
   signature is absent in `sync_state.json` → eligible. Poller calls
   `nas_sync.enqueue(run_path, files=["relative/path"])`.
3. `SyncQueue` inserts a QUEUED row with `files=("relative/path",)`.
4. Worker dequeues; `_drive_job`:
   a. Computes SHA-256 of the local file (Slot A).
   b. Writes `--files-from` tempfile.
   c. Builds rclone env from `EquipmentConfig` + keyring password +
      `rclone obscure`.
   d. `rclone copy <local> <remote> --files-from <tmp>` → success.
   e. `rclone check --download <local> <remote> --files-from <tmp>
      --combined <out>` → parses `= relative/path`.
   f. `sync_state_writer.upsert_file(run, "relative/path",
      synced_signature=(size, mtime_ns),
      verified_sha256=<step a>, verified_at=<iso>)`.
   g. Transition to VERIFIED; mark `creation.json sync_status=synced`.
   h. `_maybe_cleanup`: every tracked file has `verified_at` → rollup
      SYNCED → cleanup runs (deletes local data; `keep_local` files
      survive; `sync_state.json` stamped `cleared_at`).
5. Next poll: file's local signature still matches the recorded
   `synced_signature` (still locally present until cleanup, or absent
   after cleanup with `tombstone` semantics) → poller skips. Steady-state.

## Error handling

The queue's retry classes and the predecessor spec's per-file
reconciliation are inherited:

- **AUTH** → terminal FAILED. A missing keyring entry produces this
  immediately so the worker doesn't retry forever waiting for the
  operator. GUI surfaces "auth" in `last_error`; the Settings credential
  row is the recovery path.
- **NETWORK / UNKNOWN** → exponential backoff.
- **HASH_MISMATCH** → single retry of the transport phase; second
  occurrence terminal.
- **LOCAL_FILE_VANISHED** → terminal FAILED.
- **Partial-failure batch** → every `=` file is credited into
  `sync_state.json`; the batch job's pass/fail decision is independent.

## Testing

- **Unit:**
  - `tests/unit/config/test_models.py` — discriminator round-trips for
    each new variant; required-field validation;
    `transport_requires_keyring_password` truth table.
  - `tests/unit/sync/transports/test_rclone.py` — fake-binary harness
    verifies `push`, `check`, `about`, `obscure` argv shapes;
    env-injection forwarding; masked-key log redaction.
  - `tests/unit/sync/test_verifier.py` — `--combined` parsing
    (mismatched / missing / extra / errors).
  - `tests/unit/sync/test_nas_client.py` — `_drive_job` reconciliation
    of per-file Slot A SHAs into `sync_state.json` on full + partial
    success.
  - `tests/unit/paths/test_setup_state.py` —
    `INCOMPLETE_NO_NAS_CREDENTIAL` resolution + clearing.
- **Integration:**
  - `tests/integration/test_nas_sync.py` rewritten against a localhost
    SFTP fixture (the rsync_ssh-flavoured tests are deleted, not
    ported). Drives push + verify + cleanup end-to-end.
- **E2E:**
  - `tests/e2e/test_flow_27_nas_credential_settings.py` — register
    SFTP-password equipment via wizard → assert
    `INCOMPLETE_NO_NAS_CREDENTIAL` → set password in Settings → assert
    `READY` → quiescence sweep → file lands on localhost SFTP → cleanup
    runs.
  - Updates to existing e2e suites where transport-radio fixtures
    reference the old types.

## Open follow-up: Slot B (drift audit)

A separate `SyncDriftAuditor` background task running on a slow
schedule (e.g. every 24h, configurable) would re-verify already-synced
runs against the NAS to catch silent NAS-side corruption (hardware
flips, ransomware, restore-from-backup munge). With Slot A in place,
the audit compares the stored `verified_sha256` against a fresh
`rclone hashsum sha256 --download` pull — no local SHA pass needed.
Designed in its own spec once the rclone-only baseline ships and we
have an operational sense of how often audit failures actually surface.

## Risks / notes

- **Cryptographic verify cost on SFTP** — today SFTP via the
  `ssh sha256sum` shell-out gets server-side hashing for free. After
  this change, every verify of a freshly-synced file pays a download
  round-trip (`rclone check --download`). Bandwidth scales with the
  volume of new files per sweep, not the volume of total NAS data, so
  the hybrid model keeps it bounded — but a sweep that crosses a "many
  large files just settled" boundary will be slow. Mitigation:
  bandwidth limits + bandwidth scheduling continue to apply.
- **`rclone obscure -` per attempt** — keyring stores cleartext; the
  obscure round-trip happens at push time. If profiling shows it
  matters, cache the obscured token on `deps` keyed by
  `(equipment_id, password_hash)` and invalidate on save / clear.
- **Subprocess env leakage** — `mask_for_log` covers the wizard's
  debug logger but obscured passwords are still in the rclone
  child's environment. Nothing the wizard can do about a
  process-listing leak short of switching to stdin-passed credentials,
  which rclone's CLI doesn't support for backend secrets.
- **Probe specificity** — `rclone about` proves auth and reachability
  but not write-access to `remote_path`. If we want write-test, the
  Settings "Test connection" can do an `rclone touch
  <remote>/.exlab-wizard-probe` followed by `rclone delete
  <remote>/.exlab-wizard-probe` — minimal extra wire cost, real
  guarantee. Decide during implementation.
- **rsync delta-xfer loss** — rclone copies whole files (modulo its
  `--partial` semantics). For multi-GB files on flaky links, an
  interrupted transfer restarts from zero. Acceptable for the size
  class of files this fleet currently produces.
- **Equipment-id renames** — keyring entries don't follow `config.yaml`
  edits. Renaming an equipment leaves the credential under the old
  username; the new id starts unset and the gate fires. Document this;
  a future "migrate credentials" command is one option.
- **rclone version pinning** — `obscure`, `lsjson`, and `check
  --combined` are stable; the SMB backend in particular has matured
  over the last few releases. Pin a minimum rclone version at boot and
  log clearly when an older binary is found.
