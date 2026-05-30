# Main-window UI refresh — Design Spec

**Date:** 2026-05-29
**Status:** Approved → **implemented (Phases 0–6, branch `feature/gui-imrprovement-v2`)**, with two shipped divergences recorded below.
**Scope:** Visual + interaction refresh of the main window (`/main`). Frame the
three content panes as elevated, titled cards; add Excel-style zebra striping to
the centre file list with a defined row-state precedence; extend the right
metadata pane to describe a selected **file** (and **folder**) within its
current tree context; and land a set of low-cost intuitiveness wins (unified
selection styling, sync tooltips + legend, live status-bar segments, friendlier
empty states, toolbar grouping + density, search affordances). All visual
values flow through the existing design-token system; no hard-coded literals.

**Mockups:** `2026-05-29-main-window-ui-refresh-mockups/` (standalone HTML;
colors there are placeholders — the tokens in §3 are authoritative).

> **Reconciliation (2026-05-30) — partially superseded.** Phases 0–6 shipped.
> Two deliberate, operator-directed changes during the Phase-4 demo now diverge
> from this spec and are the **approved** behaviour; where they disagree, this
> spec defers to
> [`…-remaining-work.md`](2026-05-29-main-window-ui-refresh-remaining-work.md) §2:
>
> 1. **Metadata is a floating popover, not a docked card.** It renders as an
>    absolute overlay over the right ~40% of the Files pane, toggled by the
>    vertical "Metadata" tab on its left edge (still via `right_pane`). The
>    selection → sub-card *logic* (§4.4) is unchanged — only the container moved
>    from a docked column to an overlay. (Amends §4.1 / §4.4 / §5.)
> 2. **`register_theme` is wired app-wide.** The `:root` token block is now
>    injected on every page (`ui.add_head_html(…, shared=True)` from `mount_ui`),
>    so the §3.2 tokens resolve live everywhere — the inline `var(--x, #literal)`
>    fallbacks are a safety net, not the only source. (Amends §3.2 / §7.)

---

## 1. Summary

The main window (`ui/pages/main.py:render_file_explorer_page`) renders a header
toolbar → breadcrumb → `ui.splitter` (left: search + chips + tree; right: centre
file list + collapse tab + metadata/problems pane) → footer status bar. Today it
reads very flat: the three regions float on one white canvas separated only by
the splitter handle and a single hairline rule, with no per-pane framing and a
lot of dead space.

This refresh does five things:

1. **Frames every pane** as a white elevated card with an uppercase title strip,
   on a light-grey canvas (the "B + C hybrid").
2. **Zebra-stripes the file list** (subtle grey on even rows) and formalises the
   row-background precedence now that selection is added:
   **Selected > New-file > Tombstone > Zebra**.
3. **Extends the metadata pane** so selecting a file or folder in the centre list
   shows its metadata *nested within* the current tree context (Option B —
   anchored, not last-click-wins).
4. **Unifies selection styling** across the tree and the file list.
5. **Lands smaller wins:** sync tooltips + legend, live (coloured) status-bar
   segments, friendlier empty states, toolbar grouping + a row-density toggle,
   and search affordances (count / clear / no-matches).

The work is **token-first**: §3 adds the handful of CSS variables the rest of the
spec consumes, and fixes three variables that components already reference but
that the generated `:root` block never defines (latent dead styling).

The app has **not been deployed**, so there is no backward-compatibility or
migration burden.

---

## 2. Goals & non-goals

### Goals
- Each content pane (Explorer, Files, Metadata, footer) reads as a distinct,
  titled, framed region.
- The file list has Excel-style intermittent row shading with a single,
  unambiguous precedence rule when row states collide.
- Selecting a file or folder surfaces its metadata in the right pane without
  losing the equipment/project/run context the tree established.
- Selection looks identical on the tree and the file list.
- Sync iconography is self-explanatory (hover tooltips everywhere + a legend).
- The footer status bar conveys health at a glance (colour + counts).
- Every colour/space/radius/shadow resolves to a token in `design.py`.

### Non-goals
- **File-type icons** per extension (deferred; explicitly out of scope).
- **Keyboard navigation** of tree/file list (deferred; its own work item).
- A dark theme (the tokens are chosen to make one easy later, but it is not
  built here).
- Any change to backend sync/relay/LIMS data flow, the wizards, or Settings
  beyond what the listed UI items require.
- Replacing the URL-driven (`/main?selected=…&right_pane=…`) render model. The
  metadata extension is designed to fit it, not change it (see §4.4 / §5).

---

## 3. Design tokens (foundation)

All tokens are defined in `src/exlab_wizard/ui/design.py` and emitted into the
`:root { … }` block by `ui/theme.py:build_root_css`. Per Frontend Spec §2.1.1
this is the single source of truth; component CSS references `var(--…)` only.
`tests/unit/ui/test_design.py` keeps `design.py` and DESIGN.md in lock-step, so
DESIGN.md §07 (the token table) is updated alongside.

### 3.1 New tokens

| Token | Value (source) | Purpose |
|-------|----------------|---------|
| `--color-zebra` | `#f7f9fb` (new const `COLOR_ZEBRA`) | Even-row stripe in the file list. |
| `--color-row-selected` | `#dceaff` (new const `COLOR_ROW_SELECTED`) | Selected file/tree-row fill. |
| `--color-row-selected-bar` | `var(--color-blue)` (alias) | 3 px left accent bar on a selected row. |
| `--color-pane-header` | `#f4f6f9` (new const `COLOR_PANE_HEADER`) | Pane title-strip background. |

`--color-canvas` is **not** added — the framed-card canvas reuses the existing
`--color-bg` (`#f5f7fa`), which is already the body background. The cards sit on
`--color-surface` (`#ffffff`), so the contrast comes for free.

The new-file highlight reuses the existing-but-currently-undefined
`--color-highlight` (see §3.2); it is **not** a new token.

### 3.2 Fix latent debt — referenced-but-undefined variables

Three CSS variables are referenced by shipping components but are **never
emitted** by `build_root_css`, so today they resolve to empty and their
declarations are silently dropped:

| Variable | Referenced at | Visible symptom today |
|----------|---------------|------------------------|
| `--color-highlight` | `components/file_list.py:191,214` ("new file" row bg + "kept local" badge) | New-file highlight is invisible; kept-local badge has no fill. |
| `--color-link` | `components/breadcrumb.py:77` (segment colour) | Breadcrumb segments fall back to inherited colour, not a link colour. |
| `--color-bg-subtle` | `components/breadcrumb.py:63` (bar background) | Breadcrumb bar has no background tint. |

Add all three as tokens (consumed by this refresh anyway):

| Token | Value (source const) |
|-------|----------------------|
| `--color-highlight` | `#fff6e0` (`COLOR_HIGHLIGHT`) — warm amber, matches the new-file mock. |
| `--color-link` | `var(--color-blue)` (alias to `COLOR_BLUE`). |
| `--color-bg-subtle` | `#f4f6f9` (alias of `COLOR_PANE_HEADER`; the breadcrumb strip and pane headers share one tint). |

This is the only behavioural change outside the listed features and is in scope
because the zebra/selection work depends on a correct row-background layer.

*(Verification note for planning: grep for any other `var(--…)` not present in
`build_root_css` before implementing, and fold any stragglers in here.)*

**Shipped (2026-05-30):** all three vars are emitted, **and** `register_theme`
is now actually injected app-wide (`ui.add_head_html(…, shared=True)` from
`mount_ui`) — it had previously only ever been called by a unit test, so every
`var(--…)` reference resolved to its inline literal fallback (or to empty where
a component had none). The tokens are now live on every page; the scattered
fallbacks remain as a defensive net for a headless/cron render that skips the
injection.

---

## 4. Design

### 4.1 Panel framing — `components/framed_pane.py` (new) + `pages/main.py`

A small, pure render helper used by every pane:

```
framed_pane(title: str, *, count: str | None = None, testid: str) -> context manager
```

- Outer element: `background: var(--color-surface); border: 1px solid
  var(--color-border); border-radius: var(--radius-md); box-shadow:
  var(--shadow-sm);` with `display:flex; flex-direction:column;
  overflow:hidden;` and `data-testid="{testid}"`.
- Title strip: `background: var(--color-pane-header); border-bottom: 1px solid
  var(--color-rule);` text in `var(--text-xs)`, uppercase, letter-spaced,
  `var(--color-muted)`, weight 600. When `count` is supplied, a right-aligned
  pill (`--color-rule` bg, `--color-muted` text, `--radius-lg`).
- Body: `flex:1; min-height:0; overflow:auto; padding: var(--sp-3) var(--sp-4)`.

Applied in `render_file_explorer_page`:

- The page background is already `var(--color-bg)` via `theme.py` body CSS, so
  the grey canvas is automatic. Add `gap: var(--sp-3)` between the splitter
  panels so the cards visibly separate.
- **Explorer card** wraps the search + chips + tree column (`main.py:264-276`),
  title `EXPLORER`.
- **Files card** wraps the centre file list (`main.py:296-303`), title `FILES`,
  `count` = `"{n} items"` from `len(file_list_entries)`.
- **Metadata pane** keeps its existing tabs (`_render_right_pane`,
  `main.py:469-475`) — **no** title strip (approved default; the tabs name it).
  It still gets the card frame (border + shadow + radius) so all three regions
  match; the tab row sits where the title strip would be.
- **Footer status bar** (`main.py:376-418`) becomes a framed card (border +
  `--shadow-sm`), keeping its current contents.

The vertical collapse tab between the centre and metadata panes
(`main.py:310-364`) is unchanged in behaviour; it already paints its own raised
surface and sits *between* the cards.

> **Shipped divergence (2026-05-30):** the metadata region is **not** a docked
> card in the splitter. It floats as an absolute overlay popover over the right
> ~40% of the Files pane (`position:absolute; top:0; right:0; width:40%;
> z-index` above the Files card, with a strong left shadow so it reads as
> raised). The vertical collapse tab became the **"Metadata" tab** on the
> popover's left edge that opens/closes it via `right_pane` (open → raised panel
> over Files; closed → full-width file list, tab parked at the right edge). The
> Explorer + Files cards and the footer card are exactly as described above;
> only the metadata container changed. See remaining-work §2.

### 4.2 File list: zebra + row-state precedence — `components/file_list.py`

Add a single pure resolver so precedence lives in one place and is unit-testable:

```
def row_background(entry, *, is_selected: bool, is_new: bool) -> str
```

Returns the background/decoration fragment for a row, applying precedence
**Selected > New-file > Tombstone > Zebra**:

| State (highest first) | Background | Extra |
|-----------------------|------------|-------|
| Selected | `var(--color-row-selected)` | `box-shadow: inset 3px 0 0 var(--color-row-selected-bar)` |
| New-file (`is_new`) | `var(--color-highlight)` | — |
| Tombstone (`entry.tombstone`) | *(none)* | `opacity: 0.65; font-style: italic` (existing dim) |
| Even-row zebra | `var(--color-zebra)` | — |
| Odd row, no state | *(none)* | — |

- Zebra parity is computed from the row's index in `state.entries` (passed to
  `_render_row`), **not** CSS `:nth-child`, because tombstone/selected rows must
  not shift the stripe pattern and the table is re-rendered server-side.
- The existing per-row `border-bottom: 1px solid var(--color-rule)` stays.
- `state.new_paths` already drives `is_new` (`file_list.py:148,191`).

**Selection mechanic** (file rows):

- Add `selected_path: str | None` to `FileListState`.
- Single click on a `<tr>` selects it (sets the row state above) and invokes a
  new `on_select: Callable[[FileListEntry], None]` callback. Folders are
  selectable too (they currently only navigate on double-click).
- **Double-click** keeps its meaning: folder → navigate into; file → Open in OS.
- **Right-click** context menu is unchanged (Open in OS / Copy path / Keep
  local). Tombstones remain non-openable (`file_list.py:228`).
- Selecting a row does **not** clear the tree selection — that is the whole point
  of Option B (§4.4): the tree context persists, the file is shown beneath it.

### 4.3 Centre-pane wiring — `pages/main.py` + `ui/mount.py`

File selection rides the **same URL/navigate render model the tree already uses**
(OQ-1 / Resolved Q1). There is no live-render path today: every tree selection
calls `ui.navigate.to("/main?selected=…")` (`mount.py:170`), which re-runs the
`@ui.page` function; the metadata pane updates because the page re-renders, and
the folder feed survives because it lives in `app.storage.tab` (`mount.py:982`).
File selection reuses that exact mechanism rather than introducing a second
(refreshable) paradigm.

- `MainPageState` gains `selected_file_path: str | None` and
  `selected_file: FileListEntry | None` (the in-memory entry for the selected
  row — resolved by path-match against the feed's cached payload; no new fetch).
- The `/main` handler gains a `file: str = ""` query param alongside `selected`
  and `right_pane`; `_build_main_query` is extended to carry it (URL-encoded —
  filesystem paths contain spaces / unicode, so encode on write and decode on
  read).
- The file-row click handler calls
  `ui.navigate.to("/main" + _build_main_query(selected, right_pane, file=path))`.
  On render, `selected_file` is resolved from `feed_entries` by matching `path`;
  if no entry matches (file removed since the click), `selected_file` is `None`
  and no sub-card renders (§7).
- `_render_centre_file_list` (`main.py:421`) passes `state.selected_file_path`
  into `FileListState` and wires `on_select` to the navigate handler above.

**Files-pane refresh button (OQ-1/A mitigation).** Because the folder view only
re-renders on navigation, the **Files** card header gains a small refresh icon
(next to the item-count pill and the legend "?") that force-re-scans the current
folder on demand:

- Distinct from the global toolbar **Refresh** (`main.py:226`), which rebuilds
  the whole page (tree + folder + metadata). Tooltips differentiate them:
  toolbar = "Refresh everything"; Files header = "Refresh this folder".
- The handler calls `scan_folder_sync(current_path)` (synchronous, single-level
  — `browse.py:712`; the same cost the 2.5 s poll already pays), writes the
  result into the feed's `last_payload`, then navigates to the current URL to
  re-render. This makes the button **authoritative**: click → exactly what is on
  disk now, not the ≤2.5 s-stale cached payload.
- Wired via a new `on_refresh_folder` callback param on
  `render_file_explorer_page`.
- *Deferred:* a force-scan could diff against the prior payload to flash the
  new-file highlight, finally giving that dormant feature a purpose. Out of v1 —
  a fading highlight needs a timer, which reintroduces the live-render machinery
  this design deliberately avoids. Tracked as a follow-up (§11).

### 4.4 Metadata pane: Option B (anchored + nested file/folder) — `components/metadata_pane.py`

The pane keeps dispatching on the **tree** node kind (`render_metadata_pane`,
`metadata_pane.py:38`). What changes: when a file or folder is selected in the
centre list, a nested **"Selected file"** / **"Selected folder"** sub-card is
appended *after* the node-kind content, within the same `metadata-pane` column.

- Add `selected_file` to `MetadataPaneState` (an entry-shaped dict or `None`).
- After the kind dispatch (`metadata_pane.py:65-78`), if `selected_file` is set,
  render a framed sub-section (reuse the §4.1 header-strip style at a smaller
  scale) titled `SELECTED FILE` or `SELECTED FOLDER`:
  - **File:** Name, Size (`format_bytes` — binary KiB/MiB, the app-wide
    convention; OQ-8), Modified, Sync status (via the `sync_status_icon`
    component in tolerant mode — see §4.5), Path. Tombstone → show "On NAS" and
    omit size-on-disk.
  - **Folder:** Name, Item count, Sync rollup. **Total size is deferred** (the
    data layer does not precompute directory sizes — see §4.6 / OQ-4).
- When the tree selection is empty but a file is somehow selected, the existing
  empty-state still renders first; the sub-card appends beneath it. (In practice
  a file can only be selected inside a selected run's folder, so the run context
  is normally present.)
- No change to the Problems tab.

> **Shipped (2026-05-30):** the selection → sub-card logic above is exactly as
> built (`metadata-selected-file` / `metadata-selected-folder`, appended beneath
> the node content). Only its *container* differs — the `metadata-pane` column
> renders inside the floating popover (§4.1 divergence note), not a docked
> column.

### 4.5 Unified selection styling + sync tooltips/legend

**Unified selection (OQ-6/A):** the tree's selected node currently uses Quasar's
default `q-tree` highlight. Apply the §4.2 selected-row treatment
(`--color-row-selected` fill + 3 px `--color-row-selected-bar` left bar) to the
selected tree node via **scoped CSS** on `.q-tree__node--selected` (and its inner
header), injected through `add_head_html`. Quasar's `selected-color` prop only
tints text/control colour and cannot produce the fill + left-bar look, so CSS is
required to match the file rows. Pin the rule with a comment noting the
dependency on the Quasar internal class name so a future Quasar upgrade knows to
re-verify it.

**Sync tooltips (smaller than it looks — the text already exists):**
`components/sync_status_icon.py` already defines an icon + tooltip + colour for
every status (`_STATUS_TO_PROPS`). The gap is that the two display surfaces
bypass it:

- **Tree rows** render raw `<img>` SVGs in the `default-header` slot
  (`tree.py:312-357`) with no tooltip. Add a `title`/tooltip to the `<img>` (the
  slot is HTML, so a `:title` binding driven by `props.node.sync_status` is the
  minimal change) — or, if cleaner during implementation, map the rollup to the
  `sync_status_icon` tooltip text and emit it as the `alt`/`title`.
- **File list** shows sync as plain text (`file_list.py:194,221`). Route the
  Status cell through `sync_status_icon(entry.sync_status)` so it gets the icon +
  hover tooltip. **Note (OQ-5):** `sync_status_props` currently raises
  `ValueError` on any unknown key (`sync_status_icon.py:120`), but file rows and
  folders carry `sync_status=None`. Add a tolerant mode —
  `sync_status_props(status, *, strict: bool = True)` — that returns neutral
  props (dash, no icon, muted colour) for `None`/unknown when `strict=False`.
  The new callers (file list, folder rollup, legend) pass `strict=False`; the
  default stays `True` so existing callers are unchanged.
- **Legend:** add a small "?" affordance in the **Files** card header that opens
  a popover listing each state's icon + one-line meaning, sourced from
  `_STATUS_TO_PROPS` tooltips (single source of truth — no second copy of the
  wording).

**Live status-bar segments:** `status_bar_segment` already supports
`SEGMENT_NORMAL/WARNING/DANGER` with colour + glyph (`status_bar_segment.py`).
Today every segment is hard-wired to `SEGMENT_NORMAL` (`main.py:401-414`). Wire
the real states + counts:
- **Sync** — already partly wired (`operations_input_required` → WARNING). Add a
  count suffix when `operations_count > 0`.
- **Validator** — WARNING when `problems_count_hard > 0`, else NORMAL; label
  shows the hard count.
- **LIMS** — DANGER/ WARNING when the LIMS dependency is unreachable; NORMAL when
  reachable. (Reachability is already known to the mount layer via `deps`.)
- **Staging** — count of staged runs; WARNING when any need attention.
The state derivation lives in `mount._build_main_state` (it already has `deps`
and the counts); the segment component is unchanged.

### 4.6 Folder aggregates (item count + sync rollup) — OQ-3, OQ-4 resolved

The folder sub-card (§4.4) shows **item count + sync rollup**. Total size is
deferred. The relevant facts, confirmed in code:

- A directory row in the feed has **`size_bytes=None`** (`browse.py:793`) and
  **`sync_status=None`** (`_per_file_sync_status` returns `None` for dirs,
  `browse.py:558`). A folder row in the *current* list therefore carries no size
  and no status of its own.
- The feed only scans the **current** folder, so a selected sub-folder's children
  are not loaded at all — without a fresh scan there is no size, no rollup, *and
  no item count* for it.

**Resolution (OQ-4/B — one-level scan on select):** when a folder is selected,
the mount handler calls the existing `scan_folder_sync(folder_path)`
(single-level, synchronous — the same cost the 2.5 s poll already pays for the
current folder). From that one scan:

- **Item count** = `len(scan.entries)`.
- **Sync rollup** = worst-of the children's `sync_status` (see ordering below).
- **Total size** = **not computed.** A correct total needs a recursive walk; a
  blocking recursive walk on the click/render path is the kind of perf hazard
  this design avoids. Deferred to a follow-up (§11). The card omits the size row
  rather than showing a misleading shallow sum.

**Sync rollup ordering (OQ-3/A).** File `sync_status` values are
**`SyncStatus`** (`pending / synced / cleaned / failed / blocked_by_validation`
— `enums.py:24-38`), **not** `RunSyncState`. Neither enum defines a severity
order (the module forbids reordering without a schema bump — `enums.py:5-7`), so
the rollup uses a **UI-only severity tuple** kept beside the rollup helper
(most-attention-worthy wins):

```
failed > blocked_by_validation > pending > synced > cleaned
```

`None`/unknown child statuses are ignored for the rollup (and rendered via the
tolerant icon mode, §4.5). The helper is pure and unit-tested (§9). If a second
consumer ever needs the same order, promoting it onto `SyncStatus` becomes a
separate, coordinated change — not done here.

### 4.7 Empty states

Replace the bare labels with an icon + one-line hint, inside the framed cards:
- Files card empty (`main.py:442` "Select a folder…") → folder-open icon + hint.
- Metadata pane empty (`metadata_pane.py:61` "Select a node…") → info icon +
  hint.
- File list empty folder (`file_list.py:132` "Empty folder.") → muted icon +
  "This folder is empty."

### 4.8 Toolbar grouping + density — `pages/main.py`

- **Grouping:** in the header (`main.py:198-231`), visually separate **creation**
  actions (New Project / New Run / New Test Run / Add Equipment) from **utility**
  actions (Operations / Refresh / Settings) with a thin `var(--color-rule)`
  vertical divider and a small gap, instead of the current single flat row.
  Order and `data-testid`s are preserved (e2e depends on them).
- **Density toggle (OQ-7/A, scope = file list only):** a compact/comfortable
  control affecting **file-list row vertical padding only** (`--sp-1` vs
  `--sp-2`; cells are `p-2` today — `file_list.py:208`). Compact yields ~40–50%
  more visible rows for large acquisition folders. **Padding only** — font, icon,
  zebra, and selection-bar proportions are unchanged so the table stays legible.
  Persisted as a `?density=compact` URL param (consistent with the OQ-1/A
  navigate model — *not* tab storage), default comfortable; drives a CSS class on
  the Files card, no per-row logic. The tree is intentionally **out of scope**
  (would add Quasar-internals coupling); app-wide density is a separate future
  concern (§11).

### 4.9 Search affordances — `pages/main.py` (left Explorer card)

**Prerequisite (OQ-2): wire search to filtering first.** The search input
(`main.py:265`) currently has **no binding** — `build_tree` is always called with
`search=""`, so the box does nothing today. The filter logic already exists
(`TreeFilters.search` → `_matches_search`, `tree.py:117`); only the input→filter
link is missing. Add it via the OQ-1/A model: a `?q=` query param fed into
`chip_state_to_tree_filters(s.chip_state, search=q)`, with the box's
`on_value_change` navigating after a ~250 ms debounce (so it isn't a navigation
per keystroke). This wiring is part of this section's scope, not a precondition
owned elsewhere.

On top of that, the input gains:
- A **result count** ("12 matches") derived from the filtered tree node count
  (surface the count from `build_nodes`).
- A **clear (×)** button (Quasar `clearable` prop) that resets the search (clears
  `?q=`).
- A **no-matches** state: when the search yields zero nodes, the Explorer body
  shows "No matches for '<query>'" instead of an empty tree.

---

## 5. Architecture notes & rationale

- **Why Option B over a last-click-wins or accordion model:** the page is
  server-rendered per selection and the per-kind renderers each assume they own
  the pane. B is *additive* — it appends a sub-card and leaves the dispatcher
  untouched — so it fits the render model and adds no persistent expansion state.
  (The rejected "selection chain / accordion" option required persisting
  per-section open/closed state across full re-renders, the classic recurring-bug
  source, and duplicated the tree's own ancestor navigation.)
- **Why file selection + density use the URL, not a refreshable (OQ-1/A):** the
  page has **no live-render path** — tree selection already works by
  `ui.navigate.to("/main?selected=…")` re-running the page. Putting file
  selection and density on the same URL/navigate model keeps **one** rendering
  paradigm across the page. Introducing a `@ui.refreshable` region (the rejected
  Fix B/C) would add a second, divergent paradigm — future maintainers would
  have to know which state is URL-driven and which is refresh-driven — for a
  snappiness gain that the per-folder refresh button (§4.3) substitutes cheaply.
  The cost of OQ-1/A is a full page re-render per file click, but that is
  identical to what every tree click already costs, so it is not a new cost
  class. The path-in-URL fragility (spaces / unicode / length) is contained by
  URL-encoding. A future "make the explorer live" initiative (§11) can adopt the
  refreshable model wholesale, at which point file selection, search, and density
  migrate together.
- **Token-first ordering** means the framing/zebra/selection layers all reference
  variables that exist before any component consumes them, and the §3.2 fix
  removes three pieces of dead styling in the same pass.
- **Why the metadata pane became a popover (shipped divergence):** during the
  Phase-4 demo the operator preferred reclaiming the full Files width by default
  and surfacing metadata as a raised overlay on demand, rather than a permanently
  docked third column. The popover keeps the URL/`right_pane` toggle model and
  the Option-B sub-card logic intact — only the container is an absolute overlay
  — so the change is presentation-only. Recorded as the approved layout in
  remaining-work §2; the original docked-card framing in §4.1 is retained above
  for history.

---

## 6. Data flow

```
Tree selection (URL ?selected=) ─► _classify_node ─► node_kind
        │                                              │
        │                                  _build_metadata_payload (per kind)
        ▼                                              ▼
   Explorer card (tree)                     Metadata card: node-kind content
        │                                              ▲
        ▼                                              │ append sub-card
   Files card (folder feed) ─► row click ─► navigate /main?…&file=<path>
        │                                              │
        │              re-render: resolve selected_file from cached payload
        └────────────── by path-match (in-memory; no new fetch) ──────────┘
                                                       │
   Folder row click ─► navigate ─► scan_folder_sync(folder) ─► count + rollup
   Files header ⟳ ─► scan_folder_sync(current) ─► last_payload ─► re-render

Footer segments ◄── _build_main_state (deps + counts) ── SEGMENT_* states
```

All selection + density + search state travels through the URL (`?selected=`,
`?file=`, `?right_pane=`, `?density=`, `?q=`) and the page re-renders — one
paradigm (OQ-1/A). Local `nas`-mode and relay flows are untouched; this is
presentation + selection state only.

---

## 7. Error handling & edge cases

- **Selected file disappears** (folder feed refresh removes it): the stored
  `selected_file_path` no longer matches any entry → render no sub-card (treat as
  "no file selected"); do not error.
- **Tombstone selected:** sub-card shows "On NAS", no on-disk size, no Open.
- **Folder with no readable children:** the one-level `scan_folder_sync` returns
  an empty entry list → folder sub-card shows item count 0 and a neutral rollup
  (no size row — total size is deferred per §4.6).
- **Folder scan fails on select** (permissions / unmounted): `scan_folder_sync`
  surfaces the error path it already uses for the current folder; the sub-card
  shows item count "—" and a neutral rollup rather than erroring the render.
- **Sync status `None`/unknown** (folders, untracked files): the tolerant icon
  mode (§4.5) renders a neutral dash; such values are excluded from the folder
  rollup (§4.6).
- **Search with special characters:** the existing `_matches_search` is a plain
  case-insensitive substring (`tree.py:117`); no regex, so no injection/again
  no escaping concern. No-matches state covers the empty result.
- **Missing tokens at render time:** every new `var(--…)` is emitted by
  `build_root_css`; components that render before `register_theme` (per the
  comment at `main.py:320-323`) keep their literal fallbacks where they already
  have them.
- **e2e selectors:** all existing `data-testid`s are preserved; new framed
  wrappers add testids rather than moving existing ones.

---

## 8. Resolved questions

Eight open questions were raised against the first draft after code review and
resolved with the user before planning. Each resolution names what it stems from.

| # | Question | Resolution | Stems from |
|---|----------|------------|------------|
| OQ-1 | No live re-render path; how does file selection update the pane? | **A** — file selection via URL `?file=` + navigate (reuse the tree model); **plus** a per-folder refresh button (§4.3) as the manual-update mitigation. | The page is navigate-to-render; no `@ui.refreshable` exists. |
| OQ-2 | Search box isn't wired to filtering. | **A** — wire `?q=` → `TreeFilters.search` with a ~250 ms debounce, as part of §4.9. | Input was never bound to the existing filter logic. |
| OQ-3 | Folder rollup referenced the wrong enum; no severity order exists. | **A** — UI-only severity tuple `failed > blocked_by_validation > pending > synced > cleaned` over **`SyncStatus`**; correct the §4.4/§4.6 enum reference. | `enums.py` StrEnums are deliberately order-free (schema-versioned). |
| OQ-4 | Folder aggregates don't exist for free. | **B** — one-level `scan_folder_sync` on select → item count + rollup; **total size deferred** (no recursive walk on the render path). | The feed scans current-folder-only; dir rows carry `size_bytes=None`. |
| OQ-5 | `sync_status_icon` raises on `None`/unknown. | **B** — add `strict=False` tolerant mode → neutral dash; new callers pass it, default stays strict. | The component's intentional "unknown is a bug" contract meets optional data. |
| OQ-6 | Tree selection-highlight mechanism unspecified. | **A** — scoped CSS on `.q-tree__node--selected` (only way to get fill + left bar). | Styling a third-party (Quasar) component's internals. |
| OQ-7 | Density/search re-render mechanism. | **A** — `?density=compact` URL param; **file-list padding only**, tree out of scope. | Inherits OQ-1/A; keeps one paradigm. |
| OQ-8 | Size units/precision. | **A** — reuse `format_bytes` (binary KiB/MiB). | App-wide convention already established. |

No open items remain that can change scope. The deferred work below is
explicitly out of v1, not undecided.

---

## 9. Testing

Pure, NiceGUI-free units wherever the logic is pure (matches the existing
component test pattern):

### `tests/unit/ui/test_design.py`
- New tokens (`--color-zebra`, `--color-row-selected`,
  `--color-row-selected-bar`, `--color-pane-header`) and the three §3.2 fixes
  (`--color-highlight`, `--color-link`, `--color-bg-subtle`) appear in
  `build_root_css` output and stay in lock-step with DESIGN.md.

### `tests/unit/ui/test_file_list.py`
- `row_background` precedence: selected-over-new, new-over-tombstone,
  tombstone-over-zebra, zebra parity by index; a selected tombstone shows the
  selected treatment (highest wins) and keeps the dim/italic decoration.
- Zebra parity is index-based and unaffected by interleaved state rows.
- `on_select` fires for files **and** folders; double-click semantics unchanged.

### `tests/unit/ui/test_metadata_pane.py`
- File sub-card renders with name/size/modified/sync/path; tombstone variant
  omits on-disk size and Open.
- Folder sub-card renders item count + rollup (**no size row** — deferred per
  §4.6).
- Sub-card appends *after* the node-kind content and does not replace it.

### `tests/unit/ui/test_sync_rollup.py` (new — pure)
- Severity ordering `failed > blocked_by_validation > pending > synced >
  cleaned` (OQ-3); `None`/unknown children are excluded; empty set → neutral.

### `tests/unit/ui/test_sync_status_icon.py` (extend)
- `strict=True` (default) still raises on unknown (existing contract preserved).
- `strict=False` returns neutral props (dash, no icon, muted) for `None` and
  unknown keys (OQ-5).

### `tests/unit/ui/test_status_bar_segment.py` (extend)
- State derivation: validator WARNING on hard findings, LIMS DANGER when
  unreachable, staging count/attention — asserted on the spec produced by
  `_build_main_state` (extract the derivation into a pure helper to test without
  NiceGUI).

### `tests/unit/ui/test_main_page.py` (extend) / `test_mount.py`
- `MainPageState` carries `selected_file_path`/`selected_file`; the centre list
  receives them.
- `_build_main_query` round-trips the new `file` and `density` params with
  URL-encoding (paths with spaces/unicode survive write→read; OQ-1/A).
- `selected_file` resolves by path-match from the cached payload; a path with no
  matching entry yields `None` (no sub-card).
- Search wiring: `?q=` feeds `TreeFilters.search`; result count + no-matches
  state from `build_nodes` (OQ-2).
- Density `?density=compact` drives the Files-card class; default comfortable
  (OQ-7).
- Folder-select handler calls `scan_folder_sync` once and aggregates count +
  rollup; a scan failure degrades to count "—" + neutral rollup, no raise
  (OQ-4 / §7).
- Files-pane refresh handler force-scans the current folder and re-renders
  (OQ-1/A mitigation).

### `tests/e2e/` (smoke)
- Selecting a file row highlights it and shows the metadata sub-card while the
  tree selection (run context) remains.
- Framed panes expose their new testids; existing toolbar/tree/file testids
  still resolve.

### Regression sweep
- Existing file-list / metadata / main-page tests updated only where row
  background or pane structure assertions change; behaviour-level tests
  unaffected.

---

## 10. Affected files

| File | Change |
|------|--------|
| `src/exlab_wizard/ui/design.py` | Add `COLOR_ZEBRA`, `COLOR_ROW_SELECTED`, `COLOR_PANE_HEADER`, `COLOR_HIGHLIGHT`; alias consts for link/subtle/selected-bar. |
| `src/exlab_wizard/ui/theme.py` | Emit the new + three previously-missing `var(--…)` in `build_root_css`. |
| `DESIGN.md` | §07 token table kept in lock-step with `design.py`. |
| `src/exlab_wizard/ui/components/framed_pane.py` | **New** — reusable framed/titled pane helper. |
| `src/exlab_wizard/ui/pages/main.py` | Wrap panes in `framed_pane`; canvas gap; toolbar grouping + density toggle; search wiring + affordances; live segment derivation; centre-list selection wiring; Files-header refresh button; empty states. |
| `src/exlab_wizard/ui/components/file_list.py` | `row_background` resolver + precedence; `selected_path` + `on_select`; folder selectable; Status cell via `sync_status_icon(strict=False)`; empty state. |
| `src/exlab_wizard/ui/components/metadata_pane.py` | `selected_file` sub-card (file + folder, count+rollup); `sync_rollup` severity helper; empty state. |
| `src/exlab_wizard/ui/components/tree.py` | Unified selection styling (scoped `.q-tree__node--selected` CSS); sync-icon tooltip in the header slot. |
| `src/exlab_wizard/ui/components/status_bar_segment.py` | No change (verify); derivation lives in mount. |
| `src/exlab_wizard/ui/components/sync_status_icon.py` | Add `strict=False` tolerant mode (OQ-5); otherwise the single source for icons/tooltips/legend. |
| `src/exlab_wizard/ui/mount.py` | Segment-state + counts in `_build_main_state`; `file`/`q`/`density` query params in `_build_main_query`; file-selection + folder-select handlers (one-level `scan_folder_sync`); Files-pane force-refresh handler. |
| `tests/unit/ui/test_design.py` | New + fixed tokens present. |
| `tests/unit/ui/test_file_list.py` | Precedence + selection tests. |
| `tests/unit/ui/test_metadata_pane.py` | File/folder sub-card tests. |
| `tests/unit/ui/test_sync_rollup.py` | **New** — severity-ordering tests (OQ-3). |
| `tests/unit/ui/test_sync_status_icon.py` | Tolerant-mode tests (OQ-5). |
| `tests/unit/ui/test_status_bar_segment.py` | Segment-state derivation tests. |
| `tests/unit/ui/test_main_page.py` / `test_mount.py` | Query-param round-trip, selection resolution, search wiring, density, folder-scan + refresh handlers. |
| `tests/e2e/…` | File-selection + framing smoke flows. |
| `docs/superpowers/specs/2026-05-29-main-window-ui-refresh-mockups/` | Approved visual references (committed with this spec). |

---

## 11. Deferred follow-ups (explicitly out of v1)

These are decided-as-deferred, not undecided. Each is a clean future work item.

- **Make the file explorer live.** Wire the folder feed's `on_update` into a
  `@ui.refreshable` file-list + metadata region so the centre pane updates every
  ~2.5 s without navigation, and activate the dormant new-file highlight
  (`diff_file_lists` + a fade timer). This subsumes the §4.3 refresh button and
  would migrate file selection / search / density off URL params onto the
  refreshable model. A self-contained initiative with its own spec.
- **Folder total size.** A recursive size aggregate for the folder sub-card,
  computed off the render path (async / cached), so it never blocks on large run
  folders.
- **Promote sync severity ordering onto `SyncStatus`.** Only if a second consumer
  needs the same order; requires the coordinated schema-version discussion the
  enum module mandates.
- **App-wide / tree density.** Extend the density control beyond the file list
  (tree rows, metadata rows, toolbar) as a design-system-level feature.
- **File-type icons.** Per-extension glyphs in the file list (deferred from the
  original scope decision).
- **Keyboard navigation.** Arrow-key traversal of tree/file list + Enter-to-open
  (deferred from the original scope decision; its own work item).
