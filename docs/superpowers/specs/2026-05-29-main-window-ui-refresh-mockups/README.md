# Main-window UI refresh — approved mockups

Visual references captured during the 2026-05-29 brainstorming session for the
main-window UI refresh. Each file is **standalone HTML** — open it directly in a
browser, no server needed. A banner at the top of each states the decision it
locks in. These are *fidelity references for layout/structure intent*, not
pixel specs: all real colors, spacing, radius, and shadow values come from the
design tokens in `src/exlab_wizard/ui/design.py` (see the spec).

Companion spec: `../2026-05-29-main-window-ui-refresh-design.md`

| File | Locks in |
| --- | --- |
| `01-panel-framing.html` | Elevated cards + titled header strips on a grey canvas; Metadata pane keeps its tabs; header count pills kept. |
| `02-metadata-pane.html` | **Option B** — anchored tree context with a nested "Selected file" sub-card; folders selectable with folder metadata. |
| `03-zebra-and-row-states.html` | Zebra style **A** (subtle grey, even rows); row-state precedence **Selected > New-file > Tombstone > Zebra**; single-click selects, double-click / right-click→Open still launch. |

## Caveats for implementers

- Colors in the mockups are **placeholders**. Map to tokens: card fill
  `--color-surface`, border `--color-border`, shadow `--shadow-sm`, radius
  `--radius-md`, canvas `--color-bg`, zebra/selection via the new tokens the
  spec adds (`--color-zebra`, `--color-row-selected`, `--color-row-selected-bar`).
- The mockups are content fragments lifted from the brainstorming visual
  companion, wrapped in its frame template (the `.cards` / `.card` /
  `.subtitle` chrome). That chrome is **presentation scaffolding for review
  only** — it is not part of the app UI.
