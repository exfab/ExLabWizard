# 5. Template Format

Parent: [[ExLab-Wizard_Design_Spec]]

---

Templates are directories that the app copies and transforms at creation time using [Copier](https://copier.readthedocs.io/) as the template engine.

## 5.0 Template Locations (Global and Per-Equipment)

ExLab-Wizard maintains templates in **two distinct locations**:

| Location | Holds | Path |
|---|---|---|
| **Global templates directory** | Equipment templates and equipment-agnostic project templates. Templates that should be available to all equipment instruments. | `paths.templates_dir` in `config.yaml` (e.g. `~/exlab-templates/`). Operator-writable. Bundled starter templates are copied here on first launch (see [[15_Distribution|§15]]). |
| **Per-equipment template cache** | Run templates for that specific equipment, plus equipment-specific project templates that override or supplement the global set when this equipment is selected. | `<local_root>/<equipment_id>/.exlab-wizard/templates/` — i.e. inside the equipment's existing cache directory ([[11_Cache_Folders#11.1 Folder Placement|§11.1]]). Operator-writable. Syncs to NAS as part of the equipment cache, so other workstations that mount the same equipment folder pick up the templates after a sync round-trip. |

**Template resolution by wizard:**

| Wizard | Template source |
|---|---|
| New Equipment (one-time per equipment, during onboarding or via Settings) | Global templates dir; `_exlab_type: "equipment"` filter. |
| New Project | Merged set: global templates + per-equipment project templates, filtered to `_exlab_type: "project"`. On name collision, the per-equipment template wins (a lab can override a globally-shipped project template for a specific instrument). |
| New Run / New Test Run | Per-equipment template cache only; `_exlab_type: "run"` filter, further narrowed by `_exlab_run_scope`. Run templates are intentionally per-equipment because run procedures are highly instrument-specific. |

**Bundled starter templates.** The PyInstaller bundle ([[15_Distribution|§15]]) ships a small set of starter templates inside `_internal/templates/`: one equipment template, one generic project template, and one or two run templates demonstrating the API. On first launch, the onboarding flow asks the operator whether to copy them into `paths.templates_dir`. Operators can edit, extend, or delete them; the bundled originals remain read-only inside the app bundle as a recovery reference.

**Template manifest still applies.** Each template is a Copier template with a `copier.yml` manifest carrying `_exlab_type`, `_exlab_run_scope` (when applicable), `_exlab_version`, and the template's questions. The two locations differ only in *where* templates live, not in *how* they are structured (§5.2 onward).

Copier is an MIT-licensed Python library (requires Python >= 3.10) that renders project templates using Jinja2, supports structured question definitions in a `copier.yml` manifest, records answers for auditability, and exposes a Python API (`copier.run_copy()`) that allows ExLab-Wizard to drive it programmatically without spawning a subprocess.

## 5.1 Template Structure

Each template is a directory conforming to Copier's layout conventions:

```
my_template/
  copier.yml              # Copier manifest: questions, settings, ExLab metadata
  {{project_name}}/       # Jinja2-templated folder names are supported
    metadata.xlsx.jinja   # .jinja suffix = rendered by Jinja2 at copy time
    protocol.docx         # no suffix = copied verbatim
    README.md.jinja       # README template (see Section 10)
  .exlab-answers.yml.jinja  # Copier answers file (see Section 5.4)
```

Files ending with `.jinja` are processed by the Jinja2 templating engine; all other files are copied verbatim. Folder names can also be templated using `{{variable}}` syntax, which Copier resolves at render time.

## 5.2 Copier Manifest (`copier.yml`)

The `copier.yml` file at the template root serves as the single source of truth for both Copier settings and ExLab-Wizard metadata. ExLab-specific keys use an `_exlab_` prefix to avoid collision with Copier's reserved `_`-prefixed settings keys.

```yaml
# --- Copier settings ---
_min_copier_version: "9.0"
_answers_file: ".exlab-answers.yml"   # where Copier records answers (see 5.4)
_skip_if_exists:
  - "*.xlsx"                           # don't overwrite existing data files on re-copy
# NOTE: _tasks is intentionally absent. As of v0.7, the plugin pass is driven
# by the creation controller after copier.run_copy() returns; ExLab-Wizard
# invokes Copier with unsafe=False so any _tasks declared here are ignored.
# See §5.5 and [[06_Plugin_System#6.0 Where Plugins Sit in the Pipeline|§6.0]].

# --- ExLab-Wizard metadata ---
_exlab_type: "run"           # "project", "equipment", or "run" -- controls which capability shows this template
_exlab_run_scope: "both"     # run templates only: "experimental", "test", or "both" (see Section 3)
_exlab_description: "Standard confocal acquisition run"
_exlab_readme:
  # README is always generated for project/run scopes with the mandatory
  # core fields (label, operator, objective). Templates may declare
  # additional fields below; see Section 10.
  fields: []                 # extension list, populated per-template

# --- Questions (drive input collection and Jinja2 context) ---
project_name:
  type: str
  help: "Project name"

operator:
  type: str
  help: "Operator initials"
  default: ""

run_date:
  type: str
  help: "Run date (ISO 8601)"
  default: "{{ '%Y-%m-%dT%H-%M-%S' | strftime }}"

sample_type:
  type: str
  help: "Sample type"
  choices:
    - Fixed tissue
    - Live cell
    - Suspension
    - Other
```

Copier reads settings (underscore-prefixed keys) and question definitions from `copier.yml`, with CLI/API arguments taking priority over file-level values. ExLab-Wizard supplies all answers programmatically via the API, so Copier never prompts interactively -- the client collects all user input and passes it through as a resolved variable map.

## 5.3 Copier Python API Integration

The app invokes Copier via its Python API rather than as a CLI subprocess. The resolved variable map from the client is passed directly as the `data` parameter, bypassing Copier's interactive prompts entirely:

```python
# Illustrative -- not implementation code
from copier import run_copy

run_copy(
    src_path="path/to/templates/confocal_run_v2",
    dst_path="/data/CONFOCAL_01/PROJ-0042/Runs/Run_2026-04-17T14-32",
    data={
        "project_name": "Cortex Q3 Pilot",   # the LIMS project's name; on-disk segment is the short_id (PROJ-0042)
        "operator": "asmith",
        "run_date": "2026-04-17T14-32-00",
        "sample_type": "Fixed tissue",
    },
    overwrite=False,      # never silently overwrite existing runs
    unsafe=False,         # v0.7+: no _tasks; plugins run from the controller after this returns
    quiet=True,           # suppress Copier's stdout; app handles progress reporting
)
```

The `data` parameter accepts any Python value and injects it into the Jinja2 rendering context, making the resolved inputs available both for file content rendering and for templated file/directory names.

## 5.4 Copier Answers File (`.exlab-answers.yml`)

Copier automatically writes an answers file recording all question responses; the path is configured via `_answers_file` in `copier.yml`. ExLab-Wizard sets this to `.exlab-answers.yml` and places it in the run/project root. This file serves a complementary but distinct role from `.exlab-wizard/creation.json`:

| File | Written by | Purpose |
|---|---|---|
| `.exlab-answers.yml` | Copier | Records Jinja2 rendering context; enables `run_recopy` for template updates |
| `.exlab-wizard/creation.json` | ExLab-Wizard | Records full provenance: plugins, paths, sync status, orchestrator context |

Both files are synced to NAS. The answers file is Copier's domain; `creation.json` is ExLab-Wizard's.

## 5.5 Plugin Pass (Post-Render, Controller-Driven)

**v0.7 change.** Earlier drafts wired the plugin system into Copier's `_tasks` post-copy hook with `unsafe=True`. That coupling has been removed. The plugin pass is now invoked by the creation controller **after** `copier.run_copy()` returns, in the same long-lived FastAPI app process that owns the registry and the session state. ExLab-Wizard calls Copier with `unsafe=False`; any `_tasks` declared in a template are silently ignored.

**Why the change.** With `_tasks` Copier executes the plugin script as a fresh subprocess. That subprocess is not the long-lived "host" the [[06_Plugin_System|§6]] contract describes — it has no registry built at app startup, no in-memory session state, and no IPC channel back to the controller for `PluginInputRequired` suspend/resume. Solution A in the v0.7 architecture decision relocates the plugin host into the controller process, where the registry actually lives and where suspending the session for operator input is a normal `await`.

**Mechanics.** The controller's pipeline (see [[04_Backend_Architecture#4.7 Creation-Session State Machine|§4.7]]) is:

1. `VALIDATING` — pre-render checks (no FS).
2. `RENDERING` — `await TemplateEngine.render(...)` calls `copier.run_copy(unsafe=False)` in-process. Copier writes the directory tree, the `.exlab-answers.yml`, and any `.jinja`-suffixed files rendered through Jinja2.
3. `PLUGIN_PASS` — `await PluginHost.run_pass(...)` walks the rendered tree, dispatches plugin workers (one subprocess per matched plugin), and returns when all workers complete or `PluginInputRequired` suspends the session. See [[06_Plugin_System#6.2.2 Resolution per creation session|§6.2.2]] for the host-side flow.
4. `CACHE_WRITE` etc.

Templates remain authoritative for **plugin order** via `_exlab_plugins` (see [[06_Plugin_System#6.2.3 Order control via `_exlab_plugins`|§6.2.3]]), and for **per-plugin variable requirements** via the plugin's own `manifest.yml`. Nothing in `copier.yml` invokes the host.

**Linting.** The v0.7 plugin/template lint command (`exlab-wizard templates lint <dir>`) emits a WARN-level finding for any `copier.yml` that declares `_tasks`, since the keys are now dead config and likely indicate a template ported from an older draft of the spec.

**What this preserves.** Every other Copier feature ExLab-Wizard relied on continues to work without `_tasks`: the Jinja2 rendering of `.jinja`-suffixed files (§5.6), `_skip_if_exists` (§5.2), the `.exlab-answers.yml` write (§5.4), `_min_copier_version` enforcement, and `_exlab_*` metadata reads. None of these depend on `unsafe=True`.

## 5.6 Jinja2 Templating in File Contents

Any file with a `.jinja` suffix has its contents rendered by Jinja2 using the full answers context. This covers:

- **README.md.jinja** -- the README template (see Section 10); structured sections are rendered with variable values injected
- **Metadata files** -- e.g. a `run_log.csv.jinja` that pre-populates a header row with project and run metadata
- **Plain text configs** -- any instrument-specific config file that needs run-specific values embedded

Jinja2 filters available include the full `jinja2-ansible-filters` set, which provides `to_nice_yaml`, string manipulation, and date formatting utilities useful for lab metadata rendering.

## 5.7 Template Versioning

Copier records `_commit` and `_src_path` in the answers file, indicating the template state at generation time. Since lab templates are stored locally (not as Git repositories), `_commit` will not be populated automatically. ExLab-Wizard compensates by reading a `_exlab_version` field from `copier.yml` and recording it explicitly in `creation.json` `template.version`.

**v0.7: `_exlab_version` is required.** A template whose `copier.yml` is missing this field — or sets it to an empty string — fails to load: the template registry skips it, logs a structured error (`code: "template_load_error"`, `reason: "missing_exlab_version"`, with the offending template path), and the template does not appear in any wizard's selection list. The lint command (`exlab-wizard templates lint <dir>`) flags missing-version templates at WARN.

Format: any non-empty string. Common conventions are semver (`"1.0"`, `"2.1.3"`) or a date stamp (`"2026-04-17"`); the spec does not enforce a particular shape. Template authors must increment this manually when making breaking changes (changes to question names, mandatory variable additions, or rename of generated files).

This recorded version enables the template audit use case described in [[11_Cache_Folders#11.7 Discovery and Validation Use Cases|Section 11.7]] and lets the LIMS / cache reader determine "which template version produced this run" without inspecting the template source.

## 5.8 Lint CLI: `exlab-wizard templates lint`

The lint subcommand validates templates without loading them into a running app. Useful for template authors, CI pipelines, and pre-deployment review. Parallel in shape to the plugin lint (see [[06_Plugin_System#6.9 Lint CLI: `exlab-wizard plugins lint`|§6.9]]).

**Invocation:**

```
exlab-wizard templates lint <PATH>
exlab-wizard templates lint <PATH> --json
exlab-wizard templates lint <PATH> --strict
```

`<PATH>` is either a single template directory (containing `copier.yml`) or a parent directory holding multiple template directories. The lint walks all templates it finds.

**Checks performed (per template):**

| Check | Severity | Description |
|---|---|---|
| `copier.yml` exists | error | Required file. |
| `copier.yml` parses as YAML | error | Malformed YAML rejects the template. |
| `_exlab_type` present and one of `"project"`, `"equipment"`, `"run"` | error | Required for the wizard to know which capability surface this template serves. |
| `_exlab_version` present and non-empty string | error | Required as of v0.7 (§5.7). |
| Run templates declare `_exlab_run_scope` ∈ {`"experimental"`, `"test"`, `"both"`} | error | Required for run-template selection logic. Skipped for project / equipment templates. |
| `_min_copier_version` present and ≥ "9.0" | warn | Recommended. |
| `_answers_file` set to `.exlab-answers.yml` | warn | Convention; deviations are accepted but the cache documentation assumes this name. |
| `_tasks` is **absent** | warn | As of v0.7, `_tasks` are silently ignored ([[#5.5 Plugin Pass (Post-Render, Controller-Driven)|§5.5]]). Presence indicates a template ported from an older draft and likely indicates dead config. |
| Template references `unsafe=True` in any included script | warn | We invoke Copier with `unsafe=False`. |
| All template question types are valid Copier types | error | `str`, `int`, `float`, `bool`, `yaml`. |
| Question identifiers follow `^[a-z][a-z0-9_]*$` | warn | Convention; non-conforming names work but are inconsistent with the rest of the lab's templates. |
| README field declarations under `_exlab_readme.fields` don't redeclare core field IDs (`label`, `operator`, `objective`) | error | Core fields are backend-managed; redeclaring is a configuration error (§10.3). |
| Each `.jinja`-suffixed file parses as valid Jinja2 | error | Catches syntax errors before the template is used in a creation flow. |
| Templated folder names use only declared variables | warn | `{{undeclared_var}}` in a folder name fails at render time; lint catches it earlier. |

**Exit codes** and **output formats** are identical to the plugin lint (see §6.9).
