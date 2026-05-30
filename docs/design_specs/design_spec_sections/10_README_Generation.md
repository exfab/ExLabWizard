# 10. README Generation

Parent: [[ExLab-Wizard_Design_Spec]]

Each project and run creation generates a `README.md` in the created directory root. The file is designed to be **both machine-queryable and human-readable**: structured fields live in a YAML front matter block at the top of the file, and human prose sections follow below. Any YAML parser (`pyyaml`'s `yaml.safe_load` in Python, `js-yaml` in JavaScript, etc.) can extract all structured metadata without touching the prose body.

**Library choices for v1.** The Frontend detail-pane README preview (Frontend §3.6.3) and the read-only log viewer (Frontend §3.6.5) render Markdown via [`markdown-it-py`](https://markdown-it-py.readthedocs.io/) (CommonMark-compliant; the `front_matter` plugin extracts the YAML block separately from the prose). README front matter on the *write* path is emitted from a `msgspec.Struct` mirror of `readme_fields.json` (§11.4) using PyYAML for serialization (round-trip preservation isn't required when the writer is the app itself). Reading back uses PyYAML's `yaml.safe_load`. These commitments are pinned in `pyproject.toml`.

## 10.1 When It Runs

The README is always generated for project-scope and run-scope creations. The mandatory core fields (`label`, `operator`, `objective`; User Interaction Spec Section 2) guarantee that every README has recoverable context. The README is not optional for these scopes.

Templates may request additional README content via `readme.fields` in `copier.yml`, and `config.yaml` `readme.defaults` may extend the default field set. Template and config fields may individually be marked `required: true` to join the core set as gated fields, or left optional.

```yaml
# In a template's copier.yml
_exlab_readme:
  fields:
    - id: sample_type
      label: "Sample Type"
      type: choice
      options: ["Fixed tissue", "Live cell", "Suspension", "Other"]
      required: false
```

## 10.2 Field Sources (Merged Layers)

README fields come from four layers, merged in priority order (highest wins on label/default conflicts; no layer can disable a core field):

| Priority | Source | Defined by | Can be disabled? |
|---|---|---|---|
| 1 (highest) | Template-level fields | `copier.yml` `readme.fields` | Yes |
| 2 | Global default fields | `config.yaml` `readme.defaults` | Yes |
| 3 | **Mandatory core fields** | Backend (hard-coded: `label`, `operator`, `objective`) | **No** |
| 4 | Auto-filled system fields | App core (always injected; not user-editable) | No |

Templates extend or override labels and defaults for specific equipment or experiment types but cannot remove the mandatory core fields. Config-level defaults are the normal place to add lab-wide required fields (e.g. an IRB protocol number).

## 10.3 Field Types

| Field type | Semantic | Notes |
|---|---|---|
| `string` | Single-line text | Default type |
| `text` | Multi-line free-form text | For notes, protocols |
| `choice` | One-of a fixed set | Requires `options: [...]` in declaration |
| `date` | ISO 8601 date | Defaults to today if not set |
| `boolean` | True/false | Rendered as Yes/No in output |

Widget/presentation mapping for each field type is a frontend concern; see `ExLab-Wizard_Frontend_Spec.md`.

Field declaration example (template-level, extending the core set with template-specific fields):

```yaml
_exlab_readme:
  fields:
    # 'objective' is already a core field -- do not redeclare it here.
    # Template-specific extensions only below.
    - id: sample_type
      label: "Sample Type"
      type: choice
      options: ["Fixed tissue", "Live cell", "Suspension", "Other"]
      required: false
    - id: protocol_reference
      label: "Protocol Reference"
      type: string
      required: true    # this template requires a protocol reference
      hint: "DOI, internal SOP number, or lab-notebook page"
```

Redeclaring a core field ID (`label`, `operator`, `objective`) in a template is a configuration error. The creation controller refuses to render and returns the API error envelope (§4.6.3) with `code: "template_core_field_redeclared"`, `field` set to the offending core ID, and `details: { "template_name": "<name>", "template_version": "<version>" }`. The frontend renders this as a form-level inline error on the Confirm & Create step (Frontend §2.2.4), naming the offending field and template; Confirm & Create stays disabled until a different template is selected.

## 10.4 User-Added Custom Fields

During the README step, users may add ad-hoc fields not declared in the template.

- Custom fields are plain string key-value pairs (no type selection).
- Field order must be preserved in the generated output.
- Custom fields are session-specific and not persisted back to the template. If a custom field proves consistently useful, it should be added to the template manifest manually (intentional -- keeps templates curated).

## 10.5 Pre-fill Behavior

The form pre-fills from **template defaults only** (`default:` in field declaration). There is no carry-forward from previous runs. This prevents silently propagating stale metadata (e.g. a previous operator's name or a superseded objective) into new runs.

System auto-filled fields (Section 10.6) are the exception: they are always current and non-editable.

## 10.6 Auto-Filled System Fields

Always injected by the app core; non-editable. These appear in the YAML front matter under the `system:` block (Section 10.7).

| Field | YAML key | Value |
|---|---|---|
| Created timestamp | `created` | UTC ISO 8601 timestamp |
| Creating OS user | `created_by` | OS username (distinct from `operator`) |
| Equipment | `equipment` | Object with `id` and `label` |
| Template | `template` | Object with `name` and `version` |
| Project name | `project` | Machine-safe project identifier (project-level README) |
| Run directory | `run` | Run directory name with full timestamp (run-level README) |
| Run kind | `run_kind` | `experimental` or `test` (run-level README only) |
| Schema version | `readme_schema_version` | `"1.1"` -- bumped from 1.0 when YAML front matter replaced the mixed format |

## 10.7 Output Format

The `README.md` is a Markdown file with a **YAML front matter block** at the top followed by human prose sections. The YAML block is the complete machine-queryable surface: every structured field declared via the merged layers (core, template, config, system) appears there as typed YAML. The prose sections below are for narrative content that doesn't need to be queried programmatically, and they duplicate nothing -- a reader does not need to cross-reference the front matter and the prose to reconstruct values.

Front matter structure:

- Top-level `label`, `operator`, `objective` -- the mandatory core fields, surfaced at the top so they are the first thing a human and a parser encounter.
- `template_fields:` -- values for fields declared in the template's `copier.yml`.
- `config_fields:` -- values for fields declared in `config.yaml` `readme.defaults`.
- `custom_fields:` -- an ordered list of user-added ad-hoc fields.
- `system:` -- auto-filled, non-editable system fields (Section 10.6).

Example (run-level README):

```markdown
---
readme_schema_version: "1.1"

# Mandatory core fields (backend-enforced; User Interaction Spec Section 2)
label: "Cortex Q3 calibration sweep"
operator: "asmith"
objective: >
  Characterize layer-specific synaptic density in fixed mouse cortex sections
  across three developmental timepoints. This run is a calibration sweep at
  488 nm to validate laser power settings before the production acquisitions.

# Template-declared fields (confocal_run_v2)
template_fields:
  sample_type: "Fixed tissue"
  protocol_reference: "SOP-CONF-2025-14"

# Config-declared fields (lab policy)
config_fields:
  irb_protocol: "IRB-2026-0042"

# User-added ad-hoc fields
custom_fields:
  - label: "Collaborator"
    value: "Dr. J. Lee (Neurobiology)"
  - label: "Expected duration (hr)"
    value: "3.5"

# Auto-filled system fields
system:
  created: "2026-04-17T14:32:00Z"
  created_by: "asmith"
  equipment:
    id: "CONFOCAL_01"
    label: "Confocal Microscope 1"
  template:
    name: "confocal_run_v2"
    version: "2.1"
  project: "PROJ-0042"
  run: "Run_2026-04-17T14-32"
  run_kind: "experimental"
---

# Cortex Q3 calibration sweep

## Notes

*(none)*

---

*Generated by ExLab-Wizard. Do not edit the YAML front matter block.*
```

### 10.7.1 Machine Query Examples

The YAML front matter is consumable without a Markdown parser. Any tool that can read the top of a file until the second `---` line and feed the intermediate text to a YAML parser gets the full structured metadata.

```python
# Illustrative -- not implementation code
import yaml

def read_readme_frontmatter(path: str) -> dict:
    """Extract YAML front matter from a README.md file.

    Returns the parsed YAML block as a dict.
    Raises ValueError if the file lacks a well-formed front matter block.
    """
    with open(path, "r", encoding="utf-8") as f:
        first = f.readline()
        if first.strip() != "---":
            raise ValueError(f"{path}: missing YAML front matter")
        buf = []
        for line in f:
            if line.strip() == "---":
                return yaml.safe_load("".join(buf)) or {}
            buf.append(line)
    raise ValueError(f"{path}: unterminated YAML front matter")

meta = read_readme_frontmatter("/data/lab/CONFOCAL_01/PROJ-0042/Runs/Run_2026-04-17T14-32/README.md")
# meta["operator"]                 -> "asmith"
# meta["system"]["run_kind"]       -> "experimental"
# meta["template_fields"]["sample_type"] -> "Fixed tissue"
```

Common queries:

| Query | Implementation |
|---|---|
| "All runs operated by asmith" | `meta.get("operator") == "asmith"` |
| "All fixed-tissue runs" | `meta.get("template_fields", {}).get("sample_type") == "Fixed tissue"` |
| "Runs missing an IRB protocol" | `not meta.get("config_fields", {}).get("irb_protocol")` |
| "All test runs" | `meta.get("system", {}).get("run_kind") == "test"` |

### 10.7.2 Prose Body

Everything below the closing `---` is optional human prose. The first heading matches the `label` field (so the rendered Markdown has a sensible H1 for tools that display READMEs). Additional sections (`## Notes`, `## Protocol deviations`, etc.) are free-form -- readers and scripts should not depend on their presence or structure.

If a user edits the prose body, no structured field changes. If a user edits the front matter, standard YAML rules apply; the creation controller does not round-trip edits -- once the README is written, it is the user's document.

## 10.8 README Plugin Hook (Removed in v0.7)

Earlier drafts permitted a `transform_readme(markdown_str, variables) -> str` plugin hook that could rewrite the rendered README markdown. **This hook is removed in v0.7.** Plugins are scoped to run output files (data, metadata, vendor-template files); `README.md` is an ExLab-Wizard control-surface file and is not subject to plugin mutation. See [[06_Plugin_System#6.1.5 What plugins must not touch|§6.1.5]] for the full set of forbidden paths and the policy-violation enforcement.

Why this restriction: the README is fully determined by the merged field set in `readme_fields.json` plus the template's `README.md.jinja`. Allowing plugins to rewrite it after the fact created a divergence vector — `readme_fields.json` would no longer accurately describe the README on disk, breaking the regeneration use case in [[11_Cache_Folders#11.7 Discovery and Validation Use Cases|§11.7]]. With the hook removed, `readme_fields.json` is unconditionally authoritative.

Field values that previously motivated a README plugin (e.g. populating `objective` from a database lookup) belong instead in the four-layer field merge (§10.2): templates and config can declare fields with `default:` values driven by simple expressions, and the operator can edit them in the README form before submission. If the use case truly requires a runtime lookup, that capability would arrive as a future "field-source plugin" hooked into the merge layer rather than as a post-render mutation; this is out of scope for v1.
