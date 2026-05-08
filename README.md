# ExLabWizard

![Development Status](https://img.shields.io/badge/dev_status-alpha-red)

<div style="background-color: white; display: inline-block; padding: 10px; border-radius: 0px;">
  <img src="assets/ExLabWizardLogo.svg" alt="Phenotypic Logo" style="width: 400px; height: auto;">
</div>

## Context

ExLab-Wizard is a lightweight desktop application that creates standardized
directory structures on local disk, NAS, and a LIMS database from predefined
templates. It enforces the lab's
`<Equipment>/<Project>/Run_<ISO8601_DATE>` naming convention (and the parallel
`TestRuns/TestRun_<ISO8601_DATE>` for non-experimental runs), reduces human
error in directory creation, and provides an extensible plugin system for
transforming template file contents at creation time.

## Installation

### Prerequisites

- **Python 3.12** on `PATH` (`pyproject.toml` pins `requires-python = ">=3.12"`).
- A C toolchain only if your OS lacks pre-built wheels for `cryptography`,
  `argon2-cffi`, or `pywin32` — most Linux/macOS/Windows installs do not
  need this.
- **Linux only**: a working Secret Service implementation (GNOME Keyring,
  KWallet, `keepassxc-secret-service`, ...) for the keyring backend; the
  app falls back to an encrypted-at-rest store when none is available.

### From source (development)

The repo is managed with [`uv`](https://docs.astral.sh/uv/) and ships a
locked `uv.lock`. Either tool below works:

```bash
git clone https://github.com/exfab/ExLabWizard.git
cd ExLabWizard

# Option A — uv (recommended; honours uv.lock)
uv sync --extra dev

# Option B — pip + venv
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

The `[dev]` extra pulls `[plugin-examples,test,build,lint]`. For a
slimmer install (runtime only) drop the extra: `uv sync` /
`pip install -e .`.

### Pre-built binary

For each tagged release, the `build` workflow publishes single-folder
PyInstaller artifacts under
[Releases](../../releases) (`linux-x64`, `win-x64`, `mac-arm64`).
Unpack and run the platform-specific entry point under `ExLab-Wizard/`
(or `ExLab-Wizard.app/` on macOS).

## Running ExLab-Wizard

Three console entry points are installed (Backend Spec §15.3):

| Command | Role |
|---|---|
| `exlab-wizard` | The CLI alias an operator double-clicks; spawns or focuses the tray. Use this for normal startup. |
| `exlab-wizard-tray` | The long-lived tray + FastAPI server process; what gets registered for OS autostart. |
| `exlab-wizard-window` | The on-demand `pywebview` window subprocess; usually spawned by the tray, rarely invoked directly. |

Typical first run from a development checkout:

```bash
uv run exlab-wizard          # or: source .venv/bin/activate && exlab-wizard
```

The tray serves the FastAPI app on a free localhost port, opens a
NiceGUI window, and keeps a system-tray icon for quit/focus controls.
On Linux without a working tray backend the window is opened directly
and the process exits with it (Backend Spec §15.7.4).

Useful flags (forwarded to the tray):

- `--config <path>` — override the config-file location (default
  is OS-specific: `~/.config/exlab-wizard/config.yaml` on Linux,
  `~/Library/Application Support/...` on macOS,
  `%APPDATA%\exlab-wizard\...` on Windows).
- `--log-level <level>` — `DEBUG`, `INFO`, `WARNING`, `ERROR`. Logs
  land in the platform cache directory.

## Building a distributable binary

```bash
./scripts/build_local.sh        # macOS / Linux
.\scripts\build_local.ps1       # Windows
```

Both wrap the same PyInstaller invocation as the `build` workflow and
write a single-folder bundle into `dist/ExLab-Wizard/`. Packaging
(`.zip` / `.tar.gz` / `.app`) happens only in CI.

## Tests, lint, type-check

```bash
uv run pytest tests/unit tests/integration   # fast suite
uv run pytest tests/e2e                      # Playwright flows (browser required)
uv run ruff check . && uv run ruff format --check .
uv run mypy exlab_wizard
```

The `qc` workflow runs all of the above on every PR; the `lims-live`
workflow additionally verifies the LIMS client against a live upstream
[`mcnaughtonadm/exlab`](https://gitlab.com/mcnaughtonadm/exlab)
container weekly and on every merge to `main`.

## Documentation

- Operator-facing user guide: `docs/user_guide/` (rendered via
  Sphinx; `make -C docs html`).
- Plugin authoring guide: `docs/plugin_guide/`.
- Design specs: `design_specs/` (the authoritative source for
  capability scope, interfaces, and wire contracts).
