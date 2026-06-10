# rsync-over-ssh NAS transport

- **Date:** 2026-06-10
- **Status:** Design — decisions resolved + spec-review findings applied
  (2026-06-10), pending implementation plan
- **Builds on:**
  [`2026-05-28-rclone-conf-nas-sync-design.md`](2026-05-28-rclone-conf-nas-sync-design.md)
  (rclone stays the default transport; this spec adds a second transport, it
  does not supersede anything).

## Summary

The NAS sync subsystem shells out to exactly one binary today: rclone, via
`RcloneDriver` (`sync/transports/rclone.py`). The connection backend is chosen
by the named remote in the operator's `rclone.conf` — the app is
protocol-agnostic by design.

That design hits an IT wall on the compute cluster:

| Path | Lab acquisition PCs | Cluster nodes |
| --- | --- | --- |
| rclone → SMB share | ✅ allowed | ❌ outbound SMB blocked |
| rclone → SFTP | ❌ locked by IT on the Synology NAS | ❌ same |
| rsync over ssh | (not needed) | ✅ enabled |

This spec adds an **`rsync_ssh` transport** selected per instance by a new
`nas.transport` field. Lab PCs keep `rclone` (SMB) untouched; the cluster
instance sets `transport: rsync_ssh` and reaches the same
`<base_root>/<equipment_id>/<run>` layout over ssh. The four network
operations the sync subsystem needs — push, manifest listing, content verify,
connection probe — are all implemented from **rsync protocol primitives
only**: IT blocks every ssh command on the NAS except the rsync server
channel itself (rsync-over-ssh works by ssh-executing `rsync --server`, which
is exactly what IT allowlists — confirmed 2026-06-10). The design therefore
assumes **no remote command execution** (no `ssh host df`, `find`, or
`sha256sum`), no interactive ssh login, and no SFTP subsystem.

## Motivation / goals

- **Unblock the cluster instance.** The only NAS path IT permits from cluster
  nodes is rsync-over-ssh; today the app cannot use it at all.
- **Keep the lab PCs untouched.** rclone-over-SMB works there and stays the
  default; the transport choice is per-instance config, not a global change.
- **Preserve the integrity contract.** `VERIFIED` still means "present +
  size/mtime-reconciled on the remote"; local deletion is still gated by a
  content verify of the proof-required files. The rsync transport must offer
  a content-verify equivalent of `rclone check --download` (the
  `--checksum` dry-run, resolved OQ-1 — an accepted trust-posture change,
  see [Resolved decisions](#resolved-decisions)).
- **Preserve the credential invariant.** The 2026-05-28 migration established
  that the driver injects no credentials and never sees a password. ssh keeps
  that: key-based auth only (`BatchMode=yes`), identity file managed by the
  operator in `~/.ssh` (or pinned in config), no keyring entry, no password
  prompt path.

### Non-goals

- **Runtime failover** between transports. The blockage is static per machine;
  each instance picks one transport in config. (A future fallback design can
  layer on top of `_resolve_remote` if ever needed.)
- **rsync for the staging hop.** The orchestrator staging backend is dormant
  and hidden (see `2026-05-29-hide-orchestrator-staging-design.md`);
  `orchestrator.staging_remote` stays rclone-shaped. If staging is ever
  re-enabled on a machine that needs rsync, that is a separate spec.
- **Password ssh auth / ssh-agent orchestration.** Key-based only. An
  operator who needs an agent runs one themselves; the app just invokes ssh.
- **rsync on Windows acquisition machines.** rsync requires the binary on
  both ends plus a sane ssh; on Windows that means cwRsync/WSL pain. The
  cluster nodes are Linux; lab Windows PCs keep rclone. Documented assumption.
- **Parallel-stream parity.** rsync is single-stream; `nas.perf.transfers` /
  `checkers` are rclone-only dials and are ignored (not rejected) by the
  rsync driver.

## Decisions (from design discussion)

| Question | Decision |
| --- | --- |
| Transport selection granularity | **Per-instance** `nas.transport` selector (`rclone` default, `rsync_ssh`). No per-equipment branch, no failover. |
| Target-string shape | **Reuse the `<remote>:<path>` string.** `nas.remote = "user@host"` renders as `user@host:base/equip/run` — valid rsync syntax. `_target_for_equipment`, `_remote_subpath`, and the lsjson `strip_prefix` logic stay untouched. |
| Credentials | **ssh keys only**, `BatchMode=yes`; identity file path optionally pinned in config. No keyring, no password. Mirrors the rclone.conf invariant. |
| Remote command execution | **Assumed unavailable.** Every op is built from rsync protocol primitives (`--list-only`, dry-run itemize, transfer in either direction). |
| mtime preservation | Push always passes `-t` — the lsjson reconcile tolerance and `orchestrator/quiescence_poller.py` both depend on transports preserving source mtime. |
| Verify-before-delete fidelity | **Remote-side `--checksum` dry-run** (resolved OQ-1) — see [Resolved decisions](#resolved-decisions) for the accepted trust-posture change. |
| Manifest strategy | **Parse `--list-only`** into a true `RemoteManifest` (resolved OQ-2). |
| Host-key policy | **Pre-provision `known_hosts` via `ssh-keyscan`** (resolved OQ-3) — works with zero login permission because the host-key exchange precedes authentication/exec. |
| Config field shape | **Reuse `nas.remote` as `user@host`** + flat `ssh_port` / `ssh_identity_file` (resolved OQ-4). |
| Remote free-space probe | **Degraded by design** (resolved OQ-5): no remote exec exists, so Test-connection reports reachable/auth-ok only. |

## Config schema

### Unchanged (lab PC, rclone over SMB)

```yaml
nas:
  remote: "nas01"              # named remote in rclone.conf (type = smb)
  base_root: "/srv/lab"
  # transport: "rclone"        # new field, defaults to rclone — omitted on lab PCs
```

### New (cluster instance, rsync over ssh)

```yaml
nas:
  transport: "rsync_ssh"
  remote: "svc-sync@nas01.lab.example"   # user@host — doubles as the target prefix
  base_root: "/volume1/lab"              # absolute path on the NAS
  ssh_port: 22                           # optional, default 22
  ssh_identity_file: "~/.ssh/id_exlab"   # optional; blank = ssh default key discovery
  mtime_tolerance_s: 2                   # reused verbatim by the rsync reconcile
  bandwidth: { upload_mbps: null, schedule: [] }   # reused; maps to rsync --bwlimit
  perf: { transfers: 4, checkers: 8 }    # rclone-only; ignored by rsync_ssh (single stream)
```

- **New enum:** `SyncTransport` (`constants/enums.py`): `RCLONE = "rclone"`,
  `RSYNC_SSH = "rsync_ssh"`. (`tests/unit/constants/test_enum_literal_alignment.py`
  gains the member.)
- **`NasConfig` additions** (`config/models.py`): `transport: SyncTransport =
  SyncTransport.RCLONE`, `ssh_port: int = Field(default=22, ge=1, le=65535)`,
  `ssh_identity_file: str = ""`. A model validator rejects
  `transport: rsync_ssh` with an empty `remote` and rejects a `remote`
  lacking the `user@host` shape in rsync mode (resolved OQ-4).
- `rclone_config_path` is rclone-only and ignored in rsync mode (same
  ignore-don't-reject policy as `perf`).

## Architecture by surface

### 1. Transport protocol + shared factory — `sync/transports/__init__.py`

`RcloneDriver` is currently named concretely in three places:
`Verifier.__init__` (`sync/verifier.py:102`), `_build_driver` /
`_driver_for_equipment` (`sync/nas_client.py:132,729`), and two direct
constructions in `tray/dependencies.py` (`_snapshot_nas_remotes`,
the Test-connection probe).

- **Add a `NasTransportDriver` Protocol** to `sync/transports/__init__.py`
  with the four network ops the sync subsystem consumes:

  ```python
  class NasTransportDriver(Protocol):
      async def push(self, local: Path, remote: str, *,
                     bwlimit_kibps: int | None = None,
                     files_from: Path | None = None) -> TransportResult: ...
      async def check(self, local: Path, remote: str, *,
                      files_from: Path) -> CheckResult: ...
      async def lsjson_manifest(self, remote: str, *, strip_prefix: str) -> RemoteManifest: ...
      async def about(self, remote: str) -> AboutResult: ...
  ```

  (`listremotes` is **not** on the protocol — it is rclone.conf
  introspection, used only by the rclone-mode setup gate, and stays a
  concrete `RcloneDriver` method.)

  Note the protocol returns a parsed `RemoteManifest`, not raw lsjson text:
  the raw-JSON shape is rclone-specific, so parsing moves behind the driver
  boundary. `RcloneDriver` keeps its raw `lsjson()` method and gains a thin
  `lsjson_manifest()` that calls `parse_lsjson` — `nas_client._build_lsjson`
  slims down accordingly. `CheckResult` / `AboutResult` move (or are
  re-exported) from `rclone.py` into `transports/__init__.py` alongside
  `TransportResult`, since both drivers now produce them.

- **Add one shared factory** `build_nas_driver(nas: NasConfig, perf:
  RclonePerf) -> NasTransportDriver` (in `sync/transports/__init__.py`),
  branching on `nas.transport`. **Every** construction site routes through
  it: `nas_client._build_driver`, both `tray/dependencies.py` sites, and any
  test helper. **Verification:** `grep -rn "RcloneDriver(\|RsyncSshDriver("
  src` returns hits only inside `sync/transports/`.

- **Stage-mode bypass (spec-review blocker, 2026-06-10).**
  `_driver_for_equipment` serves *all* equipment, including the dormant
  stage-mode path, and a stage-mode target
  (`<staging_remote>:<staging_base_root>/…` from `_resolve_remote`) is an
  rclone named-remote string — handing it to an `RsyncSshDriver` would
  attempt ssh to a host literally named after the staging remote.
  `nas.transport` therefore selects the driver **only for
  `sync_mode == NAS` equipment**; stage-mode equipment always gets a
  `RcloneDriver` (constructed with `nas.rclone_config_path` + the staging
  perf dials), regardless of `nas.transport`. This honors the standing
  constraint that the hidden staging backend must not break
  (`CLAUDE.md` / `2026-05-29-hide-orchestrator-staging-design.md`). In
  practice a cluster `rsync_ssh` instance has no stage-mode equipment, but
  the branch must be explicit, not accidental.

- **`Verifier`** type-hints `NasTransportDriver`; its no-arg default
  construction is removed in favor of explicit driver injection (the only
  production caller already holds a driver).

### 2. rsync driver — new `sync/transports/rsync_ssh.py`

`RsyncSshDriver` mirrors `RcloneDriver`'s thin-wrapper philosophy: build an
argv, hand it to `run_subprocess`, classify the outcome. Constructor takes
`binary: str = "rsync"`, `ssh_port`, `ssh_identity_file`.

A single `_ssh_args()` helper renders the remote-shell flag:

```
-e "ssh -p <port> [-i <identity>] -o BatchMode=yes"
```

`BatchMode=yes` is mandatory — a host-key or passphrase prompt must fail
fast (classified `AUTH`) rather than hang the worker loop. Host keys are
**pre-provisioned during setup via `ssh-keyscan`** (resolved OQ-3): the
host-key exchange happens during the SSH handshake *before* authentication
or command execution, so `ssh-keyscan nas01 >> ~/.ssh/known_hosts` works
even though IT denies every login/exec path; the setup walkthrough has the
operator verify the fingerprint against the one DSM displays. No
`StrictHostKeyChecking` relaxation is passed — an unknown or changed key
fails as `AUTH`, which is the desired posture.

**Ops:**

- **`push`** → `rsync -rt --files-from=<list> --bwlimit=<KiB>
  -e <ssh> <local>/ <target>`
  - `-t` mandatory (mtime contract); `-r` for the whole-run case.
  - `--bwlimit` takes units of 1024 bytes since rsync 3.0, so
    `effective_bandwidth_limit_kibps` maps directly.
  - `--files-from` semantics match rclone's (run-relative paths against the
    source root); implementation must pin down trailing-slash behavior with
    a characterization test against the real binary (`tests/docker/`).
  - No `--checksum` on push: rsync's quick-check (size+mtime) decides what to
    send, mirroring how integrity is owned by the verify path, not the push.
- **`lsjson_manifest`** (resolved OQ-2) → `rsync --list-only -r --no-h
  <target>/` parsed line-by-line (perms, size, `Y/m/d H:M:S`, path) into a
  true size+mtime `RemoteManifest`; the `matches()` reconcile predicate and
  the cleanup `has()` probe work unchanged. **Parsing algorithm
  (spec-review blocker, 2026-06-10): the line must be anchored on the
  fixed-format timestamp, not whitespace-split** — filenames containing
  spaces appear literally, unquoted and unescaped. Shape:
  `^(\S+)\s+([\d,.]+)\s+(\d{4}/\d{2}/\d{2})\s+(\d{2}:\d{2}:\d{2})\s(.+)$`,
  with everything after the timestamp taken as the path. Remaining
  pitfalls, each pinned by a docker characterization test against the real
  binary:
  - timestamps print at 1 s resolution in a local timezone — *whose* local
    timezone (the client formatting the received file list, or the remote
    server) is an empirical question the characterization test must pin
    down by running the fixture NAS under a non-UTC `TZ` while the client
    parses under another; the parser takes the formatting timezone as a
    tested fact, not an assumption. (A DST-repeat-hour mismatch self-heals
    on the next sweep either way.)
  - sizes can carry locale thousands-separators — strip `,`/`.` group
    separators and pass `--no-h` defensively;
  - unusual filename bytes arrive escaped as `\#ooo` octal — unescape
    (spaces are *not* escaped, hence the timestamp anchor);
  - directory entries (`d` perm prefix) are dropped, matching
    `parse_lsjson`.
- **`check`** (resolved OQ-1) → remote-side checksum dry-run:
  `rsync -rtni --checksum --files-from=<list> -e <ssh> <local>/ <target>`.
  The remote rsync hashes whole files (MD5, or negotiated xxh3 on ≥ 3.2)
  inside the rsync protocol — it rides the same allowlisted
  `rsync --server` channel, no separate remote command. The itemized output
  is synthesized into a `CheckResult` so `VerifyResult.from_check_result`
  and everything downstream is untouched: a line with the checksum-differs
  flag (`c` at index 2) → `differ`; an all-`+` creation line → `missing_on_dst`;
  **`equal` is reconstructed by the driver as (the `files_from` list) minus
  (the itemized paths)** — the driver reads the `files_from` file it was
  handed, since matching files are simply omitted from itemized output.
  `extra_on_dst` is always `()` in rsync mode (a push-direction dry-run
  cannot see remote-only files; harmless — it never flips
  `VerifyResult.ok`). The itemize flag-string length varies across
  implementations (9 attribute columns in rsync 3.x, fewer in openrsync on
  macOS dev machines) — the parser matches the `YXc` prefix positionally
  and tolerates variable tail length. No temp disk, no re-download. **Accepted trust-posture change:** deletion decisions rest
  on "the NAS's rsync says the bytes hash equal" rather than a local
  SHA-256 of re-downloaded bytes (see [Risks](#risks)); the durable
  locally-computed `verified_sha256` in `sync_state.json` (Slot A) is
  unaffected. A download-and-rehash mode equivalent to
  `rclone check --download` was considered and can be added later as an
  opt-in strict mode if the posture ever needs tightening.
- **`about`** (resolved OQ-5) → reachability + auth probe only:
  `rsync --list-only <target-base-root>` (non-recursive, cheap). Success
  returns `AboutResult(ok=True, info={})` — free-space info is
  unobtainable without remote exec, which IT blocks; the degradation is
  accepted. The Settings panel already tolerates an empty `info` dict
  (that is `RcloneDriver.about`'s JSON-decode fallback today).
  **Classification carve-out (spec review):** a nonexistent `base_root`
  exits rc 23 ("partial transfer"), which the generic table would call
  retryable `NETWORK` — misleading for a config mistake. `about()`
  special-cases rc 23 with empty listing output into
  `reason="base_root not found or inaccessible"` so the Test-connection
  panel surfaces a configuration error, not a phantom network problem.

**Failure classification** — its own `_classify_failure(stderr, returncode)`
(the rclone one's markers are SMB/HTTP-flavored):

| Signal | Kind |
| --- | --- |
| rc 255 + `permission denied (publickey`, `host key verification failed`, `too many authentication failures` | `AUTH` (terminal) |
| rc 255 otherwise (connection refused / timed out / reset) | `NETWORK` |
| rc 5 (`error starting client-server protocol`), 10, 12, 30, 35 | `NETWORK` |
| rc 23 / 24 (partial transfer / vanished source files) | `NETWORK` (retryable; the per-file reconcile credits what landed) |
| anything else non-zero | `UNKNOWN` (treated as `NETWORK` by the queue) |

Spawn failure (`FileNotFoundError` on the rsync binary) raises
`TransportError` with `error_kind=None`, same contract as rclone.

### 3. Sync client — `sync/nas_client.py`

Deliberately minimal, thanks to the target-string reuse:

- `_build_driver` delegates to the shared `build_nas_driver` factory.
- `_build_lsjson` calls `driver.lsjson_manifest(target, strip_prefix=…)`
  instead of `parse_lsjson(await driver.lsjson(target), …)`.
- `_target_for_equipment`, `_remote_subpath`, `_resolve_remote`,
  `_write_files_from`, the worker state machine, the reconcile loop, Slot A
  SHA capture, and the whole `_maybe_cleanup` gate are **unchanged** — they
  already speak only `TransportResult` / `CheckResult` / `RemoteManifest`.

### 4. Setup gate — `paths.py`, `api/`, `tray/dependencies.py`

The §4.9 gate currently asks one question: "is `nas.remote` present in
rclone.conf?" (`_nas_remote_satisfied`, `paths.py:587`, fed by
`RcloneDriver.listremotes()` snapshots in
`tray/dependencies._hydrate_nas_remotes` and the
`tray/dependencies._make_equipment_probe` Test-connection probe). That
predicate is rclone-only. Branch by transport:

- **rclone:** unchanged (listremotes presence).
- **rsync_ssh:** static, offline predicate — `nas.remote` non-empty and, when
  `ssh_identity_file` is set, the file exists
  (`Path(...).expanduser().exists()`). No subprocess at boot, no network.
  (Network reachability stays behind the explicit Test-connection button,
  same policy as rclone mode.)

**The rsync branch must bypass rclone entirely, not merely reinterpret its
result** (spec review): on a cluster instance the rclone binary is likely
absent, and today `_hydrate_nas_remotes` would degrade its
`FileNotFoundError` to "no remotes" — tripping `INCOMPLETE_NO_NAS_REMOTE`
for the wrong reason. In rsync mode `_hydrate_nas_remotes` never spawns
rclone; the availability callable is the static predicate above.

Plumbing: the `nas_remote_available: Callable[[str], bool]` parameter of
`evaluate_setup_state` keeps its shape; `tray/dependencies.py` /
`api/app.py` supply the transport-appropriate callable from one shared
helper. `_missing_nas_fields` gains rsync-flavored reasons:
`{"field": "nas.remote", "reason": "unset"}` (shared),
`"identity_file_missing"` (new) alongside the existing
`"not_found_in_rclone_conf"`. `SetupState.INCOMPLETE_NO_NAS_REMOTE` and
`SetupNextAction.CONFIGURE_RCLONE_REMOTE` enum members are **kept as-is**
(renaming them is churn across API consumers); only the UI copy keyed off
them becomes transport-aware.

### 5. UI — `ui/pages/settings.py`, `ui/mount.py`, `ui/pages/main.py`

Copy-level changes only; the section stays read-only (the operator edits
`config.yaml`, same as the rclone remote today):

- Settings "NAS Remote" section: shows `transport` alongside remote +
  base_root. Badge branches: rclone keeps "Found in rclone.conf" / "Not
  found — run `rclone config`"; rsync_ssh shows "ssh key found" /
  "Identity file missing — see setup docs" (the static gate predicate).
- Test-connection button: already driver-routed once
  `tray/dependencies.py` uses the shared factory; the result panel renders
  `AboutResult.reason` for failures and "reachable" (no free-space figures)
  for rsync success.
- Main-page banner subline for `configure_rclone_remote`: transport-aware
  variant ("Configure ssh access to the NAS (see setup docs)").
- Equipment wizard copy ("syncs directly to the NAS using the rclone
  remote", `wizard_equipment.py:290-332`): generalize to "the NAS
  connection configured in Settings" — optional polish, not load-bearing.

### 6. Docs

- New setup-doc section **"Cluster instances: rsync over ssh"**: generating
  a dedicated keypair, installing it on the Synology (rsync service
  enabled, account permissions, home-dir `authorized_keys` quirks on DSM),
  host-key pre-provisioning via `ssh-keyscan` + DSM fingerprint
  verification (no manual `ssh` is possible — IT blocks all logins), and
  the `BatchMode` failure modes.
- Update `design_specs/design_spec_sections/09_Configuration_File.md`
  (new `nas.transport` / ssh fields), `07_Sync_and_Database_Integration.md`
  (transport matrix + verify fidelity note), `04_Backend_Architecture.md`
  (driver protocol), `README.md`.

### 7. Tests

- **New fixture** `tests/fixtures/stub_rsync.py`, sibling of
  `stub_rclone.py`: records argv, fakes `--list-only` / itemize / pull
  output per a scripted scenario. The argv contract (flag order, `-e`
  string, `--files-from` path) is the unit-test seam.
- **New unit suites:** `tests/unit/sync/transports/test_rsync_ssh.py`
  (argv building, failure classification, itemize → `CheckResult`
  synthesis) plus `--list-only` parser tests (timezone, escaped filenames,
  locale-size guards, directory dropping) in `test_manifest.py`.
- **Parameterize** the transport-touching `nas_client` paths: the existing
  suites stay green untouched (rclone default); add rsync-mode cases for
  `_build_driver` routing, push-failure classification, and the cleanup
  gate using canned driver stubs.
- **Config:** `tests/unit/config/test_models.py` (new fields, rsync-mode
  validators), `test_enum_literal_alignment.py`.
- **Setup gate / UI / e2e:** `tests/unit/test_paths.py` (transport-branched
  gate), `tests/unit/tray/test_dependencies.py`,
  `test_settings_nas_remote.py` (badge variants), an e2e settings-flow
  variant for rsync mode.
- **Integration:** `tests/docker/` gains an `sshd + rsync` container
  (restricted to rsync, no sftp subsystem — mirrors the Synology posture)
  beside the existing fixture servers; `tests/integration/test_nas_sync.py`
  runs the full push → reconcile → cleanup-gate cycle against it. This is
  where the `--files-from` / trailing-slash / timezone characterization
  tests live.

## Verify / cleanup flow (unchanged shape, annotated)

```
push (rclone copy --checksum | rsync -rt)  ──► manifest reconcile (size + mtime±tol)
                                                 rclone: lsjson JSON | rsync: --list-only parse
        └─ all files credited → VERIFIED → rollup SYNCED
maybe_cleanup (rollup == SYNCED && §7.1.6 interlocks)
  ├─ existence probe (manifest.has)            ── missing ─► defer
  ├─ content verify (check)                    ── mismatch ─► defer, surface
  │    rclone: check --download | rsync: --checksum dry-run (remote-side hash)
  └─ pass → delete local (keep_local honored) → CLEANED
```

## Memory / storage analysis (constrained machines)

| Operation | rclone (today) | rsync_ssh |
| --- | --- | --- |
| push | ~`--transfers` × buffer RAM, no temp disk | single stream, minimal RAM, no temp disk |
| manifest | bounded by entry count | same (text listing lines) |
| content verify | **streams, no temp disk** (full re-download) | dry-run, **no temp disk, no re-download** (remote-side hashing) |

The rsync verify is strictly cheaper than rclone's on disk and wire; the
cost is paid in trust posture instead (see [Risks](#risks)).

## Migration / breaking changes

None for existing installs: `nas.transport` defaults to `rclone`, all new
fields default inert, and no existing field changes meaning. The cluster
instance is a **new** config, written with `transport: rsync_ssh` from the
start. `extra="forbid"` on `NasConfig` means a config written for this
feature does not load on an older build — acceptable (new instance, new
build).

## Resolved decisions

All open questions were resolved in design discussion on 2026-06-10:

1. **Verify-before-delete fidelity (OQ-1) — remote-side `--checksum`
   dry-run.** Chosen over download-and-rehash: no temp disk, no
   re-download, and it rides the same allowlisted rsync channel. The
   trust-posture change (deletion gate relies on the NAS's rsync hashing
   rather than a local SHA-256 of re-downloaded bytes) is **accepted** and
   recorded in [Risks](#risks). The locally-computed Slot A
   `verified_sha256` record is unaffected. A strict download-and-rehash
   mode remains a possible later opt-in.
2. **Manifest mechanism (OQ-2) — parse `--list-only`** into a true
   `RemoteManifest`, keeping the `matches()` / `has()` contract unchanged;
   the timezone/escape/locale pitfalls are pinned by docker
   characterization tests. The dry-run-itemize verdict alternative is the
   documented fallback if listing parsing proves brittle against real DSM
   rsync versions.
3. **Host-key policy (OQ-3) — pre-provisioned `known_hosts` via
   `ssh-keyscan`.** Constraint surfaced during discussion: IT blocks *all*
   ssh commands/logins on the NAS, so "run one manual `ssh` to accept the
   key" is impossible. `ssh-keyscan` needs no login — the host-key exchange
   precedes authentication — so the setup walkthrough uses
   `ssh-keyscan <host> >> ~/.ssh/known_hosts` plus an out-of-band
   fingerprint check against DSM. No `StrictHostKeyChecking` relaxation.
4. **Config shape (OQ-4) — reuse `nas.remote` as `user@host`** with flat
   `ssh_port` / `ssh_identity_file` fields and a rsync-mode `user@host`
   shape validator. Keeps `_target_for_equipment` / `strip_prefix`
   untouched.
5. **Free-space probe (OQ-5) — degradation accepted.** The service account
   is strictly rsync-only (no `df`, no `sha256sum`), so Test-connection in
   rsync mode reports reachable/auth-ok with no free-space figures.

## Full surface inventory

**Transport core:** new `sync/transports/rsync_ssh.py`;
`sync/transports/__init__.py` (protocol, factory, DTO moves);
`sync/transports/rclone.py` (implements protocol, `lsjson_manifest` shim).
**Sync client / verify:** `sync/nas_client.py` (`_build_driver`,
`_build_lsjson` slimming), `sync/verifier.py` (protocol type, drop no-arg
default), `sync/manifest.py` (rsync listing parser, per OQ-2).
**Config:** `config/models.py` (`NasConfig` fields + validators),
`constants/enums.py` (`SyncTransport`).
**Setup gate:** `paths.py` (`_nas_remote_satisfied` branch,
`_missing_nas_fields` reasons), `tray/dependencies.py` (factory routing,
availability snapshot branch), `api/_dependencies.py`, `api/app.py`,
`api/setup.py`.
**UI:** `ui/pages/settings.py` (badge/copy variants), `ui/mount.py` (probe
adapter), `ui/pages/main.py` (banner subline),
`ui/pages/wizard_equipment.py` (copy polish, optional).
**Tests:** new `tests/fixtures/stub_rsync.py`,
`tests/unit/sync/transports/test_rsync_ssh.py`; updates per [§7](#7-tests);
`tests/docker/` sshd+rsync fixture.
**Docs:** setup walkthrough, `design_specs/design_spec_sections/{04,07,09}_*.md`,
`README.md`, this spec.

## Risks

- **Listing-parse fragility.** `--list-only` output is meant for humans:
  unquoted spaces in filenames (timestamp-anchored regex, not
  whitespace-split), a local-timezone timestamp at 1 s resolution (*which*
  side's timezone is pinned by the docker characterization test running
  fixture-NAS and client under different `TZ` values — the parser encodes
  the tested answer, not an assumption), `\#ooo` filename escaping, and
  potential locale thousands-separators in sizes (`--no-h` forced). A
  DST-repeat-hour mismatch (one hour, once a year) leaves files "not
  synced" for that hour and self-heals on the next sweep.
- **Weaker checksum authority (accepted, resolved OQ-1).** In rsync mode the
  pre-deletion gate's authority is "the NAS's rsync reports the whole-file
  hash (MD5/xxh3) equal", not a local SHA-256 of re-downloaded bytes.
  Accepted trade-off for zero temp disk and no re-download; partially
  offset by the unchanged locally-computed `verified_sha256` Slot A record.
  If the posture ever needs tightening, a download-and-rehash strict mode
  can be added behind a config flag without touching the state machine.
- **Synology account posture drift.** If IT later tightens the rsync service
  (e.g. rsync daemon-module-only, no remote-shell rsync), the
  `user@host:path` form stops working and a daemon-module (`rsync://`)
  variant would be a follow-up. The driver isolates this in `_ssh_args()` +
  target rendering.
- **Partial-transfer semantics.** rsync rc 23/24 (some files failed/vanished)
  is classified retryable; the per-file reconcile already credits what
  landed, so the retry loop converges — but the docker integration test must
  cover a vanished-mid-push file to prove it. Note rc 23 also fires when
  `lsjson_manifest` targets a not-yet-existing remote run directory —
  consistent with rclone's missing-path behavior (retryable
  `TransportError`), though the operator-visible reason reads "partial
  transfer"; the integration test pins this.
- **`error_kind=None` routing debt (pre-existing, inherited).** A missing
  rsync binary raises `TransportError(error_kind=None)`, which the queue
  routes through the legacy `HASH_MISMATCH` single-retry branch
  (`transports/__init__.py:74-77`); the operator still sees the
  binary-missing message via `last_error`, but the retry shape is
  misleading. Same contract as rclone today; fixing the routing is a
  follow-up, not this spec.
- **Single stream throughput.** rsync-over-ssh will not match rclone's
  parallel SMB streams on many-small-file runs; acceptable on the cluster
  (no alternative path exists), and `--bwlimit` still applies for politeness.
