# rclone.conf-based NAS sync

- **Date:** 2026-05-28
- **Status:** Design — pending implementation plan
- **Supersedes:** the credential-injection model in
  [`2026-05-26-rclone-only-nas-sync-design.md`](2026-05-26-rclone-only-nas-sync-design.md)
  (that doc's `--download` verification semantics are retained; its
  `RCLONE_CONFIG_<remote>_*` env-injection + keyring model is replaced).

## Summary

Today the NAS sync subsystem builds an *inline* rclone backend at subprocess
time: per-equipment connection params (`host`/`port`/`user`/`share`/`domain`/
`remote_path`) come from a `transport:` block in `config.yaml`, the password is
read from the OS keyring on every push, run through `rclone obscure`, and
injected as `RCLONE_CONFIG_<remote>_*` environment variables. The remote name
is synthesised (`exlab_<equipment_id>`).

This redesign removes that machinery. The operator configures their rclone
remotes **separately** with `rclone config` (stored in `rclone.conf`), and the
app simply references a remote by name. Connection details and credentials
live entirely in `rclone.conf`; the app never sees a password.

Three capabilities are added at the same time:

1. A **global base root** so all equipment folders live under one
   `<remote>:<base_root>/<equipment_id>/…` prefix.
2. **lsjson-based manifest generation** (`rclone lsjson`, read-only) plus a
   parser, used for cheap per-sync reconciliation and the cleanup
   remote-existence interlock.
3. **Tunable `--transfers` / `--checkers`** (and the existing bandwidth cap)
   exposed in config — these double as the memory dial for space- and
   RAM-constrained acquisition machines.

## Motivation / goals

- **Reduce credential burden.** No per-equipment passwords to type, store, or
  rotate inside the app. The operator manages credentials once, with the
  standard rclone tooling, supporting any backend rclone supports (SFTP, SMB,
  S3, WebDAV, …) — not just the two we hand-coded.
- **Cheaper routine verification.** Replace the per-push full re-download with
  a metadata-only `lsjson` reconcile; reserve the expensive content-hash check
  for the one moment it must be certain — immediately before deleting the local
  copy.
- **Operate within tight hardware.** Acquisition machines have ~100 GB free of
  256 GB and limited RAM. `rclone check --download` streams and hashes on the
  fly (**no temp files written to disk** — confirmed against rclone docs), so
  disk cost is ≈ 0; memory is bounded by `--transfers`/`--checkers`/
  `--buffer-size`, which we now expose.

### Non-goals

- Encrypted `rclone.conf` support. If a site encrypts its config, that is the
  operator's single `RCLONE_CONFIG_PASS` to manage, outside this app. Documented
  as an assumption.
- Per-equipment bandwidth/transfers/checkers overrides (global only for now —
  YAGNI; the model leaves room to add per-equipment overrides later).
- A migration shim for existing installs. This is a **clean break** — existing
  `transport:` blocks and NAS keyring entries become invalid; affected installs
  re-run setup.

## Decisions (from brainstorming)

| Question | Decision |
| --- | --- |
| lsjson vs. hash verification | **Keep hash verify, add lsjson.** lsjson is the routine reconcile; `rclone check --download` is retained. |
| Where the hash-verify runs | **lsjson routine + hash-gate at cleanup.** Pay the full re-download exactly once, right before local deletion (and on manual force-verify). |
| Remote/base-root topology | **Global remote + base root** in a top-level `nas:` block. |
| Credential machinery | **Clean break** — remove the NAS keyring path entirely. |
| Orchestrator staging transport | **In scope** ("both transports") — see [Orchestrator / staging](#7-orchestrator--staging-hop) for the scoped reality and the ⚑ open decision. |
| `--transfers` / `--checkers` | **Exposed in config** (added requirement). |

## Config schema

### Before (per-equipment, credential-injected)

```yaml
equipment:
  - id: "CONFOCAL_01"
    label: "Confocal Microscope 1"
    local_root: "/data/lab"
    nas_root: "//nas01/lab"
    sync_mode: "nas"
    transport:
      type: "rclone_sftp"
      host: "nas01.lab.example"
      port: 22
      user: "labuser"
      remote_path: "/srv/lab/CONFOCAL_01"
      bandwidth: { ... }
```

### After (named remote + global base root)

```yaml
nas:
  remote: "nas01"              # name of a remote defined in the operator's rclone.conf
  base_root: "/srv/lab"        # path on the remote under which <equipment_id> folders live
  rclone_config_path: ""       # optional; pins `rclone --config <path>`. Blank = rclone's default discovery
  mtime_tolerance_s: 2         # reconcile: remote modtime must match local within this many seconds
  perf:                        # parallelism knobs (also the memory dial on constrained machines)
    transfers: 4               # rclone --transfers (parallel file transfers)
    checkers: 8                # rclone --checkers (parallel checks during verify / lsjson)
  bandwidth:                   # global upload cap + schedule (moved here from per-equipment transport)
    upload_mbps: null
    schedule: []

orchestrator:
  staging_remote: "stagepc"    # rclone.conf remote for the stage-mode hop (Phase 8)
  staging_base_root: "/staging"
  staging_perf: { transfers: 4, checkers: 8 }

equipment:
  - id: "CONFOCAL_01"
    label: "Confocal Microscope 1"
    local_root: "/data/lab"
    nas_root: "//nas01/lab"    # display-only metadata (unchanged role)
    sync_mode: "nas"           # no transport block
```

- **Remote destination** for a run = `<remote>:<base_root>/<equipment_id>/<rel>`,
  where `<rel>` reproduces exactly what today's `_build_target_for` appends after
  the equipment folder (currently the run-leaf directory name — projects are
  *not* nested on the remote). The implementation must reproduce the current
  layout rather than silently introduce project nesting; if project nesting is
  desired it is a separate, explicit decision.
- **New model:** a `NasConfig` (`remote`, `base_root`, `rclone_config_path`,
  `transfers`, `checkers`, `bandwidth`) reusing the existing `BandwidthConfig`.
  A small reusable `RclonePerf` (`transfers`, `checkers`) keeps the knobs in one
  place for reuse by the staging block.
- **Removed from `config/models.py`:** `RcloneSftpTransport`,
  `RcloneSmbTransport`, the `EquipmentTransport` discriminated union, and
  `transport_requires_keyring_password`. `EquipmentConfig.transport` is removed;
  the `sync_mode == 'nas'` validator now requires the top-level `nas:` block to
  be present instead of a per-equipment `transport:` block.

## Architecture by surface

### 1. Transport driver — `sync/transports/rclone.py` (single reusable rclone-ops surface)

**DRY requirement:** `RcloneDriver` is the *one and only* place that shells out
to rclone. Every rclone operation — `copy`, `lsjson`, `check`, `about`,
`listremotes` — is a reusable method on this driver, and **every** caller (the
NAS sync client, the stage-mode push, the tray equipment probe, the
setup-availability gate) goes through it. No module outside `rclone.py` builds
an rclone argv or calls `run_subprocess` for rclone. A `_global_flags()` helper
centralises `--config <path>`; a perf helper centralises `--transfers` /
`--checkers`, so flag handling is written once. Target-string construction is a
single shared helper (`_build_target_for_run`) used by both the NAS and staging
paths. **Verification:** `grep -rn "run_subprocess\|\"rclone\"\|'rclone'" src`
returns hits only inside `sync/transports/`.

- **Delete** `build_rclone_env`, `pass_env_keys_for`, `obscure`, and all
  `RCLONE_CONFIG_<remote>_*` env plumbing (including the injection notes in
  `sync/transports/_run.py`). Driver methods drop the `env` credential dict and
  `mask_for_log`; rclone reads `rclone.conf` itself.
- **Constructor** takes `config_path` / `transfers` / `checkers`; argv builders
  for `copy`, `check`, and `lsjson` apply them via the shared helpers.
- **Add** `RcloneDriver.lsjson(remote, *, recursive=True) -> str` running
  `rclone lsjson -R <remote>:<path>` (read-only) and returning raw JSON for the
  parser.
- **Add** `RcloneDriver.listremotes() -> tuple[str, ...]` running
  `rclone listremotes` (offline, cheap) — the single source for the setup gate
  and the Settings "found?" badge.
- **Keep** `check` (`--download --combined`) for the hash-gate and `about` for
  Test-connection (now simply `rclone about <remote>:`).
- `_classify_failure` is unchanged; an `AUTH` result now means
  "remote misconfigured / unreachable in `rclone.conf`" rather than a bad
  keyring password.

### 2. Manifest + parser — new `sync/manifest.py`

```python
@dataclass(frozen=True, slots=True)
class RemoteEntry:
    size: int
    mod_time: str          # RFC3339 string as emitted by lsjson
    is_dir: bool

@dataclass(frozen=True, slots=True)
class RemoteManifest:
    entries: dict[str, RemoteEntry]                   # keyed by run-relative POSIX path
    def has(self, rel: str) -> bool: ...              # existence probe
    def matches(self, rel: str, local_size: int, local_mtime: float,
                *, tolerance_s: float) -> bool: ...   # present AND size== AND |Δmtime|<=tol

def parse_lsjson(raw: str, *, strip_prefix: str = "") -> RemoteManifest: ...
```

- Parses the lsjson JSON array, normalises `Path` to run-relative (stripping the
  `<base_root>/<equipment_id>/<run>` prefix), and drops directory entries.
- `matches()` is the single reconcile predicate: present **and** size-equal
  **and** modtime within `tolerance_s` (the RFC3339 `ModTime` is parsed to an
  epoch, fractional seconds truncated to 6 digits so Python `fromisoformat`
  accepts nanosecond-precision strings).
- Memory is bounded by entry count, not file size; the parser is the only place
  that understands the lsjson shape (isolation boundary).

### 3. Sync client + verify/cleanup flow — `sync/nas_client.py`, `sync/verifier.py`, `sync/cleanup.py`

- A single `_build_target_for_run(remote, base_root, equipment_id, run)` helper
  composes `<remote>:<base_root>/<equipment_id>/<rel>`, and a single
  `_target_for_equipment(equipment)` picks the NAS remote/base_root for nas-mode
  and the staging remote/base_root for stage-mode (Phase 8) — both branches reuse
  the same helper (DRY). `_resolve_env_for_equipment` and
  `_build_transport_driver`'s keyring/obscure/env logic are removed; the driver
  is constructed once from the `nas:` config (config path + perf knobs).
- **Routine reconcile** (replaces the per-push `--download`): after a successful
  `rclone copy`, fetch the `lsjson` manifest and mark each file synced only when
  `manifest.matches(rel, local_size, local_mtime, tolerance_s=nas.mtime_tolerance_s)`
  is true — i.e. **present AND size-equal AND modtime within tolerance**. The run
  rolls up to `SYNCED`. Any file failing a check is left for the next sweep.
- **Cleanup gate** (`_maybe_cleanup`, only when rollup = `SYNCED` and the §7.1.6
  interlocks pass):
  1. `_remote_stat_callable` becomes a **real lsjson existence probe** — confirm
     every tracked file is present on the remote.
  2. Then `rclone check --download` hash-verifies all tracked files.
  3. Local deletion happens **only** on an all-`=` result. A mismatch leaves the
     local files in place and surfaces the discrepancy.
- `force_verify` continues to run `rclone check --download` (unchanged intent).
  If cleanup is disabled, hash-verify is force-verify-only.
- State semantics: `VERIFIED` now means "present + size-matched on the remote";
  `CLEANED` requires the cleanup hash-gate to have passed.

### 4. Verify / cleanup flow

```
push (rclone copy --checksum --transfers N)
  └─ success → lsjson reconcile (size + mtime±tol) → mark files synced → rollup SYNCED
                                                                       │
maybe_cleanup (rollup == SYNCED && interlocks ok)                      │
  ├─ lsjson existence probe (all tracked files present?) ── no ──► defer
  ├─ rclone check --download (hash all tracked files) ──── mismatch ─► no delete, surface
  └─ all '=' → delete local (honour keep_local / retain_cache) → CLEANED
```

### 5. Credentials / setup gate — `paths.py`, `constants/`, `api/`, `tray/`

- **Remove** `constants/keyring.py:keyring_nas_username` (+ export in
  `constants/__init__.py`) and every NAS-keyring callsite: `nas_client`,
  `tray/dependencies` (`_get_nas_password`, `_check_nas_passwords_present`,
  `probe_equipment_transport`), `ui/mount` (`_nas_credential_handlers`,
  save/clear), `api/_dependencies.nas_password_present`, and the
  `nas_password_present` set field on `api/app.py`. **The LIMS keyring is left
  untouched.**
- **Rename** `SetupState.INCOMPLETE_NO_NAS_CREDENTIAL` →
  `INCOMPLETE_NO_NAS_REMOTE`, and `SetupNextAction.SET_NAS_CREDENTIALS` →
  `CONFIGURE_RCLONE_REMOTE`.
- **Setup-gate check:** the gate is satisfied when `nas.remote` is non-empty
  **and** present in `rclone listremotes` (cheap, offline). `rclone about`
  (network) stays behind the explicit Test-connection button so setup does not
  block on connectivity. `paths.evaluate_setup_state` swaps its
  `nas_password_present_for` callable for a `nas_remote_available()` check.

### 6. UI — `ui/pages/settings.py`, `ui/mount.py`, `ui/pages/main.py`, `ui/pages/wizard_equipment.py`, `ui/equipment_form.py`

- Settings "NAS Credentials" section → **"NAS Remote"**: shows the configured
  `nas.remote` + `base_root`, a found/not-found indicator
  (`rclone listremotes`), and a **Test connection** button (`rclone about
  <remote>:`). No password entry.
- Equipment wizard / form: drop all host/port/user/share/domain/password fields
  for nas-mode. The `nas:` block is configured once (Settings / setup step), not
  per equipment. `nas_root` display field is unchanged.
- Main-page banner copy: "Set the NAS password…" → "Configure the rclone remote
  (see setup docs)."

### 7. Orchestrator / staging hop

Two distinct flows exist:

- **Orchestrator → NAS** reuses the single `NASSyncClient` (the quiescence
  poller enqueues both orchestrator-staged and nas-mode runs). This is covered
  automatically by the `nas:` remote migration — no extra work beyond the core.
- **Stage-mode equipment → staging area** is, today, a *mount-based* hop:
  `OrchestratorStagingTransport` carries `type` (`smb_mount`/`file_transfer`),
  `mount_point`, and `staging_subpath`, and is **not wired to any active rclone
  push** (it is built from the wizard form but consumed by no transfer code).

**Decision (confirmed): the staging hop moves to rclone too.** Collapse
`OrchestratorStagingTransport` to `orchestrator.staging_remote` /
`staging_base_root` (+ a `staging_perf: RclonePerf`), drop
`OrchestratorTransportType` / `mount_point` / `staging_subpath`, and route the
stage-mode push through the **same** `RcloneDriver` ops and the **same**
`_build_target_for_run` helper as the NAS leg — `_target_for_equipment` simply
selects the staging remote/base_root when `sync_mode == 'stage'`. This is
net-new code (no rclone staging push exists today) but adds **no new rclone
op**: it reuses copy/lsjson/check verbatim, honouring the DRY requirement in
[§1](#1-transport-driver--synctransportsrclonepy-single-reusable-rclone-ops-surface).

### 8. Docs

- **New setup section — "Set up your rclone remote(s) separately":** an `rclone
  config` walkthrough for SFTP and SMB, naming the remote to match `nas.remote`,
  setting `base_root`, the unencrypted-config assumption (encrypted ⇒ operator's
  own `RCLONE_CONFIG_PASS`), and the **tray-app caveat** — `rclone.conf` must
  belong to the OS user the app runs as, or pin `nas.rclone_config_path`.
- Update `design_specs/design_spec_sections/09_Configuration_File.md` (new
  `nas:` block, removed `transport:` block), `07_Sync_and_Database_Integration.md`
  (lsjson reconcile + hash-gate), `04_Backend_Architecture.md`, and `README.md`.

### 9. Tests

- `tests/fixtures/stub_rclone.py`: add `lsjson` and `listremotes` verbs; drop
  the env-injection requirement and `obscure` expectations; make the stub
  remote/config aware.
- `tests/unit/sync/transports/test_rclone_env.py` → **`test_manifest.py`** (the
  parser). Update `test_transports`, `test_nas_client*`, `test_cleanup` (real
  remote-stat probe + cleanup hash-gate), `tests/unit/config/test_models.py`
  (new `nas:` block, removed transport union), and the setup / dependency / UI /
  e2e flows (password flow → remote-availability flow):
  `tests/unit/api/test_setup.py`, `test_config_router.py`,
  `tests/unit/tray/test_dependencies.py`,
  `tests/unit/ui/test_settings_nas_credentials.py`, `test_mount.py`,
  `test_wizard_equipment.py`, `tests/integration/test_nas_sync.py`,
  `tests/e2e/test_flow_27_nas_credential_settings.py`, and the lifecycle flows.
- `tests/docker/`: ship an `rclone.conf` pointing at the fixture SFTP/SMB
  servers instead of injecting credentials; integration tests run against named
  remotes.

## Memory / storage analysis (constrained machines)

| Operation | Disk | Memory | Network |
| --- | --- | --- | --- |
| `rclone copy --checksum` (push) | none beyond source | ~`--transfers` × `--buffer-size` | upload size |
| `rclone lsjson -R` (reconcile) | none | bounded by entry count | metadata only |
| `rclone check --download` (hash-gate) | **none** (streamed, no temp files) | ~`--checkers` × buffers | full re-download of tracked files |

`--transfers` and `--checkers` are the operator-facing dials to trade speed for
memory. Defaults (`transfers: 4`, `checkers: 8`) are conservative; a tight RAM
budget can lower them.

## Migration / breaking changes

- Existing `config.yaml` files with a per-equipment `transport:` block fail
  validation after upgrade; the operator adds a top-level `nas:` block and sets
  up `rclone.conf`.
- Existing NAS keyring entries are orphaned (harmless; LIMS entries untouched).
- The setup flow guides the operator to the new state
  (`INCOMPLETE_NO_NAS_REMOTE` → `CONFIGURE_RCLONE_REMOTE`).

## Resolved decisions

1. **Bandwidth scope** — **global** (in `nas:`). Per-equipment caps are out of
   scope (YAGNI).
2. **Reconcile predicate** — a file is credited as synced only when **present
   AND size-equal AND modtime within `nas.mtime_tolerance_s`** (default 2 s).
   The cleanup hash-gate remains the authoritative content-integrity check.
3. **Orchestrator staging** — **in scope**: the stage-mode hop moves to rclone,
   reusing the same ops/helpers (see [§7](#7-orchestrator--staging-hop)).
4. **DRY rclone ops** — all rclone operations are reusable `RcloneDriver`
   methods; no other module shells out to rclone (see [§1](#1-transport-driver--synctransportsrclonepy-single-reusable-rclone-ops-surface)).

## Full surface inventory

**Transport core:** `sync/transports/rclone.py`, `sync/transports/_run.py`,
`sync/transports/__init__.py`.
**Sync client / verify:** `sync/nas_client.py`, `sync/verifier.py`,
`sync/cleanup.py`; new `sync/manifest.py`.
**Config model:** `config/models.py` (new `NasConfig`/`RclonePerf`; removed
transport union), `constants/enums.py` (`SetupState`, `SetupNextAction`,
`OrchestratorTransportType`).
**Credentials / keyring:** `constants/keyring.py` (+ `constants/__init__.py`),
`tray/dependencies.py`, `ui/mount.py`, `api/_dependencies.py`, `api/app.py`.
**Setup gate:** `paths.py`, `api/setup.py`, `api/routers/config.py`.
**UI:** `ui/pages/settings.py`, `ui/mount.py`, `ui/pages/main.py`,
`ui/pages/wizard_equipment.py`, `ui/equipment_form.py`,
`ui/components/metadata_pane.py`.
**Controller / schema:** `controller/creation.py`, `api/schemas.py` (only if
`nas_root`/`configured_nas_root` semantics change — expected unchanged).
**Tests:** see [§9](#9-tests).
**Docs:** `design_specs/design_spec_sections/{04,07,09}_*.md`, `README.md`,
this spec (supersedes `2026-05-26-rclone-only-nas-sync-design.md`).

## Risks

- **Routine reconcile is metadata-only (size + mtime).** A file whose remote
  copy has the right size and modtime but corrupted bytes is marked synced until
  the cleanup hash-gate catches it. Acceptable because no local deletion happens
  before the hash-gate; a cleanup-disabled deployment never gets the deep check
  unless force-verify is run.
- **Modtime tolerance is a window, not exact.** `nas.mtime_tolerance_s` (2 s)
  absorbs SFTP/SMB rounding; setting it to 0 will cause false "not synced" on
  low-precision backends.
- **rclone.conf discovery under the tray/service user.** If the app runs as a
  different OS user than the one who ran `rclone config`, the remote won't be
  found. Mitigated by `nas.rclone_config_path` and the setup-doc caveat.
- **Backend hash availability.** `--checksum` on SFTP/SMB degrades to size-based
  transfer decisions; integrity rests on the `--download` hash-gate, exactly as
  in the 2026-05-26 design.
