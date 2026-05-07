# ExLabWizard

![Development Status](https://img.shields.io/badge/dev_status-alpha-red)

<div style="background-color: white; display: inline-block; padding: 10px; border-radius: 0px;">
  <img src="assets/ExLabWizardLogo.svg" alt="Phenotypic Logo" style="width: 400px; height: auto;">
</div>

## Context

ExLab-Wizard is a lightweight desktop application that creates standardized
directory structures on local disk, NAS, and a LIMS database from predefined
templates. It enforces the lab's
`<Equipment>/<Project>/Run_<ISO8601_DATE>` naming convention (and the parallel
`TestRuns/TestRun_<ISO8601_DATE>` for non-experimental runs), reduces human
error in directory creation, and provides an extensible plugin system for
transforming template file contents at creation time.
