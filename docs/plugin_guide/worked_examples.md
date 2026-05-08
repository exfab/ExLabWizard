# Worked Examples

The bundled `_internal/plugins/` directory carries the canonical
scaffolds; the package's own `exlab_wizard/plugins/` tree carries the
host, registry, worker, and base classes. The two examples below are
the reference patterns plugin authors should mirror.

## Hello plugin

The `hello_plugin` is the minimal scaffold: it implements
`can_handle` and `transform` to write a single-line marker file into
the rendered tree. It demonstrates the full lifecycle (`__init__`,
`pre_transform_all`, `transform`, `post_transform_all`) and the
correct use of `ctx.log` for structured logging. New plugins should
start by copying this scaffold and renaming.

See design spec section 06 §6.5 for the full discussion.

## xlsx_field_filler

The `xlsx_field_filler` is the realistic worked example: it reads
named cells from a workbook (`metadata.xlsx`), writes resolved
variable values into them, and reports the per-cell changes back to
the host via `describe_changes` so the wizard's preview step shows
exactly what will be modified. It is the recommended pattern for
plugins that wrap a vendor file format:

- Declares `supported_extensions: [".xlsx"]` in `manifest.yml`.
- Declares `required_variables` upfront so the host validates the
  variable map before any worker is spawned.
- Uses `pre_transform_all` to open the workbook once, `transform` to
  write per-file, and `post_transform_all` to flush and close.
- Returns a list of `FileChange` instances from `describe_changes`
  with one entry per modified cell, so the user-facing preview is
  precise.

See design spec section 06 §6.6 for the full walkthrough.

## docx_variable_replacer

The `docx_variable_replacer` is an illustrative-only plugin that wraps
`python-docx` to replace `{{ variable }}` markers inside `.docx`
files. It is shipped as a worked example for plugin authors who need
to handle Word document templates; the pattern (open, replace, save)
generalises to many proprietary formats.

See design spec section 06 §6.7.

## Where to look in the codebase

- `exlab_wizard/plugins/base.py` -- the `Plugin` abstract base class
  with the lifecycle hooks every plugin overrides.
- `exlab_wizard/plugins/host.py` -- the host that drives the
  lifecycle.
- `exlab_wizard/plugins/_worker.py` -- the worker entry point.
- `exlab_wizard/plugins/registry.py` -- the discovery and merge
  layer.

For local development, install the optional `plugin-examples` group
to pull `openpyxl` and `python-docx`:

```
pip install -e .[plugin-examples]
```
