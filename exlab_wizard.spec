# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for ExLab-Wizard. Backend Spec §15.1.

Builds **three console-script entry points** sharing a single
``_internal/`` directory via PyInstaller's ``MERGE`` directive:

* ``ExLab-Wizard-Tray``    -- long-lived tray + server (§15.3.1).
* ``ExLab-Wizard-Window``  -- on-demand pywebview window subprocess (§15.3.2).
* ``ExLab-Wizard``         -- CLI alias / desktop-launcher target (§15.3.3).

The artifact is a ``--onedir`` bundle (the spec rejects ``--onefile``;
§15.1) so plugin- and window-subprocess re-launch via ``sys.executable``
hits the bundled CPython directly without re-extracting on each spawn.

The spec is invoked by both the local-build wrappers
(``scripts/build_local.{sh,ps1}``) and the CI matrix
(``.github/workflows/build.yml``).

Usage:
    pyinstaller exlab_wizard.spec
"""

# ruff: noqa
# pyinstaller injects ``Analysis``, ``PYZ``, ``EXE``, ``COLLECT``, ``MERGE``,
# ``BUNDLE`` at runtime; static analysers cannot see them.

from __future__ import annotations

import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

# Repo root (PyInstaller runs the spec with cwd at the repo root).
REPO_ROOT = Path(".").resolve()

# Read the version from the package so the binary's metadata matches
# ``pyproject.toml`` automatically. Backend Spec §15.6.
sys.path.insert(0, str(REPO_ROOT))
from exlab_wizard import __version__ as APP_VERSION  # noqa: E402

# CFBundleIdentifier on macOS. Backend Spec §15.1 / §15.7.1.
APP_BUNDLE_ID = "com.lab.exlab-wizard"
APP_NAME = "ExLab-Wizard"

# Three entry-point script paths (all live under the package). Each
# script wraps a ``main()`` callable so PyInstaller can produce a thin
# launcher that drops directly into our code.
ENTRY_TRAY = "exlab_wizard/tray/main.py"
ENTRY_WINDOW = "exlab_wizard/window/main.py"
ENTRY_CLI = "exlab_wizard/__main__.py"

# Output executable names (per the §15.1 table). PyInstaller writes
# these into ``dist/`` together with the shared ``_internal/`` directory.
EXE_TRAY = "ExLab-Wizard-Tray"
EXE_WINDOW = "ExLab-Wizard-Window"
EXE_CLI = "ExLab-Wizard"


# ---------------------------------------------------------------------------
# Hidden imports
# ---------------------------------------------------------------------------
#
# These are libraries that PyInstaller's static analyser cannot follow
# reliably (dynamic ``importlib`` calls, plugin-style entry points,
# platform-specific backends pulled in lazily). Keep this list tight and
# documented; every entry has a §-reference for why it is needed.

HIDDEN_IMPORTS: list[str] = [
    # NiceGUI mounts on FastAPI; some sub-modules are imported via
    # registry hooks (§4.1, §15.1).
    "nicegui",
    # pywebview platform shims load the native webview lazily (§15.5).
    "webview",
    # pystray platform backends (Cocoa/Win32/AppIndicator) are picked at
    # import time but PyInstaller misses them (§15.5).
    "pystray",
    # plyer notifications backends (§15.7.3).
    "plyer",
    # keyring backends -- the OS-specific backend is selected at runtime
    # (§7.4); PyInstaller cannot follow the entry-point lookup.
    "keyring",
    "keyring.backends",
    # Copier renders templates with Jinja2; some Copier sub-modules pull
    # in plugin-style helpers via ``importlib`` (§5).
    "copier",
    # ruamel.yaml round-trip loader for ``config.yaml`` (§9.1).
    "ruamel.yaml",
]


# ---------------------------------------------------------------------------
# Bundled data
# ---------------------------------------------------------------------------
#
# ``--add-data`` pairs of ``(source, dest_in_bundle)``. PyInstaller copies
# each source into the bundle at the named destination. Backend Spec
# §15.4: bundled starter content lives under ``_internal/`` on disk too,
# so the runtime resolution of ``sys._MEIPASS / "_internal" / "..."`` is
# identical in dev and bundled mode.

DATAS: list[tuple[str, str]] = []


def _resolve_nicegui_assets_dir() -> Path | None:
    """Return the directory that holds NiceGUI's static CSS/JS assets.

    NiceGUI ships its own bundled static files alongside the package;
    these need to ride into the PyInstaller bundle so the offline UI
    renders without internet access (§15.4 / §15.5). We resolve the
    location dynamically via ``nicegui.__file__`` instead of hard-coding
    a version-specific path.
    """
    try:
        import nicegui  # type: ignore[import-not-found]
    except ImportError:
        return None
    pkg_root = Path(nicegui.__file__).resolve().parent
    # NiceGUI's static dir is typically ``static/`` next to ``__init__.py``.
    static = pkg_root / "static"
    return static if static.is_dir() else None


_nicegui_static = _resolve_nicegui_assets_dir()
if _nicegui_static is not None:
    DATAS.append((str(_nicegui_static), "nicegui/static"))

# Bundled starter content (§15.4). The directories may be empty in v1
# (each carries a ``.gitkeep``); we still emit the ``--add-data`` so the
# runtime path resolution succeeds without conditionals.
DATAS.append(("_internal/templates", "_internal/templates"))
DATAS.append(("_internal/plugins", "_internal/plugins"))

# Static UI assets (SVG icons, etc.) served at ``/assets`` by the
# NiceGUI app via ``ui/theme.py:register_static_assets``.
DATAS.append(("assets", "assets"))


# ---------------------------------------------------------------------------
# Bundled binaries
# ---------------------------------------------------------------------------
#
# TODO(v1.1): Bundle the rclone binary inside the artifact at
# ``_internal/bin/rclone[.exe]`` per Backend Spec §15.5. For v1 we leave
# rclone as a system-installed dependency (the NASSync transport probes
# the system PATH; `Settings → NAS Cleanup` surfaces a clear error if
# missing). When we revisit:
#
# * Pin a specific rclone minor; vendor the per-OS binary under
#   ``_internal/bin/`` in the build tree.
# * Add ``--add-binary`` entries below, one per target OS, gated on the
#   build host's ``sys.platform``.
# * Update §15.5 to drop the "system rclone" wording.
#
# Same pattern for the Windows ``WebView2Loader.dll`` and the Linux
# GTK-WebKit hooks (§15.5): these are present in the host OS in v1.
#
# Reference: design_specs/design_spec_sections/15_Distribution.md §15.5

BINARIES: list[tuple[str, str]] = []


# ---------------------------------------------------------------------------
# Per-OS metadata + icons
# ---------------------------------------------------------------------------

ICON_DIR = REPO_ROOT / "assets"


def _resolve_icon() -> str | None:
    """Pick the per-OS icon if present; otherwise return None.

    PyInstaller accepts ``None`` for ``icon`` (uses its default). Real
    icons land in ``assets/icons/`` in a follow-up populating step;
    Phase 15 commits the spec without binding to a specific filename.

    macOS ``BUNDLE`` rejects non-``.icns`` icons, and Windows ``EXE``
    rejects non-``.ico`` icons. On Linux any image works (PyInstaller
    treats the icon as decorative). We only return a path that matches
    the platform's required extension; otherwise return None and let
    PyInstaller emit its default icon. The SVG repo logo is NOT a
    valid platform icon for any of these targets and is never returned.
    """
    if sys.platform == "darwin":
        candidate = ICON_DIR / "icons" / "ExLabWizard.icns"
    elif sys.platform == "win32":
        candidate = ICON_DIR / "icons" / "ExLabWizard.ico"
    else:
        candidate = ICON_DIR / "icons" / "ExLabWizard.png"
    return str(candidate) if candidate.exists() else None


ICON_PATH = _resolve_icon()


# Windows app manifest XML (DPI awareness, requestedExecutionLevel).
# PyInstaller injects this when ``manifest=`` is passed to ``EXE``.
WINDOWS_MANIFEST = """<?xml version='1.0' encoding='UTF-8' standalone='yes'?>
<assembly xmlns='urn:schemas-microsoft-com:asm.v1' manifestVersion='1.0'>
  <assemblyIdentity
      type='win32'
      name='com.lab.exlab-wizard'
      version='{version}.0'
      processorArchitecture='*'/>
  <trustInfo xmlns='urn:schemas-microsoft-com:asm.v3'>
    <security>
      <requestedPrivileges>
        <requestedExecutionLevel level='asInvoker' uiAccess='false'/>
      </requestedPrivileges>
    </security>
  </trustInfo>
  <application xmlns='urn:schemas-microsoft-com:asm.v3'>
    <windowsSettings>
      <dpiAware xmlns='http://schemas.microsoft.com/SMI/2005/WindowsSettings'>true</dpiAware>
    </windowsSettings>
  </application>
</assembly>
""".format(version=APP_VERSION)


# ---------------------------------------------------------------------------
# Per-entry-point Analysis + PYZ + EXE
# ---------------------------------------------------------------------------


def _make_analysis(script: str) -> "Analysis":  # type: ignore[name-defined]
    """Construct a PyInstaller ``Analysis`` for one entry-point script."""
    return Analysis(  # noqa: F821 -- injected by PyInstaller
        [script],
        pathex=[str(REPO_ROOT)],
        binaries=BINARIES,
        datas=DATAS,
        hiddenimports=HIDDEN_IMPORTS,
        hookspath=[],
        runtime_hooks=[],
        excludes=[],
        noarchive=False,
    )


a_tray = _make_analysis(ENTRY_TRAY)
a_window = _make_analysis(ENTRY_WINDOW)
a_cli = _make_analysis(ENTRY_CLI)

# MERGE de-duplicates the shared CPython interpreter and third-party
# libraries across the three entry points so they all draw from a single
# ``_internal/`` directory at runtime. Backend Spec §15.1.
MERGE(  # noqa: F821 -- injected by PyInstaller
    (a_tray, EXE_TRAY, EXE_TRAY),
    (a_window, EXE_WINDOW, EXE_WINDOW),
    (a_cli, EXE_CLI, EXE_CLI),
)


pyz_tray = PYZ(a_tray.pure, a_tray.zipped_data)  # noqa: F821
pyz_window = PYZ(a_window.pure, a_window.zipped_data)  # noqa: F821
pyz_cli = PYZ(a_cli.pure, a_cli.zipped_data)  # noqa: F821


# Tray exe: ``console=True`` on every OS so log writes from third-party
# libs (uvicorn startup banner, plugin worker stderr passthrough) are
# visible if the operator launches from a terminal. The pywebview
# subprocess exits as soon as the window closes, so its console window
# vanishes immediately on Windows -- ``console=False`` suppresses the
# brief flash.
exe_tray = EXE(  # noqa: F821
    pyz_tray,
    a_tray.scripts,
    [],
    exclude_binaries=True,
    name=EXE_TRAY,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_PATH,
    manifest=(WINDOWS_MANIFEST if sys.platform == "win32" else None),
)

exe_window = EXE(  # noqa: F821
    pyz_window,
    a_window.scripts,
    [],
    exclude_binaries=True,
    name=EXE_WINDOW,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_PATH,
    manifest=(WINDOWS_MANIFEST if sys.platform == "win32" else None),
)

exe_cli = EXE(  # noqa: F821
    pyz_cli,
    a_cli.scripts,
    [],
    exclude_binaries=True,
    name=EXE_CLI,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_PATH,
    manifest=(WINDOWS_MANIFEST if sys.platform == "win32" else None),
)


# ---------------------------------------------------------------------------
# COLLECT -- package each entry point into the shared dist tree
# ---------------------------------------------------------------------------

coll = COLLECT(  # noqa: F821 -- injected by PyInstaller
    exe_tray,
    a_tray.binaries,
    a_tray.datas,
    exe_window,
    a_window.binaries,
    a_window.datas,
    exe_cli,
    a_cli.binaries,
    a_cli.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)


# ---------------------------------------------------------------------------
# macOS .app bundle wrapper
# ---------------------------------------------------------------------------
#
# Backend Spec §15.1: the macOS artifact is an ``ExLab-Wizard.app`` bundle.
# The bundle's ``Contents/MacOS/`` carries all three executables; the
# tray executable is named in ``CFBundleExecutable`` so a finder
# double-click hits the CLI alias which decides whether to open or spawn.

if sys.platform == "darwin":
    app = BUNDLE(  # noqa: F821 -- injected by PyInstaller
        coll,
        name=f"{APP_NAME}.app",
        icon=ICON_PATH,
        bundle_identifier=APP_BUNDLE_ID,
        version=APP_VERSION,
        info_plist={
            "CFBundleIdentifier": APP_BUNDLE_ID,
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleVersion": APP_VERSION,
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleExecutable": EXE_CLI,
            # Backend Spec §15.2: v1 ships unsigned. Set ``LSUIElement``
            # to false so the dock icon shows up; the tray's pystray
            # icon lives in the menu bar regardless.
            "LSUIElement": False,
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
        },
    )
