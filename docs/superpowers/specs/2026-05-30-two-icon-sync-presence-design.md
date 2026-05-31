# Two-icon sync-presence display — design

**Date:** 2026-05-30
**Status:** Approved (design); ready for implementation plan
**Supersedes (UI only):** the single-glyph `sync_status_icon` display and the
single-SVG run-tree rollup (`sync_local.svg` / `sync_cloud.svg`). `sync_cloud.svg`
is **retired** — the tree now uses `sync_nas.svg` so it speaks the same vocabulary
as the file rows. The backend sync states are unchanged.

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

One unified colour language across **both** files (two icons) and folders (one
rollup icon). The two per-file icons are always rendered in a fixed order —
**local on the left, NAS on the right** — so "which location" never depends on
colour (important for colour-blind operators).

**blue = "here" · green = "safe on NAS" · gray = "absent" · red = "problem" ·
amber = "held".**

| Icon | Meaning | Colour | Token |
|---|---|---|---|
| **local** | here (only copy) | 🔵 solid blue | `--color-sync-local` |
| **local** | here, also on NAS (cache — safe to clear) | 🔵 faded blue | `--color-sync-cached` |
| **local** | not here, and that's fine | ⬜ gray | `--color-sync-absent` |
| **local** | gone / lost | 🔴 red | `--color-sync-problem` |
| **nas** | safe on NAS | 🟢 green | `--color-sync-safe` |
| **nas** | not on NAS yet | ⬜ gray | `--color-sync-absent` |
| **nas** | upload failed | 🔴 red | `--color-sync-problem` |
| **nas** | held by validation | 🟠 amber | `--color-sync-held` |

`sync_local.svg` is itself blue (`#1b75bc`), so blue-for-local reinforces the
glyph rather than fighting it. The two blues both mean "here"; the **fade** is the
only difference and consistently means "also backed up" — and the NAS icon's
green is the primary "is it safe?" tell, so the fade is secondary.

We introduce sync-specific semantic aliases (added to `design.py` + emitted by
`theme.build_root_css`) so the sync UI does not couple to incidental palette
edits:

| Alias | Resolves to | Hex |
|---|---|---|
| `--color-sync-local` | solid blue | `--color-primary` `#1b75bc` |
| `--color-sync-cached` | faded blue | `--color-row-selected` `#dceaff` |
| `--color-sync-safe` | green | `--color-success` `#2e9e5b` |
| `--color-sync-absent` | tinted gray | a light `--color-muted` tint |
| `--color-sync-problem` | red | `--color-danger` `#d2492a` |
| `--color-sync-held` | amber | `--color-warning` `#e8a13a` |

## 3. State taxonomy

The display is driven by a **UI-only derived view enum**, `FileSyncView`, not by
the raw `SyncStatus`. Seven views cover every case:

| # | `FileSyncView` | local bg | nas bg | Meaning |
|---|---|---|---|---|
| 1 | `LOCAL_ONLY` | 🔵 blue | ⬜ gray | Here, not backed up yet (incl. pending/syncing/retrying — detail hidden) |
| 2 | `SYNCED` | 🔵 faded blue | 🟢 green | Safe on NAS; local is now just a cache |
| 3 | `ON_NAS` | ⬜ gray | 🟢 green | Backed up; local copy reclaimed (cleaned) |
| 4 | `UPLOAD_FAILED` | 🔵 blue | 🔴 red | Local copy fine; NAS push failed (retries exhausted) |
| 5 | `BLOCKED` | 🔵 blue | 🟠 amber | Local fine; upload **held** by a hard validation finding |
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

### 4.3 `sync_rollup.py` — folder / tree rollup (single icon)

A folder is a **summary**, so it renders **one** icon (not the two-icon pair) —
"is everything in here safe?". Two steps:

1. **Worst-of reduction** over child `FileSyncView` values, with this severity
   order (most-attention-worthy first):

   ```
   MISSING > UPLOAD_FAILED > BLOCKED > LOCAL_ONLY > SYNCED > ON_NAS
   ```

   Rationale: problems first; then **at-risk** local-only files (present but not
   yet backed up) outrank fully-`SYNCED` ones; `ON_NAS` (done, reclaimed) is
   calmest. `NONE` is ignored, as today.

2. **Single-icon mapping** of the rolled-up view, via a new
   `sync_rollup_icon(view) -> {"svg", "bg_var", "badge", "tooltip"}`:

   | Rolled-up view | SVG | bg |
   |---|---|---|
   | `SYNCED` / `ON_NAS` (all safe) | `sync_nas.svg` | 🟢 green |
   | `LOCAL_ONLY` (not fully synced) | `sync_local.svg` | 🔵 blue |
   | `BLOCKED` (a held file) | `sync_nas.svg` | 🟠 amber |
   | `UPLOAD_FAILED` / `MISSING` (an error) | `sync_nas.svg` | 🔴 red |
   | `NONE` (empty / untracked) | — | none |

   This is exactly the operator's stated model: fully synced → `sync_nas` green;
   not synced → `sync_local` blue; error → `sync_nas` red (held → amber).

### 4.4 Surfaces

- **File rows** (`file_list.py`) — per-file **two-icon** pair (detail).
- **Run tree** (`browse.py` tree headers) — per-run **single** rollup icon
  (`sync_rollup_icon`), replacing the old `sync_local.svg` / `sync_cloud.svg`
  glyph. `sync_cloud.svg` is retired.
- **Metadata pane** — selected folder shows its **single** rollup icon.

Files show both locations (detail); folders summarise to one icon. They share the
same colour language, so the summary never contradicts the detail beneath it.

## 5. Backend changes (additive)

### 5.1 Surface the "missing" view (free — `sync_state.json` only)

Today `_tombstone_entries` **skips** records that are absent on disk and
unverified (`"An unverified, absent record is not a meaningful tombstone."`).
That silently drops genuinely lost files from the listing. Change: emit such
records as `MISSING` rows (tombstone, not openable) instead of skipping. Verified
absent records remain `ON_NAS`. `_file_state_from_record` is extended/replaced to
distinguish these.

### 5.2 Propagate per-run failure from `creation.json` (Group B)

**As built (refinement of the original "live queue" idea).** Per-file failure is
never persisted, and the only per-file granularity the queue offers is a per-*run*
job state reached through an accessor (`NASSyncClient.get_by_run_path`) that does
not exist. The robust, persisted source is the run's **`creation.json`
`sync_status`** (`pending` / `synced` / `cleaned` / `failed` /
`blocked_by_validation`), already maintained by the orchestrator and read
tolerantly in browse. So failure is surfaced at the **run** level and propagated
to that run's not-yet-verified on-disk files:

- `_run_failure_flags(run_root)` reads `creation.json` (`read_msgspec_json(..., CreationJson)`,
  try/except → `(False, False)` on absence/corruption) and returns
  `(run_failed, run_blocked)`.
- `_file_state_from_record(record, *, on_disk, run_failed, run_blocked)` returns
  `upload_failed` / `blocked` for an unverified on-disk file when the run is
  failed / blocked (a **verified** file stays `synced` regardless), and `missing`
  for an unverified, locally-absent record (the lost-file fix).
- The backend emits these discriminator strings on the existing per-file
  `sync_status` field (`upload_failed`, `blocked`, `missing` join
  `synced`/`syncing`/`acquiring`/`on_nas`); the UI maps the string to a
  `FileSyncView` via `file_sync_view` — no new wire field, no UI re-derivation.

Affects `scan_folder_sync` (per-file rows) and `_run_rollup_status` (tree rollup,
which short-circuits to `blocked`/`upload_failed` before the
`SyncStateWriter.rollup_state` fallback).

No change to `FileSyncRecord` / `SyncStateJson` / `SyncStatus` enum — only reads.

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
  severity order, and add tests for `sync_rollup_icon` (the single-icon mapping:
  `sync_nas` green/amber/red vs `sync_local` blue).
- **Rewrite** `tests/e2e/test_flow_05_browse_view_sync_icons.py`: it currently
  asserts one `sync_local.svg` vs one `sync_cloud.svg` per run. Assert the new
  tree rollup icon instead (`sync_nas.svg` for synced/cleared runs, `sync_local.svg`
  for not-fully-synced) plus the per-view backgrounds, and the file-row two-icon
  pair. Update the asset-200 check to `sync_local.svg` + `sync_nas.svg`
  (`sync_cloud.svg` is no longer served).
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

**Per-file two icons:**

| View | local bg token | nas bg token | badge |
|---|---|---|---|
| `LOCAL_ONLY` | `--color-sync-local` | `--color-sync-absent` | — |
| `SYNCED` | `--color-sync-cached` | `--color-sync-safe` | — |
| `ON_NAS` | `--color-sync-absent` | `--color-sync-safe` | — |
| `UPLOAD_FAILED` | `--color-sync-local` | `--color-sync-problem` | `✕` on NAS |
| `BLOCKED` | `--color-sync-local` | `--color-sync-held` | `!` on NAS |
| `MISSING` | `--color-sync-problem` | `--color-sync-problem` | `✕` on both |
| `NONE` | — (no icons) | — | — |

**Folder / tree single rollup icon (`sync_rollup_icon`):**

| Rolled-up view | SVG | bg token | badge |
|---|---|---|---|
| `SYNCED` / `ON_NAS` | `sync_nas.svg` | `--color-sync-safe` | — |
| `LOCAL_ONLY` | `sync_local.svg` | `--color-sync-local` | — |
| `BLOCKED` | `sync_nas.svg` | `--color-sync-held` | `!` |
| `UPLOAD_FAILED` / `MISSING` | `sync_nas.svg` | `--color-sync-problem` | `✕` |
| `NONE` | — (no icon) | — | — |
