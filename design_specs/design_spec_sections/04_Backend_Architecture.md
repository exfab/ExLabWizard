# 4. Backend Architecture

Parent: [[ExLab-Wizard_Design_Spec]]

---

## 4.1 Deployment Model

ExLab-Wizard runs as a **native desktop application** with a persistent server and an on-demand native window, coordinated by a system-tray icon. Two cooperating processes per workstation:

- **`exlab-wizard-tray`** (long-lived; auto-starts at login). Hosts the FastAPI + NiceGUI server in-process, plus a [pystray](https://pystray.readthedocs.io/) tray icon. Survives window opens and closes.
- **`exlab-wizard-window`** (on-demand; spawned by the tray). A [pywebview](https://pywebview.flowrl.com/) subprocess that renders the NiceGUI UI in a native window using the platform webview (WebKit on macOS, WebView2 on Windows, GTK-WebKit on Linux). Closes independently of the server.

The server binds to `127.0.0.1:<random free port>` only — no remote, multi-tenant, or networked deployment, no system browser involvement at runtime.

Launch flow:

1. The OS-registered autostart entry executes `exlab-wizard-tray` at user login (autostart is opt-in at first launch; see §15.7).
2. The tray process starts the FastAPI server in-process on a free localhost port, writes its `{ port, pid, started_at }` to a per-user state file (`<state_dir>/server.json`), and registers a tray icon with menu **Open**, status submenu, **Quit**.
3. **Open** spawns `exlab-wizard-window` as a subprocess. The window reads `server.json`, opens a pywebview window pointed at `http://127.0.0.1:<port>`. While a window is alive, **Open** focuses it (single-instance).
4. Closing the native window terminates only the window subprocess. The tray icon and server remain alive; the operator can re-open the window at any time.
5. **Quit** initiates graceful shutdown: server stops accepting new requests, awaits in-flight controller operations up to 30 s, then terminates. Tray icon disappears; durable NAS-sync queue (§7.1) survives across launches.

**Why native window + tray over browser-served:**

- Lab workflow is "step away during a long sync, come back later". The persistent-server-with-closeable-window model maps to this directly. With browser-served, closing the browser tab orphans the running server; with native-mode-only, closing the window quits everything.
- Single-instance window eliminates an entire class of multi-tab edge cases (concurrent Settings edits, divergent wizard state, conflicting saves).
- Plugin worker subprocesses are still direct children of a stable, long-lived parent (the tray-server process; see §4.2). `PluginInputRequired` suspend/resume is unaffected.
- Native UX: app icon, tray icon, native window with proper title bar, OS notifications for events that need attention (§15.7.3). No browser decoration, no URL bar, no tab strip.

**What we lose vs. browser-served:** browser-based devtools are unavailable in shipping builds (debug-only flag re-enables them); LAN remote inspection isn't a thing (server still binds to `127.0.0.1` only); Linux without a system tray (vanilla GNOME/Wayland, certain tiling WMs) loses the persistent-server affordance and falls back to window-only mode (§15.7.4).

**Single-user assumption.** v1 is a single-operator workstation tool. The server has no auth. The tray icon is per-OS-user; autostart is registered to the user's autostart, not system-wide.

## 4.2 Process Model

```
┌──────────────────────────────────────────────────────────────────┐
│  exlab-wizard-tray (long-lived; auto-starts at login)            │
│                                                                  │
│  ┌─────────────────────────┐  ┌─────────────────────────────┐    │
│  │ pystray icon            │  │ FastAPI + NiceGUI server    │    │
│  │  - Open                 │  │  - bound to 127.0.0.1:<port>│    │
│  │  - status submenu       │  │  - all components from §4.4 │    │
│  │  - Quit                 │  │  - WebSocket events         │    │
│  └────────────┬────────────┘  └─────────────┬───────────────┘    │
│               │ in-process                  │                    │
│               └──────────────┬───────────────┘                   │
│                              │                                   │
│                              │  asyncio.create_subprocess_exec   │
└──────────────────────────────┼───────────────────────────────────┘
                               │
                               ▼
              ┌────────────────────────────────┐
              │ Plugin Worker subprocesses     │  one per plugin per session
              │ (python -m                     │  IPC: stdin/stdout JSON envelope
              │  exlab_wizard.plugins._worker) │  resource limits via setrlimit
              └────────────────────────────────┘

       ┌────────────────────────────────┐
       │ exlab-wizard-window            │  on-demand; spawned by tray's Open action
       │ (pywebview: WebKit / WebView2 / │  HTTP+WebSocket to localhost:<port>
       │   GTK-WebKit)                   │  closing this process does NOT affect tray/server
       └────────────────────────────────┘
```

Key process shape:

- **The tray process hosts the server.** They live in one process (the tray's `pystray` runs on the main thread; the FastAPI server runs on `uvicorn` in an asyncio loop). The tray queries server state via Python calls, not IPC, so there is no separate tray↔server channel to maintain.
- **The pywebview window is a separate process.** Spawned via `subprocess.Popen` of the `exlab-wizard-window` entry point. This separation lets the operator close the window cleanly (its process exits, server is unaffected).
- **Window↔server discovery.** The tray writes `<state_dir>/server.json` containing `{ "port": <int>, "pid": <int>, "started_at": <iso8601> }` on startup. The window process reads this file on launch. If the file is missing or the recorded PID is not running, the window exits with an error and the tray re-spawns the server.
- **Plugin workers** are children of the tray-server process, not the window. Surviving a window close is automatic.

**Bundle layout (§15.1).** PyInstaller `--onedir` produces a single bundle with three entry points: `exlab-wizard-tray` (the long-lived process), `exlab-wizard-window` (spawns pywebview), and `exlab-wizard` (a CLI alias that invokes the tray's Open action via the state file — useful for command-line workflows or when the tray icon is hidden by the OS).

**Copier still runs in-process.** Unchanged from v0.7: no Copier subprocess, `unsafe=False`, post-render plugin pass driven by the controller directly.

## 4.3 Package Layout

```
exlab_wizard/
  __init__.py
  __main__.py              # CLI alias entry point: invokes the running tray's Open action via the state file (or starts the tray if not running)
  tray/                    # the long-lived process; see §4.3.2
    __init__.py
    main.py                # `exlab-wizard-tray` entry point
    icon.py                # pystray icon construction + menu wiring
    status.py              # status-submenu rendering from server-side state
    autostart.py           # per-platform autostart register / unregister / is_registered
    notifications.py       # OS notifications via plyer (fallback: per-platform shims)
    server_runner.py       # starts FastAPI server in-process, writes <state_dir>/server.json
    window_launcher.py     # spawns exlab-wizard-window subprocess; tracks PID; focuses on re-open
    quit_coordinator.py    # graceful shutdown: drain in-flight ops up to 30 s, then terminate
  window/                  # the on-demand process; see §4.3.2
    __init__.py
    main.py                # `exlab-wizard-window` entry point
    pywebview_app.py       # reads <state_dir>/server.json, opens pywebview window at the port
  api/
    app.py                 # FastAPI app + lifespan; mounts NiceGUI
    routers/
      sessions.py          # POST /sessions, /sessions/{id}/*, WS /sessions/{id}/events
      problems.py          # GET /problems, POST /problems/{run_path}/override
      config.py            # GET/PUT /config
      browse.py            # GET /tree, GET /run/{path}
    schemas.py             # Pydantic request/response models
    events.py              # WebSocket event envelope types
  controller/
    creation.py            # CreationController: create_*/resume/cancel/status
    state_machine.py       # SessionState enum + transition table
    session_store.py       # in-memory dict + GC of abandoned sessions
  template/
    copier_driver.py       # wraps copier.run_copy(); resolves _exlab_* metadata
  plugins/
    __init__.py            # exports Plugin, PluginContext, FileChange, PluginError, PluginInputRequired
    base.py                # the Plugin ABC and dataclasses
    registry.py            # startup-time manifest scan + api_version gating
    host.py                # PluginHost: spawns workers, marshals IPC, applies isolation
    _worker.py             # the worker entry point (python -m exlab_wizard.plugins._worker)
    logger.py              # PluginLogger shim used in the worker
  validator/
    rules.py               # one function per rule in §8.1
    engine.py              # Validator class; creation-time + audit modes; query_problems()
    findings.py            # Finding dataclass; serialization to the §11.8 schema
  cache/
    creation_writer.py     # creation.json atomic read/write/update
    log_writer.py          # append to wizard.<hostname>.log
    equipment.py           # equipment.json read/write
    ingest_writer.py       # orchestrator-only: ingest.json
  readme/
    generator.py           # merge field layers, render YAML+Markdown, write README.md + readme_fields.json
  sync/
    nas_client.py          # NASSync interface (see §7.1)
  lims/
    client.py              # LIMSClient (read-only in v1; see §7.2)
    schemas.py             # LIMSProject, LIMSUser dataclasses
    cache.py               # SQLite-backed project-list cache (TTL-driven; §7.2.4)
  config/
    loader.py              # config.yaml -> typed model
    models.py              # pydantic models matching §9
  ui/
    design.py              # design tokens (color, typography, spacing, radius, shadows) mirroring DESIGN.md; see Frontend §2.1
    theme.py               # NiceGUI/Quasar theme registration that consumes design.py constants
    notifications.py       # canonical notification helpers (notify_success, notify_field_error, show_banner, etc.); see Frontend §2.2
    keyboard.py            # app-level keyboard-shortcut registry; see Frontend §3.7
    pages/
      main.py              # NiceGUI main window (left tree + right tabs + toolbar)
      wizard_project.py    # ui.stepper-based New Project flow
      wizard_run.py        # ui.stepper-based New Run / New Test Run flow
      settings.py
      problems.py
    components/
      tree.py              # equipment/project/run tree widget
      mode_badge.py        # experimental/test mode visual cue
      session_progress.py  # WebSocket-driven progress bar
  errors.py                # ExLabError hierarchy
  paths.py                 # path composition helpers, equipment-id canonicalization, OS-appropriate config/log/state directories
  constants/               # single source of truth for values referenced from multiple modules; see §4.3.1
    __init__.py
    schema_versions.py     # CREATION_JSON_VERSION, README_FIELDS_JSON_VERSION, INGEST_JSON_VERSION
    filenames.py           # CACHE_DIR_NAME (".exlab-wizard"), CREATION_JSON_NAME, READMME_FIELDS_JSON_NAME, EQUIPMENT_JSON_NAME, INGEST_JSON_NAME, TEST_RUNS_JSON_NAME, ANSWERS_FILE_NAME, LOG_FILE_TEMPLATE
    patterns.py            # EQUIPMENT_ID_REGEX, EQUIPMENT_ID_MAX_LENGTH, PLACEHOLDER_ANGLE_BRACKET_REGEX, PLACEHOLDER_JINJA_REGEX_*
    enums.py               # RunKind, SyncStatus, Tier, ProblemClass, FindingKind — string-valued Enum subclasses
    keyring.py             # KEYRING_SERVICE ("exlab-wizard"), KEYRING_USERNAME_LIMS, KEYRING_USERNAME_NAS_TEMPLATE
    limits.py              # PLUGIN_TIMEOUT_DEFAULT, PLUGIN_TIMEOUT_MAX, PLUGIN_MEMORY_DEFAULT, etc. (NOT user-configurable defaults — those live in config/models.py)

tests/
  unit/                    # mirrors package layout
  integration/             # backend with tmpfs, mock NAS, in-memory LIMS
  e2e/                     # Playwright-driven browser tests against localhost server
  fixtures/                # template, plugin, config fixtures
```

The `ui/` package depends on `controller/` and the API schema modules but never the reverse: no backend module imports from `ui/`. This is the testability boundary — backend can be exercised without a browser.

### 4.3.1 The `constants/` package

A small set of values appears in many specifications and must stay synchronized across the codebase: schema version numbers, file names of cache files, regex patterns, keyring service identifier, enum string values. The `constants/` package is the single source of truth for these. Rules:

- **Hard constants only.** Values that change rarely, never at runtime, and that are referenced from at least two unrelated modules. A value used in only one module stays in that module.
- **No imports from elsewhere in `exlab_wizard`.** Constants modules are leaves in the import graph. They depend only on `typing`, `enum`, and standard-library re/path. This keeps them safe to import from anywhere without risking a circular dependency.
- **No user-configurable defaults.** Operator-tunable defaults (e.g. `cache_ttl_hours: 24`, `content_scan_max_mib: 5`) live in `config/models.py` as Pydantic field defaults — that's the canonical location §9 documents. The `constants/limits.py` module holds only **internal** caps (e.g. plugin timeout maximums that the spec hard-codes; see §6.1.2's `timeout_seconds: max 300`).
- **Mirrored to spec sections.** Each constants module corresponds to a spec subsection that documents the values. Schema versions ↔ §11 history tables. Filenames ↔ §11.1 / §11.2. Regex patterns ↔ §3.1, §8.1.1. Enums ↔ §11.3, §7.1, §8.1. Keyring ↔ §7.4. Updates to a constant must update the corresponding spec subsection in the same change.

**Why this matters.** Without the discipline, the same value (e.g. `creation.json` schema version `1.7`) would appear hard-coded in `cache/creation_writer.py`, in `cache/creation_reader.py`, in `api/health.py` (the `/health` response), in test fixtures, and in spec text. A future schema bump misses one of those sites and ships with silent inconsistency. The constants package collapses N declarations to one.

### 4.3.2 The `tray/` and `window/` packages

`tray/` and `window/` implement the two-process distribution model (§4.1, §4.2). They are layered above the application core: nothing in `api/`, `controller/`, or other core packages may import from them. The split mirrors the process boundary.

**`tray/`** is the long-lived process. Its modules:

- `main.py` — the `exlab-wizard-tray` console_scripts entry point. Wires together `server_runner`, `icon`, and `quit_coordinator`; runs the pystray event loop.
- `icon.py` — pystray icon construction. Builds the menu (Open / status / Quit) and binds menu actions to the runtime objects (server reference, window launcher, quit coordinator).
- `status.py` — derives the status submenu's contents from the live server state (`SessionStore.active_sessions`, `NASSyncClient.queue_depth`, `Validator.audit_summary`). Refreshes on a 5-second ticker. The status string follows a small formatter (`"Idle"` / `"Sync: 3 jobs"` / `"⚠ 1 plugin needs input"`).
- `autostart.py` — per-platform autostart register / unregister / is_registered. macOS: writes a `LaunchAgent` plist to `~/Library/LaunchAgents/`. Windows: writes a `HKCU\...\Run` registry entry. Linux: writes a user systemd unit to `~/.config/systemd/user/` (with a fallback `~/.config/autostart/*.desktop` for non-systemd setups). All three are reversible from `Settings → Application` (Frontend §7).
- `notifications.py` — OS notifications. Uses [plyer](https://plyer.readthedocs.io/) as the cross-platform shim; falls back to platform-specific calls (`osascript display notification` on macOS, `Win10toast` on Windows, `notify-send`/`dbus` on Linux) where plyer's behavior is unsatisfactory. Two notification triggers: `PluginInputRequired` escalation (Frontend §9) and sync-failure-with-no-auto-retry-left (§7.1.5). Suppressed when the window is currently in the foreground (no point notifying about something the operator is looking at).
- `server_runner.py` — starts `uvicorn` programmatically (not via shell). Picks a free port from the OS; writes `<state_dir>/server.json` atomically (write `.tmp` → fsync → rename). On normal shutdown, deletes the file.
- `window_launcher.py` — `subprocess.Popen` of `exlab-wizard-window`. Tracks the child PID. On re-open requests, checks whether the child is alive: if yes, sends a focus signal (platform-specific: pywebview's `webview.windows[0].show()` via a one-byte localhost token; on Linux, uses `xdotool` or equivalent if available, else just spawns a second window — pywebview multi-window handling is OS-dependent).
- `quit_coordinator.py` — **canonical specification of the graceful-shutdown protocol** (referenced from Frontend §3.4.6). Steps:
  1. Send the FastAPI lifespan shutdown signal; the server stops accepting new requests and `POST /api/v1/sessions` returns `503` with `error.code: "shutting_down"`.
  2. Wait up to **30 seconds** (5 seconds for `SIGTERM` from the OS at logoff, since the OS will hard-kill anyway) for the predicate `SessionStore.active_sessions == 0 AND NASSyncClient.in_flight_jobs == 0`.
  3. If the predicate becomes true within the window: exit cleanly, tray icon disappears.
  4. If the timeout expires: prompt the operator with *"1 operation still running. Force quit anyway?"* via the open window if alive, otherwise via an OS notification. Affordances: **[Force quit]** (kills the server immediately; in-flight NAS-sync jobs remain in the durable queue (§7.1) and resume on next launch; plugin operations in progress are killed and partial creations follow the crash-recovery rules in §4.8) and **[Wait]** (resets the 30-second timer; rechecks at the next idle moment).

**`window/`** is the on-demand process. Its modules:

- `main.py` — the `exlab-wizard-window` console_scripts entry point. Reads `<state_dir>/server.json`, validates the recorded PID is alive, then hands off to `pywebview_app`. If the state file is missing or stale, prints a helpful message and exits with non-zero status (the tray's `window_launcher` interprets this as "tray died; need to restart from scratch").
- `pywebview_app.py` — opens a single pywebview window pointed at `http://127.0.0.1:<port>`. Window title, size, and icon are configured here. Devtools enabled only in debug builds (gated by an `EXLAB_DEBUG` env var that release artifacts never set). Window-close → process exits cleanly.

**Why the split.** The window process being separate from the server process is what enables the closeable-window-without-quit-server UX. Putting pywebview directly into the tray process would couple them: pywebview's main thread is its event loop, and so is pystray's, and they can't share. Separate processes sidestep the threading conflict and let each library run its preferred pattern.

**Linux fallback.** See §15.7.4 for the canonical specification of the no-tray fallback procedure; Frontend §3.4.7 documents the operator-visible UX.

## 4.4 Component Contracts

These are the public surfaces between core components. Implementation may add internal methods; consumers may rely only on what is listed here.

### 4.4.1 CreationController

```python
class CreationController:
    async def create_project(self, req: ProjectCreateRequest) -> SessionHandle: ...
    async def create_run(self, req: RunCreateRequest) -> SessionHandle: ...
    async def resume(self, session_id: str, extra_inputs: dict[str, Any]) -> SessionHandle: ...
    async def cancel(self, session_id: str) -> None: ...
    async def status(self, session_id: str) -> SessionStatus: ...
    async def subscribe(self, session_id: str) -> AsyncIterator[SessionEvent]: ...
```

`SessionHandle` carries `session_id`, current `SessionState`, `current_phase`, and `next_action` (e.g. `"awaiting_input"`, `"none"`). Methods are `async` because they `await` Copier render, plugin worker exits, and NAS/LIMS I/O.

### 4.4.2 TemplateEngine

```python
class TemplateEngine:
    def resolve(self, template_name: str, scope: TemplateScope) -> ResolvedTemplate: ...
    async def render(self, tpl: ResolvedTemplate, dst: Path, variables: dict) -> RenderResult: ...
```

`render` calls `copier.run_copy(src_path=tpl.path, dst_path=dst, data=variables, overwrite=False, unsafe=False, quiet=True)`. **`unsafe=False`** is the explicit consequence of Solution A: any `_tasks` declared in a template's `copier.yml` are silently ignored. Templates SHOULD NOT declare `_tasks`; the v0.7 plugin lint check warns on them.

### 4.4.3 PluginHost

```python
class PluginHost:
    def reload_registry(self) -> RegistryReport: ...
    def candidates_for(self, file_paths: list[Path]) -> list[PluginPlan]: ...
    async def run_pass(
        self,
        ctx: PluginContext,
        file_paths: list[Path],
        plugin_order: list[str],   # from copier.yml _exlab_plugins
        on_input_required: Callable[[PluginInputRequiredPayload], Awaitable[dict]],
    ) -> PluginPassResult: ...
```

`run_pass` spawns one worker subprocess per matched plugin, drives its lifecycle, and returns aggregated results. `on_input_required` is the callback the controller wires to the WebSocket — when a worker raises `PluginInputRequired`, the host invokes it and `await`s the resume payload before re-spawning the worker.

### 4.4.4 Validator

```python
class Validator:
    def validate_creation(self, params: CreationValidationInput) -> list[Finding]: ...   # creation-time mode
    def audit(self, scope: AuditScope) -> list[Finding]: ...                              # audit mode
    def query_problems(self, scope: AuditScope) -> list[Finding]: ...                     # public alias for §11.8
```

`validate_creation` does no disk I/O on the destination (it doesn't exist yet); it operates on the proposed path, the variable map, and the post-render content for files about to be written. `audit` walks `local_root` (and `staging_root` in orchestrator mode), reading `creation.json` per directory and scanning rendered text files under the size cap. The Pre-Sync Gate ([[07_Sync_and_Database_Integration#7.3 Pre-Sync Gate|§7.3]]) calls `validate_creation` synchronously inline before `NASSyncClient.enqueue`; it does not wait for the next audit refresh. (See §4.7 `POST_VALIDATE` state.)

### 4.4.5 CacheWriter

All `.exlab-wizard/*` mutations go through `CacheWriter`. The class enforces:

- Tempfile + `os.replace` for every JSON write (atomic on POSIX; atomic-on-same-volume on Windows).
- Per-file advisory file lock on `creation.json` updates: `fcntl.flock(LOCK_EX)` on POSIX / `LockFileEx` (exclusive) on Windows. Three writers share `creation.json` (NAS sync module, Pre-Sync Gate, override action), and the lock makes their updates serializable.
- Append-only writes for `wizard.<hostname>.log`; one log file per `(hostname, equipment, project)` triple plus per-run logs (see §4.5 concurrency note).

```python
class CacheWriter:
    async def write_creation(self, path: Path, payload: CreationJson) -> None: ...
    async def update_creation_atomic(
        self,
        path: Path,
        mutator: Callable[[CreationJson], CreationJson],
    ) -> CreationJson: ...
    async def read_creation_snapshot(self, path: Path) -> CreationJson: ...
    async def append_log(self, log_path: Path, event: LogEvent) -> None: ...
    async def write_equipment(self, path: Path, payload: EquipmentJson) -> None: ...
    async def write_ingest(self, path: Path, payload: IngestJson) -> None: ...
```

**Lock-for-full-cycle requirement.** Every writer that mutates `creation.json` MUST hold the per-file `LOCK_EX` for the **entire** read-mutate-write cycle. This is the only way to prevent the lost-update race where two writers both read the file, both apply their mutator function in memory, and both write — last writer wins, silently losing the other's change. Implementation:

```python
# Inside CacheWriter.update_creation_atomic
with FileLock(path, mode="exclusive"):
    payload = json.loads(path.read_text())          # read inside the lock
    new_payload = mutator(payload)                  # mutate inside the lock
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(new_payload))         # write inside the lock
    os.replace(tmp, path)                           # atomic rename inside the lock
```

`update_creation_atomic` is the **only** API that mutates an existing `creation.json`; direct `write_creation` is reserved for initial creation when no file exists yet (and is also wrapped in `LOCK_EX` defensively, so the second writer gets `FileExistsError` rather than racing). Callers that only need to read may use `read_creation_snapshot`, which acquires `LOCK_SH` (shared / read lock); concurrent readers don't block each other but a `LOCK_EX` writer waits for active readers to release.

The integration test suite includes a concurrent-write fixture (`tests/integration/test_creation_json_concurrent_writes.py`) that spawns N tasks mutating the same `creation.json` simultaneously and asserts all N mutations are reflected in the final file.

**Caveat: `flock` is advisory.** Both POSIX `fcntl.flock` and Windows `LockFileEx` only serialize processes that *also* call the lock API. A rogue process that opens and writes the file directly without locking will race with us. Within the ExLab-Wizard process boundary this is enforced by the `CacheWriter` contract: no other module reads or writes `creation.json` directly. Cross-process scenarios (e.g. a script the operator runs in parallel) are out of contract and the operator is responsible.

### 4.4.6 NASSyncClient and LIMSClient

`NASSyncClient` is the in-process driver of the rebuilt NASSync queue (see [[07_Sync_and_Database_Integration#7.1 NAS Sync|§7.1]]):

```python
class NASSyncClient:
    async def enqueue(self, run_path: Path) -> SyncJobHandle: ...
    async def status(self, run_path: Path) -> SyncStatus: ...
    async def retry(self, job_id: str) -> None: ...
    async def force_verify(self, run_path: Path) -> VerifyResult: ...
```

`LIMSClient` is **read-only against the LIMS in v1** (Mapping B; see [[07_Sync_and_Database_Integration#7.2 LIMS Integration|§7.2]]). Project identity is bound at project-creation time via the LIMS-project picker; runs do not write to LIMS in v1.

```python
class LIMSClient:
    async def login(self, email: str, password: str) -> None: ...
    async def list_projects(self) -> list[LIMSProject]: ...   # cache-aware
    async def get_project(self, uid_or_short_id: str) -> LIMSProject | None: ...
    async def get_me(self) -> LIMSUser: ...
    async def health_check(self) -> HealthStatus: ...
```

The earlier `LIMSClient.register` and `LIMSClient.update_sync_status` methods are removed in v0.7. They will return in v1.x when the LIMS team ships a `runs` resource (§7.2.6 ask #2).

### 4.4.7 SessionStore

```python
class SessionStore:
    def open(self, kind: Literal["project", "run"], req: Any) -> Session: ...
    def get(self, session_id: str) -> Session | None: ...
    def transition(self, session_id: str, new_state: SessionState) -> None: ...
    def attach_event_queue(self, session_id: str, queue: asyncio.Queue) -> None: ...
    def close(self, session_id: str, outcome: SessionOutcome) -> None: ...
    def abandoned_older_than(self, age: timedelta) -> list[str]: ...
```

In-memory `dict[str, Session]` for v1. Session count is bounded by concurrent in-progress wizards within the single native window (typically ≤ 5); the GC pass (every 5 minutes) closes any session in `INPUT_REQUIRED` with no client heartbeat for >1 hour. Persisted recovery is **out of scope** for v1 — server crash forfeits in-flight sessions; the operator retries.

## 4.5 Async / Threading Model

| Concern | Mechanism |
|---|---|
| Web tier (REST + WebSocket) | Native FastAPI `async def`. NiceGUI page handlers are `async`. |
| Controller, PluginHost, CacheWriter | `async`. Plugin worker subprocesses managed via `asyncio.create_subprocess_exec`, awaited with timeouts (per-plugin `isolation.timeout_seconds`). |
| Validator | Synchronous functions. Called from async code via `asyncio.to_thread()`. CPU-bound but fast (regex over strings, bounded text-file scans). |
| Background audit refresh | An `asyncio` task started in the FastAPI lifespan; runs `Validator.audit("all")` every 30 s, publishes the diff to a pub-sub channel that the Problems-tab WebSocket reads. Manually re-triggerable via `POST /api/v1/problems/refresh`. |
| NAS sync | `async`; retry handled inside `NASSyncClient`. |
| LIMS reads | `async`; cache-aware via `lims/cache.py` (SQLite TTL cache). No LIMS writes in v1 (§7.2). |
| Tray event loop (pystray) | Runs on the **main thread**; the FastAPI/uvicorn server runs on its own asyncio loop in a worker thread. The tray callbacks (Open / Quit) interact with the server via thread-safe wrappers (`asyncio.run_coroutine_threadsafe` for cross-thread coroutine invocation). The status submenu is refreshed by a synchronous 5-second timer in the tray thread that reads atomic snapshots from `SessionStore`, `NASSyncClient`, and `Validator`. |
| OS notifications | Fired from the tray thread via `tray/notifications.py`. Server-side components publish notification-eligible events to a small in-process pub-sub queue; the tray thread consumes the queue. Notifications are coalesced (a burst of N sync failures within 5 s renders as one notification *"N sync failures"*). |
| Window subprocess management | The tray's `window_launcher.py` uses `subprocess.Popen` (no asyncio); the launcher polls `Popen.poll()` periodically to detect window exit. Window↔server communication is HTTP+WebSocket, not stdin/stdout IPC. |

The FastAPI lifespan (`@asynccontextmanager`) is responsible for: loading config, building the plugin registry once at startup, refreshing the LIMS project cache (best effort; failure does not abort startup), starting the audit task, and on shutdown closing in-flight sessions and waiting up to 5 s for plugin workers to terminate cleanly before SIGKILL.

**Concurrent log writes within orchestrator mode.** Resolves Should-resolve item #7 from the audit. In orchestrator mode the same hostname can drive concurrent creation sessions on different equipment. Equipment-level logs are file-per-equipment, so cross-equipment concurrency is naturally isolated. **Same-equipment** concurrency on a single hostname uses POSIX `O_APPEND` writes (atomic up to `PIPE_BUF`, which exceeds our line size of ~1 KiB) and the Windows equivalent via `FILE_APPEND_DATA` opened with `FILE_SHARE_WRITE`. Each log line is bounded to 1 KiB; longer messages are truncated with a continuation marker. Per-run logs are single-writer by definition (only one session creates a given run) and require no special handling.

## 4.6 Frontend ↔ Backend Protocol

The HTTP API and WebSocket channels are the only contract between the frontend (NiceGUI pages, or any future replacement) and the backend. NiceGUI handlers are thin wrappers that call the controller via these same endpoints when feasible, so behavior is identical whether driven from the UI or from a Playwright e2e test.

### 4.6.1 REST surface (illustrative subset; full schema lives in `api/schemas.py`)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/sessions` | Open a creation session (project or run). Body: resolved input bundle (template, equipment, variables, README fields, mode flag). Returns `{ session_id, state, next_action }`. |
| `GET` | `/api/v1/sessions/{id}` | Snapshot of session state. |
| `POST` | `/api/v1/sessions/{id}/resume` | Supply `extra_inputs` after `PluginInputRequired`. |
| `POST` | `/api/v1/sessions/{id}/cancel` | Abort. Triggers cleanup of partially-created directory; body carries `{ "discard_files": <bool> }` matching Frontend §9.4 (`true` deletes the partial directory, `false` preserves it as orphan). |
| `GET` | `/api/v1/operations` | List all in-flight controller operations (running, suspended in `INPUT_REQUIRED`, completed-pending-cleanup). Used by the Frontend Operations panel (§9.5). Each entry: `{ id, state, started_at, equipment_id, project_short_id, run_label, plugin_name?, suspended_reason? }`. |
| `GET` | `/api/v1/problems` | Validator findings (the §11.8 query). Query params: `scope`, `severity`, `class`. |
| `POST` | `/api/v1/problems/{run_path}/override` | Append a `validation_overrides` entry. Body: `{ problem_class, reason }`. |
| `POST` | `/api/v1/problems/{run_path}/override/revoke` | Append a tombstone (see §11.3 schema). |
| `POST` | `/api/v1/problems/refresh` | Force an audit pass now (skips waiting for the 30-s tick). |
| `GET` | `/api/v1/tree` | Equipment/project/run hierarchy for the browse view. |
| `GET` | `/api/v1/run/{path}` | Run detail (template, operator, sync status, run kind, README). |
| `GET` | `/api/v1/config` | Current `config.yaml` (with secrets redacted). |
| `PUT` | `/api/v1/config` | Validate + persist new config. Returns 422 on validation failure with per-field errors. |
| `GET` | `/api/v1/setup/status` | Setup-state and what's missing. Always available. See §4.9.3. |
| `POST` | `/api/v1/setup/test-lims` | Probes LIMS reachability with the currently-configured (or supplied) credentials. |
| `POST` | `/api/v1/setup/test-equipment` | Probes the per-equipment NAS transport (rclone or rsync_ssh). |
| `POST` | `/api/v1/setup/autostart` | Register or unregister the platform autostart entry. Body: `{ "enabled": <bool> }`. Calls `tray/autostart.py`; see §4.9.5 step 0 and §15.7. |
| `GET` | `/api/v1/health` | Component-health rollup. Always available. See §4.6.3. |

All POSTs return either a `SessionHandle`-like envelope or a structured error body matching the §8 error model.

### 4.6.2 WebSocket events

`WS /api/v1/sessions/{id}/events` — server → client, JSON-per-frame. The frame envelope is one of:

```json
{ "kind": "phase",          "phase": "rendering_template",  "at": "2026-05-05T12:34:56Z" }
{ "kind": "progress",       "phase": "running_plugins",     "current": 2, "total": 4 }
{ "kind": "input_required", "fields": [...],                "reason": "...", "plugin": "xlsx_field_filler" }
{ "kind": "warning",        "phase": "queueing_nas_sync",   "message": "..." }
{ "kind": "done",           "result": { "path": "...", "sync_status": "pending", "blocked": false } }
{ "kind": "failed",         "phase": "...",                 "error": { "code": "...", "message": "..." } }
```

The phase enum (v1):
`validating_inputs`, `rendering_template`, `running_plugins`, `writing_cache`, `validating_post_creation`, `queueing_nas_sync`, `done`. The Frontend Spec's earlier `registering_with_lims` phase is removed in v0.7 (no LIMS write per run; see [[07_Sync_and_Database_Integration#7.2 LIMS Integration|§7.2]]) and returns in v1.x when the LIMS gains a `runs` resource.

`WS /api/v1/problems/events` — pub-sub channel for the always-on Problems tab. Client receives a full snapshot on connect, then `delta` frames on each background audit pass:

```json
{ "kind": "snapshot", "findings": [...], "audit_at": "..." }
{ "kind": "delta",    "added": [...], "removed": [...], "changed": [...], "audit_at": "..." }
```

### 4.6.3 API versioning, health, and error envelope

**Versioning.** All API routes are prefixed with `/api/v<MAJOR>/`. v1 is `/api/v1/...`. A breaking change to any existing route (request schema change, response schema change, semantic change, removal) bumps to `/api/v2/...`. Old major versions remain available alongside the new one for at least one full ExLab-Wizard major release after the bump (so v1 API stays available while v2 API ships in ExLab-Wizard v2). Additive changes (new fields, new endpoints, new optional query params) do **not** require a version bump; clients are expected to ignore unknown fields.

What counts as breaking:
- Removing a field from a response.
- Changing a field's type or value semantics.
- Renaming a field, route, or query parameter.
- Removing a route or method.
- Changing a status code from non-error to error or vice versa.

What does **not** count as breaking:
- Adding a field to a response.
- Adding an optional query parameter.
- Adding a new route.
- Adding a new value to an open-ended enum (e.g. a new `phase` value in WebSocket events). Closed enums (`run_kind: "experimental" | "test"`) are versioned strictly.

**Health endpoint.** `GET /api/v1/health` returns a component-health rollup. Always available, regardless of setup state (§4.9). Used by the launcher's "is the server up" check, by external monitoring (e.g. a wrapper script), and by the Settings dialog's diagnostics page. Response shape:

```json
{
  "status": "ok",
  "version": "1.0.0",
  "schema_versions": {
    "creation_json": "1.7",
    "readme_fields_json": "1.1",
    "ingest_json": "1.1"
  },
  "components": {
    "validator":    { "status": "ok",   "last_audit_at": "2026-05-05T12:34:00Z" },
    "nas_sync":     { "status": "ok",   "queue_depth": 3,  "in_flight": 1 },
    "lims":         { "status": "warn", "reason": "unreachable; using cache" },
    "plugin_host":  { "status": "ok",   "registered_plugins": 8 },
    "session_store": { "status": "ok",  "active_sessions": 1, "input_required": 0 }
  },
  "setup_state": "READY"
}
```

Per-component `status` is `"ok"` | `"warn"` | `"error"`. Top-level `status` is the most severe of the components: `"ok"` if all are ok, `"warn"` if any warn (and none error), `"error"` if any error. The HTTP status code is always 200 — `/health` does not use HTTP semantics to signal degradation, because monitors that retry on non-200 would thrash. Top-level `status` is the contract.

**Error envelope.** Every error response across the API uses the same JSON body shape:

```json
{
  "error": {
    "code": "validation_failed",
    "message": "Operator field cannot be empty.",
    "field": "operator",
    "details": { "min_length": 1 },
    "trace_id": "abc123def456"
  }
}
```

Required: `code` (stable string identifier; this is what client code branches on), `message` (human-readable). Optional: `field` (for field-level validation errors), `details` (free-form structured detail), `trace_id` (echoed back from the request's `X-Trace-Id` header if present, else server-generated; used to correlate with the central app log).

The `code` enum is open-ended and additive: new codes can appear without a version bump. Documented codes include: `setup_incomplete`, `validation_failed`, `plugin_variable_validation_failed`, `template_load_error`, `lims_unreachable`, `keyring_unavailable`, `session_not_found`, `session_already_completed`, `nas_sync_failed`, and the equipment-id and field-length validation errors. Component sections in this spec define their own codes; the full enum is enumerated in `api/schemas.py` (see [[#4.3 Package Layout|§4.3]]).

HTTP status codes used: `200` (success), `201` (created — sessions, overrides), `204` (delete), `400` (validation), `401` (auth — only for the LIMS-passthrough case if any), `404` (not found), `409` (conflict — e.g. session already in terminal state when resume requested), `422` (unprocessable — schema-valid but semantically invalid), `503` (setup incomplete or component unavailable). No `5xx` other than `503`; uncaught exceptions are caught at the FastAPI exception-handler boundary and surfaced as `500` with `code: "internal_error"` and a `trace_id` for log correlation.

## 4.7 Creation-Session State Machine

```
                    create_project / create_run
                              │
                              ▼
                       ┌──────────────┐
                       │   PENDING    │
                       └──────┬───────┘
                              │ controller.start()
                              ▼
                       ┌──────────────┐
                       │  VALIDATING  │  Validator.validate_creation()  (creation-time mode, no FS)
                       └──────┬───────┘
                       fail───┴───pass
                          │       │
                          ▼       ▼
                       ┌──────┐ ┌──────────────┐
                       │FAILED│ │  RENDERING   │  TemplateEngine.render() (Copier in-process)
                       └──────┘ └──────┬───────┘
                                       ▼
                                ┌──────────────┐
                                │ PLUGIN_PASS  │  PluginHost.run_pass()
                                └──┬────────┬──┘
                                   │        │ PluginInputRequired
                                   │        ▼
                                   │  ┌──────────────────┐
                                   │  │ INPUT_REQUIRED   │ ← held; emits ws "input_required"
                                   │  └────────┬─────────┘
                                   │           │ POST /sessions/{id}/resume
                                   │           ▼
                                   │     PLUGIN_PASS (re-enter trigger plugin's worker only)
                                   │
                                   ▼
                           ┌──────────────┐
                           │ CACHE_WRITE  │  CacheWriter.write_creation() + ReadmeGenerator
                           └──────┬───────┘
                                  ▼
                           ┌──────────────┐
                           │ POST_VALIDATE│  Validator.validate_creation() second pass over rendered tree
                           └──────┬───────┘  (catches plugin-introduced findings)
                                  │ pass: enqueue
                                  │ fail: skip enqueue, set sync_status="blocked_by_validation"
                                  ▼
                           ┌──────────────┐
                           │ SYNC_QUEUED  │  NASSyncClient.enqueue()  (only if POST_VALIDATE passed)
                           └──────┬───────┘
                                  ▼
                           ┌──────────────┐
                           │     DONE     │
                           └──────────────┘
```

Notes:

- **No `LIMS_REGISTER` state in v1.** ExLab-Wizard is read-only against the LIMS in v1 (Mapping B; see [[07_Sync_and_Database_Integration#7.2 LIMS Integration|§7.2]]). Project identity is bound at project-creation time via the LIMS-project picker; run creation does not touch the LIMS. When the LIMS team ships a `runs` resource (§7.2.6 ask #2), this state returns in v1.x between `CACHE_WRITE` and `POST_VALIDATE`.
- **`POST_VALIDATE` is a synchronous second pass** that runs the same validator engine over the rendered tree, picking up findings that plugins introduced (e.g. a buggy `filename_renamer` produces an illegal path). This resolves Should-resolve item #4: the Pre-Sync Gate runs synchronously inline at this state, not as a side-effect of the next 30-s audit.
- `cancel` from any non-terminal state transitions to `ABORTED` and runs the cleanup hook (§8: partially-created directories are removed). Mutated files left by earlier successful plugin transforms are part of that directory and are removed wholesale.
- `failed` at any step transitions to `FAILED` with a structured error event over the WebSocket and the same cleanup hook.
- `INPUT_REQUIRED` is the only state held indefinitely. The session-store GC closes any `INPUT_REQUIRED` session with no client heartbeat for >1 hour as `ABORTED`.

### 4.7.1 SessionState → Phase mapping

The internal `SessionState` enum (`controller/state_machine.py`) and the externally-emitted `Phase` enum (`api/events.py`, sent over the WebSocket per §4.6.2) are two distinct enums and must stay synchronized. The internal state has additional values that don't emit a phase event (transitional, terminal-error, or holding states). The mapping is:

| Internal `SessionState` | Emitted `Phase` event over WebSocket |
|---|---|
| `PENDING` | (none — not yet started) |
| `VALIDATING` | `validating_inputs` |
| `RENDERING` | `rendering_template` |
| `PLUGIN_PASS` | `running_plugins` |
| `INPUT_REQUIRED` | `input_required` (via the `kind: "input_required"` event envelope, not a `phase` frame) |
| `CACHE_WRITE` | `writing_cache` |
| `POST_VALIDATE` | `validating_post_creation` |
| `SYNC_QUEUED` | `queueing_nas_sync` |
| `DONE` | `done` (via the `kind: "done"` envelope) |
| `FAILED` | (none — emits `kind: "failed"` envelope instead) |
| `ABORTED` | (none — session closes silently from the WebSocket's perspective) |

Both enums live in `exlab_wizard/controller/state_machine.py` (defined together so a refactor of one is visible against the other in code review). The mapping function `state_to_phase(state: SessionState) -> Phase | None` is the single point that knows the relationship; every code path that emits a phase event calls it. New values added to either enum require updating the mapping table here AND adding a corresponding entry in `state_to_phase`.

## 4.8 Crash Recovery

Three concrete scenarios with explicit policy:

1. **Crash after FS write, before `creation.json` write.** The directory exists with no `creation.json`. The audit-mode validator's orphan rule ([[08_Error_Handling_Principles#8.1.4 Orphan rule (soft tier)|§8.1.4]]) catches it on next audit. **Recovery action:** none automatic. The Problems tab surfaces the orphan; the operator decides whether to delete or salvage. The app does **not** auto-delete because the directory may already contain valuable acquisition data the operator has begun to populate.

2. **Crash mid-plugin-pass.** The directory exists with partially-rendered files; `creation.json` does not yet; the plugin worker is dead. **Recovery action:** same as scenario 1. Surfaced as an orphan; operator-decided.

The session store is **not** persisted across restarts. All in-flight sessions are lost on crash. The operator retries. Acceptable for v1 because creation is foreground; revisit if v2 introduces unattended workflows.

(Earlier drafts described a "Crash after `creation.json` write, before LIMS write" recovery scenario. With v0.7's read-only LIMS integration there is no LIMS write per run, so the scenario does not exist. It returns in v1.x alongside the `LIMS_REGISTER` state.)

**Schema implications for v0.7.** `creation.json` schema_version is `1.7` (see [[11_Cache_Folders#11.3 `creation.json` Schema|§11.3]] history table). Readers expecting older versions ignore unknown fields per the migration policy in [[11_Cache_Folders#11.9 Schema Versioning and Migration Policy|§11.9]].

## 4.9 First-Launch and Setup-Incomplete State

The FastAPI app starts cleanly even when `config.yaml` doesn't exist or is incomplete. The launcher does not refuse to boot; the controller refuses to *create* runs until setup is complete. This separation lets the frontend onboarding flow drive setup without bootstrapping a separate executable.

### 4.9.1 Setup states

The app maintains a single computed enum at startup (and after every `PUT /api/v1/config`):

| State | Meaning |
|---|---|
| `INCOMPLETE_NO_CONFIG` | `config.yaml` does not exist at the OS-appropriate path ([[09_Configuration_File|§9]]). |
| `INCOMPLETE_MISSING_PATHS` | `config.yaml` exists but `paths.local_root`, `paths.templates_dir`, or `paths.plugin_dir` is unset, missing, or unreadable. |
| `INCOMPLETE_NO_EQUIPMENT` | Paths are valid but the `equipment` list is empty. |
| `INCOMPLETE_NO_LIMS` | Equipment is configured but `lims.endpoint` or `lims.email` is unset, OR the keyring/encrypted-store has no password under `(service="exlab-wizard", username="lims")`. |
| `INCOMPLETE_LIMS_UNREACHABLE` | LIMS configuration is complete but `LIMSClient.health_check()` fails on startup. (This is a soft block; setup proceeds, but operator sees a banner. See §4.9.4.) |
| `READY` | Every preceding gate passes. |

States are evaluated in the order listed; the first failing gate is reported and subsequent gates are not evaluated. This is intentional — it produces a single concrete next-step for the onboarding UI.

### 4.9.2 Endpoint gating

While in any `INCOMPLETE_*` state:

| Endpoint | Behavior |
|---|---|
| `GET /api/v1/setup/status` | Returns the current setup state and a structured list of what's missing. Always available. |
| `GET /api/v1/config` | Returns current `config.yaml` (sanitized — secrets never returned). Always available. |
| `PUT /api/v1/config` | Validates and persists. On success, re-evaluates setup state and returns the new state. Always available. |
| `POST /api/v1/setup/test-lims` | Triggers `LIMSClient.health_check()` against the currently-configured (but possibly not-yet-saved) LIMS settings. Used by the Settings dialog's "Test connection" affordance. |
| `POST /api/v1/setup/test-equipment` | Triggers a transport probe (`rclone lsd <remote>:` or `ssh -o BatchMode=yes <target> true`) against an equipment configuration. |
| `GET /api/v1/health` | Always available. See §4.6.3. |
| `POST /api/v1/sessions` (creation flow) | Returns `503 Service Unavailable` with `error.code: "setup_incomplete"`, `error.state: <enum>`, `error.missing: [...]`. The Wizard UI is expected to consult `/setup/status` first and surface the onboarding flow. |
| `GET /api/v1/problems`, `GET /api/v1/tree`, etc. | Same: 503 with `setup_incomplete`. |

Once `READY`, all endpoints work as specified elsewhere. No endpoint behavior depends on whether the app *was* incomplete at startup; once setup completes mid-session, creation flows immediately become available without an app restart.

### 4.9.3 `GET /api/v1/setup/status` response shape

```json
{
  "state": "INCOMPLETE_MISSING_PATHS",
  "missing": [
    { "field": "paths.local_root", "reason": "unset" },
    { "field": "paths.templates_dir", "reason": "unset" }
  ],
  "next_action": "set_paths",
  "ready": false
}
```

When `ready: true`, the response is `{ "state": "READY", "missing": [], "next_action": null, "ready": true }`. The `next_action` is one of `set_paths`, `add_equipment`, `configure_lims`, `test_lims`, or `null`; the onboarding UI uses it to decide which step to render.

### 4.9.4 LIMS unreachability is a soft block

`INCOMPLETE_LIMS_UNREACHABLE` is the only `INCOMPLETE_*` state that does not gate creation flows. Rationale: a lab on a fully-offline acquisition machine may have valid LIMS credentials configured but no network path to the LIMS at the moment of first launch. The operator should not be locked out of creating runs because of a transient network condition; the LIMS project list is cache-backed (§7.2.4) and works offline once seeded.

So: when state evaluation reaches `INCOMPLETE_LIMS_UNREACHABLE`, the controller treats it as `READY` for endpoint-gating purposes but the `/setup/status` endpoint surfaces `state: "INCOMPLETE_LIMS_UNREACHABLE"` so the frontend can render a banner. The "test connection" button is the operator's recourse; once it succeeds, the state moves to `READY`.

### 4.9.5 Initial setup ordering (informational)

The onboarding flow is a frontend concern (Frontend Spec §3.1), but the backend's setup-state ordering implicitly suggests this sequence:

0. **(First-launch only) Autostart prompt.** The welcome card (Frontend §3.1.3) asks the operator whether to register ExLab-Wizard to start at user login. The operator's answer is persisted via `POST /api/v1/setup/autostart` (body: `{ "enabled": <bool> }`), which calls `tray/autostart.py` to register or unregister the platform-specific autostart entry (§4.3.2, §15.7). The toggle defaults to **on**; both the welcome card's primary "Get started" button and the secondary "Skip for now" link send the operator's current toggle state to the endpoint, so there is no path that dismisses the prompt without sending. Reversible from `Settings → Application` at any time.
1. Set `paths.local_root`, `paths.templates_dir`, `paths.plugin_dir`. Bundled starter templates are copied into `paths.templates_dir` if the operator agrees ([[15_Distribution#15.4 Bundled Starter Content|§15.4]]).
2. Add at least one equipment with a transport configuration. The "Test connection" button verifies the transport works before saving.
3. Set LIMS endpoint and operator email. Set the LIMS password via the keyring affordance (Settings dialog or onboarding equivalent). Alternatively, set `lims.offline_catalogue_path` for an offline workstation (§7.2.9).
4. (Optional) Test LIMS connection. Even on failure, setup is complete; the LIMS-unreachable banner appears in the main UI.

Each step ends with a `PUT /api/v1/config` that persists the partial config and triggers a re-evaluation of `setup/status`. The frontend can render the next step based on the returned `next_action`.

**Boot-flow relationship to setup state.** First launch (no `config.yaml`) brings up the tray + window pair as usual; the tray is registered to autostart only AFTER the operator's autostart-prompt response (step 0). Subsequent launches with INCOMPLETE_* state still bring up the tray + window — the operator can complete setup via the Settings dialog without losing background server lifecycle.

## 4.10 Testing Strategy

The app has three test layers, each scoped to a different boundary.

### 4.10.1 Unit tests (`tests/unit/`)

Mirror the package layout under `exlab_wizard/`. Each module under test gets a corresponding test module (e.g. `exlab_wizard/validator/rules.py` ↔ `tests/unit/validator/test_rules.py`). Unit tests:

- Run synchronously via `pytest`. Async-only code uses `pytest-asyncio` with function-scoped event loops.
- Use no real filesystem (paths are constructed in-memory via `pathlib.PurePath`) where possible; when filesystem behavior matters, use `pytest`'s `tmp_path` fixture.
- Use no external processes. Plugin worker tests exercise the worker entry point in-process via direct function call, not subprocess.
- Use no real network. `LIMSClient`, `NASSyncClient`, and the keyring backend are mocked via injection; no `httpx`-level mocks.

Coverage target: every public method on every component contract (§4.4) has at least one happy-path test and one failure-path test. Validator rules (§8.1) have one fixture per finding shape, exercising both creation-time and audit modes.

### 4.10.2 Integration tests (`tests/integration/`)

Exercise the FastAPI app end-to-end, but with external systems stubbed:

- The app is started in-process via `httpx.AsyncClient(app=app)` — no uvicorn, no real port.
- **LIMS:** a small FastAPI fixture app implementing the OCaml LIMS's `/api/v1/login`, `/api/v1/me`, `/api/v1/projects`, and `/api/v1/projects/{id}` endpoints with in-memory state. Lives in `tests/fixtures/mock_lims.py`. The test runs both apps as separate `AsyncClient`s; the ExLab-Wizard app's `LIMSClient` is configured to point at the mock.
- **NAS / rclone:** a stub `rclone` binary written in Python and registered on the test PATH. Returns success/failure based on a per-test config file. Lives in `tests/fixtures/stub_rclone.py`.
- **NAS / rsync:** same approach via a stub `rsync` binary.
- **Keyring:** `keyring.set_keyring(InMemoryKeyring())` at test setup.
- **Filesystem:** `tmp_path` for `local_root`, templates dir, plugin dir.

Integration tests cover: full creation flow (project + run + test run), `PluginInputRequired` suspend/resume, NAS sync queue lifecycle, validator gate behavior, override + revoke flows, LIMS picker + cache, setup-incomplete state transitions.

### 4.10.3 End-to-end tests (`tests/e2e/`)

Drive the real browser against a real localhost server using Playwright. The test harness:

- Spawns a real `uvicorn` process bound to a free port.
- Provides the same mocks as integration tests for LIMS / NAS / keyring (the launcher has a `--testing` flag that loads stub backends from `tests/fixtures/`).
- Uses Playwright's Python bindings to drive Chromium against `http://127.0.0.1:<port>`.
- Asserts both DOM state and side effects on the filesystem / mock LIMS / mock NAS queue.

Coverage target: every wizard flow (project creation, run creation, test-run creation), the Problems tab interactions (override / revoke), Settings dialog, and the onboarding flow when present.

### 4.10.4 Test fixtures (`tests/fixtures/`)

Shared across all three layers:

- `templates/` — minimal Copier templates for project and run scopes, valid against the v0.7 manifest.
- `plugins/` — minimal plugins exercising every contract surface (success, `PluginError`, `PluginInputRequired`, timeout, policy violation).
- `configs/` — pre-built `config.yaml` files for each setup state (`incomplete_no_paths.yaml`, `complete.yaml`, etc.).
- `lims_data/` — JSON fixtures the mock LIMS server returns.
- `mock_lims.py`, `stub_rclone.py`, `stub_rsync.py`, `inmemory_keyring.py` — the stub implementations.

### 4.10.5 What's NOT tested at each layer

- Unit: no Copier rendering (use a stubbed render that just writes a fixed file tree).
- Integration: no browser; no real subprocess for plugins (the in-process worker entry is exercised, but the subprocess spawn path itself is unit-tested separately with a real subprocess against a known-good plugin).
- E2E: no validator-rule edge cases (those live in unit tests; e2e exercises only the critical paths).

This split keeps the e2e suite small enough to run on every PR (target: under 2 minutes) while pushing exhaustive case coverage into the faster unit and integration suites.
