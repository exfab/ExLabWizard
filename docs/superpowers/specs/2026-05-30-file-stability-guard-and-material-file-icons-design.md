# File-stability pre-sync guard + materials file-type icons

- **Status:** Draft (brainstormed 2026-05-30)
- **Branch / worktree:** `materials-ico`
- **Scope:** one combined spec covering two independent subsystems
- **Deliverable of this cycle:** *this design document only* — no production
  code is committed and no implementation plan follows. The module contract,
  a reference-implementation sketch, the test matrix, and the README usage
  note live in the appendices so a later implementer (or an external
  consumer of `file_stability`) has everything in one place.

---

## Summary

Two features ship on the `materials-ico` branch. They are independent and
touch disjoint code, but are bundled into one spec because each is modestly
sized.

1. **File-stability pre-sync guard (Part A).** A new stdlib-only module
   `sync/file_stability.py` polls a file's `st_size` on a fixed interval and
   reports whether it has stopped growing. It is wired into the rclone push
   path as a **last-instant completeness check**, so `rclone copy` only
   transfers files that are done being written. The module is implemented
   verbatim to the supplied contract (pure functions, no classes,
   `ThreadPoolExecutor`, `time.sleep`) and is invoked from the async sync
   worker via `asyncio.to_thread`, which keeps it independently reusable by
   external job scripts / a CLI.

2. **Materials file-type icons (Part B).** Material Design Icons (MDI),
   category-colored via the existing Okabe-Ito palette, are shown on the file
   surfaces (centre file list + the metadata pane's selected-file card) and,
   reinterpreted as **node-type** icons, on the equipment/project/run tree.
   The MDI webfont is vendored locally so the frozen desktop app renders it
   offline.

---

## Motivation / goals

### Part A — why a guard at all (honest scoping)

The codebase already settles files before sync: `QuiescenceSyncPoller`
observes each file's `(st_size, st_mtime_ns)` signature across sweeps and
treats a file as *quiet* only once that signature is unchanged for
`sync.quiescence_minutes` (default 10). The poller measures from
first-observed time, **not** from `mtime` age, so it already side-steps the
classic "`rclone --min-age` is fooled because `mtime` updates on every
`write()`" problem. Final integrity is also already guaranteed: after
`rclone copy` a reconcile/verify pass compares remote size+mtime to local
and refuses to credit a mismatched file, and a download-and-rehash gate runs
before any local deletion.

So the guard is **not** the integrity authority and **not** a fix for
`--min-age`. Its value, verified against `NASSyncClient._drive_job`, is:

1. **Closes the enqueue → transfer time gap.** `_drive_job` transfers the
   `job.files` subset *captured at enqueue time*. A job can sit queued for
   minutes–hours (worker busy, bandwidth windows, backoff). If a writer
   *resumes* on a file after the poller declared it quiet (append writers, an
   instrument re-acquiring into the same path), rclone copies it mid-write —
   and because `enqueue` is a **no-op against an active job**, the poller
   cannot rescue the in-flight subset. The stale file ships, fails verify,
   and re-uploads later. The guard re-confirms completeness *at the instant
   of transfer* and drops the still-growing file from this batch.
2. **One uniform chokepoint over every path into rclone** — not just the
   10-minute auto-sweep. Manual force-sync, whole-run enqueue, first-sync,
   and deployments that set `quiescence_minutes` as low as 1 all reach
   `rclone copy` today without a real settle. The guard covers them
   identically.
3. **Avoids wasted partial uploads + transient partial-on-NAS churn.**
   Verify catches incompleteness *after* paying for a doomed transfer; the
   guard skips it *before* — meaningful on throttled / metered NAS links.

**Acknowledged limits.** In the common case (auto-sweep, file truly finished
well before transfer) the guard passes in one short poll cycle — near-zero
benefit, near-zero cost. It cannot catch a writer that is *paused* at the
transfer instant (looks stable, is not finished); only re-enqueue-on-change
converges that, exactly as today.

### Part B — why file-type icons

The centre file list renders only a text name today (`file_list.py`); files
of every kind look identical. Type-distinct icons let an operator scan a run
folder at a glance (which outputs are images vs. tabular data vs. instrument
binaries), and node-type icons give the equipment/project/run tree the same
legibility. MDI is chosen for its rich, file-type-specific glyph set;
category coloring (reusing the established Okabe-Ito palette) speeds visual
grouping without minting a new color language.

### Non-goals

- Re-architecting the settle model. The `QuiescenceSyncPoller` stays the
  coarse cross-sweep settle detector; the guard is an additive fine-grained
  last-instant filter. (Refactoring the poller to call `file_stability` was
  considered and rejected as unjustified by the bounded benefit.)
- Making `file_stability` the integrity authority. Verify
  (`rclone check --download` + reconcile) remains authoritative.
- `rclone` invocation changes beyond narrowing `--files-from` to the stable
  subset. No new transport flags; the supplied contract explicitly excludes
  rclone integration.
- Per-file-type colors in the *status* language, or any change to
  `sync_status_icon`.
- A user-configurable icon map / Settings surface for icons (YAGNI — the map
  is a static module constant, easily extended in code).

---

## Decisions (from brainstorming)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Deliverable for this cycle | **Design spec only** — no plan, no code committed |
| 2 | Packaging | **One combined spec** (this document) |
| 3 | Guard ↔ poller relationship | **Final pre-transfer gate** in `_drive_job`; complements the poller, adds no new `SyncJobState` |
| 4 | Module fidelity | **Literal stdlib / blocking** module, called from the async worker via `asyncio.to_thread`; stays reusable by sync callers |
| 5 | Icon surfaces | **File list + metadata pane** (true file surfaces); **tree gets node-type icons** (it has no file rows) |
| 6 | Icon set | **Material Design Icons (MDI)**, webfont vendored locally for offline use |
| 7 | Icon color | **Category-colored via the existing `--oi-*` (Okabe-Ito) palette**; overlap with the 4 status-aliased hues accepted, mitigated by cell separation |

---

## Part A — File-stability guard

### A.1 New module — `src/exlab_wizard/sync/file_stability.py`

Implemented exactly to the supplied contract. Single module, pure functions,
no classes, no global state, stdlib only (`pathlib`, `time`, `logging`,
`concurrent.futures`). Internal helpers underscore-prefixed. Google-style
docstrings with Args / Returns / Raises / Notes. Full type hints.

Public surface:

```python
def is_stable(path: Path, interval: float, checks: int, timeout: float) -> bool: ...
def wait_until_stable(
    paths: Iterable[Path],
    interval: float,
    checks: int,
    timeout: float,
    max_workers: int = 8,
) -> tuple[list[Path], list[Path]]: ...
```

Behavioural contract (unchanged from the brief):

- `is_stable` polls `path.stat().st_size` every `interval` seconds and
  returns `True` once the size is identical across `checks` consecutive
  observations. Returns `False` if the file disappears at any poll or if
  wall-clock elapsed exceeds `timeout`.
- `wait_until_stable` polls all `paths` concurrently (one thread per file,
  bounded by `max_workers`, default 8) and returns
  `(stable_paths, unstable_paths)`. Per-file outcomes are logged at DEBUG via
  stdlib `logging`; no `print`.
- **Validation:** `ValueError` for `interval <= 0` or `checks < 2`.
- **Edge cases:** missing file at poll time → `False` (no raise); size `0` at
  every observation → **stable** (empty file is valid); no busy-wait
  (`time.sleep(interval)` between polls); thread-safe (no shared mutable
  state between threads).
- **Documented Notes (no code workaround):** on NFS mounts callers should set
  `interval >= actimeo` (typically ≥30 s) to defeat attribute-cache
  staleness; on Windows an exclusively-locked file may report `size == 0`
  via `stat()` and callers should account for that; `stat()` follows symlinks
  by default.

A reference implementation sketch is in **Appendix 1**.

### A.2 Integration — `NASSyncClient._drive_job` (`sync/nas_client.py:447`)

The guard runs at the `QUEUED → RUNNING` boundary, *after* the transfer
subset is computed and *before* `push(...)`:

```text
1. subset_rel  = job.files or self._discover_run_files(run_path)
2. abs_paths   = [run_path / rel for rel in subset_rel]
3. cfg         = self._config.sync.stability
   if cfg.enabled:
       stable, unstable = await asyncio.to_thread(
           wait_until_stable, abs_paths,
           interval=cfg.interval_seconds,
           checks=cfg.checks,
           timeout=cfg.timeout_seconds,
           max_workers=cfg.max_workers,
       )
       log.debug("stability: %d stable, %d deferred", len(stable), len(unstable))
       stable_rel = sorted(p.relative_to(run_path).as_posix() for p in stable)

       if not stable_rel:
           # Nothing settled this pass — DEFER, not a failure.
           await self._queue.defer(job.id, next_attempt_at=now + poll_interval_seconds)
           return

       # Narrow the transfer to the stable subset (always an explicit
       # --files-from; a whole-run job becomes an explicit-subset transfer,
       # which is strictly more precise).
       verify_files  = tuple(stable_rel)
       files_from    = self._write_files_from(verify_files)
4. proceed with push(run_path, bwlimit, files_from=files_from) for the stable subset
```

Key points:

- `SyncJobRow` is a frozen dataclass — the job is **not** mutated. The stable
  subset is expressed purely through the `--files-from` temp file and the
  local `verify_files` used by the downstream reconcile.
- **Deferral** (no file stable): the job returns to `QUEUED` with
  `next_attempt_at = now + sync.poll_interval_seconds` and **no** attempt /
  backoff increment — a deferral is explicitly *not* a failure, so it never
  consumes the 5-attempt budget. This needs a small queue affordance: either
  extend `SyncQueue.reset_to_queued` to accept a `next_attempt_at` without
  touching `attempts`, or add `SyncQueue.defer(job_id, next_attempt_at)`.
  (Flagged in Open decisions.)
- **Unstable-but-some-stable:** the unstable files simply remain unsynced
  (their signature still differs from `synced_signature`), so the next
  poller sweep re-enqueues them once this job reaches a terminal state. For
  promptness, `SyncQueue.requeue_with_files` *may* be used to fold the
  unstable subset into a follow-up job — noted as an optional enhancement,
  not required for correctness.
- The pre-existing `run_path.exists()` vanished-local check (`:471`) is
  retained and runs first; the guard sits after it.

### A.3 Config — new `sync.stability` sub-block (`config/models.py`)

Add a nested model to `SyncConfig` (which already carries `enabled`,
`retry_attempts`, `quiescence_minutes`, `ignore_globs`,
`poll_interval_seconds`):

```python
class FileStabilityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    enabled: bool = True
    interval_seconds: float = Field(default=2.0, gt=0)      # mirrors interval > 0
    checks: int = Field(default=3, ge=2)                    # mirrors checks >= 2
    timeout_seconds: float = Field(default=30.0, gt=0)
    max_workers: int = Field(default=8, ge=1)


class SyncConfig(BaseModel):
    ...
    stability: FileStabilityConfig = Field(default_factory=FileStabilityConfig)
```

- The field constraints mirror the module's `ValueError` rules so a bad
  config is rejected at load, not at transfer time.
- **Default rationale.** Because the poller has already held the file ~10 min,
  a genuinely complete file passes in `(checks - 1) * interval ≈ 4 s`;
  `timeout_seconds` only bounds a still-growing file before deferral. The
  docstring carries the NFS-`actimeo` guidance for operators whose source is
  an NFS mount (raise `interval_seconds` to ≥ the mount's `actimeo`).
- Picked up live through the existing `apply_config` path: `_drive_job` reads
  `self._config.sync.stability` per job, so a settings save applies on the
  next job with no tray relaunch.

---

## Part B — Materials file-type icons

### B.1 Vendoring the MDI webfont (offline)

NiceGUI's bundled Quasar runtime already understands `mdi-*` icon names, but
the MDI **webfont** itself is not in NiceGUI's static assets (only Roboto /
Material families ship). Because the app is a frozen PyInstaller desktop
binary running in a pywebview window (no CDN), MDI must be vendored locally:

- Vendor `@mdi/font`'s `css/materialdesignicons.min.css` and
  `fonts/materialdesignicons-webfont.woff2` under
  `assets/fonts/mdi/`. License: **Apache-2.0** (the MDI font); recorded in
  the spec and alongside the vendored files.
- Served automatically at `/assets/fonts/mdi/…` by the existing
  `register_static_assets()` mount; **no `exlab_wizard.spec` change** — the
  `DATAS.append(("assets", "assets"))` tree already bundles everything under
  `assets/`.
- Injected once, app-wide, next to the theme CSS:
  `ui.add_head_html('<link rel="stylesheet" '
  'href="/assets/fonts/mdi/materialdesignicons.min.css">', shared=True)` in
  (or beside) `ui/theme.py:register_theme`. Trim the vendored CSS's
  `@font-face src` URL to the bundled `.woff2` only.

### B.2 New pure component — `src/exlab_wizard/ui/components/file_type_icon.py`

Mirrors the established `sync_status_icon.py` pattern exactly: a single
source-of-truth map + pure prop functions + a thin NiceGUI render wrapper, so
the mapping is unit-testable without NiceGUI.

```python
def file_type_props(name: str, *, is_dir: bool) -> dict[str, str]:
    """-> {"icon_name", "color_var", "tooltip", "category"}.

    Case-insensitive suffix lookup. Directories -> mdi-folder. An unknown
    extension -> mdi-file-outline / --color-muted. Pure; no NiceGUI import.
    """

def file_type_icon(name: str, *, is_dir: bool) -> Any:
    """NiceGUI <q-icon> built from file_type_props (lazy ui import,
    matching sync_status_icon's import-guard idiom)."""

def file_type_legend_entries() -> list[dict[str, str]]:
    """One row per category, for an optional Files-header legend popover."""
```

The mapping is a small dict keyed by lowercased suffix, grouped into
categories; each category carries an MDI glyph and an `--oi-*` color token
(referenced directly, no new design tokens). Representative rows (full map
lives in the module and is trivially extended):

| Category | Example extensions | MDI glyph | Color token |
|---|---|---|---|
| Documents / text | `txt` `md` `rtf` `log` | `mdi-file-document-outline` | `--oi-grey` |
| PDF | `pdf` | `mdi-file-pdf-box` | `--oi-vermilion` |
| Tabular / data | `csv` `tsv` `xlsx` `xls` `parquet` | `mdi-file-delimited` / `mdi-microsoft-excel` | `--oi-green` |
| Images | `png` `jpg` `jpeg` `tif` `tiff` `gif` `bmp` `svg` | `mdi-file-image` | `--oi-sky` |
| Structured / markup | `json` `yaml` `yml` `xml` `toml` `ini` | `mdi-code-json` / `mdi-file-code` | `--oi-blue` |
| Code / scripts | `py` `js` `ts` `sh` `r` `m` `c` `cpp` `java` | `mdi-language-python` / `mdi-file-code` | `--oi-purple` |
| Archives | `zip` `tar` `gz` `7z` `rar` | `mdi-folder-zip` | `--oi-orange` |
| Sci / instrument | `h5` `hdf5` `fits` `nd2` `czi` `dm3` `mrc` `raw` | `mdi-microscope` | `--oi-yellow` |
| Folder | — (`is_dir`) | `mdi-folder` | `--oi-grey` |
| Unknown / other | (fallthrough) | `mdi-file-outline` | `--color-muted` |

Suffix handling: lowercased `Path(name).suffix`; a short allow-list of
double extensions (`.tar.gz`, `.tar.bz2`) is special-cased to the archive
category. Color overlap with status hues (green/sky/orange/vermilion) is
accepted per Decision #7 — the file-type icon sits in the **Name** column and
the sync-status icon in the **Status** column, so context disambiguates, and
the glyphs themselves differ.

### B.3 Wiring the surfaces

1. **File list — `ui/components/file_list.py` (`_render_row`, ~line 289).**
   In the Name `<td>`, prepend `file_type_icon(entry.name, is_dir=entry.is_dir)`
   before the existing name label and ahead of the "kept local" badge. Pure
   resolver stays NiceGUI-free; only the render call site changes.

2. **Metadata pane — `ui/components/metadata_pane.py`.** In the selected-file
   sub-card header, render a larger `file_type_icon(...)` next to the file
   name (`font-size` bumped via style, same props source).

3. **Tree (node-type icons) — `ui/components/tree.py`.** The tree's leaves are
   equipment / project / run nodes, **not** files, so this is a *node-type*
   icon, distinct from file-type icons but drawn from the same MDI set for
   visual consistency:
   - equipment → `mdi-microscope` (or `mdi-flask`), project → `mdi-folder`,
     run → `mdi-file-document`, test-run → `mdi-flask-outline`.
   - The mapping is a tiny `TreeNode.kind → (glyph, color)` table (a separate
     constant from the file-type map, since the domains differ). Node-type
     icons coexist with the existing run **sync** icon (rendered as an
     `<img>` via `_sync_icon_url`); the node-type icon precedes the label,
     the sync icon stays where it is. Placement must avoid crowding the run
     row — verified during implementation.

### B.4 DESIGN.md note

DESIGN.md §01 currently scopes the Okabe-Ito palette to "data-visualization
only." File-type / node-type category coloring is a categorical encoding in
the same spirit; the spec adds a one-line note extending the rule to cover
"categorical UI encodings (file-type / node-type icons)" so `design.py` and
DESIGN.md stay aligned. No new tokens are introduced.

---

## Config schema (summary)

```yaml
sync:
  enabled: true
  retry_attempts: 3
  quiescence_minutes: 10
  poll_interval_seconds: 120
  ignore_globs: [ ... ]
  stability:                 # NEW
    enabled: true
    interval_seconds: 2.0
    checks: 3
    timeout_seconds: 30.0
    max_workers: 8
```

No icon-related config (static module map). The unrelated
`validator.content_scan_extensions` Settings field is **not** touched.

---

## Tests

| Area | File | Coverage |
|------|------|----------|
| Module (the 6 required cases) | `tests/unit/sync/test_file_stability.py` | (1) stable file; (2) unstable file; (3) file disappears mid-poll; (4) timeout before stability; (5) empty file → stable; (6) invalid args → `ValueError`. Plus: `wait_until_stable` partitioning, `max_workers` bound, DEBUG logging emitted, symlink-follow. Driven with `tmp_path` + a controllable clock / tiny writer thread. |
| Worker integration | `tests/unit/sync/test_nas_client.py` (extend) | stable subset transfers (`--files-from` narrowed); unstable file dropped; all-unstable → `defer` (no attempt increment, `next_attempt_at` set); guard disabled (`stability.enabled = false`) → legacy behaviour. |
| Config | `tests/unit/config/test_models.py` (extend) | `sync.stability` defaults; `interval_seconds <= 0` and `checks < 2` rejected; `extra="forbid"`. |
| Icon map | `tests/unit/ui/test_file_type_icon.py` | extension → glyph/category/color; case-insensitive; `.tar.gz` double-suffix; `is_dir` → folder; unknown → fallthrough; legend entries cover every category. |
| Tree node-type map | `tests/unit/ui/test_tree.py` (extend) | `TreeNode.kind` → node glyph/color; coexistence with sync icon. |
| Render smoke | existing file-list / metadata / factories smoke tests (extend) | icon element present in the Name cell / selected-file card. |

*Repo convention note:* the brief named the test file `test_file_stability.py`;
the repo keeps tests under `tests/unit/<area>/`, so it lands at
`tests/unit/sync/test_file_stability.py` (same coverage, repo-idiomatic
location) rather than co-located with the module.

Commands (per repo memory): `uv run --extra test pytest`, lint with
`uvx ruff`.

---

## Risks / trade-offs

- **MDI bundle size.** Adds a webfont (~hundreds of KB compressed) to the
  frozen app. Acceptable for a desktop binary; isolated under
  `assets/fonts/mdi/`.
- **Category/status color overlap.** 4 of 8 OI hues are aliased by status
  colors. Mitigated by column separation + distinct glyphs; revisit if user
  testing shows confusion (fallback: restrict to the 4 non-status OI hues, or
  go neutral — both pre-considered).
- **Guard is not integrity.** A paused-mid-write file can still pass the
  guard; verify remains the safety net. The guard's job is efficiency /
  timeliness, framed as such throughout.
- **Deferral starvation (theoretical).** A perpetually-growing file defers
  forever (by design — it is never complete). It never blocks other files
  (per-file partitioning) and never burns the retry budget.
- **Queue API touch.** Deferral needs a non-failing re-queue affordance
  (`defer` / extended `reset_to_queued`) — small, additive.

---

## Open decisions (for the implementer / plan phase)

1. **Deferral mechanism:** new `SyncQueue.defer(job_id, next_attempt_at)` vs.
   extending `reset_to_queued` with an optional `next_attempt_at` that leaves
   `attempts` untouched. (Either is fine; pick during implementation.)
2. **Unstable-file promptness:** rely on the next poller sweep (simplest) vs.
   eager `requeue_with_files` follow-up. Default to the simple path unless a
   latency need appears.
3. **Equipment node glyph:** `mdi-microscope` vs. `mdi-flask` — a cosmetic
   pick to settle when wiring the tree.

---

## Appendix 1 — `file_stability.py` reference implementation (sketch)

> Illustrative; the implementation cycle owns final form, but this captures
> the intended shape so the contract is unambiguous and externally reusable.

```python
"""Pre-rclone file-stability guard: confirm files have stopped growing.

Polls ``st_size`` on a fixed interval and reports whether a file's size has
been identical across N consecutive observations. Used as a pre-flight check
before ``rclone`` sync invocations so only complete files transfer.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_log = logging.getLogger(__name__)


def _poll_size(path: Path) -> int | None:
    """Return ``path``'s current size, or ``None`` if it does not exist."""
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return None
    except OSError:
        return None


def is_stable(path: Path, interval: float, checks: int, timeout: float) -> bool:
    """Return True once ``path``'s size is constant across ``checks`` polls.

    Args:
        path: File to observe (symlinks are followed by ``stat()``).
        interval: Seconds between polls. Must be > 0.
        checks: Consecutive equal observations required. Must be >= 2.
        timeout: Wall-clock budget in seconds; exceeding it returns False.

    Returns:
        True if the size was identical across ``checks`` consecutive polls
        (an all-zero-size file counts as stable). False if the file
        disappears at any poll or the timeout elapses first.

    Raises:
        ValueError: If ``interval <= 0`` or ``checks < 2``.

    Notes:
        NFS: set ``interval >= actimeo`` (typically >= 30 s) so the attribute
        cache does not mask a still-growing file. Windows: an exclusively
        locked file may report ``size == 0`` via ``stat()``; account for that
        at the call site. No busy-wait: ``time.sleep(interval)`` between polls.
    """
    if interval <= 0:
        raise ValueError("interval must be > 0")
    if checks < 2:
        raise ValueError("checks must be >= 2")

    deadline = time.monotonic() + timeout
    last = _poll_size(path)
    if last is None:
        _log.debug("unstable (missing): %s", path)
        return False

    stable_count = 1
    while stable_count < checks:
        if time.monotonic() >= deadline:
            _log.debug("unstable (timeout): %s", path)
            return False
        time.sleep(interval)
        size = _poll_size(path)
        if size is None:
            _log.debug("unstable (vanished): %s", path)
            return False
        if size == last:
            stable_count += 1
        else:
            stable_count = 1
            last = size
    _log.debug("stable: %s (size=%d)", path, last)
    return True


def wait_until_stable(
    paths: Iterable[Path],
    interval: float,
    checks: int,
    timeout: float,
    max_workers: int = 8,
) -> tuple[list[Path], list[Path]]:
    """Poll ``paths`` concurrently; return ``(stable, unstable)``.

    Args:
        paths: Files to observe.
        interval: Seconds between polls (forwarded to ``is_stable``).
        checks: Consecutive equal observations required.
        timeout: Per-file wall-clock budget in seconds.
        max_workers: Thread cap (one thread per file, bounded) to avoid NFS
            overload. Defaults to 8.

    Returns:
        Two lists: files that reached stability, and files that did not
        (missing, still growing, or timed out).

    Raises:
        ValueError: If ``interval <= 0`` or ``checks < 2`` (validated per file
            by ``is_stable``).
    """
    items = list(paths)
    if not items:
        return [], []
    workers = max(1, min(max_workers, len(items)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(
            pool.map(lambda p: (p, is_stable(p, interval, checks, timeout)), items)
        )
    stable = [p for p, ok in results if ok]
    unstable = [p for p, ok in results if not ok]
    _log.debug("wait_until_stable: %d stable, %d unstable", len(stable), len(unstable))
    return stable, unstable
```

## Appendix 2 — README usage note (3 sentences)

> **File-stability pre-sync check.** Before invoking `rclone`, call
> `wait_until_stable(paths, interval, checks, timeout)` to partition a batch
> into files that have stopped growing and files that are still being written,
> and hand only the stable list to `rclone` via `--files-from`. Use the
> single-file `is_stable(path, interval, checks, timeout)` for a one-off
> pre-flight check inside a job script or pipeline runner. On NFS-mounted
> sources, set `interval` to at least the mount's `actimeo` (typically ≥30 s)
> so attribute-cache staleness cannot mask an in-progress write.

## Appendix 3 — Surface / file inventory

| Concern | Files |
|---|---|
| Guard module (new) | `src/exlab_wizard/sync/file_stability.py` |
| Guard integration | `src/exlab_wizard/sync/nas_client.py` (`_drive_job`); `src/exlab_wizard/sync/queue.py` (defer affordance) |
| Guard config | `src/exlab_wizard/config/models.py` (`FileStabilityConfig`, `SyncConfig.stability`) |
| Icon component (new) | `src/exlab_wizard/ui/components/file_type_icon.py` |
| MDI webfont (new assets) | `assets/fonts/mdi/materialdesignicons.min.css`, `assets/fonts/mdi/materialdesignicons-webfont.woff2` |
| Icon injection | `src/exlab_wizard/ui/theme.py` (`register_theme` head link) |
| Icon surfaces | `src/exlab_wizard/ui/components/file_list.py`, `…/metadata_pane.py`, `…/tree.py` |
| Design tokens note | `DESIGN.md` §01, `src/exlab_wizard/ui/design.py` (note only; no new tokens) |
| Tests | `tests/unit/sync/test_file_stability.py`, `tests/unit/sync/test_nas_client.py`, `tests/unit/config/test_models.py`, `tests/unit/ui/test_file_type_icon.py`, `tests/unit/ui/test_tree.py` |
