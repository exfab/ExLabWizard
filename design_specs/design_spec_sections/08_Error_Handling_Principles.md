# 8. Error Handling Principles

Parent: [[ExLab-Wizard_Design_Spec]]

- **Creation atomicity:** If directory creation fails mid-tree, partially created directories are cleaned up before the error is returned to the client.
- **Cache write failures:** If writing the `.exlab-wizard` cache fails after the directory is successfully created, this is treated as a non-fatal warning. The failure is written to the equipment-level `wizard.<hostname>.log` if accessible, otherwise to the app's central log only.
- **Plugin failures:** Non-fatal by default; the created directory is still usable.
- **Sync failures:** Retried by the background NASSync daemon; the app does not retry inline.
- **Validation:** All inputs (variable values, paths, dates, character set) are validated before any filesystem operations begin. Mode-invariant checks (User Interaction Spec Section 4) are enforced here. The full rule set is enumerated in §8.1.

## 8.1 Path Validation Rules

The validator engine implements the rules below. The same engine runs in two modes ([[11_Cache_Folders#11.8 Validator Engine and Problem Query Contract|§11.8]]): **creation-time mode** (input: a proposed destination path and a resolved variable map; output: pass/fail with named offending segment, blocking) and **audit mode** (input: a directory subtree; output: a list of findings, non-blocking, used to populate the Problems tab).

### 8.1.1 Unresolved-placeholder rule (hard tier)

No path segment, file name, or rendered text-file content under a run, project, or equipment directory may match either of the following patterns:

- **Angle-bracket token regex:** `<[A-Za-z_][A-Za-z0-9_]*>` -- matches `<name>`, `<date>`, `<project>`, `<equipment>`, `<run_date>`, etc. The character class is restricted to identifier-like tokens to avoid false positives on legitimate angle-bracketed content (e.g. SMB paths beginning with `\\`, HTML examples in README prose, or chemistry notation such as `<2 mM>`). Identifiers must start with a letter or underscore and contain only alphanumerics and underscores between the brackets.
- **Jinja delimiter regex:** `\{\{[^}]*\}\}` and `\{%[^%]*%\}` -- matches any leftover Copier/Jinja2 marker, indicating the Jinja2 renderer was bypassed or the file was not processed.

The unresolved-placeholder rule applies to:

1. Every segment of the destination path (equipment, project, run-folder leaf).
2. Every file or directory name created under the destination.
3. The text content of rendered files, subject to the configurable size and extension limits in `config.yaml` `validator.content_scan_max_mib` (default **5 MiB**) and `validator.content_scan_extensions` (default list of common text extensions; see [[09_Configuration_File|§9]]). Scope rules within the eligible set:
   - **Markdown files (`.md`, `.md.jinja` rendered into `.md`):** scan only the **YAML front matter block** (the content between the first two `---` lines at the file head). The prose body is exempt from scanning, because legitimate prose can contain identifier-shaped angle-bracket text (e.g. *"see `<your_protocol>` reference"*) that should not gate sync. The front matter is the structured machine-queryable surface; placeholder leakage there is a real bug. The prose is human-readable, and a literal `<your_protocol>` the operator forgot to fill in is visible at READ time and not a sync-blocking concern.
   - **Other text files** (configs, CSVs, scripts, plain text, etc., from a `.jinja` source): scan the entire file content.
   - **Files larger than `validator.content_scan_max_mib`** or **with extensions outside `validator.content_scan_extensions`**: skipped from content scanning. Filenames and directory segments are still validated regardless of size or extension.
   - **Files copied verbatim** (no `.jinja` suffix): exempt from content scanning regardless of extension. The rule scans only files that Copier rendered.
   - **Binary detection** (for files that DO have a `.jinja` suffix and an extension in the allowlist): the validator reads the first 8 KiB of the rendered file; if any of those bytes is `0x00` (NUL), the file is treated as binary and skipped from content scanning. This is a deterministic byte-level rule, not a heuristic — it does not depend on file libraries, MIME-type sniffing, or the operating system. The 8 KiB cap bounds I/O on extremely large files. A file of fewer than 8 KiB is fully checked; the same NUL-byte rule applies.

The rule's per-finding output names: the offending **segment-or-file path**, the **matched token text**, the **rule** (`unresolved_placeholder_token` or `leftover_jinja_marker`), and (for content findings in Markdown front matter) the YAML key path of the offending value. The Problems tab and creation-time error UI both consume this structured output.

**Markdown front-matter parsing.** The validator extracts the front matter block by reading from the file head until the second line equal to `---`. If no such block is found (no `---` at line 1), the entire file is treated as prose and exempt from content scanning. If the block is unterminated (a `---` at line 1 but no closing `---`), the file is treated as malformed and surfaces a separate `malformed_yaml_front_matter` soft-tier finding (rather than scanning the whole file).

**Why both regexes.** The angle-bracket form catches a frequent template-authoring mistake where the author wrote `<project>` as documentation-style placeholder text and forgot to convert it to `{{ project }}`. The Jinja-delimiter form catches the opposite mistake where the renderer was bypassed and the literal `{{ project }}` survived. Both produce equally unrecoverable directory names and both must be flagged.

### 8.1.2 Illegal-filesystem-character rule (hard tier)

The illegal-character set is the union of:

- POSIX-illegal: NUL byte, `/` (when occurring inside a single segment, not as a separator).
- Windows-illegal: `< > : " / \ | ? *`, ASCII 0-31, and the trailing-dot/space rule.
- Reserved Windows names: `CON`, `PRN`, `AUX`, `NUL`, `COM1..COM9`, `LPT1..LPT9` (case-insensitive, with or without extension).

A path passes this rule when no segment contains any character in the union set and no segment matches a reserved name. The rule applies to all three creation segments (equipment, project, run-folder leaf) and to every file or directory name under the destination. Per-finding output names: the offending segment, the offending character or reserved name, and the rule (`illegal_filesystem_character` or `reserved_filesystem_name`).

The angle-bracket characters `<` and `>` are forbidden by this rule on Windows targets. On POSIX targets `<` and `>` are technically legal in filenames, but the unresolved-placeholder rule (§8.1.1) catches the structured `<identifier>` form on every platform; isolated `<` or `>` in non-token positions remains permitted on POSIX so that legitimate names like `2020-2021_<analysis>` are not falsely flagged on POSIX while still being caught when they contain an identifier token.

### 8.1.3 Mode-prefix mismatch rule (hard tier)

A run-level directory's leaf-folder name must agree with its `creation.json` `run_kind`:

- `creation.json` `run_kind: "experimental"` requires leaf prefix `Run_` and parent folder *not* equal to `TestRuns/`.
- `creation.json` `run_kind: "test"` requires leaf prefix `TestRun_` and parent folder equal to `TestRuns/`.

The two redundant signals from [[03_Directory_Structure_Convention|§3]] (parent folder, leaf prefix) must agree with each other and with `run_kind`. Disagreement on any of the three is a hard-tier finding. Per-finding output names: which signal disagrees and the resolved value of each.

### 8.1.4 Orphan rule (soft tier)

**v0.7 scope change.** The orphan rule applies at **project and run levels only**. Equipment-level directories use `equipment.json` (a registry record) rather than `creation.json` (a creation provenance record); see [[11_Cache_Folders#11.1 Folder Placement|§11.1]] and [[11_Cache_Folders#11.2 File Inventory per Level|§11.2]]. An equipment directory on disk that lacks `creation.json` is therefore not an orphan — the file isn't expected at that level. (An equipment directory on disk that exists but is not registered in `config.yaml` `equipment` is a *different* problem class; v0.7 surfaces it as a Settings warning rather than a Problems-tab finding.)

A **project or run** directory with no `.exlab-wizard/creation.json` is an orphan. The rule applies only in audit mode ([[11_Cache_Folders#11.8 Validator Engine and Problem Query Contract|§11.8]]); at creation time the controller writes `creation.json` synchronously with the directory tree, so orphans cannot be created by the app. Orphans typically arise from manual filesystem operations outside the app or from a partial cache-write failure (§8 bullet "Cache write failures"). Per-finding output: the orphan's path and detected level (`run` / `project`).

### 8.1.5 Missing-required-field rule (soft tier)

A run's `readme_fields.json` is missing a field that is now required by the current `config.yaml` `readme.defaults` or by the run's recorded template (referenced by name and version in `creation.json` `template`). This typically arises after a config policy change. Per-finding output: the run's path, the missing field ID, and the source layer that requires it.

### 8.1.6 Tier mapping

| Rule | Tier | Gates sync? |
|---|---|---|
| 8.1.1 Unresolved-placeholder | Hard | Yes |
| 8.1.2 Illegal filesystem character | Hard | Yes |
| 8.1.3 Mode-prefix mismatch | Hard | Yes |
| 8.1.4 Orphan | Soft | No |
| 8.1.5 Missing required field | Soft | No |

Hard-tier findings on a run set its `sync_status` to `"blocked_by_validation"` per the Pre-Sync Gate ([[07_Sync_and_Database_Integration#7.3 Pre-Sync Gate|§7.3]]). Soft-tier findings are surfaced in the Problems query but do not change `sync_status`.
