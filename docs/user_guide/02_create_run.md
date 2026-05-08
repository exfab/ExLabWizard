# 3.2 Create a New Experimental Run

## Capability summary

From within a selected `<equipment>/<project>` context, the operator
chooses a run-scope template whose `_exlab_run_scope` includes
`"experimental"`, fills the variable form and the README form, and
confirms. The controller validates, Copier renders into
`<local_root>/<equipment>/<lims_short_id>/Run_<ISO8601_DATE>/`, plugins
execute (host-driven post-render pass), the `.exlab-wizard/` cache is
written with `run_kind: "experimental"`, and the run is queued for
sync. See section 02 §3.2 for the authoritative contract.

## Walkthrough

1. **Open the wizard.** From the toolbar, click *New run*
   (`data-testid="toolbar-new-run"`).
2. **Confirm the mode.** The mode badge shows *Experimental*; this
   binding cannot be changed mid-session (section 02 §4 mode invariant).
3. **Step through the six-step stepper.** Project context, template
   selection, variable form, README form, preview, confirm.
4. **Submit.** The Create button finalises the run; the wizard renders
   a success card.

## Screenshots

```{image} ../_static/screenshots/02_create_run/01_initial.png
:alt: New experimental run wizard, initial render
:align: center
```

## Related material

- {doc}`03_create_test_run` -- the test-mode counterpart.
- {doc}`05_readme` -- the README form is a sub-step.
- Design spec section 03 (Directory Structure) -- where the rendered
  run lands on disk.
- Design spec section 06 (Plugin System) -- plugins execute on the
  rendered tree before the cache is sealed.
