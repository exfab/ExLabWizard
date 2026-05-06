# 16. Logging Architecture

Parent: [[ExLab-Wizard_Design_Spec]]

This section is the canonical home for the ExLab-Wizard logging story: the runtime logger module, the on-disk log file layout, format / levels / rotation, plugin-worker logging, and operator-facing log access. Every other section that emits log events references back here -- the goal is one place that captures the design so future debugging and behavior changes are simple.

Cross-references TO this section (not duplications):

- Backend [[#4.5 Async / Threading Model|§4.5]] mentions the concurrent-log-write rules; details live here.
- Backend [[09_Configuration_File|§9]] declares the `logging` config block; per-field semantics live here.
- Backend [[11_Cache_Folders|§11.5 / §11.5.1]] documents the on-disk format of `wizard.<hostname>.log` and the cache-folder placement; this section is the umbrella that ties §11.5 into the broader logger architecture.
- Frontend [[ExLab-Wizard_Frontend_Spec|Frontend Spec §7.11]] is the operator-facing Settings UI for log configuration; it backs the `logging` block this section specifies.

## 16.1 Goals

The logging system has four explicit goals, in priority order:

1. **Self-describing per-run audit trail.** Every creation operation produces a per-run `wizard.<hostname>.log` that records what happened, in what order, with timestamps and structured tags. The audit trail travels with the data when synced to NAS (§11.6).
2. **Operator-debuggable failures.** When something goes wrong, the operator (or lab IT) can find the relevant log file in seconds via the where-to-look quick reference (§16.3) and read enough context to act on it.
3. **Single point of configuration.** All log levels, rotation policies, and file destinations are controlled from one place (`config.yaml` `logging` block, §9) and read through one runtime module (`exlab_wizard/logging/manager.py`, §16.2).
4. **No log-config drift.** Components do not reach into Python's `logging` module directly to set levels, formats, or handlers. They go through the manager. This makes a future change to the format or rotation policy a single-file edit.

## 16.2 The `logging/` package

```
exlab_wizard/logging/
  __init__.py          # exports get_logger, configure_logging, set_run_context
  manager.py           # canonical logger factory; reads config; configures handlers
  format.py            # the format string template + structured-tag renderer (§16.4)
  context.py           # per-thread / per-task context vars (run_id, equipment_id, etc.)
  handlers.py          # equipment-scoped FileHandler subclass; central RotatingFileHandler config
```

### 16.2.1 The `get_logger(name)` entry point

Every component imports the logger via:

```python
from exlab_wizard.logging import get_logger

logger = get_logger(__name__)

logger.info("Run created: %s", run_path)
```

`get_logger` returns a Python `logging.Logger` configured with:

- The format string from `format.py` (matching §11.5 and §16.4).
- Structured tags pulled from the active context (§16.2.3) at log time, NOT at logger-creation time. So a logger created at module import has no run context; calling `set_run_context(run_id=..., equipment_id=..., project=...)` before the actual log call attaches the right tags to subsequent entries within the same async task.
- Both an equipment-scoped `FileHandler` (resolved from context; §16.2.4) AND the central `RotatingFileHandler` for `WARN`-and-above events.
- A stderr `StreamHandler` for `WARN`-and-above events, visible in the launcher console (or in the tray-process stderr capture).

Components MUST NOT instantiate `logging.Logger` directly via `logging.getLogger(...)`. A pre-commit lint rule (analogous to the `ui.notify` rule in Frontend §2.2.5) forbids this in any module under `exlab_wizard/` other than `logging/manager.py` itself.

### 16.2.2 `configure_logging(config)` at startup

Called once during the FastAPI lifespan startup (§4.5):

```python
from exlab_wizard.logging import configure_logging

@asynccontextmanager
async def lifespan(app):
    configure_logging(config.logging)
    # ... other startup
    yield
    # shutdown ...
```

This applies the config-driven level threshold, opens the central `RotatingFileHandler` against the OS-appropriate path (§16.3), and registers the stderr handler. `configure_logging` is idempotent -- re-calling it (e.g. after a `PUT /api/v1/config`) reconfigures handlers without losing in-flight log events.

### 16.2.3 Context vars

`logging/context.py` exposes a small set of `contextvars` for the structured tags:

```python
from exlab_wizard.logging.context import set_run_context, clear_run_context

# At the start of a creation session:
with set_run_context(host=os.uname().nodename, equipment_id="CONFOCAL_01",
                     project_short_id="PROJ-0042", run_kind="experimental",
                     run_id="Run_2026-04-17T14-32-00"):
    await controller.run(session)
# Context auto-clears on exit
```

Logs emitted inside the `with` block carry the `[host:]`, `[equip:]`, `[proj:]`, `[kind:]`, and (where present) `[run:]` tags from the format string (§16.4). Logs outside any context block omit those tags and fall through to the central log only.

`contextvars` are async-safe: a `set_run_context` in one task does not bleed into another concurrent task. This matters in orchestrator mode where multiple equipment sessions run concurrently.

### 16.2.4 Equipment-scoped handlers

`handlers.py` exposes an `EquipmentScopedFileHandler` that resolves the destination at log-emit time using the active context's `equipment_id`. There is one such handler per running equipment (created lazily on first emit and cached). On emit:

- Resolves the path: `<local_root>/<equipment_id>/.exlab-wizard/wizard.<hostname>.log` (§11.1).
- Opens the file with `O_APPEND` (POSIX) or `FILE_APPEND_DATA | FILE_SHARE_WRITE` (Windows) so concurrent emits from the same hostname don't tear (§4.5 same-equipment concurrency rule).
- Writes one structured line per event.
- Does NOT close the file between emits within a session; the handler holds the descriptor open for the lifetime of the process and uses fsync only on `ERROR`-level events.

Project- and run-level scoping (writing to `<local_root>/<equipment>/<project>/.exlab-wizard/...` or its run subdirectory) follows the same pattern with the path resolved from `project_short_id` / `run_id` in the active context.

## 16.3 Log file layout (where-to-look quick reference)

| If you want to debug... | Look at... |
|---|---|
| A single creation that failed | `<local_root>/<equipment>/<project>/<run>/.exlab-wizard/wizard.<hostname>.log` |
| All runs that have touched an equipment | `<local_root>/<equipment>/.exlab-wizard/wizard.<hostname>.log` |
| All runs under a project (any equipment) | `<local_root>/<equipment>/<project>/.exlab-wizard/wizard.<hostname>.log` (per equipment) |
| App-wide errors, startup, plugin registry, validator audits, sync queue, LIMS cache refresh | The **central app log**, OS-conditional path: |
| | macOS: `~/Library/Logs/exlab-wizard/app.log` |
| | Windows: `%LOCALAPPDATA%\exlab-wizard\Logs\app.log` |
| | Linux: `${XDG_STATE_HOME:-~/.local/state}/exlab-wizard/app.log` |
| Plugin worker stderr (a plugin crashed; what did it print?) | The same central app log carries the plugin worker stderr, prefixed with `[plugin:<name>][worker:<pid>]`. The full per-worker stderr also lives at `<central_log_dir>/plugins/<plugin>/<run_id>.stderr` for postmortem inspection (preserved per the plugin failure-handling rules in §6.3.4). |
| Tray + window subprocess events (autostart, window spawn, graceful shutdown) | Central app log, prefixed with `[component:tray]` or `[component:window]`. |
| NAS sync details for a specific job | The run's `wizard.<hostname>.log` for the per-run lifecycle entries; the central app log for queue-level events (retry batches, cleanup reaper). |
| Setting changes (who saved what, when) | Central app log, `[component:settings]` entries on every successful `PUT /api/v1/config`. |

The Frontend `[View log]` action in the Detail pane (Frontend §3.6.5) opens the run-level log in a read-only viewer. The action falls back to the equipment-level log when the run-level file is missing (e.g. for a run that was deleted by hand on disk while the cache survived).

## 16.4 Format

All log entries use one structured-tag format:

```
<UTC ISO 8601 timestamp> [<LEVEL:5>] [host:<hostname>] [equip:<equipment_id>] [proj:<short_id>] [kind:<run_kind>] [run:<run_id>] <message>
```

Tags `[host:]`, `[equip:]`, `[proj:]`, `[kind:]`, `[run:]` are present when their corresponding context var is set and omitted otherwise (§16.2.3). The level field is left-padded to 5 characters so columns line up across `INFO ` / `WARN ` / `DEBUG` / `ERROR` lines. Examples in §11.5.

Additional structured tags reserved for non-creation events:

- `[component:tray]` / `[component:window]` -- emitted by tray/window subprocess (§4.3.2).
- `[component:settings]` -- emitted on Settings-dialog save.
- `[component:nas_sync]` -- queue-level events in NAS sync (§7.1).
- `[component:lims]` -- LIMS cache refresh / health-check events.
- `[component:plugin_host]` -- plugin registry build, worker spawn, worker exit.
- `[plugin:<name>][worker:<pid>]` -- emitted on behalf of a plugin worker subprocess (the worker's stderr is captured and re-emitted by the plugin host with these tags).
- `[trace:<trace_id>]` -- echoed when an HTTP request carrying `X-Trace-Id` triggers the log entry; correlates with the error envelope's `trace_id` (§4.6.3).

The format is fixed in `logging/format.py`; downstream tooling (e.g. log-aggregation scripts, DESIGN.md observability guides) parses this shape. Adding a new structured tag is a deliberate spec change to this section + a `format.py` update.

## 16.5 Levels and configuration

Standard Python `logging` levels: `DEBUG`, `INFO`, `WARN`, `ERROR`. Threshold is configured in `config.yaml` `logging.level` (default and field semantics in §9). The threshold applies to all handlers (per-equipment, per-run, central, stderr), with the stderr handler additionally capped at `WARN` so the launcher console is not overwhelmed with `INFO` traffic.

**Level guidance for component authors:**

- `DEBUG` -- step-by-step internal events. Disk writes, individual plugin file iterations, validator rule applications. Suppressed in production by default.
- `INFO` -- the per-run lifecycle (creation started / template resolved / cache written / sync queued / sync complete) and the central-log per-component startup events. This is the level operators see in the per-run log.
- `WARN` -- recoverable failures or degradation. Sync retries, LIMS unreachability, validator findings discovered (the finding itself is in the Problems tab; the log records that the audit pass surfaced it).
- `ERROR` -- non-recoverable failures within the operation's scope. Plugin worker crashes, keyring access failures, malformed `creation.json` files, atomic-write failures.

**Per-component level overrides** are NOT supported in v1. The threshold is global. (If a future need arises, an `OQ` would be opened to add per-component overrides via `logging.component_levels: { lims: WARN, plugin_host: DEBUG }` — not in scope today.)

**Settings-UI control.** Frontend §7.11 surfaces the level as a radio in the Settings dialog's Logging section. Changing the level via Settings calls `configure_logging` (§16.2.2) with the new value; in-flight log statements continue at the old level until the next `configure_logging` call completes (atomic from the operator's perspective: the level changes between API calls, never mid-call).

## 16.6 Rotation

Per-equipment, per-project, and per-run `wizard.<hostname>.log` files are **not rotated**. They are bounded in practice by the lab's run cadence (§11.5.1):

- Per-run logs are written once per creation event; final size is typically a few KB to a few hundred KB.
- Per-project logs accumulate one entry block per run plus setup events; final size grows linearly with run count.
- Per-equipment logs accumulate likewise.

The **central app log** (`app.log`) uses Python's `logging.handlers.RotatingFileHandler`:

- Rotate when the active file exceeds `logging.central_log_max_mb` (default 10 MB; §9).
- Keep `logging.central_log_keep` rotated files (default 5; §9). Older files are deleted.
- File name pattern: `app.log`, `app.log.1`, `app.log.2`, ..., `app.log.<central_log_keep>`.

Rotation is performed by the handler synchronously on the rotation-triggering write; this can introduce a few-millisecond stall on a single log call but does not require a separate rotation task.

## 16.7 Debugging recipes

These are the standard operator/IT recipes for the most common debug scenarios. Each is a quick path from "something's wrong" to "I can see the relevant log lines."

### 16.7.1 "A run failed; what happened?"

1. Open the run in the Detail pane (Frontend §3.6).
2. Click the global `[View log]` action.
3. Read the per-run `wizard.<hostname>.log` from the bottom up. The terminal `ERROR` or final state transition is at the end.

### 16.7.2 "Sync is failing for many runs"

1. Open the central app log (path in §16.3).
2. Filter for `[component:nas_sync]` entries.
3. Look at the latest queue-level entries: retry-batch summaries, transport probe failures, credential errors. The pattern (which equipment, which transport type, which error code) is usually visible within the last ~50 entries.

### 16.7.3 "A plugin is misbehaving in production"

1. Note the plugin name from the run's Detail pane Plugin output section (Frontend §3.6.3).
2. Open the central app log.
3. Filter for `[plugin:<name>]` entries.
4. If the worker crashed, additionally inspect `<central_log_dir>/plugins/<name>/<run_id>.stderr` for the full stderr.
5. To reproduce locally, run `exlab-wizard plugins exec <plugin_dir> --against <fixture> --no-isolation` (§6.10) with the same input the run used.

### 16.7.4 "I don't trust my config; what's actually loaded?"

1. Tail the central app log on next launch.
2. The startup sequence emits a `[component:config]` line listing the resolved `config.yaml` path, schema version, and a one-line digest of significant fields (paths, equipment count, LIMS endpoint, logging level). Compare against `config.yaml` directly.

### 16.7.5 "Increase verbosity for one debug session"

1. Settings → Application → Logging → Level → `DEBUG`.
2. Click `[Save]`. The level applies on save (no restart required).
3. Reproduce the issue. The relevant log file (per the §16.3 quick-reference) now carries DEBUG-level entries.
4. After collecting, set the level back to `INFO`. (DEBUG produces enough traffic that leaving it on is noisy in the central log over time.)

A planned v1.1 affordance: a `Settings → Application → [Bundle logs for support]` action that zips the central log + the most recent per-equipment log + a sanitized snapshot of `config.yaml` (with passwords already absent because they live in the keyring) for sharing with lab IT or the spec maintainers. Tracked in Design Spec §14 OQ list as a candidate addition.

## 16.8 Plugin worker logging

Plugin workers are subprocesses (§6.3) and so cannot write directly to the host's log files. Instead:

- The worker uses `PluginLogger` (§6.1.2) which writes JSON-encoded log frames to its stdout via the IPC envelope (§6.3.2).
- The host reads worker stdout, deserializes log frames, and forwards them to the equipment-scoped `wizard.<hostname>.log` (with `[plugin:<name>][worker:<pid>]` tags) AND the central app log at the matching level.
- The worker's stderr (anything it prints outside the IPC envelope: tracebacks, third-party library output) is captured to `<central_log_dir>/plugins/<plugin>/<run_id>.stderr` (overwritten on each run for the same `(plugin, run_id)`).

A plugin author who wants to debug their plugin should:

1. Use `PluginLogger` for structured, host-forwarded log entries (visible in the run log).
2. Use plain `print(..., file=sys.stderr)` for verbose diagnostics that should land in the per-run stderr file but stay out of the run log.
3. Use `exlab-wizard plugins exec --no-isolation` (§6.10) for in-process debugging with `pdb` or similar.

## 16.9 Operator-facing log access

The Frontend Spec defines the operator-facing surfaces:

- Detail-pane `[View log]` action (Frontend §3.6.5) opens the run- or equipment-level log in a read-only scrollable viewer.
- Settings → Application → Logging (Frontend §7.11) controls level and rotation parameters.
- The cheatsheet in Frontend §3.7 includes no log-specific shortcut in v1; opening logs is via the Detail pane affordance.

Operators do NOT have an in-app surface for the central app log -- that file is intended for lab IT debugging via the OS file manager, not for end-user inspection. The §16.3 where-to-look table gives the absolute path on every platform.

## 16.10 Anti-patterns

These are explicit don'ts for component authors:

- **Don't `print()`.** Use `get_logger(__name__).info(...)` (or appropriate level). `print` writes to stdout, which on the tray subprocess is unconfigured.
- **Don't `logging.getLogger(...)` directly.** Use `exlab_wizard.logging.get_logger`. The pre-commit lint rule rejects direct `logging.getLogger` calls in `exlab_wizard/`.
- **Don't set a log level inside a component.** Use the global threshold from `config.yaml`. Per-component overrides are not in v1.
- **Don't log secrets.** No passwords, API tokens, or operator-personal data. The keyring layer (§7.4) keeps secrets out of process memory at log time, but the format string can still capture, e.g., a leaked credential in a URL. Use `redact_secret(...)` from `logging/format.py` when emitting any URL or auth-bearing string.
- **Don't write log files outside the manager.** Components that need to record large blob output (e.g. a plugin emitting a verbose dump) write to a separate file under `<run_path>/.exlab-wizard/blobs/<filename>` and emit a single log line referencing it. Keep the structured-line log lean.
