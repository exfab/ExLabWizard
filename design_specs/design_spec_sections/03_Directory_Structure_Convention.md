# 3. Directory Structure Convention

Parent: [[ExLab-Wizard_Design_Spec]]

---

```
<equipment>/
  <project>/                           # human-readable LIMS project name, used verbatim (see §3.2)
    Run_<YYYY-MM-DDTHH-MM-SS>/         # experimental run (ISO 8601, colons replaced with hyphens for filesystem safety)
      [template files and subdirs]
    TestRuns/                          # isolated subfolder for non-experimental runs
      TestRun_<YYYY-MM-DDTHH-MM-SS>/   # test-run leaf folder; the TestRun_ prefix is itself a marker
        [template files and subdirs]
```

A worked example of the resolved layout for a confocal run on `CONFOCAL_01` under the LIMS project named *"UCR-000-I-D_WHEELDON"* (LIMS short ID `PROJ-0042`):

```
/data/lab/CONFOCAL_01/UCR-000-I-D_WHEELDON/Run_2026-04-17T14-32-00/¡
```

The human-readable name *"UCR-000-I-D_WHEELDON"* **is** the `<project>/` path segment, used verbatim (spaces and all); it is also shown in the browse view's project label and the wizard, and is sourced from the LIMS (live or via the local LIMS-project cache; §7.2.4). The LIMS short ID `PROJ-0042` is a barcoding identifier and does **not** appear in the path — it is recorded in the project's metadata (README front matter) instead. See §3.2 for the project-folder naming rule.

The equipment-first hierarchy reflects the operational mental model: each acquisition machine is the physical anchor for its data, and the `config.yaml` equipment registry is keyed by equipment ID. A project lives under each equipment it touches; the same LIMS project (same human-readable name, same `short_id`) may therefore appear under multiple equipment folders, with each `<equipment>/<project>/` pair being its own self-contained tree.

### 3.1 Equipment-ID format

Equipment IDs are filesystem path segments and must be cross-platform safe. The spec enforces a canonical form at config validation time:

- Regex: `^[A-Z][A-Z0-9_]*$` — starts with an uppercase letter; remainder is uppercase letters, digits, and underscores.
- Length: 1 to 32 characters.
- Examples: `CONFOCAL_01`, `FLOW_01`, `NANOPORE_03`, `LIGHTSHEET_2A`.
- Rejected: `confocal_01` (lowercase), `Confocal-01` (hyphen), `01_CONFOCAL` (leading digit), `CONFOCAL 01` (space), `仪器_01` (non-ASCII).

This enforcement removes the cross-platform case-sensitivity footgun (macOS APFS and Windows NTFS are case-insensitive; Linux ext4 is case-sensitive) by collapsing the input space to a single canonical form. Two equipment entries that differ only in case are not allowed and are rejected at config load with a structured error.

The canonical form is enforced in `config.yaml` validation ([[09_Configuration_File|§9]]) and in any "Add equipment" UI affordance (Settings dialog, onboarding flow). Migration of v0.5/v0.6 equipment folders that don't match the regex is out of scope for the v1 backend; non-conforming on-disk equipment directories are treated as orphans by the validator (audit-mode walk; surfaces as a soft-tier finding).

### 3.2 Project-folder name

The `<project>/` path segment is the **human-readable LIMS project name, used verbatim** — e.g. `UCR-000-I-D_WHEELDON/`, spaces and all. Human-readable names are guaranteed unique by the LIMS, so they serve as a stable, collision-free folder identity with no further transformation; this is the intended project-folder name.

The LIMS **short ID** (`short_id`, e.g. `PROJ-0042`) is a barcoding identifier, not a path component. It is recorded in the project's metadata — the README front matter written at project creation — so that downstream tooling and physical-sample barcodes can still resolve a folder back to its LIMS short ID, but it never appears in the directory path.

Unlike equipment IDs (§3.1), the project name is **not** canonicalized — there is no rewrite to a collapsed form. Because LIMS names are free-text, they can in principle contain characters that are unsafe as a filesystem path segment: `/` or `\`, leading or trailing whitespace, control characters, reserved Windows device names (`CON`, `NUL`, …), or non-ASCII that round-trips poorly across filesystems. The spec does not silently sanitize these. Instead, the validator **rejects** any project name that is not a safe single path segment, with a structured error, at project-creation time; a non-conforming project directory found on disk is flagged during the audit-mode walk as a soft-tier finding. A LIMS project whose name cannot be used verbatim must be renamed in the LIMS before a project folder can be created for it.

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
