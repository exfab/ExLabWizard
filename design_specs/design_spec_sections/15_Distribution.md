# 15. Distribution and Installation

Parent: [[ExLab-Wizard_Design_Spec]]

This section specifies how the ExLab-Wizard binary reaches a lab workstation and how the operator launches it. It covers the v1 build pipeline (PyInstaller), the code-signing posture (unsigned for v1, signing planned for v1.1), and the wrinkles introduced by fully-offline acquisition machines.

## 15.1 Build Pipeline (PyInstaller)

The v1 binary is produced by [PyInstaller](https://pyinstaller.org). One build per target OS produces one artifact per OS. PyInstaller bundles a private CPython interpreter and every dependency into the artifact, so the target machine does not need a system Python install.

| Target | Artifact (zip/dmg of a folder, NOT a one-file binary) | Build host |
|---|---|---|
| Windows x64 | `ExLab-Wizard/` directory containing three executables (`ExLab-Wizard-Tray.exe`, `ExLab-Wizard-Window.exe`, `ExLab-Wizard.exe`) + `_internal/`, packaged as `ExLab-Wizard-<version>-win-x64.zip` | Windows runner |
| macOS arm64 | `ExLab-Wizard.app` bundle (a directory) inside `ExLab-Wizard-<version>-mac-arm64.dmg`; the app bundle's `Contents/MacOS/` carries all three executables | macOS arm64 runner |
| macOS x64 | `ExLab-Wizard.app` bundle inside `ExLab-Wizard-<version>-mac-x64.dmg` | macOS x64 runner |
| Linux x64 | `ExLab-Wizard/` directory inside `ExLab-Wizard-<version>-linux-x64.tar.gz`; carries all three executables | Linux runner |

**Three entry points per artifact** (matching the package split in §4.3.2):

- `ExLab-Wizard-Tray` (`exlab-wizard-tray`) — the long-lived tray + server process. The autostart entry points at this binary.
- `ExLab-Wizard-Window` (`exlab-wizard-window`) — the on-demand pywebview window subprocess. Spawned by the tray; not normally invoked by the operator.
- `ExLab-Wizard` (`exlab-wizard`) — CLI alias that signals the running tray to open its window (or starts the tray if not running). This is the binary the operator double-clicks from the file manager and the one referenced by `.desktop` files / Start menu shortcuts / Dock icons.

Why PyInstaller (chosen over native installers):

- One toolchain per OS rather than three (PyInstaller, plus WiX for `.msi`, plus `pkgbuild` / `productbuild` for `.pkg`, plus `dpkg-buildpackage` for `.deb`).
- Output is a self-contained directory; the operator can copy it to a USB stick and walk it onto a fully-offline acquisition machine.
- Native installers (`.msi`, `.pkg`, `.deb`) can wrap the PyInstaller output later if lab IT requires installer-style deployment; the directory is the authoritative artifact, the installer is a thin shell.

**PyInstaller is invoked in `--onedir` mode** on every platform (not `--onefile`). Three entry-point executables share one `_internal/` subdirectory holding the bundled CPython interpreter, third-party libraries, and our bundled starter content (§15.4). The trade-off: operators see a folder, not a single double-clickable file. The benefits over `--onefile`:

- **Plugin worker subprocess re-launch works correctly.** `sys.executable` resolves to the real launcher binary, and `python -m exlab_wizard.plugins._worker` (called by the host to spawn workers; §6.3) re-enters the same bundled interpreter without re-extracting the bundle. With `--onefile` builds, `sys.executable` points at a self-extracting stub; calling it `-m`-style re-extracts the entire bundle into a temp directory per worker, multiplying startup latency.
- **Window subprocess re-launch works correctly.** The tray's `subprocess.Popen` of `ExLab-Wizard-Window` uses the bundled interpreter directly, no re-extraction.
- **Faster cold start** (no bundle extraction at launcher startup).
- **Easier debugging.** The operator (or lab IT) can inspect `_internal/` to see which Python and which libraries shipped.

Distribution wrappers (`.zip` / `.dmg` / `.tar.gz`) preserve the directory structure. macOS gets a proper `.app` bundle (`--windowed`) which is itself a directory under the hood; users still see one icon to launch.

The PyInstaller spec file (`exlab_wizard.spec`) lives at the repo root. It pins:

- Three entry-point definitions sharing the same `_internal/` collection (PyInstaller multi-app bundling — `MERGE` directive).
- `hiddenimports` for `nicegui`, `pywebview`, `pystray`, `plyer`, `keyring` backends, and any plugin dynamic imports the static analyzer misses.
- A `--add-data` for the bundled NiceGUI static assets (CSS/JS) so the UI renders offline.
- A `--add-data` for `templates/` and `plugins/` (the bundled starter content; §15.4).
- A `--add-binary` for the bundled `rclone` binary (§15.5).
- A `--add-binary` for the platform-specific webview shim where pywebview needs help finding it (Windows: `WebView2Loader.dll`; Linux: GTK-WebKit hooks).
- The icon (per OS) and per-OS metadata (CFBundleIdentifier on macOS, app manifest on Windows).

## 15.2 Code Signing Posture

**v1 ships unsigned.** The cost is a one-time per-machine click-through on Windows and macOS; Linux does not have a desktop-app signing concept that affects this.

| OS | Unsigned behavior | Operator action required |
|---|---|---|
| Windows 10/11 | SmartScreen warns: *"Windows protected your PC."* On a machine connected to the internet, the warning may be more aggressive (reputation lookup). On a fully-offline machine, the warning falls back to local policy and is typically just the click-through. | Click "More info" → "Run anyway." If lab IT enforces a strict AppLocker / SmartScreen policy that blocks unsigned binaries entirely, IT must allowlist `ExLab-Wizard.exe` (by hash or path) once. |
| macOS 13+ | Gatekeeper blocks the first launch: *"`ExLab-Wizard.app` cannot be opened because the developer cannot be verified."* | Right-click `ExLab-Wizard.app` → Open → confirm. macOS remembers the decision per app per user; subsequent launches go through normally. |
| Linux | No signature check at the desktop level. `chmod +x` and run. | None. |

This posture is a deliberate v1 trade-off. Code signing on Windows costs ~$200-500/year (standard cert) or more for an EV cert that bypasses SmartScreen reputation. macOS notarization requires an Apple Developer account ($99/year) plus the notarization workflow in CI. We will revisit in v1.1.

### 15.2.1 Offline-machine specifics

Some lab acquisition machines are fully offline (no internet, only LAN to NAS over ethernet). For these machines:

- **Windows SmartScreen** cannot reach Microsoft's reputation service to query the binary. SmartScreen falls back to local policy. In practice, this often means the warning is simpler (no "this file was downloaded from the internet" reputation cue) and the click-through is the same.
- **macOS Gatekeeper notarization check** uses an OCSP/CRL call by default. With *stapled* notarization (notarization ticket embedded in the bundle) the check works offline; without notarization there is no check to perform, and the right-click-Open dance applies.
- **Auto-updates** do not work on offline machines. v1 has no auto-update mechanism. Operators copy the new binary in via USB or LAN file share.

For an offline acquisition machine the recommended install flow is:

1. Lab IT downloads the artifact on an internet-connected machine.
2. IT (or the operator) copies the artifact to the offline machine via USB stick or internal LAN file share.
3. First-launch click-through (Windows SmartScreen / macOS right-click-Open).
4. The app runs fully offline; NASSync uses the LAN-connected NAS via ethernet.

## 15.3 Launcher Behavior

There are three entry-point binaries per artifact (§15.1). Their behavior:

### 15.3.1 `ExLab-Wizard-Tray` (long-lived, autostart target)

1. Parses CLI args (`--config <path>`, `--log-level <level>`, `--no-autostart-prompt`). The `--testing` flag is recognized only in development builds and is absent from release artifacts ([[04_Backend_Architecture#4.10.3 End-to-end tests|§4.10.3]]).
2. Loads `config.yaml` from the OS-appropriate path (§9). Missing or incomplete config is permitted; the tray starts the server in setup-incomplete state and the welcome card / setup-incomplete banner takes over from the window side (Frontend §3.1).
3. Picks a free localhost port (no fixed default; OS-allocated). Starts `uvicorn` bound to `127.0.0.1:<port>`.
4. Writes `<state_dir>/server.json` atomically: `{ "port": <int>, "pid": <int>, "started_at": <iso8601> }`. State directory is OS-appropriate (`~/Library/Application Support/exlab-wizard/state/` on macOS, `%LOCALAPPDATA%\exlab-wizard\state\` on Windows, `$XDG_STATE_HOME/exlab-wizard/` on Linux with the standard `~/.local/state/exlab-wizard/` fallback).
5. Registers a pystray system-tray icon with menu **Open** (focuses or spawns `ExLab-Wizard-Window`), status submenu (live state from `SessionStore` / `NASSyncClient` / `Validator`), **Quit** (graceful shutdown via `tray/quit_coordinator.py`).
6. **First-launch only:** spawns the window directly (the welcome card guides the operator through setup including the autostart-prompt; Frontend §3.1.3).
7. **Subsequent launches:** does NOT auto-spawn the window. The operator clicks the tray's **Open** to bring it up. (Rationale: an autostart-launched tray would otherwise pop a window every login.)
8. Stays in the foreground until tray **Quit** or OS shutdown signal.

On unexpected exit (crash or SIGKILL), the `<state_dir>/server.json` may be left behind. The next tray launch detects the stale file (recorded PID is not running) and overwrites it.

### 15.3.2 `ExLab-Wizard-Window` (on-demand)

Spawned by the tray's `window_launcher.py`; not normally invoked by the operator.

1. Parses CLI args (`--debug` to enable pywebview devtools — debug builds only).
2. Reads `<state_dir>/server.json`. If missing or the recorded PID is not alive, exits with status 2 and a one-line message to stderr (the tray interprets this as "tray died; restart").
3. Opens a single pywebview window pointed at `http://127.0.0.1:<port>` from the state file. Window title = `"ExLab-Wizard"`, default size 1280×800 (operator-resizable), icon per OS.
4. Runs the pywebview event loop until the window closes.
5. Exits when the window closes.

### 15.3.3 `ExLab-Wizard` (CLI alias / desktop-launcher target)

The binary the operator double-clicks from a file manager, the Start Menu, or the Dock.

1. Reads `<state_dir>/server.json`.
   - If present and PID alive: signals the tray to invoke its **Open** action (the tray spawns or focuses the window). This binary then exits 0.
   - If absent or stale: spawns `ExLab-Wizard-Tray` as a detached background process (using platform-appropriate detachment: `fork` + `setsid` on POSIX, `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS` on Windows), then exits 0.
2. CLI args mirror `ExLab-Wizard-Tray`'s for cases where the operator wants to start the tray with non-default config from the command line.

The "open existing tray vs. start new tray" branch is what makes the desktop launcher feel like a normal app: clicking the icon either brings up the window or starts everything fresh, with no visible double-click distinction to the operator.

### 15.3.4 Linux fallback (no system tray)

See §15.7.4 for the canonical specification of the no-tray fallback procedure (when `pystray` cannot register an icon, the tray spawns the window directly and exits with it). Frontend §3.4.7 documents the operator-visible UX.

## 15.4 Bundled Starter Content

The PyInstaller bundle ships a small read-only starter set inside `_internal/`:

- `_internal/templates/` — one equipment template, one generic project template, and one or two run templates demonstrating the API. On first launch the onboarding flow offers to copy these into `paths.templates_dir`. The bundled copies remain read-only inside the app bundle as a recovery reference; the user-writable copies in `paths.templates_dir` are what the wizards actually use. See [[05_Template_Format#5.0 Template Locations (Global and Per-Equipment)|§5.0]].
- `_internal/plugins/` — the canonical scaffolds: `hello_plugin` ([[06_Plugin_System#6.5 Base Plugin Scaffold (`hello_plugin`)|§6.5]]) and the worked example `xlsx_field_filler` ([[06_Plugin_System#6.6 Worked Example: `xlsx_field_filler`|§6.6]]). The plugin host scans both `_internal/plugins/` (read-only, bundled) AND `paths.plugin_dir` (lab-writable) and merges into one registry. On name collision, the lab plugin wins.

This gives operators a working app on day one with no separate provisioning step, while leaving full control to extend or replace.

## 15.5 Bundled vs External Binaries

Some dependencies are external binaries that PyInstaller does not bundle by default:

- **rclone.** The NASSync rclone transport (§7.1.3) shells out to `rclone`. v1 bundles a `rclone` binary inside the PyInstaller artifact at `_internal/bin/rclone[.exe]` and prefers it over any system `rclone` to avoid version skew. The bundled version is pinned in the build spec.
- **rsync / ssh.** The rsync-over-SSH transport relies on system `rsync` and `ssh`. These are pre-installed on macOS and Linux; on Windows the operator must install OpenSSH (Microsoft-supplied feature on Windows 10+) and `rsync` (typically via `cwRsync`, MSYS2, or WSL). Settings dialog runs a probe at startup and surfaces a clear error if either is missing.
- **Python plugin runtime.** Plugin workers run via `python -m exlab_wizard.plugins._worker` (§4.3). On a PyInstaller install, the plugin worker invocation goes to the bundled CPython, not a system Python — `sys.executable` resolves to the launcher binary, which is a thin wrapper around the bundled CPython at `_internal/python` (or `_internal/Python.framework/Versions/Current/Python` on macOS). This re-launch works cleanly because we ship in `--onedir` mode (§15.1); `--onefile` would re-extract the entire bundle on each worker spawn and was rejected for that reason.
- **pywebview platform webview.** pywebview itself is bundled as a Python wheel, but it depends on the platform's native webview at runtime:
  - **macOS:** WebKit is part of the OS; no extra dependency.
  - **Windows:** [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/). Pre-installed on Windows 11 and recent Windows 10 builds; older Windows 10 may need the Evergreen Bootstrapper. The launcher detects a missing WebView2 runtime at first launch and surfaces a one-time installation prompt with a download link (the WebView2 bootstrapper is small and can be vendored alongside the artifact for fully-offline install). This check lives in `tray/main.py` before pystray initialization.
  - **Linux:** GTK-WebKit (`webkit2gtk-4.0` or `webkit2gtk-4.1` depending on distro). Most desktop distributions include it; headless / minimal installs may not. Detected at startup; missing-webkit error directs the operator at the distro-appropriate package name.
- **pystray.** Bundled as a Python wheel. Per-platform tray APIs are shimmed by pystray itself (Cocoa on macOS, win32 on Windows, AppIndicator/StatusNotifier on Linux). No external runtime dependency on macOS or Windows. Linux requires either AppIndicator (most desktop environments) or a working StatusNotifier daemon — see §15.3.4 for the no-tray fallback.
- **plyer.** Bundled as a Python wheel for cross-platform notifications. Uses platform-native APIs internally (UNUserNotificationCenter on macOS 12+, ToastNotificationManager on Windows 10+, libnotify/notify-send on Linux). No external runtime dependency on macOS or Windows.

## 15.6 Versioning and Release Artifacts

- Semantic versioning: `MAJOR.MINOR.PATCH`. Version is read from `pyproject.toml` and embedded in the binary at build time.
- Each release tags the repo and produces the four artifacts in the §15.1 table.
- Release notes call out: schema-version bumps for `creation.json` / `readme_fields.json` / `ingest.json`; new config fields; plugin API version changes; any operator-visible behavior change.
- Old binaries are not auto-removed from a workstation; the operator overwrites or relocates the previous binary manually.

## 15.7 Autostart Registration

The tray process (`ExLab-Wizard-Tray`) is registered as a per-user autostart entry on the platform's standard mechanism. Registration is opt-in at first launch (Frontend §3.1.3 welcome card) and reversible at any time from `Settings → Application` (Frontend §7).

### 15.7.1 Per-platform registration

| Platform | Mechanism | Path / location |
|---|---|---|
| macOS | LaunchAgent plist | `~/Library/LaunchAgents/com.exlab-wizard.tray.plist` with `RunAtLoad: true`, `ProgramArguments: ["/Applications/ExLab-Wizard.app/Contents/MacOS/ExLab-Wizard-Tray"]`, `KeepAlive` set to relaunch only on crash (not on intentional Quit). |
| Windows | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` registry entry | Value name `ExLabWizard` (no hyphen, matching Windows conventions for registry value names), data is the absolute path to `ExLab-Wizard-Tray.exe`. Per-user only (no `HKLM` writes). |
| Linux (systemd) | User systemd unit | `~/.config/systemd/user/exlab-wizard-tray.service` with `WantedBy=default.target`, enabled via `systemctl --user enable`. |
| Linux (non-systemd fallback) | XDG autostart `.desktop` | `~/.config/autostart/exlab-wizard-tray.desktop` with `Exec=`, `X-GNOME-Autostart-enabled=true`. |

The autostart helpers (`tray/autostart.py`) expose three functions: `register()`, `unregister()`, `is_registered()`. Each is idempotent. On Linux, `register()` prefers systemd-user if the system has it (`systemctl --user --version` succeeds) and falls back to XDG autostart otherwise. All four mechanisms support being toggled at runtime with no privilege escalation (no `sudo` / no admin elevation).

### 15.7.2 Upgrade and uninstall

On app upgrade (operator overwrites the binary directory), the autostart entry's recorded path may become stale. The tray's startup includes an integrity check: `is_registered()` returns the path the autostart points at; if it differs from the current `sys.executable`, `register()` is called to update it. This is silent — no operator interaction needed.

On uninstall (operator deletes the binary directory), the autostart entry is left dangling until the next user login, at which point the OS surfaces a "missing executable" warning (macOS) or silently fails (Windows / Linux). v1 does not provide an explicit uninstaller; operators wishing to fully clean up call `Settings → Application → Disable autostart` before deleting the binary, which calls `unregister()`.

### 15.7.3 OS notifications

Two notification triggers (referenced from Frontend §3.4.5):

- **`PluginInputRequired` escalation.** When a plugin worker emits `PluginInputRequired` and the window is not currently in the foreground, the tray fires a notification: *"ExLab-Wizard: 1 plugin needs input"* with a click-action that opens the window (focuses if alive, spawns if not).
- **Sync failure with no retries left.** When a NAS sync job exhausts its retry budget (§7.1.5), the tray fires: *"ExLab-Wizard: Sync failed for `<run_label>`"*. Click-action opens the window's Problems tab.

Notifications are coalesced per-trigger-type within a 5-second window: a burst of N escalations or N sync failures becomes one notification with a count (*"ExLab-Wizard: N plugins need input"*). The tray suppresses notifications entirely when the window is in the foreground.

### 15.7.4 Window-only fallback (Linux without tray support)

When `pystray` cannot register a tray icon on the current Linux desktop environment (some Wayland configurations, certain tiling WMs, or SSH-into-GUI sessions), the tray process detects this at startup, logs a warning, and falls back to direct-window mode:

- The tray process spawns `ExLab-Wizard-Window` immediately and waits on its exit.
- When the window closes, the tray exits with it. The persistent-server feature is silently disabled.
- Autostart, if registered, still works — the tray launches at login as usual, but it auto-spawns the window instead of waiting for a tray click.
- OS notifications still work (notifications are independent of the tray icon — plyer talks directly to the OS notification daemon).

Operators are not blocked by this fallback; they get a normal native-window app instead of a tray-resident app. The Settings dialog's `Application` section surfaces a small note: *"System tray not available on this desktop. Closing the window will quit the app."*

## 15.8 Open Questions

1. **Bundled rclone version.** Which rclone version do we pin? Trade-off: latest gets bug fixes; pinning means we don't surprise operators with backend behavior changes. Default: pin to a known-good minor and bump on each ExLab-Wizard release.
2. **macOS universal vs separate arm64/x64 builds.** A universal binary doubles size but halves the artifact count. v1 default: separate builds.
3. **Self-update channel for v1.1.** Once the app is in lab use, how do updates reach offline machines? Candidates: a Settings-pinned NAS path the tray checks at startup; a manual "Check for updates" button that points at an internal lab share; nothing (operators copy new binaries in manually). Default for v1: nothing.
4. **WebView2 bootstrapper bundling.** Should the Windows artifact ship the WebView2 Evergreen Bootstrapper alongside the binary for fully-offline first-launch? It's small (~2 MB) and the alternative is a download prompt that doesn't work without internet. Default: yes, bundle it.
5. **macOS LaunchAgent vs Login Items.** macOS supports two autostart mechanisms: the older Login Items (per-user) and LaunchAgents (per-user, more flexible). The spec commits to LaunchAgent because it survives `Login Items` UI tweaking less surprisingly and supports `KeepAlive`. Open question: should the macOS Settings UI also surface the entry in Login Items for discoverability, even though the actual mechanism is LaunchAgent?
