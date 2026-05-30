# Template Authoring GUI + Per-Instance Template Folders — Design

Date: 2026-05-30
Status: Approved (brainstorming); pending implementation plan
Related specs: [[05_Template_Format]] (§5), [[11_Cache_Folders]] (§11), [[04_Backend_Architecture]] (§4.7)

---

## 1. Problem & Goals

Today the GUI can *consume* templates (the project/run wizards render each
template's `copier.yml` questions as bound widgets via
`render_question_field`), but it cannot *author* them beyond a bare scaffold:
`ui/pages/templates.py::create_template` writes a `copier.yml` with the
`_exlab_*` metadata keys plus one fixed `notes.md.jinja`. There is no way
through the GUI to:

- edit a template's content files,
- define Copier questions (they must be hand-edited in `copier.yml` on disk),
- upload files into a template directory (`ui.upload` is used nowhere; no
  multipart endpoint exists).

This design adds GUI template authoring and makes the system more robust, and
it generalizes the template-location model so **each created project and run
gets its own template folder**.

### Goals

1. **Edit content files** (text/`.jinja`) inline through the GUI.
2. **Put questions into `copier.yml`** through a structured form (no raw YAML).
3. **Upload files** into a template directory (verbatim by default, opt-in
   `.jinja`).
4. **Per-instance template folders**: every equipment / project / run instance
   may hold a typed template store, used both as a *source for children* and as
   a *provenance copy* of the template that produced the instance.
5. **Robustness**: validate-on-save/lint, atomic & safe writes, path-traversal
   / upload safety, and don't-break-templates-in-use guards.

### Non-goals

- A raw-YAML editor (structured form only; can be added later).
- A new hierarchy level ("experiment" is **not** a new level — the hierarchy
  remains equipment → project → run, runs having a test-run variant).
- A REST-first template API (architecture A: in-process service + NiceGUI).
- Re-enabling Copier `_tasks` / `unsafe=True` (stays disabled; lint WARNs).

### Terminology note

The original request said "each experiment, project, run." This codebase has
no `experiment` level: `TemplateType` is `{project, equipment, run}` and
`CreationLevel` is `{project, run}`. "Per-instance template folders" therefore
means: per **equipment**, per **project**, and per **run** instance directories.

---

## 2. Folder Model & Resolution

### 2.1 Typed-subfolder layout

Every instance directory may contain a template store under its cache dir
(`CACHE_DIR_NAME = ".exlab-wizard"`), partitioned by template *type* so a
folder's own provenance copy never collides with the templates it offers its
children:

```
templates_dir/                          # global (unchanged) — config.paths.templates_dir
  <tmpl>/copier.yml ...

<local_root>/<equipment_id>/.exlab-wizard/templates/
  equipment/<tmpl>/     # provenance: template that onboarded this equipment (future use)
  project/<tmpl>/       # child-source: project templates for this instrument
  run/<tmpl>/           # child-source: run templates for this instrument

<equipment_id>/<project>/.exlab-wizard/templates/
  project/<tmpl>/       # provenance: frozen copy of the template that made THIS project
  run/<tmpl>/           # child-source: run templates scoped to this project

<equipment_id>/<project>/Runs/<run>/.exlab-wizard/templates/
  run/<tmpl>/           # provenance: frozen copy of the template that made THIS run
```

The subfolder name matching the instance's **own** type holds the provenance
copy; subfolders matching **child** types hold child-source templates. A run is
a leaf, so its `run/` subfolder normally holds only its own provenance copy.

This generalizes spec §5.0's existing two-location model (global +
per-equipment) downward to per-project. It does not remove the global or
per-equipment locations — it extends the same idea.

### 2.2 Resolution chain (nearest scope wins)

A new module `template/resolution.py` computes the ordered search path per
wizard and merges by template name (nearest scope wins on collision):

| Wizard | Search order (highest precedence → lowest) |
|---|---|
| New Project | `<equipment>/.exlab-wizard/templates/project/` → `templates_dir/` (filter `_exlab_type: project`) |
| New Run / Test Run | `<project>/.exlab-wizard/templates/run/` → `<equipment>/.exlab-wizard/templates/run/` → `templates_dir/` (filter `run`, narrow by `_exlab_run_scope`) |
| New Equipment | `templates_dir/` only (filter `equipment`) |

`list_templates()` (existing, pure) remains the per-directory primitive; the
resolver calls it across the chain and de-dupes by name, keeping the first
(nearest) occurrence.

### 2.3 Provenance copy

After `CACHE_WRITE`, the controller copies the resolved template source into
`<dst>/.exlab-wizard/templates/<own_type>/<tmpl_name>/` (verbatim, including
`copier.yml` and `.jinja` files), and records the relative path in
`creation.json` as `template.provenance_path`. Every run/project is then
reproducible from its own folder even if the source template later changes —
this is what satisfies "each project/run gets its own folder to hold
templates."

Trade-off (accepted): a provenance copy adds a few KB–MB per creation and syncs
to NAS with the cache. The folder model was chosen with both "source for
children" and "provenance copy" roles in mind.

---

## 3. Component Architecture (Approach A)

A pure, testable service layer plus a thin NiceGUI view.

### 3.1 New modules

| Module | Purpose | Depends on |
|---|---|---|
| `template/authoring.py` | Write-side service (no NiceGUI): `create_template_dir`, `read_manifest`, `write_manifest`, `read_content`, `write_content_file`, `upload_file`, `rename_path`, `delete_path`. Every write via `atomic_write_bytes`. All new paths go through `_safe_target`. | `io/atomic_write`, `paths`, `template/lint`, `template/manifest` |
| `template/lint.py` | Factored-out validation: `lint_template(dir) -> list[Finding]`, `lint_manifest_dict(manifest, path) -> list[Finding]`. Extracts the §5.8 checks currently embedded in `TemplateEngine.resolve` and **adds** Jinja2 `.jinja` parse checks. One rule source for both resolve-time and author-time. | `template/copier_driver` types, `jinja2`, `validator/findings.Finding`, `constants` |
| `template/manifest.py` | Typed `TemplateManifest` model (questions + `_exlab_*`) with `from_yaml` / `to_yaml`. Round-trips `copier.yml` so the structured form never hand-writes YAML. Reuses `TemplateQuestion`. | `pyyaml`, `constants` |
| `template/resolution.py` | Read-side resolver (Section 2.2): build the per-wizard search chain, merge by name (nearest wins). | `ui/pages/templates.list_templates`, `paths`, `config` |
| `template/provenance.py` | `copy_template_into_instance(resolved, dst, own_type) -> Path` — the post-`CACHE_WRITE` provenance copy. | `paths`, `io`, `shutil` |
| `ui/pages/template_editor.py` | NiceGUI view: file tree, inline editor (`ui.codemirror`), structured question form, `ui.upload`, save/validate. Pure-view split (logic in services). | `template/authoring`, `template/lint`, `template/manifest` |

### 3.2 Changed units

- **`ui/pages/templates.py`** — manager page gains an "Edit" affordance per row
  routing to the editor; `create_template` delegates to
  `authoring.create_template_dir` (a thin shim keeps existing unit tests green).
  Adds a **scope/location selector** (Global | Equipment `<id>` | Project
  `<path>`) so authoring can target a per-instance folder.
- **`controller/creation.py`** — (a) resolved-template lookup switches from
  "templates_dir only" to `template/resolution`; (b) one new pipeline step after
  `_write_cache` calls `provenance.copy_template_into_instance(...)`; (c)
  POST_VALIDATE walk excludes `.exlab-wizard/templates/**`; (d) `creation.json`
  `template` block gains `provenance_path`.
- **`ui/mount.py`** — new `@ui.page("/templates/edit")` route;
  `_template_questions_map` / `_template_names` switch to the resolver so
  wizards see per-instance templates.

### 3.3 Rationale

The service is 100% unit-testable without a browser (matching how
`list_templates` / `create_template` are already pure); `lint.py` removes the
duplication risk between resolve-time and author-time validation; the GUI stays
a thin shell. Each module is small and single-purpose.

---

## 4. Data Flow

### 4.1 Flow A — Authoring a template in the GUI

```
Operator opens /templates
  → picks scope/location (Global | Equipment <id> | Project <path>)
  → resolution lists templates in that location (list_templates per dir)
  → "New" or "Edit" → /templates/edit?dir=<abs path under chosen location>

In ui/pages/template_editor.py:
  1. Load: authoring.read_manifest(dir) → TemplateManifest → render question form
           + build file tree (walk dir, classify text vs binary by extension)
  2. Edit questions: add / remove / reorder rows in the structured form (in-memory)
  3. Edit content: select a text/.jinja file → ui.codemirror → edits held in memory
  4. Upload: ui.upload → bytes →
           sanitize filename (authoring._safe_target) ; reject ../ etc.
           if "render as template" toggled → append .jinja
           confine target inside dir
  5. Save (per action or one Save):
           manifest.to_yaml() OR content bytes
           lint.lint_manifest_dict(...) / Jinja2 parse for .jinja
           ERROR-tier finding → refuse, show inline, disk untouched
           else authoring.write_* (atomic: temp + fsync + rename)
  6. Post-save: lint.lint_template(dir) → WARN/ERROR banner summary
```

Binary files: upload / replace / delete only (no codemirror).

### 4.2 Flow B — Creation consuming per-instance templates

```
Wizard Variables step
  → _template_questions_map(deps, type) calls template/resolution
    (project? → equipment → global), merged nearest-wins
  → operator answers questions (unchanged render_question_field)

POST /sessions → CreationController pipeline:
  VALIDATING → resolve template VIA RESOLVER (was: templates_dir only)
  RENDERING  → copier.run_copy (unchanged)
  PLUGIN_PASS → (unchanged)
  CACHE_WRITE → README + creation.json (unchanged)
  PROVENANCE  → NEW: provenance.copy_template_into_instance(resolved, dst, own_type)
                writes <dst>/.exlab-wizard/templates/<own_type>/<name>/...
                stamps creation.json.template.provenance_path
  POST_VALIDATE → walks dst; MUST skip .exlab-wizard/templates/** so the
                  provenance copy's copier.yml/.jinja aren't validated as output
  SYNC_QUEUED → enqueue dst (provenance copy syncs as part of the cache)
```

### 4.3 Correctness points

1. **POST_VALIDATE exclusion.** The provenance copy contains raw `.jinja` and
   placeholder-style template files; the existing post-validate rule set would
   flag those as malformed output. The walk must add
   `.exlab-wizard/templates/` to its ignore set.
2. **Resolution agreement.** The wizard lists templates by resolver and passes
   the **absolute resolved `template_path`** (the session body already carries
   `template_path`) into the pipeline, so the pipeline renders exactly what the
   operator saw. The chain is re-walked only for validation, never to re-pick.

---

## 5. Error Handling, Validation & Safety

### 5.1 Validate-on-save / lint (`template/lint.py`)

Returns `list[Finding]` (reusing `validator/findings.Finding`, tier =
`ERROR` | `WARN`):

| Check | Tier | On save |
|---|---|---|
| `copier.yml` parses as YAML | ERROR | refuse |
| `_exlab_type` ∈ {project, equipment, run} | ERROR | refuse |
| `_exlab_version` present, non-empty | ERROR | refuse |
| run template has valid `_exlab_run_scope` | ERROR | refuse |
| `_exlab_readme.fields` doesn't redeclare core ids (label/operator/objective) | ERROR | refuse |
| each `.jinja` file parses (`jinja2.Environment.parse`) | ERROR | refuse |
| question types valid; ids match `^[a-z][a-z0-9_]*$` | WARN | save + banner |
| `_min_copier_version` ≥ "9.0"; `_answers_file` convention; `_tasks` absent | WARN | save + banner |

**Save gate:** any ERROR → reject write, show findings inline, disk untouched.
WARN → proceed, banner summarizes. `TemplateEngine.resolve` is refactored to
call `lint_manifest_dict` and raise its existing
`TemplateLoadError` / `TemplateCoreFieldRedeclaredError` on the same ERROR set —
one rule source, two callers.

### 5.2 Atomic & safe writes

Every content/manifest write goes through `atomic_write_bytes` (temp + fsync +
atomic rename); no partial files. **Stale-edit guard:** the editor records each
opened file's mtime+size; on save, if the on-disk stat changed underneath,
prompt (overwrite / reload / cancel) rather than clobbering an external or
synced change.

### 5.3 Path-traversal / upload safety

A single `authoring._safe_target(template_dir, user_segment)` chokepoint for
*every* new path (uploads, new files, renames, new template dirs):

- split into segments; each validated by reusing `paths.project_name_violations`
  (already rejects `/`, `\`, `..`-class, control/non-ASCII, Windows-reserved
  names, trailing dot);
- `(template_dir / rel).resolve()` must be inside `template_dir.resolve()` —
  else reject;
- uploads size-capped (config `limits`, default 25 MiB) and count-capped per
  template;
- Copier stays `unsafe=False` / `skip_tasks=True` (unchanged) so authored
  `_tasks` remain inert; lint WARNs on their presence.

### 5.4 Don't break templates in use

- **Provenance copy** is the backstop: existing runs/projects reproduce from
  their own frozen copy regardless of later source edits.
- Editing a template that has produced creations and changing a **question key**
  or **`_exlab_type`** → "incompatible change" confirm dialog that *requires* a
  `_exlab_version` bump (pre-fills a suggested bump).
- **Deleting** a template that has descendants using it → warn with the
  dependent count before allowing it.

### 5.5 Concurrency

Authoring writes target template dirs; the orchestrator quiescence poller and
sync watch instance trees. Template stores live under
`.exlab-wizard/templates/` and are written atomically, so an in-flight sync
sees either the old or new file, never a torn one. No new locking required.

---

## 6. Testing Strategy

Pure services get exhaustive unit tests (no browser); the NiceGUI view gets the
light payload-dict treatment used by `tests/unit/ui/test_templates_page.py`.
Verification gate per project convention:
`uv run --extra test pytest` and `uvx ruff check`.

- **`test_lint.py`** — one test per §5.1 row (valid passes; each ERROR/WARN
  fires the right `Finding`); parametrized WARN cases. **Regression:**
  `TemplateEngine.resolve` still raises
  `TemplateLoadError` / `TemplateCoreFieldRedeclaredError` after the factor-out
  (existing resolve tests stay green).
- **`test_authoring.py`** — atomic round-trip; mid-write failure leaves the
  original intact (no temp residue); `_safe_target` attack table
  (`../escape`, abs paths, `foo/../../bar`, `CON`, trailing dot, control,
  non-ASCII) all rejected, legit nested names accepted; upload verbatim vs
  `.jinja` toggle; size/count caps; stale-edit conflict path.
- **`test_manifest.py`** — `from_yaml(to_yaml(m)) == m` for
  str/int/float/bool/choice/secret; choice dict- and list-forms; `_`-prefixed
  keys preserved.
- **`test_resolution.py`** — project chain (equipment→global) and run chain
  (project→equipment→global) ordering; name-collision nearest-wins;
  `_exlab_run_scope` narrowing; missing locations skipped gracefully.
- **`test_provenance.py`** — copy lands under
  `<dst>/.exlab-wizard/templates/<own_type>/<name>/`, includes `copier.yml` +
  `.jinja` verbatim; stamps `creation.json.template.provenance_path`.
- **`tests/integration/test_creation_provenance.py`** — end-to-end create_run:
  provenance copy exists; POST_VALIDATE does **not** flag copied
  `.jinja`/placeholder files; rendered output unaffected; resolver prefers a
  project-scoped run template over a global one of the same name.
- **`test_template_editor.py`** — headless payload-dict assertions: tree lists
  expected files; question form reflects manifest; save-with-ERROR surfaces
  findings and calls no writer; save-clean calls the authoring service once.

---

## 7. Open Items / Future Work

- Raw-YAML advanced editor tab (deferred; structured form only for v1).
- Equipment-template rendering through Copier (today equipment onboarding writes
  `EquipmentConfig` only; `_exlab_type: equipment` provenance subfolder is
  reserved but the New-Equipment wizard does not yet render a template).
- Optional config flag to make the provenance copy opt-in if cache/NAS size
  becomes a concern.
