# 3.4 Browse Existing Equipment, Projects, and Runs

## Capability summary

The main window reads the equipment-first hierarchy from the
configured `local_root` (or NAS mount), surfaces it as an expandable
tree, and supports per-row inspection via the Details and Problems
tabs. For each discovered run the application may consult
`.exlab-wizard/creation.json` to determine `run_kind`, template, and
provenance; test runs are distinguishable from experimental runs as a
data attribute. The browse view is also the primary entry point for
the creation wizards and for the Settings dialog. See section 02 §3.4
for the authoritative contract.

## Walkthrough

1. **Open the application.** The main window renders the toolbar, the
   left-pane equipment tree, the search box, and the Details and
   Problems tabs (`data-testid="main-tree"`,
   `data-testid="tab-details"`, `data-testid="tab-problems"`).
2. **Expand the tree.** Each equipment node holds projects; each
   project node holds experimental runs and a `TestRuns/` parent
   holding test runs. Sync-status icons surface per-run state.
3. **Inspect a node.** Selecting a run populates the Details tab with
   the run's metadata; selecting a project populates with the
   project's metadata.

## Screenshots

```{image} ../_static/screenshots/04_browse/01_initial.png
:alt: Main window with the equipment / project / run tree
:align: center
```

## Related material

- {doc}`08_problems` -- the Problems tab is mounted alongside the
  browse view.
- Design spec section 03 (Directory Structure) -- the on-disk layout
  the tree mirrors.
- Design spec section 11 (Cache Folders) -- the `.exlab-wizard/`
  subtree the browse view consults for `run_kind` etc.
