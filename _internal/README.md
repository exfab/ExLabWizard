# `_internal/` -- bundled starter content

This directory holds the read-only starter content that ships inside the
PyInstaller artifact. See Backend Spec §15.4 for the contract.

At runtime, the launcher resolves these paths from inside the bundle
(via `sys._MEIPASS` on PyInstaller, or the repo root in a development
checkout). The plugin host scans **both** this directory **and** the
lab-writable `paths.plugin_dir`; on name collision the lab plugin wins
(§6.5). The onboarding flow offers to copy templates from
`_internal/templates/` into `paths.templates_dir` on first launch.

## Layout

```
_internal/
  templates/          # Bundled starter templates (copy-on-first-launch)
  plugins/            # Bundled canonical plugin scaffolds (read-only)
  bin/                # Bundled binaries -- `rclone` for v1.1+; see TODO
                      # in `exlab_wizard.spec`. `bin/` is created by the
                      # build, not committed.
```

## Populating for v1

The Phase 15 implementation ships **empty** placeholders
(`templates/.gitkeep`, `plugins/.gitkeep`). The actual bundled starter
templates and plugins are out of scope for v1; populate them like so:

### Bundled templates (Spec §5)

Add Copier templates to `_internal/templates/`. Each is a directory
with a `copier.yml` and a `_exlab_*` block inside that file. Minimum
viable starter set per §15.4:

- `_internal/templates/equipment_default/` -- one equipment template
- `_internal/templates/project_default/` -- one generic project
  template
- `_internal/templates/run_experimental_default/` -- one experimental
  run template
- `_internal/templates/run_test_default/` -- one test-run template

The bundled copies remain read-only inside the app bundle. The
onboarding flow copies them into `paths.templates_dir` (lab-writable)
on first launch and the wizards drive the writable copies thereafter.

### Bundled plugins (Spec §6.5, §6.6)

Add plugin scaffolds to `_internal/plugins/`:

- `_internal/plugins/hello_plugin/` -- canonical scaffold (§6.5)
- `_internal/plugins/xlsx_field_filler/` -- worked example (§6.6)

Each is a directory with a `manifest.yml` and an entry-point Python
module. The plugin host merges this directory with
`paths.plugin_dir` at launch.

## v1 status

- `_internal/templates/.gitkeep` -- placeholder; replace with actual
  templates per the §15.4 contract.
- `_internal/plugins/.gitkeep` -- placeholder; replace with actual
  plugin scaffolds.
- `_internal/bin/rclone` -- **NOT BUNDLED IN v1.** v1 documents the
  rclone-as-system-binary requirement; see the TODO in
  `exlab_wizard.spec` near the `--add-binary` block.
