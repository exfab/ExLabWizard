# ExLab-Wizard documentation

ExLab-Wizard is a desktop tool that standardises lab run and project
directory creation, NAS sync, and LIMS integration. The documentation
is split into three trees:

```{toctree}
:maxdepth: 2
:caption: Contents

user_guide/index
plugin_guide/index
api/index
```

## What is ExLab-Wizard

ExLab-Wizard runs as a small system-tray application backed by a
FastAPI server and a NiceGUI frontend that the operator drives through
a native window. Each new project or run is created via a guided
wizard that enforces a consistent on-disk layout, captures a small
set of mandatory metadata fields, and queues the result for sync to a
shared NAS. The application is extensible through Python plugins that
mutate files inside a freshly rendered run directory.

## Where to start

- Operators should begin in the {doc}`user_guide/index`. Every
  user-visible capability has a dedicated page with screenshots
  generated from the live test surface.
- Plugin authors should read {doc}`plugin_guide/index`. The guide
  mirrors the design specification and links to the worked example
  shipped under ``exlab_wizard/plugins/``.
- Developers extending the application should consult
  {doc}`api/index`, which exposes the full autosummary tree across
  every public subsystem.

## Project metadata

| Item            | Value                       |
| --------------- | --------------------------- |
| Package version | ``{{ release }}``           |
| Python target   | 3.12                        |
| Repository      | github.com/ExFAB/ExLabWizard |
