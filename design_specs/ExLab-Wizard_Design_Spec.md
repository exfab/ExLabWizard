
# ExLab-Wizard: Design Specification

Frontend: [[ExLab-Wizard_Frontend_Spec]]

This Design Spec is the index for the backend behavior, schemas, and persistence contracts that implement the User Interaction Spec. Sections 3 through 13 have been split out into individual design docs under `design_spec_sections/`; the entries below are stubs that link to them. Sections 1, 2, and 14 remain in this document because they define cross-cutting framing (purpose, capability boundary with the User Interaction Spec) and the running open-question log.

---

## Table of Contents

1. [Purpose and Goals](#1-purpose-and-goals)
2. [User Capabilities (Reference)](#2-user-capabilities-reference)
3. [[design_spec_sections/03_Directory_Structure_Convention|Directory Structure Convention]]
4. [[design_spec_sections/04_Backend_Architecture|Backend Architecture]]
5. [[design_spec_sections/05_Template_Format|Template Format]]
6. [[design_spec_sections/06_Plugin_System|Plugin System]]
7. [[design_spec_sections/07_Sync_and_Database_Integration|Sync and Database Integration]]
8. [[design_spec_sections/08_Error_Handling_Principles|Error Handling Principles]]
9. [[design_spec_sections/09_Configuration_File|Configuration File]]
10. [[design_spec_sections/10_README_Generation|README Generation]]
11. [[design_spec_sections/11_Cache_Folders|`.exlab-wizard` Cache Folders]]
12. [[design_spec_sections/12_Orchestrator_Mode|Orchestrator Mode]]
13. [[design_spec_sections/13_Equipment_to_Orchestrator_Data_Flow|Equipment-to-Orchestrator Data Flow]]
14. [Open Questions](#14-open-questions)
15. [[design_spec_sections/15_Distribution|Distribution and Installation]]
16. [[design_spec_sections/16_Logging|Logging Architecture]]

---

## 1. Purpose and Goals

A lightweight desktop application that allows lab users to create standardized directory structures on local disk, NAS, and a LIMS database from predefined templates. The app enforces the lab's naming convention (`<Equipment>/<Project>/Run_<ISO8601_DATE>` for experimental runs, or `<Equipment>/<Project>/TestRuns/TestRun_<ISO8601_DATE>` for test/calibration runs), reduces human error in directory creation, and provides an extensible plugin system for transforming template file contents at creation time.

**Non-goals for v1:** Remote template editing, multi-user conflict resolution, real-time sync status dashboards.

---

## 2. User Capabilities (Reference)
Full Spec: [[02_User_Interaction]]

The user-visible capability contract -- triggers, required inputs, validation order, mode invariants, and the creation-gate Mandatory Core Fields (`label`, `operator`, `objective`) -- has moved to **the User Interaction Spec** at [[design_spec_sections/02_User_Interaction|`design_spec_sections/02_User_Interaction.md`]]. That document is now the authoritative source for:

- The mandatory core field set and its enforcement gate (was Section 2.0).
- Create a New Project (was 2.1).
- Create a New Experimental Run (was 2.2).
- Create a New Test Run (was 2.3), including the mode invariant.
- Browse Existing Equipment, Projects, and Runs (was 2.4).
- Author a README at Creation Time (was 2.5).
- Configure Equipment, Paths, and Integrations (was 2.6).
- Monitor Orchestrator Staging (was 2.7).

This Design Spec retains the backend behavior the controller must implement to satisfy each of those capabilities. Where a backend section needs to reference a capability, it does so by short name (e.g. "experimental run creation") rather than re-stating the user contract. When the user contract and backend behavior disagree, the User Interaction Spec is authoritative for the user contract; this document is authoritative for backend behavior, schemas, and persistence.

The creation controller's hard-coded behaviors that this document continues to own:

- **Filesystem and database side effects.** No filesystem write or LIMS write occurs before all required-field validation passes ([[design_spec_sections/08_Error_Handling_Principles|Section 8]] and [[design_spec_sections/07_Sync_and_Database_Integration|Section 7]]).
- **Core field hard-coding.** `label`, `operator`, and `objective` are hard-coded as mandatory at the backend level. Templates and `config.yaml` may extend the required set but cannot disable the core. The hard-coded list lives in the validation module and is not driven by config or templates.
- **Mode-flag plumbing.** `run_kind` is set from the mode flag at creation-session start and propagated to `creation.json`, `readme_fields.json`, the README front matter, the LIMS record, and (in orchestrator mode) `ingest.json`. The mode flag itself is sourced from the client per the User Interaction Spec.

---

## 3. Directory Structure Convention

Moved to [[design_spec_sections/03_Directory_Structure_Convention]].

Defines the equipment-first hierarchy, the `Run_<DATE>` / `TestRuns/TestRun_<DATE>` split, and the three template scopes (project, equipment, run).

---

## 4. Backend Architecture

Moved to [[design_spec_sections/04_Backend_Architecture]].

Component diagram of the creation controller, template engine, plugin registry, validation, FS writer, sync/DB client, and cache writer.

---

## 5. Template Format

Moved to [[design_spec_sections/05_Template_Format]].

Covers the Copier template layout, `copier.yml` manifest (including `_exlab_*` metadata), Python API integration, the `.exlab-answers.yml` answers file, post-copy `_tasks` hooks, Jinja2 templating in file contents, and template versioning.

---

## 6. Plugin System

Moved to [[design_spec_sections/06_Plugin_System]].

Defines the class-based `Plugin` contract (lifecycle hooks: `validate_variables`, `pre_transform_all`, `can_handle`, `transform`, `describe_changes`, `post_transform_all`, `on_plugin_failure`, optional `transform_readme`), the host/worker subprocess isolation model with JSON-over-stdio IPC, the `manifest.yml`-driven registry with `api_version` gating, the `_exlab_plugins` ordering surface, the `PluginInputRequired` escape hatch, the canonical `hello_plugin` scaffold, and the worked `xlsx_field_filler` example. Resolves Open Questions #1 (sandboxing) and #7 (input declaration).

---

## 7. Sync and Database Integration

Moved to [[design_spec_sections/07_Sync_and_Database_Integration]].

Covers NAS mirror behavior, the LIMS record schema, and the Pre-Sync Gate that consumes validator findings to block flagged runs from sync.

---

## 8. Error Handling Principles

Moved to [[design_spec_sections/08_Error_Handling_Principles]].

Top-level error handling principles plus the §8.1 path validation rules: unresolved-placeholder, illegal filesystem character, mode-prefix mismatch, orphan, and missing-required-field, with the hard/soft tier mapping.

---

## 9. Configuration File

Moved to [[design_spec_sections/09_Configuration_File]].

Full annotated `config.yaml` example: paths, database, README defaults, equipment registry (with transports and completeness signals), sync, and orchestrator settings.

---

## 10. README Generation

Moved to [[design_spec_sections/10_README_Generation]].

When the README runs, the four field-source layers, field types, user-added custom fields, pre-fill behavior, auto-filled system fields, the YAML front-matter output format with worked example, machine-query examples, and the README plugin hook.

---

## 11. `.exlab-wizard` Cache Folders

Moved to [[design_spec_sections/11_Cache_Folders]].

Per-level cache folder layout, file inventory by level, the `creation.json` and `readme_fields.json` schemas, log format, NAS sync behavior for cache files, discovery use cases, and the Validator Engine + Problem Query contract (§11.8).

---

## 12. Orchestrator Mode (Multi-Equipment Workstations)

Moved to [[design_spec_sections/12_Orchestrator_Mode]].

What changes in orchestrator mode (equipment scope, concurrent runs, logging tags, DB additions, staging pipeline activation), configuration pointers, and concurrent-run handling.

---

## 13. Equipment-to-Orchestrator Data Flow

Moved to [[design_spec_sections/13_Equipment_to_Orchestrator_Data_Flow]].

Topology, staging area layout, the five-state run lifecycle, `ingest.json` schema, completeness signals, transport handling, staging cleanup modes, and the staging state query backend contract.

---

## 15. Distribution and Installation

Moved to [[design_spec_sections/15_Distribution]].

PyInstaller-based build pipeline, code-signing posture (unsigned for v1; click-through-once UX on Windows and macOS), offline-machine specifics, launcher behavior, bundled vs external binaries (rclone bundled; rsync/ssh system-installed), versioning, and v1 distribution open questions.

---

## 16. Logging Architecture

Moved to [[design_spec_sections/16_Logging]].

The canonical home for ExLab-Wizard logging: the `exlab_wizard/logging/` package (`get_logger`, `configure_logging`, context vars), the on-disk log layout with a where-to-look quick reference, the structured-tag format, level / rotation / config integration, plugin-worker logging, debugging recipes, and the no-direct-`logging.getLogger` pre-commit rule. Other sections cross-reference here for any logging concern; this is the single source for the logger architecture.

---

## 14. Open Questions

1. ~~**Plugin sandboxing:** Should plugins run in a subprocess to prevent a plugin crash from taking down the app?~~ **Resolved (v0.7):** Yes. Each plugin runs in a dedicated worker subprocess with JSON-over-stdio IPC, declared timeout/memory caps, and a default network deny. Crashes, hangs, and OOM are contained per plugin and do not abort the creation session unless the template opts in via `_exlab_plugins_fatal: true`. See [[design_spec_sections/06_Plugin_System#6.3 Subprocess Isolation|§6.3]].
2. ~~**Template versioning:** Should template manifests carry a version field so existing runs can record which template version was used at creation time?~~ **Resolved (v0.7):** `_exlab_version` is required; templates without it fail to load with a structured error and do not appear in any wizard's selection list. Format is any non-empty string (semver or date stamp common; not enforced). See [[design_spec_sections/05_Template_Format#5.7 Template Versioning|§5.7]].
3. ~~**Multi-equipment projects: cross-equipment LIMS view.**~~ **Resolved (v0.7) on the on-disk side.** Each `<equipment>/<lims_short_id>/` tree's `creation.json` carries `lims_project.uid` (Mapping B; [[design_spec_sections/11_Cache_Folders#11.3 `creation.json` Schema|§11.3]]), so an offline cross-equipment join by `lims_project.uid` is computable from cache files alone. Whether the LIMS service exposes the same join as a first-class query is a downstream LIMS-team concern, not a v1 ExLab-Wizard blocker.
4. **Test-run promotion:** If a "test" run turns out to be data that should be analyzed (e.g. the test exposed real biological signal the operator wants to keep), should the app support an explicit promotion action (move the folder out of `TestRuns/` and flip the LIMS `run_kind` to `experimental`, appending a provenance note) or is re-running from the creation flow the intended path? Promotion preserves data but adds a mutation path to historical records, which complicates audit.
4a. **Test-run promotion -- path renaming:** If promotion is supported, should the leaf folder also be renamed from `TestRun_<DATE>` to `Run_<DATE>` so that the leaf-prefix invariant ([[design_spec_sections/03_Directory_Structure_Convention|Section 3]]) holds for promoted runs? Renaming preserves the invariant but breaks any external reference to the original path. Not renaming preserves external references but means a promoted run's leaf prefix no longer matches its `run_kind`. v0.6 leans toward renaming-with-symlink-back as a candidate, but this is unresolved.
5. **Test-run retention:** Test runs accumulate from calibration work, template debugging, and smoke tests. Should the app expose a retention policy (e.g. auto-archive test runs older than N days to a compressed tarball, or prompt the operator to delete) to prevent `TestRuns/` from ballooning over time? Currently no cleanup is defined; test runs live forever.
6. ~~**Offline DB writes:** Queue to local SQLite when LIMS is unreachable, flush when reconnected?~~ **Moot (v0.7):** ExLab-Wizard is read-only against the LIMS in v1 (Mapping B; [[design_spec_sections/07_Sync_and_Database_Integration#7.2 LIMS Integration|§7.2]]). There are no per-run LIMS writes to queue. The LIMS project list is a *read* cache (§7.2.4), not a write queue. Re-opens in v1.x if the LIMS team ships a `runs` resource (§7.2.6 ask #2) and ExLab-Wizard starts writing per-run records.
7. ~~**Plugin input interactions:** Is the `PluginInputRequired` exception pattern the right interface, or should plugins declare all needed variables in their manifest upfront?~~ **Resolved (v0.7):** Both. `manifest.yml` `required_variables` is the primary mechanism and is validated at registration time before Copier renders ([[design_spec_sections/06_Plugin_System#6.1.2 `manifest.yml` schema|§6.1.2]]). `PluginInputRequired` is retained as an escape hatch for inputs only knowable mid-transform (e.g., a workbook's named cells discovered after open); see [[design_spec_sections/06_Plugin_System#6.4 Plugin Input Escalation (`PluginInputRequired`)|§6.4]].
8. **Concurrent creation limit (orchestrator):** Should the app enforce a maximum number of simultaneously active creation sessions to prevent NAS or DB saturation? Likely unnecessary for typical lab scales (2-5 instruments) but worth revisiting if the equipment list grows.
9. **Post-sync checksum record (orchestrator):** If NAS sync is verified and staging is cleared, and a silent checksum mismatch is discovered later, there is no local recovery copy. Should the orchestrator permanently retain a lightweight manifest or checksum record (not the full data) so data loss is at minimum detectable?
10. ~~**Core field length limits:** Should `label` and `objective` have enforced maximum lengths?~~ **Resolved (v0.7):** Yes. `label` ≤ 100 chars, `objective` ≤ 2000 chars after whitespace trim. Enforced at the creation-time validation gate. See [[design_spec_sections/02_User_Interaction#2 Mandatory Core Fields (Creation Gate)|UI Spec §2]].
11. ~~**Operator identity source:** Should `operator` be validated against a known lab roster?~~ **Resolved (v0.7):** Free-text by default; optional allowlist via `config.yaml` `operators.allowlist` (case-sensitive). When the allowlist is non-empty, the wizard renders a dropdown instead of a free-text field, and the OS-username pre-fill is applied only when the username appears in the allowlist. See [[design_spec_sections/09_Configuration_File|§9]].
12. **v0.5 → v0.6 directory migration:** Existing v0.5 trees on disk follow the old `<project>/<equipment>/Run_<DATE>/` convention. The v0.6 backend writes new trees in the equipment-first form but does not auto-migrate prior trees. Open question: should the app ship a one-shot migration script that walks v0.5 roots and rewrites them to the v0.6 layout (including renaming `test_runs/` to `TestRuns/` and adding the `TestRun_` leaf prefix), or should v0.5 directories be treated as read-only legacy and only new creations follow the new convention? Migration preserves consistency; legacy-coexistence avoids touching settled data. The decision affects whether the browse capability (User Interaction Spec 3.4) needs to read both layouts indefinitely.
13. ~~**Override expiry:** Should `validation_overrides` entries carry an optional expiry timestamp?~~ **Resolved (v0.7):** Yes. Optional `expires_at` (UTC ISO 8601) field on override entries (not on tombstones). The matching algorithm in [[design_spec_sections/11_Cache_Folders#11.3 `creation.json` Schema|§11.3]] skips entries past `expires_at` without requiring an explicit tombstone; expired overrides remain in the array for audit. Bumps `creation.json` schema to 1.7. UI surface: optional date picker in the override dialog with quick-pick chips (+30d / +90d / +1y).
14. ~~**Validator content-scan cap:** Is the size cap and the extension allowlist user-configurable in `config.yaml`, or hard-coded?~~ **Resolved (v0.7):** Configurable. `config.yaml` `validator.content_scan_max_mib` (default **5**) and `validator.content_scan_extensions` (default list of common text extensions). Determinism guarantee is footnoted as "across identical configs." See [[design_spec_sections/09_Configuration_File|§9]] and [[design_spec_sections/08_Error_Handling_Principles#8.1.1 Unresolved-placeholder rule (hard tier)|§8.1.1]].
15. **Cross-run override propagation:** Overrides today are scoped per run, per problem class. Open question: for a known template-level issue (a vendor template that emits angle-bracket tokens by design), should the operator be able to record a template-scoped override in `config.yaml` that applies automatically to every run created from that template? This would reduce override toil at the cost of weakening the per-run audit trail.
16. **Equipment-ID rename (v2 commitment):** v1 documents a manual workaround (delete + re-add with new ID, manually move data, sync re-register) via a help-link in Frontend §7.7.2. v2 plans an in-app guided migration that handles `paths.py`, NASSync transport state, validator state, validation-overrides keyed on equipment, and the central audit log atomically. Tracked here so the v2 commitment doesn't get lost.
17. **Run-level LIMS records (v1.x):** Blocked on the LIMS team shipping a `runs` resource (see Backend §7.2.6 ask #2). When that lands, ExLab-Wizard adds `LIMSClient.register_run()`, restores the `LIMS_REGISTER` state in the §4.7 state machine, restores the `registering_with_lims` phase in WS events, and starts writing one record per run per the §7.2.7 logical schema.
18. **Offline-catalogue authenticity:** Should the producer sign the catalogue JSON (e.g. detached PGP / minisign / SSH-key signature) so consumers can detect a compromised producer that writes forged project metadata? v1 punts on this; defenses today are filesystem ACLs only (Backend §7.2.9.5). Adds a key-distribution and trust-root concern; revisit if labs surface compromised-NAS scenarios as a real threat model.
19. **Config-schema migration UX:** When a future version renames a required `config.yaml` field (or changes the YAML structure), what does the launcher show on first launch with the old config? In-place rewrite with backup, exit-with-instructions, or a guided migration screen? Backend §11.9.6 currently says the launcher "refuses to start with an unmigrated config" but the operator UX is undefined. v1 has no rename in flight; revisit when the first one ships.
20. **Retroactive-finding remediation:** When the validator detects a hard-tier finding on a run whose `sync_status` is already `synced` (e.g. a config tightening introduced a stricter rule, see Backend §7.3 / User Interaction Spec §7.5), the gate is forward-only: the row tags `Synced under prior policy` and the operator's actions are diagnostic only. Open question: should v1.x add an explicit remediation flow (re-validate, optionally re-sync after remediation, audit-log the change)? The current "out of scope for the gate" stance leaves operators with no way to act on retroactive findings.
21. **Dark mode (v1.x):** v1 ships light-mode-only per Frontend §2.1. DESIGN.md is single-mode. Adding dark mode requires DESIGN.md to ship a dark-mode token map and ExLab-Wizard's `design.py` to honor a runtime mode flag (auto-follow OS appearance or in-app toggle). v1.x candidate; not blocking.

Open questions that were previously listed as UI-only (GUI framework selection, `.exlab-wizard` tree visibility) have been relocated to `ExLab-Wizard_Frontend_Spec.md`.
