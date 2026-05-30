# Main-window UI refresh — Remaining work (v2)

**Date:** 2026-05-29
**Status:** Planning — picks up where the original refresh left off
**Branch:** `feature/gui-imrprovement-v2`
**Supersedes planning for:** the unfinished tail of
`2026-05-29-main-window-ui-refresh-design.md` (Phases 5–6) **plus** the new
divergences introduced during the Phase-4 live demo (the metadata **popover**
and the now-wired **design-token theme**).

This doc is the single source of truth for what is *left* to do on the
main-window (`/main`) refresh. The original design spec
(`2026-05-29-main-window-ui-refresh-design.md`) and plan
(`~/.claude/plans/happy-cooking-horizon.md`) remain the authority for the
parts already shipped; where this doc and the original spec disagree (the
metadata popover), **this doc wins** and the original spec must be reconciled
(see §A).

---

## 1. What has shipped (committed on the branch)

| Commit | Phase | Summary |
|--------|-------|---------|
| `8b78ef7` | 0 | Design spec + approved mockups |
| `cb3ceb0` | 1 | Design tokens + fixed undefined CSS vars (`design.py` / `theme.py`) |
| `65334e2` | 2 | Pure resolvers: `row_background`, tolerant `sync_status_props(strict=)`, `sync_rollup` |
| `4ec1ce0` | 3 | Panel framing (`framed_pane.py` + `main.py` wrapping) |
| `dcf7095` | 4 | Selection → metadata sub-card (URL/navigate `?file=`/`?q=`/`?density=`), per-folder refresh, tolerant status icons in the file list |
| `c201ddc` | post-4 | **Metadata popover** + **`register_theme` wired app-wide** + Files-header left grouping + metadata overflow + e2e demo wiring |
| `f4a7149` | post-4 | Panes pinned to the viewport height with internal scroll |

Unit gate is green at the tip: `uv run ruff check` + `ruff format --check` +
`mypy src` clean; `tests/unit/ui/` **603 passed**. The Phase-4 interaction was
demo-verified live (Playwright MCP against the seeded e2e app on `:8099`):
file/folder selection → sub-card, tombstone variant, per-folder refresh,
status icons, viewport-height + internal scroll all confirmed.

---

## 2. Divergences from the approved design spec (must be reconciled)

Two deliberate, user-directed changes during the demo now differ from the
approved `…-design.md`:

1. **Metadata is a floating popover, not a docked pane (changes §4.4 / §5).**
   The approved spec chose **Option B** — a metadata pane docked beside the
   Files pane, describing the selected file/folder within tree context. The
   shipped UI instead floats the metadata as an **absolute overlay popover**
   over the right of the Files pane (`position: absolute; top:0; right:0;
   width:40%`), toggled by the vertical "Metadata" tab on its left edge via
   the existing `right_pane` URL param. Open → raised panel over Files; closed
   → full-width file list, tab parked at the right edge.
   - File: `src/exlab_wizard/ui/pages/main.py` (`render_file_explorer_page`,
     the `outer_split.after` block).
   - The selection → sub-card *logic* (mount `_build_selected_file` /
     `_build_selected_folder`, `metadata_pane` sub-card) is unchanged — only
     the container changed from a flex column to an absolute overlay.

2. **The design-token theme is now actually injected (fixes a latent bug).**
   `register_theme()` (the `:root { … }` block from `build_root_css`) was
   **never called** by the running app — only by a unit test — so every
   `var(--color-*)`/`var(--sp-*)`/… reference resolved to its scattered inline
   literal fallback (or to empty where a component had none). It is now wired
   app-wide (`shared=True`) from `mount_ui` (`src/exlab_wizard/ui/mount.py`)
   and from the e2e harness. This turns on the intended grey canvas
   (`--color-bg = #f5f7fa`), the body/heading typography, and resolved tokens
   on **every** page, not just `/main`.

**Action:** update `2026-05-29-main-window-ui-refresh-design.md` §4.4/§5 to
record the popover as the approved layout (or explicitly mark it experimental),
and add a one-line note in §3.2 that `register_theme` is now wired so tokens
are live (the inline literal fallbacks are now a safety net, not the only
source).

---

## A. Spec reconciliation (do first)

- [ ] Amend the design spec for the **popover** layout (§4.4/§5) and the
      **`register_theme` wired** fact (§3.2). Keep the approved mockups but
      annotate that the metadata region is now an overlay.
- [ ] Decide & record: is the popover **the** direction, or a toggle between
      docked/popover? (Current code is popover-only.) This decision gates how
      much of Phase 5's metadata-pane polish applies.

---

## B. Phase 5 — Polish (remaining; reconcile each item with the popover)

Each item is self-contained. Now that `register_theme` is wired, scoped CSS on
Quasar classes (e.g. tree selection) will actually resolve its tokens.

- [ ] **Unified selection (OQ-6)** — `components/tree.py` + `theme.py`. Give
      the selected **tree** node the same `--color-row-selected` fill + 3px
      `--color-row-selected-bar` left bar as file rows (file rows already do
      this via `row_background`, which now carries literal fallbacks). Comment
      the Quasar-class (`.q-tree__node--selected`) dependency.
- [ ] **Sync tooltips + legend** — `tree.py`, `file_list.py`, `main.py`. Tree
      header-slot `<img>` gets a `:title` from the node's sync status; the
      file-list Status cell already routes through `sync_status_icon`
      (Phase 4) so it carries the icon tooltip — add a "?" legend popover in
      the Files header (next to the count + refresh) listing each state's icon
      + meaning sourced from `_STATUS_TO_PROPS` (single source of truth).
- [ ] **Live status segments** — `mount.py` `_build_main_state`. Derive
      `SEGMENT_NORMAL/WARNING/DANGER` + counts for Sync / Validator
      (`problems_count_hard > 0` → WARNING) / LIMS (unreachable → DANGER) /
      Staging. Extract the derivation into a pure helper for unit testing;
      the footer component is otherwise unchanged.
- [ ] **Empty states** — `main.py`, `metadata_pane.py`, `file_list.py`.
      Replace the three bare labels with an icon + one-line hint.
- [ ] **Toolbar grouping + density** — `main.py`. Divider + gap between
      creation and utility actions (order/testids preserved); density
      (`?density=compact`, already threaded onto `MainPageState`) → a CSS class
      on the Files card toggling row padding `--sp-1`/`--sp-2` (**file list
      only**, padding only).
- [ ] **Search wiring + affordances (OQ-2)** — `main.py`. The search input is
      still **inert** (`main.py`, the Explorer pane). Bind it → `?q=` (already
      threaded) → `chip_state_to_tree_filters(..., search=q)` via
      `on_value_change` + ~250 ms debounce (same re-navigate pattern as the
      filter chips); add a result count from `build_nodes`, `clearable` (×),
      and a no-matches state.

**Tests (Phase 5):** `test_status_bar_segment.py` (derivation helper);
`test_main_page`/`test_pages.py` (search wiring → `TreeFilters.search`, result
count, no-matches, density class); `test_tree`/`test_components` (selection
style + tooltip shape where assertable).

---

## C. Phase 6 — e2e + final verification (highest-risk remaining work)

The e2e suite has **not been run** since Phases 3–4 and the post-demo layout
changes. Several flows almost certainly need updates because the DOM shape
changed. Known break points to check first:

- [ ] **File-list Status cell is now an icon, not text** (`file_list.py`
      `_render_row` → `sync_status_icon(..., strict=False)`). Any flow asserting
      the literal status string ("synced"/"acquiring"/…) in a file row will
      fail — assert on the icon / a `data-*` hook instead. (Add a status
      `data-*` attribute if flows need a stable selector.)
- [ ] **Metadata is an absolute popover** (`data-testid="metadata-pane-card"`
      now `position: absolute`, overlaying Files). Flows asserting a docked /
      side-by-side metadata pane, or its width/position, need updating. Open/
      close still rides `right_pane`.
- [ ] **Toggle label changed** to **"Metadata"** (was "Collapse metadata" /
      "Expand metadata"); `data-testid="toggle-right-pane"` preserved. Update
      any text assertions.
- [ ] **Framed panes** add testids `explorer-pane`, `files-pane`,
      `files-pane-header`, `files-pane-count`, plus `files-refresh`,
      `metadata-selected-file`, `metadata-selected-folder`, and file rows now
      carry `data-selected="true"` + `data-path`. Use these for new selection
      flows.
- [ ] **`register_theme` app-wide** changes the rendered look of **every**
      page (grey canvas, body/heading fonts, resolved tokens). Re-check any
      screenshot/visual e2e and `scripts/generate_screenshots.py` output
      against the mockups; this is the single biggest visual blast radius.
- [ ] **`tests/e2e/_test_app.py`** was modified (its `/main` handler now wires
      `file`/`q`/`density`, `on_select_file`, `on_refresh_folder`, and
      `register_theme`) as a thin slice pulled forward to enable the demo.
      Finalize/cover it properly here; confirm it still mirrors production
      (`mount.py` `_main`).
- [ ] New e2e smoke: select a file row → it highlights + the metadata sub-card
      appears in the popover while the tree run-context remains; per-folder
      refresh works; full-height + internal scroll holds (no page scroll).
- [ ] Full suite: `uv run pytest` (respect the **91% coverage gate**).
- [ ] Static: `uv run ruff check` + `ruff format --check` + `mypy src`.
- [ ] Visual confirm: launch the app (the `/run` or `/verify` skill, or
      `scripts/generate_screenshots.py`) and compare `/main` (run selected +
      file selected, popover open/closed) against the approved mockups.

---

## D. Cleanup / tech-debt

- [ ] **Thin redundant literal fallbacks (optional).** Now that
      `register_theme` is wired, the scattered `var(--x, #literal)` fallbacks
      in `framed_pane.py`, `file_list.row_background`, `main.py` toggle, and the
      footer are a safety net rather than the only source. Either keep them
      (defensive, since `register_theme` could be skipped in a headless/cron
      render) and document that, or remove them in a focused pass. **Decide and
      record; do not silently leave both.**
- [ ] **Scratch artifacts.** Delete the demo PNGs in the repo root
      (`phase4-*.png`), the `.playwright-mcp/` snapshots, and the throwaway
      uvicorn server on `:8099`. Delete `RESUME.md` before merge (it is a
      working note, intentionally uncommitted).
- [ ] **Confirm `register_theme` has no double-injection.** It is called once
      from `mount_ui` with `shared=True`; verify no per-page duplicate creeps
      in during Phase 5.

---

## E. Risks & open questions

1. **`register_theme` app-wide visual regression (highest risk).** Only
   `/main` was visually verified this session. Welcome, Settings, the three
   wizards, Problems, Staging, and Templates now render with the theme's body
   background + typography + resolved tokens for the first time. Some
   components used `var(--x)` with **no** fallback, so they previously rendered
   browser defaults and now snap to real tokens. **Action:** screenshot every
   page before/after and eyeball for regressions.
2. **Popover usability.** An absolute overlay covers the right ~40% of the
   Files pane when open. Confirm the file list's Status column / wide rows are
   still reachable (horizontal scroll), and that the closed-state re-open tab
   at the right edge is discoverable.
3. **Full-height overrides are `/main`-only** (`ui.query` in
   `render_file_explorer_page`). If other pages should also fill the viewport,
   that is separate, explicit work.
4. **Density + search are threaded but not wired** (`MainPageState.density` /
   `search_query` exist; the search box is inert) — Phase 5 finishes them.

---

## F. Deferred / out of scope (from design spec §11)

Make-the-explorer-live (`@ui.refreshable` + new-file-highlight fade); folder
**total size** (recursive, off the render path); promoting sync severity onto
`SyncStatus` (schema-version-coordinated); app-wide / tree density; file-type
icons; keyboard navigation.

---

## Working notes (carry forward)

- **Sequential tool calls.** Earlier in this effort, parallel/batched Bash
  triggered a classifier outage that cancelled queued calls and the edits
  after them. Keep mutating tool calls one-per-turn; wait for each result.
- **Stage files explicitly.** Never `git add -A`/`.` — `graphify-out/` churns
  hundreds of untracked cache files; `git add <exact paths>` only.
- **mypy is the gate, not Pyright.** Pyright "import nicegui/pytest/msgspec/
  fastapi could not be resolved" warnings are venv-invisibility noise.
- **The e2e harness has its OWN `/main` handler** (`tests/e2e/_test_app.py`),
  separate from production `mount.py`. Both must be kept in step.
- **NiceGUI renders into the auto-index page outside `@ui.page`,** so
  components can be rendered directly in unit tests and their element tree
  inspected (walk `.slots`, read `._props`/`._style`, invoke
  `EventListener.handler`) — see `test_file_list.py`/`test_metadata_pane.py`.
- **Relaunch the demo server:**
  `EXLAB_TESTING=1 EXLAB_STATE_DIR=/tmp/exlab-demo-state EXLAB_PORT=8099 \
  PYTHONPATH="$PWD:$PWD/src" uv run python -m uvicorn \
  tests.e2e._test_app:create_app_factory --factory --port 8099`
  then drive `/main?selected=TEST_EQ1/LIMS-001/Run_2026-05-07&file=…`.
- **Per-phase gate** (every phase, in order): `ruff check` + `ruff format
  --check` → `mypy src` → scoped `pytest` → `feature-dev:code-reviewer` over
  the diff → apply valid fixes → re-run pytest → commit (Co-Authored-By
  trailer).
