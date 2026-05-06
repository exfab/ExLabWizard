# 6. Plugin System

Parent: [[ExLab-Wizard_Design_Spec]]

---

Plugins transform files within a newly created directory. They are the extensibility seam: the core app does not know about spreadsheet formats, document templates, or lab-specific naming rules. The plugin host is invoked by the creation controller as the `PLUGIN_PASS` state in the session state machine ([[04_Backend_Architecture#4.7 Creation-Session State Machine|§4.7]]), immediately after Copier rendering completes; it has no direct coupling to the GUI, the LIMS client, or NAS sync beyond what the controller orchestrates. This section is the authoritative source for the plugin contract, the host/worker isolation model, the registry, the lifecycle, and the base templates new plugins must follow.

## 6.0 Where Plugins Sit in the Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│  exlab-wizard server process (FastAPI app, single OS process)   │
│                                                                 │
│  ┌──────────────────────────┐   ┌──────────────────────────┐    │
│  │  Creation Controller     │   │  Plugin Registry         │    │
│  │  (session state machine) │   │  (built at app startup;  │    │
│  │                          │   │   manifest scan only)    │    │
│  └────────┬─────────────────┘   └────────────┬─────────────┘    │
│           │                                  │                  │
│           │  await TemplateEngine.render()   │                  │
│           ▼                                  │                  │
│  ┌──────────────────────────┐                │                  │
│  │  Copier (in-process)     │                │                  │
│  │  Jinja2, file copy,      │                │                  │
│  │  unsafe=False, no _tasks │                │                  │
│  └────────┬─────────────────┘                │                  │
│           │ render returns                   │                  │
│           ▼                                  │                  │
│  ┌──────────────────────────────────────────┴────────────────┐  │
│  │  Plugin Host                                              │  │
│  │  - resolves candidates against rendered tree              │  │
│  │  - validates variables (host-side)                        │  │
│  │  - spawns one worker subprocess per plugin                │  │
│  │  - marshals JSON IPC, applies isolation limits            │  │
│  │  - on PluginInputRequired: emit ws event, await resume    │  │
│  └─────────────────────────┬─────────────────────────────────┘  │
└───────────────────────────-┼────────────────────────────────────┘
                             │ asyncio.create_subprocess_exec
                             ▼
                  ┌────────────────────────┐
                  │  Plugin Worker         │  one per plugin per session
                  │  (isolated Python)     │  IPC: stdin/stdout JSON envelope
                  │  - imports plugin pkg  │  resource limits via setrlimit
                  │  - runs lifecycle      │
                  │  - reads/writes dst    │
                  └────────────────────────┘
```

The **host** is the long-lived FastAPI app process. It owns side effects on app state (`creation.json` updates routed through `CacheWriter`, the wizard log, the session store, the WebSocket back to the browser). The **worker** is a short-lived subprocess scoped to a single creation session for a single plugin; it owns side effects on the rendered destination tree (file mutation). The two communicate over a JSON envelope on the worker's stdin/stdout. There is no intermediate `run_plugins.py` subprocess — this is the v0.7 simplification (Solution A) that makes `PluginInputRequired` suspend a normal `await` rather than a multi-hop IPC chain.

## 6.1 Plugin Contract

A plugin is a **Python package directory** under the configured plugin root. Each plugin defines a single class that subclasses `exlab_wizard.plugins.Plugin`. The class is the unit of registration, lifecycle, and isolation: one class instance is constructed per creation session, inside the worker subprocess.

### 6.1.1 Package layout

```
<plugin_dir>/
  xlsx_field_filler/
    __init__.py         # must export `Plugin` symbol pointing at the plugin class
    manifest.yml        # static metadata read by the host without importing Python
    plugin.py           # the Plugin subclass
    requirements.txt    # optional; documented dependencies, not auto-installed
    README.md           # plugin documentation (not consumed by the app)
```

The host scans the plugin root and treats each direct child directory as one plugin. It reads `manifest.yml` to populate the registry without importing any plugin code; the Python module is only imported when a worker subprocess is spawned for that plugin (see §6.3).

### 6.1.2 `manifest.yml` schema

```yaml
# Required identity fields
name: "xlsx_field_filler"
version: "0.3.1"
author: "ExFAB"
description: "Writes resolved variable values into named cells of metadata.xlsx workbooks."

# Required dispatch fields
supported_extensions: [".xlsx"]   # file-extension or "readme" pseudo-ext or glob list
api_version: "1"                  # plugin-API major version this plugin targets

# Optional declaration block (resolves Open Question #7)
required_variables:               # variables the host must guarantee in the variable map
  - project_name
  - operator
  - run_date
optional_variables:
  - sample_type

# Optional execution policy (host-enforced; see §6.3.4)
isolation:
  timeout_seconds: 30             # default 30; max 300
  memory_mb: 512                  # default 512; max 2048
  network: false                  # default false; true requires explicit operator opt-in in config.yaml
```

`api_version` is the **plugin-API major version** the plugin targets. The host refuses to load any plugin whose `api_version` does not match the current host's supported set (currently `["1"]`). This is the single mechanism by which plugin-contract breaking changes are gated.

`required_variables` is the upfront declaration that resolves Open Question #7. The host validates the variable map against every loaded plugin's declared variables **before** Copier's render phase starts; missing variables surface as a structured pre-flight error rather than as a mid-pipeline `PluginInputRequired` exception. `PluginInputRequired` is retained for genuinely lab-specific inputs that are only knowable after partial transformation (see §6.4) but is no longer the primary mechanism for variable discovery.

### 6.1.3 The `Plugin` base class

```python
# exlab_wizard/plugins/__init__.py -- shipped with the app
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FileChange:
    """A single mutation a plugin would make. Used by describe_changes()."""
    path: Path                       # absolute path under the rendered dst
    kind: str                        # "modify" | "create" | "rename" | "delete"
    summary: str                     # one-line human-readable description
    detail: dict[str, Any] = field(default_factory=dict)


class PluginError(Exception):
    """Raised by plugins for any non-recoverable failure during transform.

    The host catches this, records it in creation.json plugins_applied[].status,
    appends to wizard.<hostname>.log, and continues with remaining plugins
    (default policy; see §6.3.5)."""


class PluginInputRequired(Exception):
    """Raised by transform() when the plugin discovers (after starting work) that
    it needs an additional input that was not declarable upfront. See §6.4."""

    def __init__(self, fields: list[dict[str, Any]], reason: str):
        self.fields = fields         # field definitions; same shape as README fields
        self.reason = reason         # short message surfaced to the user
        super().__init__(reason)


class Plugin(ABC):
    """Base class for all ExLab-Wizard plugins.

    Lifecycle (one instance per creation session, all in the worker subprocess):

        __init__()                           # cheap construction; no I/O
        validate_variables(variables)        # called once at registration
        pre_transform_all(ctx)               # called once before the file loop
        for file in matched_files:
            can_handle(file, variables)      # cheap predicate
            describe_changes(file, ctx)      # only in dry-run mode
            transform(file, ctx)             # the actual mutation
        post_transform_all(ctx)              # called once after the file loop
        on_plugin_failure(exc, ctx)          # called only if any hook raised
    """

    # --- Required class attributes (mirror manifest.yml; the host cross-checks) ---
    name: str
    version: str
    supported_extensions: list[str]
    api_version: str = "1"

    # --- Optional class attributes ---
    required_variables: list[str] = []
    optional_variables: list[str] = []

    # --- Required methods ---

    @abstractmethod
    def can_handle(self, file_path: Path, variables: dict[str, Any]) -> bool:
        """Secondary filter, called after the extension match in the dispatcher.

        Cheap and side-effect-free. Returning False means this file is skipped
        for this plugin only (other plugins still get a chance)."""

    @abstractmethod
    def transform(self, file_path: Path, ctx: "PluginContext") -> None:
        """Mutate file_path in place.

        On unrecoverable failure raise PluginError with a human-readable message.
        On a discovered need for additional input, raise PluginInputRequired.
        Return value is ignored."""

    # --- Optional lifecycle hooks (default to no-ops) ---

    def validate_variables(self, variables: dict[str, Any]) -> list[str]:
        """Called once per creation session, in a short-lived **validation worker
        subprocess** (NOT in the host process). The host spawns one worker per
        candidate plugin, sends the variable map over stdin, awaits the returned
        error list (or empty), and joins the worker. This preserves the §6.3
        crash-isolation guarantee — a plugin with a broken import (e.g. missing
        third-party dependency) raises in the worker, not the host. See §6.2.2
        for the call site and §6.3.6 for the validation-worker protocol.

        Return value semantics:
            - An EMPTY list means the variable map satisfies this plugin's
              expectations. The plugin will be dispatched to a worker.
            - A NON-EMPTY list is treated as a HARD failure for the whole
              creation session. The host concatenates all error strings from
              every candidate plugin's validate_variables into a single
              structured error event (phase: 'running_plugins',
              error.code: 'plugin_variable_validation_failed') and aborts
              the session BEFORE any worker is spawned. No plugins run; the
              partially-rendered destination tree is cleaned up.

        The default implementation checks `required_variables`; override only
        to add custom validation (e.g., date-format checks, equipment-id
        allowlist checks). When overriding, return error strings that are
        operator-actionable: name the variable, name the constraint, suggest
        a fix when feasible.
        """
        return [
            f"required variable '{v}' is missing or empty"
            for v in self.required_variables
            if not variables.get(v)
        ]

    def pre_transform_all(self, ctx: "PluginContext") -> None:
        """Called once before the file loop. Use for batch setup that should
        be paid once per session (open a workbook, open a DB connection, etc.).
        State stored on `self` is preserved through the loop because the
        worker holds one instance for the whole session."""

    def post_transform_all(self, ctx: "PluginContext") -> None:
        """Called once after the file loop, even if the loop was empty.
        Symmetric to pre_transform_all -- close handles, flush buffers, etc.
        Not called if pre_transform_all itself raised."""

    def describe_changes(self, file_path: Path, ctx: "PluginContext") -> list[FileChange]:
        """Dry-run: return the changes transform() would make to file_path,
        without writing anything to disk. Default returns a single 'modify'
        FileChange with no detail; plugins should override when the user-facing
        preview matters (e.g., 'would write 3 cells: B7=asmith, C7=2026-...')."""
        return [FileChange(path=file_path, kind="modify",
                           summary=f"{self.name} would modify {file_path.name}")]

    def on_plugin_failure(self, exc: Exception, ctx: "PluginContext") -> None:
        """Called if any of pre_transform_all / transform / post_transform_all
        raised. Use to roll back partial state (delete a half-written sidecar
        file, restore a backup, close a leaked handle). The exception that
        caused the failure is passed in; the plugin must NOT re-raise.
        Returning normally means cleanup succeeded; raising means the cleanup
        itself failed and will be logged separately."""

    # NOTE (v0.7): the optional `transform_readme` hook has been removed.
    # Plugins are restricted to mutating run output files (data, metadata,
    # vendor-template files, etc.) and may NOT modify README.md or any
    # ExLab-Wizard cache file (.exlab-wizard/*). README content is sealed
    # after ReadmeGenerator writes it; readme_fields.json is the authoritative
    # source for regeneration. See §10.8 and the "What plugins must not touch"
    # subsection below.
```

### 6.1.5 What plugins must not touch

Plugins write only to files **inside the rendered destination tree** that are not part of the ExLab-Wizard control surface. Specifically forbidden:

- `README.md` and any `README.md.jinja` source. The README is rendered by `ReadmeGenerator` after plugins finish (state `CACHE_WRITE` in [[04_Backend_Architecture#4.7 Creation-Session State Machine|§4.7]]) and is not subject to plugin mutation. README content is fully determined by the merged field set in `readme_fields.json` plus the template's `README.md.jinja`.
- The entire `.exlab-wizard/` subtree (`creation.json`, `readme_fields.json`, `wizard.<hostname>.log`, `ingest.json`, `equipment.json`, `test_runs.json`). These are app-managed and only `CacheWriter` may write them.
- The Copier answers file `.exlab-answers.yml`. This is Copier's record of inputs and is read-only after rendering.

The host enforces the README and `.exlab-wizard/` restrictions by checking each plugin's reported `files_affected` after a successful run; any path matching the forbidden set causes the plugin to be marked `status: "policy_violation"` (a new value in schema 1.6, alongside `success` / `failed` / `skipped` / `timeout`) and the worker's mutations are reverted from a snapshot taken before its `transform` loop began. Other plugins still proceed.

The `.exlab-answers.yml` restriction is enforced by Copier's `_skip_if_exists` and the worker's CWD; plugins that try to overwrite it produce a `PluginError` from the Copier layer.

### 6.1.4 The `PluginContext` object

`PluginContext` is the only argument (besides `file_path`) the host hands to plugins. It is a frozen dataclass; plugins read from it but do not mutate it. The host constructs one per creation session and passes a serialized copy across the IPC boundary on each call.

```python
@dataclass(frozen=True)
class PluginContext:
    variables: dict[str, Any]            # resolved variable map (read-only view)
    dst_root: Path                       # the rendered destination directory
    answers_file: Path                   # absolute path to .exlab-answers.yml
    template_name: str
    template_version: str
    run_kind: str                        # "experimental" | "test" | "" for project-level
    equipment_id: str
    project: str
    dry_run: bool                        # True when describe_changes is being driven
    log: "PluginLogger"                  # wraps stderr -> host -> wizard.<host>.log
```

`log` is a thin shim that forwards structured log records over stderr to the host, which timestamps them and appends to the appropriate `wizard.<hostname>.log`. Plugins must use `ctx.log` rather than `print()` or `logging.getLogger()`; output to stdout is reserved for the IPC envelope and will corrupt the protocol if a plugin writes to it directly.

## 6.2 Plugin Registry

### 6.2.1 Discovery

At app startup (and again whenever the operator triggers a manual reload from settings), the host scans **two plugin roots** and merges the results into a single registry:

1. **Bundled plugin root** — `_internal/plugins/` inside the PyInstaller bundle. Read-only. Ships with the app and contains the canonical scaffolds: `hello_plugin` (§6.5) and the worked example `xlsx_field_filler` (§6.6). On a development install (e.g. `uv tool install`), this resolves to the package's `plugins/` data dir; the same code path applies.
2. **Lab plugin root** — `paths.plugin_dir` from `config.yaml` ([[09_Configuration_File|§9]]). Operator-writable. Holds plugins the lab develops or installs.

Discovery procedure:

1. List direct child directories of each root (bundled first, then lab).
2. For each child, read `manifest.yml`. A directory without `manifest.yml`, or with a malformed one, is skipped and logged at `WARN`.
3. Validate `api_version` against the host's supported set. Mismatches are logged at `ERROR` and the plugin is excluded from the registry.
4. **Merge with lab-wins precedence.** If a plugin in the lab root has the same `name` as a bundled plugin, the lab plugin replaces the bundled one in the registry; a single `INFO`-level log records the override (e.g. *"plugin 'xlsx_field_filler' v0.4.0 from /home/lab/plugins overrides bundled v0.3.1"*). Operators who want a clean re-bootstrap can delete their copy and the bundled version becomes active again on next reload.
5. Index each surviving plugin by every entry in `supported_extensions`. A single plugin may register against multiple extensions; multiple plugins (with different names) may register against the same extension.

The registry is an in-memory mapping `extension -> list[PluginRecord]` where each record carries the manifest, the resolved package path, and the source root (`bundled` | `lab`). **No plugin Python code is imported at this stage.** The host's startup remains crash-isolated from plugin import errors.

The `bundled` source flag is exposed in the Settings dialog's plugin list so operators can see which plugins came with the app and which they installed locally. It does not affect runtime behavior.

### 6.2.2 Resolution per creation session

When the controller transitions the session into `PLUGIN_PASS` ([[04_Backend_Architecture#4.7 Creation-Session State Machine|§4.7]]), it calls `await PluginHost.run_pass(ctx, file_paths, plugin_order, on_input_required)`. The host then:

1. Reads `_exlab_plugins` from the template's resolved `copier.yml` (passed in via `plugin_order`; see §6.2.3) to get the explicit-order set.
2. Walks the list of rendered files (`file_paths`, supplied by the controller from the just-completed Copier render) and, for each file, resolves the candidate plugins by extension against the in-memory registry. Files matched by `_exlab_plugins` plugins run first in declared order; remaining matches run in plugin-name lexical order.
3. For each candidate plugin, spawns a short-lived **validation worker subprocess** (mode `validate`) and calls `validate_variables` inside it against `ctx.variables`. See §6.3.6 for the validation-worker protocol. The default implementation checks `required_variables` from the plugin's manifest; plugins may override (see §6.1.3). A non-empty error list returned by any candidate plugin — OR a plugin import failure inside its validation worker — is a hard failure: the host concatenates all error strings across all candidates into a single structured error event (`phase: "running_plugins"`, `error.code: "plugin_variable_validation_failed"`), aborts the session before any *transform* worker is spawned, and triggers the cleanup hook on the partially-rendered destination tree. No partial-success path exists at this stage — either every candidate plugin's validation passes, or no plugin runs. The validation workers are joined before the host moves on; their per-spawn cost (~50 ms cold start × N candidate plugins) is paid once per session.
4. For each plugin that survives validation, spawns one worker subprocess (§6.3) and drives its lifecycle for the files it matched. Worker exit-code handling and per-status logging follow §6.3.4.

The variable map is constructed once by the controller from the resolved input bundle and the rendered template's answers file, then passed in via `ctx.variables` for the duration of the pass. The host does not re-read `.exlab-answers.yml` per plugin.

### 6.2.3 Order control via `_exlab_plugins`

Templates declare plugin order in `copier.yml`:

```yaml
_exlab_plugins:
  - filename_renamer        # rename first so later plugins see final names
  - xlsx_field_filler
  - csv_header_initializer
```

Plugins not listed run after listed plugins, in lexical order.

README generation is **post-plugin and non-pluggable** in v0.7. The README is rendered by `ReadmeGenerator` during the `CACHE_WRITE` state ([[04_Backend_Architecture#4.7 Creation-Session State Machine|§4.7]]), strictly after all file-mutating plugins complete. Plugins cannot observe or modify the rendered README; the previous `transform_readme` hook is removed (see [[#6.1.5 What plugins must not touch|§6.1.5]] and [[10_README_Generation#10.8 README Plugin Hook (Removed in v0.7)|§10.8]]).

### 6.2.4 What gets recorded in `creation.json`

After the session, the host writes one entry per attempted plugin into `creation.json` `plugins_applied` ([[11_Cache_Folders#11.3 `creation.json` Schema|§11.3]]):

```json
{
  "plugin": "xlsx_field_filler",
  "version": "0.3.1",
  "files_affected": ["metadata.xlsx"],
  "status": "success",
  "isolation": {
    "duration_ms": 412,
    "exit_code": 0,
    "peak_memory_mb": 38
  }
}
```

`status` is one of `"success"`, `"failed"`, `"skipped"` (no files matched after `can_handle`), or `"timeout"` (worker exceeded `isolation.timeout_seconds`). The `isolation` block is added in schema version 1.3 of `creation.json`; readers expecting 1.2 ignore it.

## 6.3 Subprocess Isolation

Open Question #1 is resolved in favor of subprocess isolation. The cost (IPC serialization, ~50ms cold-start per plugin) is acceptable at the cadence of plugin invocations (single-digit count per creation session), and the benefit (a plugin crash, infinite loop, or memory blow-up cannot take down the app or corrupt the controller's state) is high given that plugins are written by lab staff who are not necessarily Python specialists.

### 6.3.1 Process model

One worker subprocess per plugin per creation session. The worker is a fork of the bundled `exlab_wizard.plugins._worker` entry point with the plugin's package directory prepended to `sys.path`. The worker:

1. Imports the plugin's `Plugin` class via `from <plugin_pkg> import Plugin`.
2. Reads a JSON envelope from stdin describing the session (`PluginContext` payload, list of files to process, dry_run flag).
3. Constructs the plugin instance and runs the lifecycle in §6.1.3.
4. Writes a JSON envelope to stdout summarizing results (per-file status, raised `FileChange` lists for dry-run, any `PluginInputRequired` payload).
5. Exits with code 0 on success, 1 on `PluginError`, 2 on `PluginInputRequired`, 3 on uncaught exception, 124 on host-side timeout (delivered as SIGTERM then SIGKILL).

The worker has the rendered destination directory as its CWD and inherits a sanitized environment: only `PATH`, `HOME`, `LANG`, and a small allowlist of `EXLAB_*` variables are passed through.

### 6.3.2 IPC envelope

Stdin (host → worker), one JSON object terminated by `\n`:

```json
{
  "context": { /* PluginContext fields, paths as strings */ },
  "files": ["metadata.xlsx", "subdir/calibration.xlsx"],
  "dry_run": false,
  "extra_inputs": null
}
```

Stdout (worker → host), one JSON object terminated by `\n`:

```json
{
  "result": "success",
  "per_file": [
    {"path": "metadata.xlsx", "status": "modified", "changes": null},
    {"path": "subdir/calibration.xlsx", "status": "modified", "changes": null}
  ],
  "log_records": [],
  "input_required": null
}
```

Stderr is a structured log channel: each line is a JSON object with `level`, `message`, and optional `context` keys. The host parses stderr line-by-line and merges into `wizard.<hostname>.log`.

### 6.3.3 Resource limits

The host enforces, per worker, the limits declared in `manifest.yml` `isolation`. On POSIX:

- `RLIMIT_AS` set from `isolation.memory_mb`.
- `RLIMIT_CPU` set from `isolation.timeout_seconds * 2` as a hard fallback.
- An asyncio wall-clock timer kills the process at `isolation.timeout_seconds`.
- `RLIMIT_NOFILE` set to 256 to prevent file-handle exhaustion.

On Windows the equivalents are job-object memory and CPU limits via the `pywin32` API. The wall-clock timer is portable.

**`isolation.network` is an installation-gate declaration, not a runtime block.** The host does not install firewall rules, seccomp filters, or any other technical mechanism to prevent the worker from making network calls. Real per-process network deny across our three target platforms (Linux, macOS, Windows) requires either administrator privileges (Windows WFP, macOS network extensions, Linux iptables) or platform-specific code paths that are non-trivial to maintain (Linux seccomp-bpf works for non-admin but Linux-only). Rather than ship a half-enforcement that misleads operators about the security model, v0.7 commits to **policy enforcement only**:

1. `isolation.network: false` (the default) is a **declaration** by the plugin author that the plugin does not need network access. The host treats this as informational metadata; it is shown in the Settings dialog's plugin list and surfaced in the lint output.
2. `isolation.network: true` is a **declaration** that the plugin needs network access. At plugin registration, the host checks `config.yaml` `plugins.allow_network`; if `false` (default), the plugin is **refused at the install/load gate** with a structured error (`code: "plugin_network_declined"`, `plugin: "<name>"`). The plugin is not added to the registry. If `allow_network: true`, the plugin is loaded normally and runs without network restriction.
3. The host does not attempt to verify whether a plugin's declaration matches its actual behavior. A plugin that declares `network: false` and then makes network calls anyway will succeed (subject to the OS user's own network permissions); the contract is that lab tooling owners review plugins before installing them in `paths.plugin_dir`.

This matches the security model already stated in §6.3.5: the trust boundary is filesystem permissions on `paths.plugin_dir`, not the subprocess. Every other facet of `isolation` (memory, CPU, wall-clock timeout, file handles) IS enforced by the host because POSIX `setrlimit` and Windows job-object equivalents work without admin privileges and are uniform across platforms.

The bundled `exlab-wizard plugins lint` command (§6.9) flags any plugin whose code statically appears to import `urllib`, `requests`, `httpx`, `socket`, or other common network libraries while declaring `isolation.network: false`. This is a hint, not enforcement; static analysis cannot catch indirect network access.

### 6.3.4 Failure handling

| Failure mode | Host behavior |
|---|---|
| Worker exits 0 | Record `status: "success"` and per-file results. |
| Worker exits 1 (`PluginError`) | Record `status: "failed"` with the worker's stderr message. Continue with remaining plugins. |
| Worker exits 2 (`PluginInputRequired`) | Suspend the pipeline, return the `extra_inputs` payload to the controller, which surfaces it to the client (§6.4). |
| Worker exits 3 (uncaught) | Record `status: "failed"` with `crash: true`. Continue. |
| Worker times out | SIGTERM, wait 1s, SIGKILL. Record `status: "timeout"`. Continue. |
| Worker stdout is unparseable JSON | Treat as exit-3 crash. The worker's stderr is preserved verbatim in the log for debugging. |
| Worker emits a JSON object missing a required IPC field (e.g. `kind`, `request_id`) | Treat as exit-3 crash. Log includes the malformed envelope (truncated at 4 KiB to avoid log bloat) for debugging. |
| Worker stdin closes mid-frame (host has data to send, worker has hung up) | Treat as worker-side abnormal exit; record `status: "failed"` with reason `worker_stdin_closed`. Continue with remaining plugins. |
| IPC frame exceeds the per-frame size cap (1 MiB by default; covers any single plugin event) | Drop the frame and record `status: "failed"` with reason `ipc_frame_oversize`. The cap protects the host from a runaway worker emitting unbounded data. |
| Worker emits valid JSON for an unrecognized `kind` value | Log a warning, ignore the frame, continue. Forward-compatible with future plugin-protocol additions. |

The default policy is **non-fatal continuation**: a single broken plugin does not abort the creation session, consistent with [[08_Error_Handling_Principles|§8]] "Plugin failures: Non-fatal by default". A template can opt into fatal behavior by setting `_exlab_plugins_fatal: true` in `copier.yml`, which causes the host to abort the post-copy task on the first non-success.

### 6.3.6 Validation-worker subprocess (mode = `validate`)

Distinct from the transform worker (§6.3.1) but reuses the same worker entry point (`python -m exlab_wizard.plugins._worker`). Invoked with `--mode validate` instead of the default `--mode transform`. Its job is to evaluate `Plugin.validate_variables` and return the result without doing any file mutation.

**Lifecycle:**

1. Host spawns the worker via `asyncio.create_subprocess_exec` (the args-list, no-shell variant — never `subprocess.run` with `shell=True`) with the plugin's package directory prepended to `sys.path`.
2. Worker reads a JSON envelope from stdin: `{ "context": { "variables": {...}, "template_name": "...", ... } }`. The full `PluginContext` is passed (no `dst_root` because the destination doesn't exist yet at this phase; the worker treats it as `None`).
3. Worker imports `Plugin` and calls `Plugin().validate_variables(variables)`.
4. Worker writes a JSON envelope to stdout: `{ "errors": [...] }` (empty list = pass) or `{ "errors": ["import_failed: <message>"] }` if `import` raised.
5. Worker returns 0 on success (regardless of whether `errors` is empty), 3 on uncaught exception (e.g. import error caught and re-formatted as an entry, OR an exception escaping `validate_variables` itself), 124 on timeout.

**Resource limits.** Validation workers use a fixed budget independent of the plugin's manifest:
- `RLIMIT_CPU`: 5 seconds (validation is supposed to be fast; this catches buggy regex catastrophic-backtracking validators).
- `RLIMIT_AS`: 256 MB (plugin imports may pull in numpy/pandas; 256 MB covers normal scientific stacks).
- Wall-clock timeout: 10 seconds. Past this, SIGTERM then SIGKILL.

These are deliberately not configurable from `manifest.yml` — the manifest's `isolation.timeout_seconds` and `isolation.memory_mb` apply only to the transform worker. Validation should not need a 5-minute timeout; if it does, the plugin author has misunderstood the contract.

**Why a subprocess instead of in-process.** Three reasons: (a) preserves the §6.3 crash-isolation guarantee for plugin import errors; (b) lets the host parallelize validation across plugins (one subprocess per candidate, joined together) for sessions with many candidate plugins; (c) keeps the Python module-import side effects (e.g. a plugin importing a vendor SDK that does network calls in its `__init__`) out of the host's process state.

### 6.3.5 Security model: what isolation does and does not solve

This is the authoritative statement on the plugin security model. Other sections referring to "isolation" defer to this one.

**Subprocess isolation enforces** (uniformly across Linux, macOS, Windows):

- **Memory cap** via `setrlimit(RLIMIT_AS, isolation.memory_mb)` on POSIX and Job Object memory limits on Windows. A plugin that allocates beyond its declared cap is killed by the OS.
- **CPU cap** via `setrlimit(RLIMIT_CPU, isolation.timeout_seconds * 2)` on POSIX and Job Object CPU time on Windows.
- **Wall-clock timeout** via the host's `asyncio` timer, portable across platforms. SIGTERM then SIGKILL.
- **File-handle exhaustion** via `RLIMIT_NOFILE = 256`.
- **Crash containment.** A plugin that segfaults, raises an uncaught exception, or hangs cannot take down the host or corrupt other plugins' state — its worker subprocess is killed and the host continues with the remaining plugins.

**Subprocess isolation does NOT enforce:**

- **Filesystem reach.** A plugin worker inherits the OS user's filesystem permissions. It can read or write anywhere the OS user can; the host does not chroot, bind-mount, or otherwise sandbox the filesystem. The §6.1.5 "what plugins must not touch" list (`README.md`, `.exlab-wizard/`, `.exlab-answers.yml`) is *contractual* and verified by post-run inspection of `files_affected`, not prevented at the OS level.
- **Network access.** v0.7 does not install firewall rules. `isolation.network` is a declaration, not a block. See §6.3.3.
- **Filesystem mutation outside the rendered destination.** Plugin authors are expected to confine their writes to files under `dst_root`; the host does not prevent writes elsewhere.
- **System call surface.** No seccomp filters, no syscall allowlist. A plugin can use any syscall the OS user is allowed to use.

**Trust model.** The plugin root (`paths.plugin_dir`) is a **trusted directory**. Only plugins that have been reviewed by the lab's tooling owner should be installed there. This matches how every other Python application uses third-party packages: trust is established by review, not by sandboxing. The bundled scaffolds (`_internal/plugins/`; §15.4) are reviewed by the ExLab-Wizard project; lab additions are reviewed by the lab.

**What this implies for operators:**

- Treat plugin installation with the same care as installing a Python package via pip from an untrusted source.
- The lint command (§6.9) catches obvious manifest issues but cannot verify behavioral correctness of plugin code.
- Plugins that need network access (e.g. a plugin that fetches a calibration value from an external service) MUST declare `isolation.network: true`, AND the lab MUST set `plugins.allow_network: true` in `config.yaml` to opt in. The opt-in is a deliberate per-deployment choice, not a runtime gate.

## 6.4 Plugin Input Escalation (`PluginInputRequired`)

Most plugin variable needs should be declared in `manifest.yml` `required_variables` and validated at registration (§6.1.2, §6.2.2). `PluginInputRequired` is reserved for inputs that are only knowable after the plugin starts inspecting files. Examples:

- A workbook is found to contain a named cell `instrument_calibration_id` that the plugin can fill, but only if the operator supplies a calibration ID for the run.
- A vendor template references a sample-prep batch number that varies per run but is not declared in any `copier.yml`.

### 6.4.1 Suspend / resume flow

When a worker raises `PluginInputRequired`, the worker exits with code 2, having written the `fields` and `reason` payload to stdout. The host:

1. Parses the payload and transitions the session state to `INPUT_REQUIRED` ([[04_Backend_Architecture#4.7 Creation-Session State Machine|§4.7]]).
2. Emits an `input_required` frame over the session's WebSocket channel (`WS /api/v1/sessions/{id}/events`) carrying the field definitions and the human-readable reason. The frontend renders these fields with the same widget machinery as the README form ([[10_README_Generation|§10]]).
3. `await`s the controller's `on_input_required` callback. The callback resolves when the operator submits values via `POST /api/v1/sessions/{id}/resume`, or rejects when the operator cancels.
4. On resume: re-spawns **only the trigger plugin's worker**, with `extra_inputs` populated in the IPC envelope. The session transitions back to `PLUGIN_PASS`.
5. On cancel: the session transitions to `ABORTED`. The cleanup hook ([[08_Error_Handling_Principles|§8]]) removes the partially-created run directory wholesale, including any files mutated by plugins that ran successfully before the trigger.

### 6.4.2 Resume contract (what the plugin author must guarantee)

Resume semantics are deliberate and minimal so plugin authors can reason about correctness without modeling the host's internals:

- **Files mutated before the trigger remain mutated.** The host does not roll back successful per-file work from earlier in the same `transform` loop. Plugin authors must treat per-file mutation as **commit-as-you-go**: a file that has been successfully written and saved is final unless the whole creation is aborted.
- **Only the trigger plugin's worker is re-spawned.** Other plugins that completed successfully before the suspend are not re-run. Other plugins that had not yet started are not started until the trigger plugin finishes.
- **`pre_transform_all` does NOT re-run on resume.** The fresh worker is constructed and `pre_transform_all` is invoked exactly once per worker process. Because the worker is fresh, plugins MUST NOT rely on in-memory state from before the suspend; anything they need must be re-derivable from `ctx` plus on-disk state.
- **`transform()` is re-entered for the trigger file only.** The host hands the worker the same `file_path` that triggered `PluginInputRequired`, and only that file. Files that the plugin had already finished processing in the previous worker invocation are NOT replayed.
- **`transform()` for the trigger file must be safe to re-enter.** If the plugin had partially mutated the trigger file before raising, re-entering `transform()` must produce a consistent end state (idempotent re-application, or detect-and-resume from partial state). The reference implementation pattern is: read the file fresh on each `transform` call; do not rely on instance state set by an earlier partial pass on the same file.
- **`extra_inputs` is the only new value source.** On the resumed call, the plugin reads `ctx.extra_inputs` (a `dict[str, Any]` populated from the operator's submitted values) for the previously-missing inputs. `ctx.variables` is unchanged from the original call.

`PluginInputRequired` is therefore an **escape hatch**, not the primary input pathway. New plugins should prefer declared `required_variables` whenever possible. The xlsx_field_filler example (§6.6) demonstrates the safe pattern: it computes `_plan_writes` fresh from the file, so re-entry on resume produces the correct final cell values regardless of what the previous (suspended) call had done.

## 6.5 Base Plugin Scaffold (`hello_plugin`)

This is the canonical starting point for new plugin authors. Copy the directory, rename, and modify `plugin.py`.

### 6.5.1 Directory

```
hello_plugin/
  __init__.py
  manifest.yml
  plugin.py
  README.md
```

### 6.5.2 `__init__.py`

```python
"""hello_plugin -- minimal example plugin for ExLab-Wizard.

Edit plugin.py to add real behavior. The Plugin symbol export below is what
the host's worker imports.
"""
from .plugin import HelloPlugin as Plugin

__all__ = ["Plugin"]
```

### 6.5.3 `manifest.yml`

```yaml
name: "hello_plugin"
version: "0.1.0"
author: "Your Name"
description: "Minimal example plugin: logs that it would have transformed each matched file."

supported_extensions: [".txt"]
api_version: "1"

required_variables: []
optional_variables: []

isolation:
  timeout_seconds: 10
  memory_mb: 128
  network: false
```

### 6.5.4 `plugin.py`

```python
from pathlib import Path
from typing import Any

from exlab_wizard.plugins import Plugin, PluginContext, FileChange


class HelloPlugin(Plugin):
    name = "hello_plugin"
    version = "0.1.0"
    supported_extensions = [".txt"]
    api_version = "1"

    def can_handle(self, file_path: Path, variables: dict[str, Any]) -> bool:
        # Default: accept anything the extension match handed us.
        return True

    def transform(self, file_path: Path, ctx: PluginContext) -> None:
        ctx.log.info(
            "hello_plugin would transform",
            context={"file": str(file_path), "operator": ctx.variables.get("operator")},
        )
        # Real plugins write to file_path here. This scaffold is a no-op.

    def describe_changes(self, file_path: Path, ctx: PluginContext) -> list[FileChange]:
        return [FileChange(
            path=file_path,
            kind="modify",
            summary=f"hello_plugin would log a hello for {file_path.name}",
        )]
```

### 6.5.5 `README.md`

A one-paragraph description of what the plugin does, the variables it expects, and any known limitations. The app does not consume this file; it exists for the next person who opens the plugin directory.

## 6.6 Worked Example: `xlsx_field_filler`

Reference implementation that exercises every contract surface. Use this as the model when a new plugin needs more than the scaffold.

### 6.6.1 What it does

Opens an `.xlsx` workbook in the rendered destination, reads a `metadata` sheet that lists named cells the lab wants populated, and writes resolved variable values into those cells. If the workbook references a named cell whose value is not in the variable map, it raises `PluginInputRequired` to ask the operator for the missing value, then resumes.

### 6.6.2 `manifest.yml`

```yaml
name: "xlsx_field_filler"
version: "0.3.1"
author: "ExFAB"
description: "Writes resolved variable values into named cells listed in the metadata sheet of .xlsx workbooks."

supported_extensions: [".xlsx"]
api_version: "1"

required_variables:
  - project_name
  - operator
  - run_date
optional_variables:
  - sample_type
  - protocol_reference

isolation:
  timeout_seconds: 60       # workbooks can be large
  memory_mb: 512
  network: false
```

### 6.6.3 `plugin.py`

```python
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.workbook import Workbook

from exlab_wizard.plugins import (
    Plugin,
    PluginContext,
    PluginError,
    PluginInputRequired,
    FileChange,
)


class XlsxFieldFiller(Plugin):
    name = "xlsx_field_filler"
    version = "0.3.1"
    supported_extensions = [".xlsx"]
    api_version = "1"

    required_variables = ["project_name", "operator", "run_date"]
    optional_variables = ["sample_type", "protocol_reference"]

    METADATA_SHEET = "metadata"

    # ---- Lifecycle ---------------------------------------------------------

    def __init__(self) -> None:
        # State carried across files in the same session. Held only in the
        # worker process, so it cannot leak across creation sessions.
        self._workbooks: dict[Path, Workbook] = {}
        self._planned_writes: dict[Path, list[tuple[str, Any]]] = {}

    def validate_variables(self, variables: dict[str, Any]) -> list[str]:
        # Default behavior plus a date-format check (run_date must be ISO 8601).
        errors = super().validate_variables(variables)
        run_date = variables.get("run_date", "")
        if run_date and "T" not in run_date:
            errors.append("run_date must be ISO 8601 (e.g. 2026-04-17T14-32-00)")
        return errors

    def pre_transform_all(self, ctx: PluginContext) -> None:
        ctx.log.info("xlsx_field_filler starting", context={"dst": str(ctx.dst_root)})

    def can_handle(self, file_path: Path, variables: dict[str, Any]) -> bool:
        # Skip tilde-prefixed Excel temp files and anything not opening cleanly.
        if file_path.name.startswith("~$"):
            return False
        try:
            wb = load_workbook(file_path, read_only=True, data_only=False)
        except Exception:
            return False
        try:
            return self.METADATA_SHEET in wb.sheetnames
        finally:
            wb.close()

    # ---- Core mutation -----------------------------------------------------

    def _plan_writes(
        self, file_path: Path, ctx: PluginContext
    ) -> tuple[list[tuple[str, Any]], list[str]]:
        """Read the metadata sheet, resolve each named cell against the variable
        map, and split into (writable, missing). Reused by transform and
        describe_changes."""
        wb = load_workbook(file_path, read_only=True, data_only=False)
        try:
            sheet = wb[self.METADATA_SHEET]
            named_cells = [
                (str(row[0].value).strip(), str(row[1].value).strip())
                for row in sheet.iter_rows(min_row=2, max_col=2)
                if row[0].value and row[1].value
            ]
        finally:
            wb.close()

        writable: list[tuple[str, Any]] = []
        missing: list[str] = []
        for variable_id, target_cell in named_cells:
            if variable_id in ctx.variables and ctx.variables[variable_id] != "":
                writable.append((target_cell, ctx.variables[variable_id]))
            else:
                missing.append(variable_id)
        return writable, missing

    def transform(self, file_path: Path, ctx: PluginContext) -> None:
        writable, missing = self._plan_writes(file_path, ctx)

        if missing:
            # Escape hatch: the workbook references variables we cannot resolve
            # from the upfront map. Ask the operator.
            raise PluginInputRequired(
                fields=[
                    {"id": var, "label": var.replace("_", " ").title(),
                     "type": "string", "required": True}
                    for var in missing
                ],
                reason=(
                    f"{file_path.name} references {len(missing)} named cell(s) "
                    f"that were not in the resolved variable map: {', '.join(missing)}."
                ),
            )

        try:
            wb = load_workbook(file_path)  # read-write this time
        except Exception as exc:
            raise PluginError(f"failed to open {file_path.name}: {exc}") from exc

        self._workbooks[file_path] = wb
        self._planned_writes[file_path] = writable

        for cell_ref, value in writable:
            try:
                wb.active[cell_ref] = value  # active sheet by convention
            except Exception as exc:
                raise PluginError(
                    f"failed to write {cell_ref!r}={value!r} in {file_path.name}: {exc}"
                ) from exc

        wb.save(file_path)
        ctx.log.info(
            "xlsx_field_filler wrote cells",
            context={"file": str(file_path), "count": len(writable)},
        )

    # ---- Dry-run reporter --------------------------------------------------

    def describe_changes(
        self, file_path: Path, ctx: PluginContext
    ) -> list[FileChange]:
        writable, missing = self._plan_writes(file_path, ctx)
        return [
            FileChange(
                path=file_path,
                kind="modify",
                summary=f"would write {len(writable)} cell(s); {len(missing)} would prompt",
                detail={
                    "writes": [{"cell": c, "value": str(v)} for c, v in writable],
                    "missing_variables": missing,
                },
            )
        ]

    # ---- Cleanup -----------------------------------------------------------

    def post_transform_all(self, ctx: PluginContext) -> None:
        for path, wb in self._workbooks.items():
            try:
                wb.close()
            except Exception:
                pass
        self._workbooks.clear()
        self._planned_writes.clear()

    def on_plugin_failure(self, exc: Exception, ctx: PluginContext) -> None:
        # Roll back: any workbook we opened but did not save cleanly should be
        # closed, and any half-written file should be reported. We do NOT
        # restore from a backup here (the host is responsible for that policy);
        # we just ensure handles are released so the host can take action.
        for path, wb in self._workbooks.items():
            try:
                wb.close()
            except Exception:
                pass
        ctx.log.error(
            "xlsx_field_filler rolled back after failure",
            context={"exception": type(exc).__name__, "message": str(exc)},
        )
```

### 6.6.4 What this exercises

| Contract surface | Where in the example |
|---|---|
| Class attributes mirror manifest | top of `XlsxFieldFiller` |
| `validate_variables` extension | adds a date-format check beyond defaults |
| `pre_transform_all` | logs session start |
| `can_handle` | guards against tilde-temp files and missing metadata sheet |
| `transform` | the actual mutation |
| `PluginInputRequired` | escape hatch for unresolved named cells |
| `PluginError` | hard failures during open/write |
| `describe_changes` | dry-run preview surface |
| `post_transform_all` | resource cleanup |
| `on_plugin_failure` | rollback on any hook raising |
| Per-instance state | `self._workbooks` carries between hooks because the worker holds one instance per session |

## 6.7 Example Plugins (Illustrative Catalog)

| Plugin | Trigger | Action |
|---|---|---|
| `xlsx_field_filler` | `.xlsx` files with a `metadata` sheet | Worked example; see §6.6 |
| `docx_variable_replacer` | `.docx` files | Replaces `{{variable_id}}` tokens in document body using `python-docx` |
| `filename_renamer` | any file matching `_exlab_filename_glob` | Renames files using variable values; runs first per `_exlab_plugins` order |
| `csv_header_initializer` | `.csv` files | Writes a header row sourced from project/run metadata |
| ~~`readme_db_lookup`~~ | (removed in v0.7) | The README plugin hook is removed; plugins cannot modify README.md. README field values come from the four merged layers in [[10_README_Generation#10.2 Field Sources (Merged Layers)|§10.2]]. |

## 6.8 Plugin Authoring Checklist

Before submitting a new plugin to the lab's plugin root:

1. `manifest.yml` validates against the schema in §6.1.2 (run `exlab-wizard plugins lint <dir>`).
2. `api_version` matches the host's current version.
3. Every variable read from `ctx.variables` is listed in `required_variables` or `optional_variables`.
4. `can_handle` is cheap and side-effect-free.
5. `transform` raises `PluginError` (not bare `Exception`) on failures and `PluginInputRequired` only for genuinely undeclarable inputs.
6. `describe_changes` is consistent with `transform` (the dry-run output reflects what the real run would do).
7. `post_transform_all` releases all handles opened in `pre_transform_all` or `transform`.
8. The plugin runs to completion under the declared `isolation.timeout_seconds` on a representative workbook/file.
9. The plugin's `README.md` documents the variables it consumes, the files it touches, and any LIMS or network calls it makes (network calls require explicit operator opt-in; see §6.3.3).

## 6.9 Lint CLI: `exlab-wizard plugins lint`

The lint subcommand validates plugins without loading them into a running app. Useful for plugin developers, CI pipelines, and pre-deployment review.

**Invocation:**

```
exlab-wizard plugins lint <PATH>
exlab-wizard plugins lint <PATH> --json
exlab-wizard plugins lint <PATH> --strict
```

`<PATH>` is either a single plugin directory (containing `manifest.yml`) or a parent directory holding multiple plugin directories. The lint walks all plugins it finds.

**Checks performed (per plugin):**

| Check | Severity | Description |
|---|---|---|
| `manifest.yml` exists | error | Required file. |
| `manifest.yml` parses as YAML | error | Malformed YAML rejects the plugin. |
| Required manifest fields present | error | `name`, `version`, `supported_extensions`, `api_version`. |
| `api_version` matches host's supported set | error | Default supported set: `["1"]`. Mismatches in CI mean the plugin needs updating before the next host release. |
| `name` is filesystem-safe | error | Letters, digits, underscore, hyphen. No spaces or path separators. |
| `supported_extensions` items start with `.` (or are the literal `"readme"` — but `"readme"` is rejected as of v0.7 since `transform_readme` was removed; §6.1.5) | error / warn | `"readme"` produces an error since the hook is gone. |
| `isolation.timeout_seconds` ≤ 300 | warn | Soft cap; very long timeouts likely indicate a misconfigured plugin. |
| `isolation.memory_mb` ≤ 2048 | warn | Soft cap. |
| `isolation.network: true` declared | info | Reminder that this requires `plugins.allow_network: true` at the host config level. |
| `__init__.py` exports `Plugin` | error | The host's worker imports `from <pkg> import Plugin`. |
| `Plugin` subclasses `exlab_wizard.plugins.Plugin` | error | Static check via AST or import-and-isinstance. |
| Class attributes mirror manifest (`name`, `version`, `supported_extensions`, `api_version`) | warn | Drift between manifest and class is a common bug source. |
| Methods declared abstract are implemented | error | `can_handle`, `transform`. |
| `transform_readme` is NOT implemented | error | Removed in v0.7 (§6.1.5). |
| `requirements.txt` (if present) is valid | warn | Documented dependencies; not auto-installed. |

**Exit codes:**

| Code | Meaning |
|---|---|
| `0` | All plugins lint clean (no errors, possibly warnings). |
| `1` | Warnings only. |
| `2` | At least one plugin has errors. |
| `3` | The supplied `<PATH>` is not a directory or doesn't exist. |

**Output formats:**

- Default: human-readable, one finding per line, grouped by plugin. Color-coded by severity.
- `--json`: machine-readable JSON for CI consumption. Shape: `{ "plugins": [{"path": "...", "name": "...", "findings": [{"severity": "error", "code": "missing_manifest_field", "message": "...", "field": "..."}]}] }`.

`--strict` treats warnings as errors (exits 2 instead of 1 if any warnings present). Useful in CI pipelines that want zero tolerance.

## 6.10 Test / Debug CLI: `exlab-wizard plugins exec`

Plugin authors need a way to exercise their plugin against synthetic input without spinning up a full creation session. The lint CLI (§6.9) checks static structure; this CLI runs the plugin's `transform()` method against a fixture.

**Usage:**

```
exlab-wizard plugins exec <plugin_dir> --against <fixture_dir> [--no-isolation] [--input <field>=<value>]...
```

| Argument | Purpose |
|---|---|
| `<plugin_dir>` | Path to a plugin directory (the same shape `--against` lints). |
| `--against <fixture_dir>` | Path to a fixture directory containing the files the plugin should `transform()`. The fixture is treated as the run-root: the plugin's `transform()` is called once per matching file in the fixture. |
| `--no-isolation` | Runs the plugin in-process rather than via the subprocess worker. Strips resource limits and timeout enforcement. **Required** for using a Python debugger (e.g. `pdb`) against the plugin -- subprocess workers cannot have a debugger attached. |
| `--input <field>=<value>` | Pre-supplies values that the plugin would otherwise request via `PluginInputRequired`. Repeatable (one per field). When the plugin emits `PluginInputRequired`, the CLI satisfies it from these flags; if a required field has no `--input`, the CLI prints the request and prompts on stdin (or aborts with `--non-interactive`). |

**Synthetic `PluginContext`.** The CLI constructs a minimal `PluginContext` matching the production shape (Backend §6.1.2) but with synthesized values: a fixture-rooted `run_path`, a stub `lims_project` block (default `PROJ-TEST` / `"Test Project"`), a writable `tmp_path` per invocation, and a `PluginLogger` that writes to stdout. Plugin authors can override individual context fields via `--ctx-override <field>=<json_value>` for advanced cases.

**Output.** Per-file `transform()` calls print a single line: `<file> -> <action>` where `action` is one of `mutated` / `skipped` / `failed: <reason>`. On `--no-isolation`, exceptions propagate; on the default subprocess path, exceptions are caught and the worker exits with the protocol-defined exit code (Backend §6.3.4).

**Exit codes:**

- `0` -- all transforms succeeded.
- `1` -- at least one transform failed (file-level error).
- `2` -- the plugin failed to load (manifest missing, import error, etc.).
- `3` -- the fixture directory is missing or unreadable.
- `4` -- a required `PluginInputRequired` field was not supplied via `--input` and the CLI is in `--non-interactive` mode.

**Why this exists.** Without it, plugin authors had to create a real run via the wizard, watch their plugin fail, inspect log files, and iterate. The CLI shrinks that loop to seconds and supports debugging via `--no-isolation`. Together with the lint CLI (§6.9), this is the v1 plugin-authoring toolchain. Plugin tests in CI invoke `exlab-wizard plugins exec` against test fixtures stored alongside the plugin source; the spec recommends `tests/fixtures/<file_kind>/` per plugin.

**What this CLI does NOT do:**

- It does not exercise the full controller flow (validator pre-check, cache writer, NAS sync). Those are tested via the integration-test layer (Backend §4.10.2).
- It does not validate that the plugin's `transform()` produces output that satisfies post-creation validation rules (Backend §8.1). Authors who care about post-validation test in the integration layer.
- It does not run the README pre-fill flow that wizards use (§10).
