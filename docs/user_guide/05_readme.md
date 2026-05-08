# 3.5 Author a README at Creation Time

## Capability summary

Every project-scope and run-scope creation always produces a
`README.md` carrying at minimum the mandatory core fields (`label`,
`operator`, `objective`). There is no client-level skip path. The
field set is a four-layer merge -- core fields, template-declared
fields (from `copier.yml`), config-declared fields (from `config.yaml`
`readme.defaults`), and system-supplied fields. The operator fills in
editable values (and may add custom fields), the controller validates
core + extended-required fields, and `ReadmeGenerator` writes the
output as YAML front matter plus Markdown prose to the created
directory root, with `readme_fields.json` mirrored into the
`.exlab-wizard/` cache. See section 02 §3.5 for the authoritative
contract.

## Walkthrough

The README form is a sub-step of the project wizard
({doc}`01_create_project`) and the run wizards
({doc}`02_create_run`, {doc}`03_create_test_run`). It is not a
standalone surface; the screenshot below shows the project wizard with
the README step active.

1. **Reach the README step.** From inside the active wizard, navigate
   to the README step on the stepper.
2. **Fill the merged field set.** Core fields are always present and
   always required. Template- and config-declared fields appear in
   their configured order. The operator may add custom fields
   alongside the merged set.
3. **Continue.** The Next button advances to the preview step; on
   submit, `ReadmeGenerator` writes the file.

## Screenshots

```{image} ../_static/screenshots/05_readme/01_initial.png
:alt: README step inside the new-project wizard
:align: center
```

## Related material

- {doc}`01_create_project`, {doc}`02_create_run`,
  {doc}`03_create_test_run` -- the host wizards.
- Design spec section 10 (README Generation) -- the merge order, the
  output format, and the rendering contract.
- Design spec section 11 §11.4 (`readme_fields.json`) -- the cached
  field set used for re-rendering.
