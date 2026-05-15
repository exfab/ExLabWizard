# Plugin Author Guide

Plugins are the extensibility seam of ExLab-Wizard. They transform
files inside a freshly rendered run directory; the core application
does not know about spreadsheet formats, document templates, or
lab-specific naming rules. This guide is the authoring contract for
plugin developers and mirrors design spec section 06 (Plugin System).

## What plugins do

A plugin is a Python package directory under the configured plugin
root. Each plugin defines a single class that subclasses
`exlab_wizard.plugins.Plugin`. The class is the unit of registration,
lifecycle, and isolation: one class instance is constructed per
creation session, inside a worker subprocess. The worker has the
rendered destination directory as its working directory and runs in a
sanitised environment with resource limits. Plugins write only to
files inside the rendered destination tree; they may not touch
`README.md`, the `.exlab-wizard/` cache, or `.exlab-answers.yml`.

## Where plugins fit in the pipeline

Plugins execute in the controller's `PLUGIN_PASS` state, immediately
after Copier rendering completes and strictly before
`ReadmeGenerator` writes the README:

```
Creation Controller
        |
        v
Copier (in-process, unsafe=False, no _tasks)
        |
        v
Plugin Host (resolves candidates, validates variables, dispatches workers)
        |
        v
Plugin Worker (one subprocess per plugin per session)
        |
        v
ReadmeGenerator + CacheWriter (post-plugin, non-pluggable)
```

```{toctree}
:maxdepth: 1
:caption: Topics

manifest_schema
ipc_envelope
worked_examples
```

## Where to look in the codebase

- `exlab_wizard/plugins/base.py` -- the `Plugin` abstract base class,
  the `FileChange` dataclass, and the `PluginError` /
  `PluginInputRequired` exception hierarchy.
- `exlab_wizard/plugins/host.py` -- the host that scans the registry,
  spawns workers, marshals IPC, and applies isolation limits.
- `exlab_wizard/plugins/_worker.py` -- the worker entry point that
  imports the plugin package, runs the lifecycle, and writes the
  result envelope back over stdout.
- `exlab_wizard/plugins/registry.py` -- the registry built at app
  startup from `manifest.yml` files.
- `exlab_wizard/plugins/logger.py` -- the structured-log shim that
  forwards plugin log records over stderr.
