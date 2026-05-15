# User Guide

The ExLab-Wizard user guide is organised by **user-visible capability**.
Each page below corresponds to one of the eight capabilities listed in
section 02 (User Interaction) of the design specification, and is the
contract the application is expected to satisfy from the operator's
point of view. Every page embeds screenshots that are regenerated from
the live e2e test surface (`tests.e2e._test_app`) by
`scripts/generate_screenshots.py`; if a particular flow cannot be
captured in the current environment the page documents the limitation
inline.

## Capability map

| ID  | Capability                                                                | Surface                                |
| --- | ------------------------------------------------------------------------- | -------------------------------------- |
| 3.1 | Create a new project                                                      | New-project wizard (7 steps)           |
| 3.2 | Create a new experimental run                                             | New-run wizard (6 steps)               |
| 3.3 | Create a new test run                                                     | New-test-run wizard (6 steps)          |
| 3.4 | Browse existing equipment, projects, and runs                             | Main window (toolbar + tree + tabs)    |
| 3.5 | Author a README at creation time                                          | Embedded in 3.1 / 3.2 / 3.3 wizards    |
| 3.6 | Configure equipment, paths, and integrations                              | Settings dialog (nine sections)        |
| 3.7 | Monitor orchestrator staging                                              | Staging dock + main-window panel       |
| 3.8 | Review and resolve naming and validation problems                         | Problems tab + override dialog         |

The capability set is closed in v1: section 02 of the design spec is the
authoritative source. Capabilities that require behaviour outside the
NiceGUI surface (for example, quitting the coordinator from the OS
tray) are exercised by separate end-to-end harnesses and are not
covered by the screenshot pipeline.

```{toctree}
:maxdepth: 1
:caption: Capabilities

01_create_project
02_create_run
03_create_test_run
04_browse
05_readme
06_settings
07_orchestrator
08_problems
```

## Conventions

- **Screenshots** live under `_static/screenshots/<capability_id>/` and
  are regenerated from the test app on every documentation build. They
  show NiceGUI's standard light theme at a 1280x720 viewport.
- **Cross-references** to backend behaviour use the `{doc}` and
  `{ref}` MyST roles and resolve into the API reference tree.
- **Mandatory core fields** (`label`, `operator`, `objective`) are
  enforced at the creation gate for capabilities 3.1, 3.2, 3.3, and
  3.5; see section 02 §2 of the design spec for the full contract.
