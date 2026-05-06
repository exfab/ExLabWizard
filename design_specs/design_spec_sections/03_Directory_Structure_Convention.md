# 3. Directory Structure Convention

Parent: [[ExLab-Wizard_Design_Spec]]

---

```
<equipment>/
  <project>/                           # LIMS short_id, e.g. PROJ-0042 (never the human-readable name)
    Run_<YYYY-MM-DDTHH-MM-SS>/         # experimental run (ISO 8601, colons replaced with hyphens for filesystem safety)
      [template files and subdirs]
    TestRuns/                          # isolated subfolder for non-experimental runs
      TestRun_<YYYY-MM-DDTHH-MM-SS>/   # test-run leaf folder; the TestRun_ prefix is itself a marker
        [template files and subdirs]
```

A worked example of the resolved layout for a confocal run on `CONFOCAL_01` under LIMS project `PROJ-0042` (named *"Cortex Q3 Pilot"* in the LIMS):

```
/data/lab/CONFOCAL_01/PROJ-0042/Run_2026-04-17T14-32-00/
```

The human-readable name *"Cortex Q3 Pilot"* never appears in the path; it is shown in the browse view's project label, the wizard, and the README front matter, and is sourced from the LIMS (live or via the local LIMS-project cache; §7.2.4).

The equipment-first hierarchy reflects the operational mental model: each acquisition machine is the physical anchor for its data, and the `config.yaml` equipment registry is keyed by equipment ID. A project lives under each equipment it touches; the same LIMS project (same `short_id`) may therefore appear under multiple equipment folders, with each `<equipment>/<short_id>/` pair being its own self-contained tree.

### 3.1 Equipment-ID format

Equipment IDs are filesystem path segments and must be cross-platform safe. The spec enforces a canonical form at config validation time:

- Regex: `^[A-Z][A-Z0-9_]*$` — starts with an uppercase letter; remainder is uppercase letters, digits, and underscores.
- Length: 1 to 32 characters.
- Examples: `CONFOCAL_01`, `FLOW_01`, `NANOPORE_03`, `LIGHTSHEET_2A`.
- Rejected: `confocal_01` (lowercase), `Confocal-01` (hyphen), `01_CONFOCAL` (leading digit), `CONFOCAL 01` (space), `仪器_01` (non-ASCII).

This enforcement removes the cross-platform case-sensitivity footgun (macOS APFS and Windows NTFS are case-insensitive; Linux ext4 is case-sensitive) by collapsing the input space to a single canonical form. Two equipment entries that differ only in case are not allowed and are rejected at config load with a structured error.

The canonical form is enforced in `config.yaml` validation ([[09_Configuration_File|§9]]) and in any "Add equipment" UI affordance (Settings dialog, onboarding flow). Migration of v0.5/v0.6 equipment folders that don't match the regex is out of scope for the v1 backend; non-conforming on-disk equipment directories are treated as orphans by the validator (audit-mode walk; surfaces as a soft-tier finding).

Test runs live in a dedicated `TestRuns/` subfolder that sits parallel to experimental runs inside each project folder. Two redundant signals separate test data from experimental data:

1. **Parent folder.** Test runs are children of `TestRuns/`; experimental runs are direct children of the project folder.
2. **Leaf folder prefix.** Test runs use `TestRun_<DATE>`; experimental runs use `Run_<DATE>`.

Either signal alone is sufficient to identify a test run. Downstream tooling can walk `<equipment>/<project>/Run_*` to enumerate experimental runs while ignoring everything under `TestRuns/`, and can additionally assert that no leaf-folder name beginning with `TestRun_` is processed as experimental. The redundancy is intentional: the folder-level separation is the primary defense, and the leaf-name prefix protects against partial-tree copies, glob misuse, or scripts that walk on leaf names alone.

Three template scopes exist:

| Template Type          | Scope                                                                                                                                                                | Triggering capability |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------- |
| **Project template**   | Creates the `<project>/` skeleton inside an equipment folder                                                                                                          | Create a New Project (User Interaction Spec 3.1) |
| **Equipment template** | Creates `<equipment>/` (typically once per equipment, before the first project lands under it)                                                                        | (Invoked during initial equipment setup or as a side-effect of the first project creation under that equipment) |
| **Run template**       | Creates `Run_<DATE>/` directly under `<equipment>/<project>/` (experimental) or `TestRun_<DATE>/` under `<equipment>/<project>/TestRuns/` (test). Custom per equipment. | Experimental and Test run creation (User Interaction Spec 3.2, 3.3) |

Templates declare which run modes they support via `_exlab_run_scope` in `copier.yml` (see [[05_Template_Format#5.2 Copier Manifest (`copier.yml`)|Section 5.2]]): `"experimental"`, `"test"`, or `"both"`. The run creation flow only considers templates whose scope includes the mode the user selected at session start.
