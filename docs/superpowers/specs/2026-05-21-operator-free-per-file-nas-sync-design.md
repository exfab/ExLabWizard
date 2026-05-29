# Operator-free, per-file quiescence-driven NAS sync — design

- **Date:** 2026-05-21
- **Status:** Approved (design); implementation plan pending
- **Affects:** orchestrator staging, NAS sync, config schema, Add-Equipment wizard, main GUI

## Context

Today a staged run is promoted from `staging` to `complete` when the
orchestrator observes a *completeness signal* — a sentinel file the
**equipment machine** writes, or a manifest the equipment emits
(Design Spec §13.5). Completeness, the sentinel/manifest filenames, and
the choice between them are configured **per equipment** in
`config.yaml` and collected by step 4 of the Add-Equipment wizard.

This couples the orchestrator to equipment-side cooperation and to a
fixed per-equipment policy. It also offers no protection against
syncing a file that is still being written: the post-transfer SHA-256
verifier (`sync/verifier.py`) catches *wire* corruption but not a
partial *source* file — a half-written file copies and hashes
consistently on both ends and passes verification.

## Goals

- Decide a file is ready to sync with **zero equipment cooperation**.
- Never transfer a file that is still being written.
- Remove operator/equipment involvement from the sync trigger entirely:
  no marking, no sentinel, no manifest.
- Sync each file **independently**, as soon as it is safe.

## Non-goals

- Changing the equipment → staging transport (outside the app, §13.6).
- Operator "mark complete / incomplete" actions (considered and
  dropped — quiescence is the trigger, so marking is unnecessary).
- Automatic completeness *detection* by sentinel/manifest (removed).

## The model

A background **quiescence poller** sweeps every run directory pending
NAS sync — **both** orchestrator staging-area runs **and** runs
acquired directly on `nas`-mode equipment — on an interval. One
unified poller is the single sync trigger for every run that must reach
the NAS. A file becomes eligible to sync when **all** hold:

1. the poller has **observed** its `(size, mtime)` unchanged across its
   own consecutive sweeps spanning ≥ the settle threshold
   (`sync.quiescence_minutes`, default **10**). Eligibility is measured
   from the poller's own observations, *not* the absolute age of
   `mtime` — transports (`rsync -t`, `rclone`) preserve the source
   `mtime`, so a file can land in staging already showing an old
   timestamp. The poller must therefore persist or carry forward a
   prior-sweep snapshot to compare against.
2. it does not match any `sync.ignore_globs` entry (default
   `["*.partial", "*.tmp"]`);
3. it is not already synced *at its current state* — a file modified
   after a prior successful sync becomes eligible again once it
   re-settles.

Eligible files are synced to the NAS independently (file-level
granularity). There is no run-level "complete" gate.

## Config changes (`config/models.py`)

**Remove from `EquipmentConfig`:**

- `completeness_signal`, `sentinel_filename`, `manifest_filename`;
- the `_completeness_signal_requires_matching_filename` model validator;
- the `_serialize_completeness_signal` field serializer.

**Remove the `CompletenessSignal` enum** (`constants/enums.py`) — no
remaining consumer once the watcher's signal logic is gone.

**Add to `SyncConfig`:**

- `quiescence_minutes: int = 10` (ge ≥ 1) — the per-file settle window.
- `ignore_globs: list[str] = ["*.partial", "*.tmp"]` — names skipped
  from eligibility (in-progress transport temp files).
- `poll_interval_seconds: int = 120` — how often the poller sweeps.
  Coarse by design: with a 10-minute settle window there is no value
  in sweeping faster.

These are **global** settings, not per-equipment — completeness policy
is no longer an equipment attribute.

## Run lifecycle — rollup (Approach 1)

`IngestState` collapses from five states (`staging`, `complete`,
`sync_queued`, `sync_verified`, `cleared`) to the milestones that are
genuinely monotonic plus a derived rollup:

- `SYNCING` — at least one non-ignored file is unsynced/unverified.
- `SYNCED` — every non-ignored file is verified on the NAS.
- `CLEARED` — the run's staging copy has been cleaned up.

A run's `SYNCING`/`SYNCED` status is **derived on read** from
`sync_state.json` (below); it is never an appended history entry,
because it can oscillate (a `SYNCED` run whose file is modified again
returns to `SYNCING`).

## Per-file sync state — `sync_state.json`

A new per-run file `<run>/.exlab-wizard/sync_state.json`, written by the
orchestrator only. It is a **freely-mutable current-state map** (not
append-only): relative file path → record:

```
{
  "<relative/path>": {
    "synced_signature": [size, mtime],   // (size, mtime) at last successful sync; null if never synced
    "verified_at": "<iso8601>",          // null until SHA-256 verified
    "keep_local": false
  },
  ...
}
```

- `synced_signature` answers both "already synced?" and "modified since
  sync?" — if the file's current `(size, mtime)` differs, it is
  re-eligible once it re-settles.
- This file is the source of truth the GUI's existing
  `sync_status_icon` reads for per-file status.

## `ingest.json` contract (risk 2 resolution)

`ingest.json` keeps its append-only `history`, but its entries are
**milestone events**, not rollup states: `created` at run bootstrap and
`cleared` at cleanup. It no longer stores a `syncing`/`synced`
`current_state`. Consequently `CLEARED` is the only `IngestState`
member ever persisted; `SYNCING` and `SYNCED` exist solely as the
computed rollup and are never written to `history`. All churny
per-file and rollup data lives in the freely-mutable `sync_state.json`,
and the run-level rollup is computed on read from it whenever the GUI
or cleanup needs it. This preserves the append-only history contract
without spamming it with rollup oscillation.

## `keep-local` (per-file)

A per-file boolean in `sync_state.json`. A `keep_local` file **still
syncs** to the NAS; it is **excluded from cleanup deletion**. The
operator toggles it from the file-list context menu, but
`sync_state.json` has a **single writer — the orchestrator** — so the
context-menu action calls a backend API endpoint that applies the flag;
the GUI never writes `sync_state.json` directly.

## GUI per-file display state

The GUI file list for a run is sourced from `sync_state.json` (the
durable per-file record), unioned with any local files not yet
recorded. Because `sync_state.json` lives in `<run>/.exlab-wizard/` and
**survives cleanup**, the operator never loses visibility — a fully
cleared run still expands to show every file as an "On NAS" tombstone.

Each file resolves to one display state:

| In `sync_state.json` | Local file on disk | GUI state |
|---|---|---|
| not recorded yet | present | **Acquiring** — new, still settling |
| recorded, `verified_at` null | present | **Syncing** — settled / in transfer |
| recorded, `verified_at` set | present | **Synced** — on NAS, local copy still here |
| recorded, `verified_at` set | absent | **On NAS** — tombstone, local copy cleared |
| `keep_local` true | present | **Kept local** badge, plus its sync state |

The run-node rollup (`SYNCING` / `SYNCED` / `CLEARED`) is derived from
these per-file states.

## Failure handling

A file whose transfer or SHA-256 verification fails keeps
`synced_signature` and `verified_at` null in `sync_state.json`, so it
stays eligible and is retried on the next sweep, bounded by
`sync.retry_attempts`. After retries are exhausted the file is surfaced
as a per-file error in the GUI (existing problems/sync-status
machinery) and the run rollup stays `SYNCING`.

## Cleanup — rollup

Per-run, trigger style unchanged: once a run is `SYNCED` and
`retain_hours` has elapsed, clear it — delete every file **except**
those flagged `keep_local` and the `.exlab-wizard/` metadata directory.
`StagingCleanupMode.MANUAL` still never auto-clears.

## Transport batching (risk 1 resolution)

Per-file eligibility must not become per-file transport invocations.
The poller groups a sweep's eligible files **by run** and emits **one
transport job per run**, carrying that run's eligible-file list. Runs
cannot be batched together — each has its own `local_root → nas_root`
src/dst pair — so per-run is the natural and maximal batch.

- `sync/queue.py` job payload becomes `(run_path, [relative paths])`.
- `sync/transports/rclone.py` and `rsync_ssh.py` gain a files-from
  mode: write the list to a temp file, pass `--files-from`. Both tools
  support this natively (`rclone copy --files-from`, `rsync
  --files-from`).

After each per-run batched transfer the existing `sync/verifier.py`
SHA-256 pass runs over the transferred files; each file's `verified_at`
in `sync_state.json` is set when its hash is confirmed against the NAS
copy. A file counts toward the `SYNCED` rollup only once `verified_at`
is set.

## Components touched

- **New `QuiescenceSyncPoller`** — the single sync trigger for **every**
  run pending NAS sync, orchestrator-staged *and* `nas`-mode. It
  supersedes the run-state machine in
  `orchestrator/staging_watcher.py`'s `evaluate_run`: sweep → per-file
  eligibility → group by run → enqueue → on verify, update
  `sync_state.json`. The existing `StagingWatcher`'s sentinel/manifest
  watching is removed; its run-discovery for the staging area is reused
  and extended to also discover `nas`-mode run directories.
- **`sync/queue.py` + transports** — per-run file-list jobs (above).
- **`config/models.py`, `constants/enums.py`** — config + enum changes.
- **`api/schemas.py`** — `IngestJson` state set; new `sync_state.json`
  schema/struct.
- **API** — a `keep_local` toggle endpoint (single-writer; see
  *`keep-local`* above).
- **Add-Equipment wizard** (`ui/pages/wizard_equipment.py`) — drop
  step 4 "Completeness signal"; `EQUIPMENT_WIZARD_STEPS` 5 → 4; update
  `EquipmentWizardState`, `can_advance`, `_STEP_RENDERERS`,
  `assemble_equipment_config`/`build_equipment_config`.
- **GUI** — run tree shows the `syncing`/`synced` rollup; file list
  is sourced from `sync_state.json` (per *GUI per-file display state*),
  keeps per-file sync icons including "On NAS" tombstones, and gains a
  "Keep local" context-menu toggle.

## Testing

- **Unit:** quiescence eligibility (settle window boundary, ignore-glob,
  modified-since-sync re-eligibility); rollup derivation from
  `sync_state.json`; `sync_state.json` read/write; cleanup skipping
  `keep_local` files; `EquipmentConfig` no longer accepts the removed
  fields.
- **Integration:** poller sweep → per-run batched enqueue → verify →
  `sync_state.json` updated → rollup flips to `SYNCED`; a re-modified
  file flips the rollup back to `SYNCING`; the poller discovers and
  drives both a staging-area run and a `nas`-mode run; a transfer
  failure leaves the file eligible and retries.
- **E2E:** Add-Equipment wizard is 4 steps and persists (extends the
  existing flow_16 / flow_26 coverage); file-list "Keep local" toggle;
  a cleared run still lists its files as "On NAS" tombstones.

## Migration

Existing `config.yaml` files carry per-equipment `completeness_signal`
etc. The config loader must tolerate and drop these removed keys on
load (they currently use `extra="forbid"`, so a one-time prune or a
pre-validation migration step is required) so an upgraded install does
not fail to boot.

## Open questions / out of scope

- Whether a stalled writer that pauses > `quiescence_minutes` mid-file
  can produce a false-stable read. Accepted risk for this iteration;
  the manifest-based check that would close it was explicitly declined
  to avoid re-introducing equipment coupling.
- Cross-run global batching is intentionally not pursued (impossible
  given per-run src/dst roots).
