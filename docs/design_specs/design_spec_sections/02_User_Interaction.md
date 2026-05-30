# 2. User Interaction

**Scope:** User-facing capabilities and user-flow contracts. Backend behavior, schemas, persistence, and integrations live in `ExLab-Wizard_Design_Spec.md`. Concrete widget choices and screen layouts live in `ExLab-Wizard_Frontend_Spec.md`.

**Relationship to the other specs:**

- The **Design Spec** owns the question "what does the system do internally" -- schemas (`creation.json`, `ingest.json`, `readme_fields.json`), Copier integration, plugin contracts, NAS sync, LIMS records, orchestrator staging.
- The **Frontend Spec** owns "how is each surface drawn" -- widget mappings, wizard step layouts, color tokens, keyboard navigation.
- This **User Interaction Spec** sits between them. It defines the user-visible capability set, the inputs each capability requires, the validation gates, and the order in which the user encounters them. It is intentionally framework-agnostic and does not prescribe widgets.

---

## Table of Contents

1. [Purpose and Scope](#1-purpose-and-scope)
2. [Mandatory Core Fields (Creation Gate)](#2-mandatory-core-fields-creation-gate)
3. [User Capabilities](#3-user-capabilities)
   - 3.1 [Create a New Project](#31-create-a-new-project)
   - 3.2 [Create a New Experimental Run](#32-create-a-new-experimental-run)
   - 3.3 [Create a New Test Run](#33-create-a-new-test-run)
   - 3.4 [Browse Existing Equipment, Projects, and Runs](#34-browse-existing-equipment-projects-and-runs)
   - 3.5 [Author a README at Creation Time](#35-author-a-readme-at-creation-time)
   - 3.6 [Configure Equipment, Paths, and Integrations](#36-configure-equipment-paths-and-integrations)
   - 3.7 [Monitor Orchestrator Staging](#37-monitor-orchestrator-staging-orchestrator-mode-only)
   - 3.8 [Review and Resolve Naming and Validation Problems](#38-review-and-resolve-naming-and-validation-problems)
4. [Mode Invariants and Safety Boundaries](#4-mode-invariants-and-safety-boundaries)
5. [Validation Order and Error Surfacing](#5-validation-order-and-error-surfacing)
6. [User-Visible State Across Surfaces](#6-user-visible-state-across-surfaces)
7. [Pre-Sync Gate](#7-pre-sync-gate)

---

## 1. Purpose and Scope

This document specifies the user-visible capability contract that the ExLab-Wizard application must satisfy. Each capability has a triggering condition, the inputs the user must supply, the validation gates that the input passes through, and a brief description of the resulting backend data flow (with pointers to the Design Spec for the authoritative behavior).

The document does **not** specify:

- How any surface is drawn -- see `ExLab-Wizard_Frontend_Spec.md`.
- How any backend component is implemented -- see `ExLab-Wizard_Design_Spec.md`.

Where this doc references a backend term (`run_kind`, `_exlab_run_scope`, `creation.json`, `ingest.json`, `readme_fields.json`, NASSync, LIMS), the definition lives in the Design Spec; follow the reference rather than duplicating it.

---

## 2. Mandatory Core Fields (Creation Gate)

Every **project** and **run** creation must supply values for the **mandatory core field set** before any filesystem or database operations begin. The creation controller rejects submissions missing any core field with a validation error that names the missing field(s). Core fields are hard-coded at the backend level; templates and `config.yaml` may extend the required set but cannot disable the core.

| Field       | Meaning                                                                                                                                                                                            | Default behavior                                                                   |
| ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| `label`     | Short human-readable name for the project or run, distinct from the machine-safe project/run identifier (the on-disk path uses the LIMS `short_id`, e.g. `PROJ-0042`; see Design Spec §3 and §7.2). For project creation: pre-filled with the selected LIMS project's `name` (e.g. `"Cortex Q3 Pilot"`); editable. For run creation: no default; operator supplies (e.g. `"calibration sweep, 488 nm"` for a test run). | Project: pre-filled from LIMS name, editable. Run: no default; user must supply. Must be **non-empty and ≤ 100 characters** after trimming whitespace. |
| `operator`  | Person responsible for this creation. Used for attribution in logs, LIMS records, and README output.                                                                                               | Pre-filled with OS username; user may override but must confirm a non-empty value. If `config.yaml` `operators.allowlist` is non-empty, the value must match an entry in the allowlist (case-sensitive). |
| `objective` | One-paragraph description of what this project or run is for. Prevents unlabeled directories with no recoverable context.                                                                          | No default; user must supply. Must be **non-empty and ≤ 2000 characters** after trimming whitespace. (Local-only in v1; not synced to LIMS — see Design Spec §7.2.6.) |

The core set applies to **Create a New Project** (3.1), **Create a New Experimental Run** (3.2), and **Create a New Test Run** (3.3). It does **not** apply to equipment-level creation, which is a structural consequence of project creation rather than a user-authored event.

Core field values flow into two places in v1 (Design Spec §7.2 commits to read-only LIMS integration via Mapping B; per-run LIMS writes are deferred to v1.x):

1. The `readme_fields.json` `core_fields` block (Design Spec §11.4).
2. The generated `README.md` YAML front matter (Design Spec §10.7).

A future v1.x will additionally mirror these fields to LIMS run records when the LIMS team ships the `runs` resource (Design Spec §7.2.7).

Additional fields may be marked mandatory via `config.yaml` `readme.defaults[].required: true` (lab-policy extension) or via a template's `copier.yml` `readme.fields[].required: true` (template-specific extension). Extensions are validated alongside the core set; none of the three layers can disable a core field.

---

## 3. User Capabilities

Each capability below is described as a use-case with its triggering condition, required inputs, and the resulting backend data flow. **How each capability is surfaced to the user is specified in the Frontend Spec; this section defines only what inputs the user must produce and what validation gates apply.**

### 3.1 Create a New Project

- **Trigger:** User initiates project creation.
- **Inputs:** **LIMS project selection** (the existing LIMS project this ExLab project will be tracked under; see Design Spec §7.2 Mapping B); equipment selection (the equipment under which this project will live); selected project-scope template; resolved variable form values; **mandatory core fields (`label`, `operator`, `objective`)** — `label` pre-filled with the LIMS project's name, the operator may amend; any additional README field values required by template or config.
- **Validation gates (in order):** A LIMS project is selected → equipment ID is in the configured equipment list → core fields non-empty → template-required fields non-empty → config-required fields non-empty → no character-set or path violations.
- **Backend flow (summary):** App validates inputs → on any missing required field, reject with a structured error before touching the filesystem → Copier renders the project-scope template into `<local_root>/<equipment>/<lims_short_id>/` (path segment is the LIMS `short_id`, e.g. `PROJ-0042`) → `.exlab-wizard/creation.json` and `readme_fields.json` written at project level, with `lims_project.uid`, `lims_project.short_id`, and `lims_project.name_at_creation` recorded → NAS sync queued → summary returned. **No LIMS write** in v1; the LIMS project already exists. Authoritative backend description: Design Spec Sections 4 (Backend Architecture), 7 (Sync and Database Integration), 11 (`.exlab-wizard` cache).

A LIMS project may be tracked under more than one equipment over its lifetime; each `<equipment>/<lims_short_id>/` pair is its own self-contained tree, with its own README and `.exlab-wizard` cache. The same LIMS project (same `short_id`) may therefore appear under multiple equipment folders; the equipment registry in `config.yaml` remains authoritative for equipment IDs, and the LIMS remains authoritative for project identity.

### 3.2 Create a New Experimental Run

- **Trigger:** User initiates experimental run creation from within a selected `<equipment>/<project>` context.
- **Inputs:** Selected run-scope template whose `_exlab_run_scope` is `"experimental"` or `"both"`; variable form values; **mandatory core fields**; any additional README field values required by template or config.
- **Validation gates:** Same as 3.1, plus: the selected template's `_exlab_run_scope` must include `"experimental"`.
- **Backend flow (summary):** App validates → on any missing required field, reject before touching the filesystem → compose destination path `<local_root>/<equipment>/<lims_short_id>/Runs/Run_<ISO8601_DATE>/` (creating `Runs/` on demand) → `.exlab-wizard/runs.json` marker written at the `Runs/` level if this is the first experimental run there → Copier renders template (in-process; no `_tasks`) → plugins execute (host-driven post-render pass) → `.exlab-wizard/creation.json` written with `run_kind: "experimental"` and the `lims_project` block, `readme_fields.json`, and `wizard.<hostname>.log` entry appended → NAS sync queued. **No LIMS write per run in v1** (Design Spec §7.2.2; deferred to v1.x). Authoritative backend description: Design Spec Sections 3 (Directory Structure), 5 (Template Format / Copier integration), 6 (Plugin System), 7 (Sync and LIMS).

### 3.3 Create a New Test Run

- **Trigger:** User initiates test run creation from within a selected `<equipment>/<project>` context. Test runs cover instrument calibration, dry runs, plugin/template debugging, and QC checks.
- **Inputs:** Selected run-scope template whose `_exlab_run_scope` is `"test"` or `"both"`; variable form values; **mandatory core fields**; any additional README field values required by template or config. Core-field enforcement applies equally to test runs -- a test run with no objective is as unrecoverable as an experimental run with no objective.
- **Mode invariant:** The "test" vs. "experimental" mode is a single flag bound at creation-session start and cannot be changed mid-session. This is the integrity boundary that prevents accidental miscategorization; the mode must be expressed by the client as an explicit user choice, not inferred. See Section 4 for the full invariant list.
- **Validation gates:** Same as 3.1, plus: the selected template's `_exlab_run_scope` must include `"test"`.
- **Backend flow (summary):** App validates → on any missing required field, reject before touching the filesystem → compose destination path `<local_root>/<equipment>/<lims_short_id>/TestRuns/TestRun_<ISO8601_DATE>/` (creating `TestRuns/` on demand) → `.exlab-wizard/test_runs.json` marker written at the `TestRuns/` level if this is the first test run there (filename retained from v0.5 for backward compatibility) → Copier renders template (in-process; no `_tasks`) → plugins execute (host-driven post-render pass) → `creation.json` written with `run_kind: "test"` and the `lims_project` block → NAS sync queued. **No LIMS write per run in v1** (Design Spec §7.2.2; deferred to v1.x). Authoritative backend description: Design Spec Sections 3 (Directory Structure, including the redundant folder + leaf-prefix separation), 7 (Sync and LIMS), 11 (`.exlab-wizard` cache).
- **Downstream contract:** Automated analysis pipelines must filter `is_test = false` (or `run_kind = 'experimental'`) by default. Folder-level separation (`TestRuns/` parallel to the `Runs/` experimental container, with the leaf-folder prefix `TestRun_` as a secondary cue) is the primary defense; the LIMS flag is a redundant cross-check.

### 3.4 Browse Existing Equipment, Projects, and Runs

- **Trigger:** User opens the application.
- **Inputs:** Configured `local_root` (or NAS mount) from `config.yaml`.
- **Backend flow (summary):** App reads the equipment-first hierarchy from the filesystem. For each run discovered, it may read `.exlab-wizard/creation.json` to determine `run_kind`, template, and provenance. Test runs (those under a `TestRuns/` parent or whose leaf folder begins with `TestRun_`) must be distinguishable to the client as a data attribute; visual treatment is a Frontend Spec concern.

### 3.5 Author a README at Creation Time

- **Trigger:** Always invoked for project-scope and run-scope creations. There is no "README disabled" mode for these scopes -- every project and run carries a `README.md` with at minimum the mandatory core fields (`label`, `operator`, `objective`; see Section 2).
- **Inputs:** Merged field set from four layers (core, template, config, system; see Design Spec Section 10.2); user values for editable fields; optional user-added custom fields.
- **Validation gates:** Core fields non-empty → template-required fields non-empty → config-required fields non-empty → no duplicate field IDs.
- **Backend flow (summary):** App merges field layers, collects user input, validates core + extended required fields, renders the README output (Design Spec Section 10.7) as YAML front matter + Markdown prose, writes `README.md` at the created directory root, writes `readme_fields.json` to the `.exlab-wizard/` cache.
- **No skip path:** There is no client-level skip for the README step on project or run creation. The core fields must be present. Templates may still add optional extension fields that the user can leave blank.

### 3.6 Configure Equipment, Paths, and Integrations

- **Trigger:** User modifies application configuration.
- **Inputs:** Template directory, NAS sync root, LIMS endpoint, plugin directory, equipment list (add/remove/edit), orchestrator toggle and staging root.
- **Validation gates:** Paths are resolvable on the local filesystem → DB connection string parses → equipment IDs are unique → equipment IDs are filesystem-safe (the equipment ID is a path segment under the equipment-first convention, so it must not contain illegal characters for the target OS).
- **Backend flow (summary):** Settings persist to `config.yaml`. Validation runs on save before persistence.

### 3.7 Monitor Orchestrator Staging (Orchestrator Mode Only)

- **Trigger:** User views staging status on a machine with `orchestrator.enabled: true`.
- **Inputs:** None; the view is a live read of `/staging/`.
- **Backend flow (summary):** App enumerates staged runs under the configured staging root, reads each run's `ingest.json`, exposes current state, file count, byte total, elapsed time since last activity, and per-run actions (force sync, clear verified, view log). Action semantics are backend operations (Design Spec Section 13.7); presentation is a Frontend Spec concern.

### 3.8 Review and Resolve Naming and Validation Problems

- **Trigger:** Always-on. The capability is exposed as a persistent surface (a "Problems" tab; Frontend Spec) that the user can open at any time, and is updated automatically by the same background refresh that powers the browse view (Frontend Spec §3.3). The user does not have to initiate an audit; the audit is continuous.
- **Scope of the audit:** The validator walks every directory under the configured `local_root` for the active equipment list, plus the orchestrator `staging_root` when `orchestrator.enabled: true`. It does not require a NAS round-trip; the audit is local.
- **Problem classes surfaced:**
  1. **Unresolved placeholder tokens.** Any path segment, file name, or rendered text-file content that still contains a literal Jinja-style placeholder (`<name>`, `<date>`, `<project>`, `<equipment>`, or any other `<...>` token, plus any leftover `{{ ... }}` marker). These indicate a template variable that was never substituted at creation time. The validator names the offending segment or file and the specific token.
  2. **Illegal filesystem characters.** Any path segment containing characters illegal on the target OS (the same character-set validation used at creation time, run retroactively against existing trees).
  3. **Mode-prefix mismatches.** A leaf folder named `Run_*` whose `creation.json` has `run_kind: "test"`, or `TestRun_*` whose `creation.json` has `run_kind: "experimental"`, or a `Run_*` leaf not under a `Runs/` parent, or a `TestRun_*` leaf not under a `TestRuns/` parent. (Cross-check against Design Spec §3.)
  4. **Orphans.** Directories at run, project, or equipment level with no `.exlab-wizard/creation.json` (Design Spec §11.7).
  5. **Missing required README fields.** Existing runs whose `readme_fields.json` is missing a current core or config-required field (e.g. a lab-policy field added after the run was created).
- **Severity tiers:** Each problem has one of two tiers, reported as a data attribute. The Frontend Spec specifies how each tier is rendered.
  - **Hard.** Unresolved placeholder tokens, illegal filesystem characters, and mode-prefix mismatches. These block NAS sync via the Pre-Sync Gate (§7) until resolved or explicitly overridden.
  - **Soft.** Orphans and missing-required-field problems. These do not block sync; they are surfaced for operator review only.
- **Inputs (per-row actions):** For each flagged item the user may: jump to the offending node in the browse tree; open the directory in the OS file manager; view the relevant `wizard.<hostname>.log`; mark the problem as a known issue (suppresses from default view, kept in audit log); or, for hard-tier problems on items currently blocked from sync, open an override dialog (§7).
- **Validation gates:** None at the capability level itself; the Problems tab is read-only diagnostic state. Gates apply when the user takes an action (e.g. override requires a non-empty reason string and is logged).
- **Backend flow (summary):** A validator engine produces a deterministic problem list from a single walk of the managed tree. The same engine is invoked at creation time (per-run, before filesystem writes; Design Spec §8) and continuously by the Problems audit (per-tree, on the background refresh cadence). Hard-tier problems on a run cause the Pre-Sync Gate (§7) to mark that run ineligible for NASSync queueing. Authoritative backend description: Design Spec §8.x (Path Validation Rules), §7.3 (Pre-Sync Gate), and §11.7 (Discovery and Validation Use Cases).

---

## 4. Mode Invariants and Safety Boundaries

The experimental-vs-test distinction is a correctness boundary. The user interaction must enforce these invariants:

- **Single-flag binding.** The mode flag is bound at the moment the user initiates a creation session. It is not derived from the selected template, the destination folder, or any other input.
- **No mid-session mutation.** The mode cannot change after binding. If the user picks the wrong mode, the only valid recovery is to abort and restart the creation session.
- **Always visible.** Every surface that follows mode binding (variable form, README form, preview, confirmation) must surface the active mode unambiguously. The user must never be uncertain whether they are creating an experimental or a test run.
- **Folder-name parity.** The mode determines the leaf-folder prefix and parent folder: `Run_<DATE>` under `<equipment>/<project>/Runs/` for experimental, `TestRun_<DATE>` under `<equipment>/<project>/TestRuns/` for test. Any divergence is a backend bug, not a user-recoverable state.

These are user-visible invariants because the user is the actor whose miscategorization the system must defend against. The Frontend Spec describes the concrete affordances that implement them (mode badge, color cues, button labels).

---

## 5. Validation Order and Error Surfacing

The validation order is part of the user contract because it determines what the user sees first when something is wrong. The order is:

1. **Mode invariants** (Section 4) -- a misbound mode is rejected before anything else, with a structured error naming the conflict.
2. **Mandatory core fields** -- if any of `label`, `operator`, `objective` are empty after trim, reject with a per-field error naming each missing field. Length limits are also enforced at this gate: `label` ≤ 100 chars, `objective` ≤ 2000 chars (after trim). If `config.yaml` `operators.allowlist` is non-empty, `operator` must match an allowlisted entry; mismatch is reported as a structured error with the allowed values listed.
3. **Template-required fields** -- per-field errors for any `required: true` field declared in the selected template's `copier.yml`.
4. **Config-required fields** -- per-field errors for any `required: true` field declared in `config.yaml` `readme.defaults`.
5. **Path and character-set validation** -- the resolved destination path must not contain illegal characters for the target filesystem; the equipment, project, and run-folder segments are each validated independently so the error names which segment is at fault. This gate also includes the **unresolved-placeholder check**: no path segment, file name, or rendered text-file content may contain a literal Jinja-style placeholder (`<name>`, `<date>`, any other `<...>` token, or any `{{ ... }}` marker). An unresolved placeholder indicates a template variable that was not substituted; the error names the offending segment or file and the specific token. The same rule is applied retroactively by the Problems audit (§3.8) against existing trees.
6. **Filesystem-state validation** -- the destination path must not already exist (Copier's `overwrite=False` is set; the user-facing contract is "creation never silently overwrites").

Errors must name the offending field or segment. Generic "Please fill in all required fields" messages are not acceptable for the core set, because they obscure which field is missing when multiple are empty.

---

## 6. User-Visible State Across Surfaces

State that the user can see and act on, listed by the surface that owns it:

- **Main view (browse):** Equipment list; per-equipment list of projects; per-project list of runs (experimental and test, distinguished by parent folder and leaf prefix); per-run summary (template, operator, sync status, run kind).
- **Creation flow:** Active mode flag; resolved destination path; merged required-field list; per-field validation status; phase-by-phase progress during creation.
- **Orchestrator staging panel (orchestrator mode only):** Per-staged-run lifecycle state; file count and byte total; elapsed time since last activity; per-run actions (force sync, clear verified, view log); test-vs-experimental classification.
- **Settings:** Current `config.yaml` values; per-field validation status on save.
- **Problems tab (always-on; §3.8):** The list of every flagged item under the managed tree, each with its severity tier (hard or soft), problem class (placeholder, illegal-char, mode-mismatch, orphan, missing-required-field), the offending path segment or file, and the run's current sync-gate status (eligible, blocked-by-hard-problem, override-allowed). Each row also exposes its history of operator interventions (mark-as-known, override-with-reason).

The Frontend Spec specifies how each item is drawn. The Design Spec specifies where each item's underlying data lives. This document specifies that these items must exist as user-visible state, and what a correct user contract requires of them.

---

## 7. Pre-Sync Gate

The Pre-Sync Gate is the contract by which the validator that powers the Problems audit (§3.8) also gates NAS sync. It exists so that a directory with a hard-tier validation problem cannot leave the workstation until the operator has either fixed it or made an explicit, logged decision to allow it through.

### 7.1 What the gate blocks

A run is **ineligible for NAS sync** when the validator reports any **hard-tier** problem on any path segment, file name, or rendered file under that run. NASSync (Design Spec §7.1) must not enqueue an ineligible run. In orchestrator mode (Design Spec §13), the gate applies at the staging-to-NAS boundary; on single-equipment workstations it applies at the local-to-NAS boundary.

Soft-tier problems do not block sync. They are surfaced in the Problems tab for review only.

### 7.2 What the user sees

- The Problems tab shows each flagged item's gate status. Hard-tier items on a run that is otherwise ready to sync show a **"Sync blocked"** badge. Items with an active override show an **"Override active"** badge with the reason text.
- The browse view's per-run sync-status icon (Frontend Spec §3.2) reflects the gate: a run blocked by a hard-tier problem shows a distinct status, distinct from "pending", "synced", and "failed".
- A creation that *just* succeeded but produced a hard-tier problem (e.g. a template bug left a `<...>` token in a file name) shows the success-summary card on the wizard's final step *and* a banner that NAS sync has been gated; the run's directory was created locally but will not be uploaded until the problem is resolved.

### 7.3 How the gate is cleared

There are exactly two ways to make a blocked run eligible for sync again:

1. **Resolve the underlying problem.** The operator fixes the offending name or file (typically by deleting and re-creating the run with a corrected template, or by renaming the segment if no internal references would break). On the next validator pass, the hard-tier problem disappears and the run becomes eligible.
2. **Explicit override with reason.** The operator opens the override dialog from the Problems tab, supplies a non-empty free-text reason (e.g. "vendor template uses `<` and `>` legitimately in folder name; whitelisting"), and confirms. The override is recorded in the run's `wizard.<hostname>.log` with the operator name, timestamp, problem class, and reason. Overrides are scoped to a single run and a single problem class; an override does not apply to other runs or to other problem classes within the same run.

There is no "global mute" or "disable validator" toggle. Suppression at the surface level (the "Mark as known issue" action in the Problems tab) hides a row from the default view but does not clear the gate; only resolution or override does.

### 7.4 Override audit contract

Every override is auditable:

- The override is appended to `wizard.<hostname>.log` at the equipment level (Design Spec §11.5) and to the run's own `.exlab-wizard/wizard.<hostname>.log` if present.
- The override reason is stored in `creation.json` under a `validation_overrides` array (per run, per problem class), so that downstream tooling reading the cache can see which runs synced under override and why. Schema details: Design Spec §11.3.
- The override dialog requires the operator to acknowledge that the override will be visible in the LIMS attribution (the operator field is reused; no separate "approver" identity is collected).

### 7.5 Gate semantics on retroactive problems

If the validator detects a hard-tier problem on a run that has already synced (e.g. a config change introduced a stricter character rule, or a downstream consistency check flagged a previously valid name), the gate does not retroactively unsync the data. The Problems tab surfaces the issue with a "Synced under prior policy" tag. The operator's available actions are diagnostic only (jump-to, open-in-finder, mark-as-known); the remediation path is out of scope for the gate.

