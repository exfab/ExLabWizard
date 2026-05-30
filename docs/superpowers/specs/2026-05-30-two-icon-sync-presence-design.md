# Two-icon sync-presence display — design

**Date:** 2026-05-30
**Status:** Approved (design); ready for implementation plan
**Supersedes (UI only):** the single-glyph `sync_status_icon` display and the
single-SVG run-tree rollup (`sync_local.svg` / `sync_cloud.svg`). The backend
sync states are unchanged.

## 1. Problem

The file browser currently shows sync status as a **single colour-coded glyph**
drawn from **ten** backend states (`pending`, `acquiring`, `retrying`,
`syncing`, `on_nas`, `synced`, `cleaned`, `failed`, `blocked_by_validation`,
`override_active`). Most of those distinctions describe the *machinery* of
syncing, which the operator does not act on. The only questions an operator
actually has about a file are:

1. Is it **here** (local)?
2. Is it **on the NAS** (backed up)?
3. Is anything **wrong**?

This design collapses the ten states into a **two-icon presence display** — one
icon for "local", one for "NAS" — using the existing `sync_local.svg` and
`sync_nas.svg` assets, with the **background colour** of each icon encoding that
location's state. The syncing machinery (pending / acquiring / syncing /
retrying) is hidden: an in-progress upload simply reads as "local, not backed up
yet". The backend states, queue, and `sync_state.json` schema are untouched —
this is a presentation change plus one additive backend read.

## 2. Design principle

**Each icon's background colour describes that one location.** The two icons are
always rendered in a fixed order — **local on the left, NAS on the right** — so
"which location" never depends on colour (important for colour-blind operators).

| Colour | Token | Meaning at this location |
|---|---|---|
| 🟢 green | `--color-success` `#2e9e5b` | Present — this is the working copy |
| 🔵 light-blue | `--color-row-selected` `#dceaff` | Present but secondary/cached — safe to clear, it's on the NAS |
| ⬜ gray | `--color-muted` (tinted) | Absent, and that's fine |
| 🔴 red | `--color-danger` `#d2492a` | Problem **here** |
| 🟠 amber | `--color-warning` `#e8a13a` | Held / needs attention **here** |

We introduce sync-specific semantic aliases so the sync UI does not couple to
incidental palette edits: `--color-sync-present`, `--color-sync-cached`,
`--color-sync-absent`, `--color-sync-problem`, `--color-sync-held`. These alias
the tokens above and are added to `design.py` + emitted by
`theme.build_root_css`.

## 3. State taxonomy

The display is driven by a **UI-only derived view enum**, `FileSyncView`, not by
the raw `SyncStatus`. Seven views cover every case:

| # | `FileSyncView` | local bg | nas bg | Meaning |
|---|---|---|---|---|
| 1 | `LOCAL_ONLY` | 🟢 green | ⬜ gray | Here, not backed up yet (incl. pending/syncing/retrying — detail hidden) |
| 2 | `SYNCED` | 🔵 light-blue | 🟢 green | Safe on NAS; local is now just a cache |
| 3 | `ON_NAS` | ⬜ gray | 🟢 green | Backed up; local copy reclaimed (cleaned) |
| 4 | `UPLOAD_FAILED` | 🟢 green | 🔴 red | Local copy fine; NAS push failed (retries exhausted) |
| 5 | `BLOCKED` | 🟢 green | 🟠 amber | Local fine; upload **held** by a hard validation finding |
| 6 | `MISSING` | 🔴 red | 🔴 red | Tracked, but gone locally **and** never confirmed on NAS |
| 7 | `NONE` | — | — | Untracked file / folder — no icons (neutral) |

### 3.1 Mapping from backend signals

`FileSyncView` is derived from four inputs available at the browse layer:

- `on_disk: bool` — file exists locally (disk scan)
- `nas_verified: bool` — `FileSyncRecord.verified_at is not None` (confirmed on NAS)
- `record_present: bool` — a `FileSyncRecord` exists in `sync_state.json`
- `job_status: SyncStatus | None` — **live** sync-queue status for this path
  (`failed` / `blocked_by_validation` / others), or `None` if not queued

```
def file_sync_view(*, on_disk, nas_verified, record_present, job_status):
    if job_status == FAILED and on_disk and not nas_verified:           return UPLOAD_FAILED
    if job_status == BLOCKED_BY_VALIDATION and on_disk and not nas_verified:
                                                                        return BLOCKED
    if on_disk and nas_verified:                                        return SYNCED
    if on_disk and not nas_verified:                                    return LOCAL_ONLY
    if not on_disk and nas_verified:                                    return ON_NAS
    if not on_disk and record_present and not nas_verified:             return MISSING
    return NONE
```

Precedence note: an active `FAILED`/`BLOCKED` job overrides the plain
presence views so a stuck upload is never masked as "local only".

## 4. Components

### 4.1 `sync_status_icon.py` → two-icon pair

Replace the single-glyph model. The module keeps its role as the **pure,
NiceGUI-free single source of truth** for sync presentation, now exposing:

- `FileSyncView` (enum, UI-only).
- `file_sync_view(...)` — the pure mapping in §3.1.
- `sync_pair_props(view) -> {"local": IconCell, "nas": IconCell}` where each
  `IconCell` is `{"svg", "bg_var", "badge", "tooltip", "aria_label"}`. `svg` is
  the asset path (`/assets/sync_local.svg`, `/assets/sync_nas.svg`); `bg_var` is
  the background token; `badge` is an optional corner overlay glyph (see §6);
  `tooltip`/`aria_label` are the worded state.
- `sync_pair_icons(view)` — builds the NiceGUI two-icon row (local left, NAS
  right), each icon an `<img>` on a coloured, rounded background, with the
  tooltip and `data-sync-*` attributes for tests.
- `sync_legend_entries()` — updated to the new six visible views (excludes
  `NONE`), so the Files-header legend popover stays in lock-step with what rows
  render.

The old `_STATUS_TO_PROPS` glyph table, `STATUS_RETRYING`/`STATUS_OVERRIDE`/
`STATUS_ACQUIRING`/`STATUS_SYNCING`/`STATUS_ON_NAS` constants, and the
`strict`/`retry_n`/`retry_m` machinery are removed (no consumer survives the
collapse). `override_active` and the retry counter disappear from the UI — they
were syncing-machinery detail the new model deliberately hides.

### 4.2 `file_list.py` — Status cell

`_render_row`'s Status `<td>` calls `sync_pair_icons(view)` instead of
`sync_status_icon(status, strict=False)`. The row resolves its `FileSyncView`
from the `FileListEntry` (which gains `nas_verified`, `record_present`, and
`job_status` fields, or a single precomputed `sync_view` field — see §5). The
existing tombstone treatment (dim/italic, no "Open in OS") is unchanged; a
tombstone row is simply one whose view is `ON_NAS` or `MISSING`.

### 4.3 `sync_rollup.py` — folder / tree rollup

Generalise the worst-of reduction to operate over `FileSyncView` values with
this severity order (most-attention-worthy first):

```
MISSING > UPLOAD_FAILED > BLOCKED > LOCAL_ONLY > SYNCED > ON_NAS
```

Rationale: problems first; then **at-risk** local-only files (present but not yet
backed up) outrank fully-`SYNCED` ones; `ON_NAS` (done, reclaimed) is calmest.
`NONE` is ignored, as today. The folder metadata pane and the run tree both
render the rolled-up view via `sync_pair_icons`.

### 4.4 Surfaces (all three speak the same language)

- **File rows** (`file_list.py`) — per-file two-icon pair.
- **Run tree** (`browse.py` tree headers) — per-run rolled-up pair, replacing
  the single `sync_local.svg` / `sync_cloud.svg` icon.
- **Metadata pane** — selected folder shows its rolled-up pair.

## 5. Backend changes (additive)

### 5.1 Surface the "missing" view (free — `sync_state.json` only)

Today `_tombstone_entries` **skips** records that are absent on disk and
unverified (`"An unverified, absent record is not a meaningful tombstone."`).
That silently drops genuinely lost files from the listing. Change: emit such
records as `MISSING` rows (tombstone, not openable) instead of skipping. Verified
absent records remain `ON_NAS`. `_file_state_from_record` is extended/replaced to
distinguish these.

### 5.2 Thread live queue status into browse (Group B)

To colour the NAS icon on `UPLOAD_FAILED` / `BLOCKED`, the per-file/per-folder
scans must consult the **sync queue** (the same `deps`-provided queue
`_run_log_from_queue` already reads), building a `path -> SyncStatus` map for the
run and passing each file's `job_status` into `file_sync_view`. This affects:

- `scan_folder_sync` (per-file rows),
- `_build_run_node` / `_run_rollup_status` (tree rollup),
- the browse response schema: the per-file `sync_status` discriminator gains
  `upload_failed` and `missing` (alongside the existing `blocked_by_validation`),
  **or** the response carries a precomputed `sync_view` string. Decision for the
  plan: prefer emitting a precomputed `sync_view` so the wire and the UI share
  one vocabulary and the UI does no re-derivation.

No change to `FileSyncRecord` / `SyncStateJson` / `SyncStatus` enum — the queue
already holds failed/blocked; we only *read* it at browse time.

## 6. Accessibility

Position (local left / NAS right) and the two distinct SVG shapes already encode
"which location" without colour. The remaining colour-only distinction is
*green-present vs red-problem vs amber-held* on the **same** icon. Mitigations:

- **Every icon carries a worded `tooltip` and `aria-label`** (e.g. "On NAS",
  "Upload failed — local copy safe", "Held by validation", "Missing — not found
  locally or on NAS").
- **Problem/held views add a small corner badge glyph** so red/amber are not
  colour-only: `✕` for `UPLOAD_FAILED`/`MISSING`, `!` for `BLOCKED`. Green / blue
  / gray ("everything is fine") stay colour-only — lower stakes.

## 7. Testing impact

- **Rewrite** `tests/unit/ui/test_sync_status_icon.py` for `file_sync_view`
  (mapping table, all seven views) and `sync_pair_props` (colours, badges,
  tooltips). Drop the `strict`/neutral-dash tests.
- **Update** `tests/unit/ui/test_sync_rollup.py` for the new `FileSyncView`
  severity order.
- **Rewrite** `tests/e2e/test_flow_05_browse_view_sync_icons.py`: it currently
  asserts one `sync_local.svg` vs one `sync_cloud.svg` per run; assert the
  two-icon pair and per-view backgrounds instead. Keep the asset-200 check,
  adding `sync_nas.svg`.
- **New** unit tests for the `MISSING` surfacing in `browse.py` and the queue
  status threading (`UPLOAD_FAILED` / `BLOCKED`).
- Existing sync backend / `sync_state_writer` / `pre_sync_gate` tests are
  unaffected.

## 8. Out of scope

- Any change to the sync engine, queue, retry policy, validation gating, or
  `sync_state.json` schema.
- The hidden orchestrator/staging surfaces (see `CLAUDE.md`) — untouched.
- An operator action to *retry* a failed upload from the file row (the row only
  *reports*; retry stays automatic). Could be a follow-up.

## 9. Token summary (for the plan)

| View | local bg token | nas bg token | badge |
|---|---|---|---|
| `LOCAL_ONLY` | `--color-sync-present` | `--color-sync-absent` | — |
| `SYNCED` | `--color-sync-cached` | `--color-sync-present` | — |
| `ON_NAS` | `--color-sync-absent` | `--color-sync-present` | — |
| `UPLOAD_FAILED` | `--color-sync-present` | `--color-sync-problem` | `✕` on NAS |
| `BLOCKED` | `--color-sync-present` | `--color-sync-held` | `!` on NAS |
| `MISSING` | `--color-sync-problem` | `--color-sync-problem` | `✕` on both |
| `NONE` | — (no icons) | — | — |
