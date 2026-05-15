# Manifest Schema

Every plugin ships a `manifest.yml` that the host reads at startup
without importing any Python. The host parses it with PyYAML
(`yaml.safe_load`); round-trip preservation is unnecessary because the
host never writes back. The schema below is the authoritative shape;
see design spec section 06 §6.1.2 for the full discussion.

## Required identity fields

```yaml
name: "xlsx_field_filler"
version: "0.3.1"
author: "ExFAB"
description: "Writes resolved variable values into named cells of metadata.xlsx workbooks."
```

## Required dispatch fields

```yaml
supported_extensions: [".xlsx"]   # file-extension or "readme" pseudo-ext or glob list
api_version: "1"                  # plugin-API major version this plugin targets
```

`api_version` is the **plugin-API major version** the plugin targets.
The host refuses to load any plugin whose `api_version` does not match
the current host's supported set (currently `["1"]`). This is the
single mechanism by which plugin-contract breaking changes are gated.

## Optional declaration block

```yaml
required_variables:
  - project_name
  - operator
  - run_date
optional_variables:
  - sample_type
```

`required_variables` is the upfront declaration that resolves the
"plugin-input discovery" question. The host validates the variable map
against every loaded plugin's declared variables **before** Copier's
render phase starts; missing variables surface as a structured
pre-flight error rather than as a mid-pipeline `PluginInputRequired`
exception.

## Optional execution policy

```yaml
isolation:
  timeout_seconds: 30   # default 30; max 300
  memory_mb: 512        # default 512; max 2048
  network: false        # default false; true requires explicit operator opt-in in config.yaml
```

These are host-enforced. The worker is killed (SIGTERM, then SIGKILL
after a grace period) on timeout; memory limits are applied via
`setrlimit` on POSIX and via Job Objects on Windows.

## Discovery rules

The host scans **two plugin roots** and merges with lab-wins
precedence:

1. **Bundled plugin root** -- `_internal/plugins/` inside the
   PyInstaller bundle. Read-only. Ships with the app.
2. **Lab plugin root** -- `paths.plugin_dir` from `config.yaml`.
   Operator-writable.

If a plugin in the lab root has the same `name` as a bundled plugin,
the lab plugin replaces the bundled one in the registry; a single
`INFO`-level log records the override.
