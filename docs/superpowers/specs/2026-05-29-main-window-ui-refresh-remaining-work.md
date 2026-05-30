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
| `01edee7` | 5 | **Search wiring (OQ-2)** + shared **empty-state** placeholder + toolbar **group divider** |
| `1bb3c8d` | 5 | **Live footer status segments** (Validator → WARNING on hard finding, LIMS → DANGER when unreachable) via a pure `derive_footer_segment_states` helper |
| `a31b916` | 5 | **File-list density** toggle + **unified tree selection** (OQ-6) + **sync tooltips** + **sync-status legend** popover |
| `e72cd73` | 6 | **e2e flow 28** (file selection + search/density/legend) + UX catalog/doc; suite + 91.70% coverage + static all green |

**Phases 5 and 6 are complete.** Unit gate is green at the tip: `uv run ruff check` +
`ruff format --check` + `mypy src` clean; `tests/unit/ui/` **623 passed**. Every
Phase-5 item was **live-verified via Playwright** (DOM inspection + screenshots)
against the seeded e2e app on `:8099`, in addition to the Phase-4 verification:

- Search filters the tree + result-count / no-matches pill (URL-seeded and
  interactive typing → debounce → navigate).
- Empty states (Files / metadata) render the icon + hint.
- Toolbar divider separates creation vs utility actions.
- Density toggle: compact row padding `4px` (--sp-1) vs comfortable `8px`
  (--sp-2), horizontal padding preserved; toggle navigates `?density=`.
- Tree selection: selected node computed `background #dceaff` + inset `3px
  #1b75bc` bar — matches the file-row selection, selected node only.
- Sync tooltips: run icons carry "Data on local disk" / "Cleared -- data on
  NAS only".
- Legend popover: "?" lists all 10 sync states + icons + meanings.

Footer WARNING/DANGER colours are unit-verified only (the e2e harness has no
backend signals, so it renders NORMAL; the derivation fires in production).

**Phase 6** ran the full e2e suite (green; flow 28 added), met the **91.70%**
coverage gate, and spot-checked the app-wide theme on welcome/settings/problems
(no regression) — see §C. The only remaining work is **§D cleanup** (optional
literal-fallback thinning; delete scratch PNGs + `RESUME.md` before merge) and
**§A** (reconciling the *original* design spec for the popover + `register_theme`).

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

## A. Spec reconciliation ✅ DONE

- [x] Amended `2026-05-29-main-window-ui-refresh-design.md`: a reconciliation
      banner up top + inline "shipped divergence" notes at §3.2 (`register_theme`
      wired app-wide), §4.1 (metadata is a floating popover, not a docked card),
      §4.4 (only the container changed; the sub-card logic is as built), and §5
      (rationale for the popover). The mockups are kept; the metadata region is
      annotated as an overlay.
- [x] Decided & recorded: the popover is **the** direction (popover-only, no
      docked/popover toggle) — operator-directed during the Phase-4 demo.

---

## B. Phase 5 — Polish (reconcile each item with the popover)

Each item is self-contained. Now that `register_theme` is wired, scoped CSS on
Quasar classes (e.g. tree selection) will actually resolve its tokens.

**Done this session** (`01edee7`, `1bb3c8d`) — unit-verified, visual pass
pending:

- [x] **Search wiring + affordances (OQ-2)** — `main.py` / `mount.py` /
      `_test_app.py`. The search query now reaches `TreeFilters.search` via
      `chip_state_to_tree_filters(..., search=s.search_query)`; the input
      carries `value` + `clearable` + a 300 ms Quasar `debounce`, and
      `on_change` re-navigates `?q=` through `_on_search` (production + e2e
      mirror). A result-count / no-matches pill below the box is sourced from
      the new pure `count_search_results(build_nodes(...))`.
- [x] **Empty states** — new shared `components/empty_state.py`
      (`empty_state(icon, message, testid)`, centred icon + hint); the three
      bare labels (`file_list.py`, `metadata_pane.py`, `main.py`) now route
      through it, each testid preserved.
- [x] **Live status segments** — pure `derive_footer_segment_states` in
      `status_bar_segment.py`; `mount._build_main_state` sources
      `problems_count_hard` + `deps.lims_reachable` onto three new
      `MainPageState` fields the footer reads. Validator → WARNING on a hard
      finding, LIMS → DANGER when unreachable. **Staging** has no cheap cached
      count yet (a per-render `list_staged_runs` scan would be I/O on the
      render path), so it stays NORMAL with the `staging_pending` param wired
      and ready (one-line change when a cached signal exists). Sync was already
      live off the operations counts.
- [x] **Toolbar grouping** — `main.py`. Vertical `q-separator` divider + gap
      between the creation actions (New Project / Run / Test Run / Add
      Equipment) and the utility actions (Operations / Refresh / Settings);
      every button's order + testid unchanged.

**Done this session** (`a31b916`) — all live-verified:

- [x] **File-list density (`?density=compact`)** — pure `density_card_class`;
      `framed_pane` `card_classes` param; scoped `.exlab-density-compact td`
      theme rule (--sp-1 vs --sp-2); a compact/comfortable **header toggle**
      (decided: header toggle, not URL-only) wired through `mount._main` + the
      e2e handler. Verified: 4px vs 8px row padding, toggle navigates.
- [x] **Unified selection (OQ-6)** — `build_tree` seeds Quasar's
      `v-model:selected` from `?selected=` (via `tree._props["selected"]`, so
      ids with spaces/slashes survive); a `.q-tree__node-header.q-tree__node--selected`
      theme rule gives the fill + 3px bar. Verified: computed `#dceaff` + inset
      `#1b75bc` on the selected node only.
- [x] **Sync tooltips + legend** — tree header-slot `<img>` gets a friendly
      `:title` (`sync_title`: local vs cleared); a "?" legend popover in the
      Files header lists every state's icon + meaning via
      `sync_status_icon.sync_legend_entries()` (`_STATUS_TO_PROPS`, single
      source of truth). The file-list Status cell already carried the Phase-4
      icon tooltip.

**Tests (Phase 5):** `test_status_bar_segment.py` (derivation helper);
`test_main_page`/`test_pages.py` (search wiring → `TreeFilters.search`, result
count, no-matches, density class); `test_tree`/`test_components` (selection
style + tooltip shape where assertable).

---

## C. Phase 6 — e2e + final verification ✅ DONE (`e72cd73`)

**Outcome: the suite was already green.** The Phase 3–5 DOM changes broke
**no** existing flow — the predicted break points below were all absorbed
because the `data-testid`s were preserved through every refactor and the flows
assert on testids, not on rendered text. The actual work was *adding* the
missing coverage + verifying.

Predicted break points, all checked clean:

- [x] **Status cell is now an icon, not text** — no flow asserted the literal
      status string (the file-list rows expose `data-tombstone` / `data-path`
      hooks; `flow_05_browse_view_sync_icons` already asserts on the icon).
- [x] **Metadata is an absolute popover** — no flow asserted a docked layout
      or its width/position; open/close rides `right_pane` (`flow_25`).
- [x] **Toggle label "Metadata"** — no flow asserted the old label text;
      `toggle-right-pane` testid preserved (`flow_25`).
- [x] **Framed-pane / selection testids** — `explorer-pane`, `files-pane`,
      `data-selected`, `data-path`, `metadata-selected-file` now driven by the
      new flow 28 (below).
- [x] **`register_theme` app-wide** — every flow navigates its page and
      passes; spot-screenshotted `/` (welcome), `/settings`, `/problems` — all
      render the grey canvas + navy serif headings + themed controls with **no
      regression**. (`/main` was already fully walked through in Phase 5.)
- [x] **`tests/e2e/_test_app.py`** mirrors production `mount._main` (now also
      `on_search` / `on_toggle_density`); proven by `flow_25` + `flow_28`.

Delivered:

- [x] **New flow 28** (`test_flow_28_selection_search_density.py`, 6 tests):
      file-row single-click → `?file=` + `data-selected` + the
      `metadata-selected-file` sub-card while run metadata stays; search
      result-count + no-matches pill; density toggle → `?density=`; sync-status
      legend popover; the selected tree node's `.q-tree__node--selected` hook.
- [x] **UX catalog** (`ux_catalog.py`) gains the new affordances
      (`file-list-row`, `toggle-right-pane`, `main-search`,
      `files-density-toggle`, `files-legend`); `docs/UX_INTERACTIONS.md`
      regenerated; testid-exists + e2e-covered checks pass.
- [x] **Full e2e:** `pytest tests/e2e` — **70 passed, 3 skipped** (the skips
      are pre-existing: an unconditional skip in `flow_12` + production-app
      health skips in `flow_00`/`19`/`26`). Note: `flow_06`/`flow_09` are
      intermittently order-flaky under the shared session server — pre-existing,
      pass in isolation.
- [x] **Coverage gate:** `pytest tests/unit tests/integration --cov
      --cov-fail-under=91` (the CI command — unit **first**, e2e excluded since
      it runs in a subprocess) → **91.70%**, 2338 passed.
- [x] **Static:** `ruff check` + `ruff format --check` + `mypy src` clean.
- [x] **Visual confirm:** `/main` (run + file selected, popover open/closed,
      density compact/comfortable, legend, tree selection) verified live in
      Phase 5; welcome/settings/problems spot-checked here.

> **Gotcha recorded:** run the suite as CI does — `pytest tests/unit
> tests/integration` (unit first). `pytest tests` (alphabetical) runs
> `integration` before `unit`, and an integration test pollutes NiceGUI's
> global auto-index slot stack, making the in-process `tests/unit/ui` render
> tests fail. Not a product bug — a collection-order constraint.

---

## D. Cleanup / tech-debt

- [x] **Literal fallbacks — decided: KEEP.** The scattered `var(--x, #literal)`
      fallbacks stay as a deliberate defensive net: `register_theme` can be
      skipped in a headless/cron render (and was, in fact, never injected at
      all before the post-Phase-4 fix), so a bare `var(--x)` would resolve to
      empty and drop the declaration. Documented here rather than thinned.
- [x] **Scratch artifacts deleted.** `RESUME.md`, the `phase4/5/6-*.png` demo
      PNGs, and `.playwright-mcp/` removed; the throwaway `:8099` uvicorn server
      stopped. (All were untracked/gitignored — no commit needed for their
      removal.)
- [x] **No `register_theme` double-injection.** Confirmed it is called exactly
      once per app — `mount_ui` (production) and `_test_app` (e2e), both
      `shared=True`; no per-page duplicate.

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
