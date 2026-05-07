#!/usr/bin/env bash
# Local build wrapper for ExLab-Wizard. Backend Spec §15.1.
#
# Mirrors the steps in ``.github/workflows/build.yml`` so a developer
# can reproduce the CI artifact on their workstation. Run from the repo
# root:
#
#   ./scripts/build_local.sh
#
# Output lands in ``dist/`` (PyInstaller default).
#
# Notes:
# * Requires Python 3.12 on PATH and the ``[build]`` extras installed
#   (``pip install -e .[build]``).
# * Does NOT package the artifact -- the CI workflow handles zipping /
#   tar.gz / .app -> .dmg. The local script stops at ``dist/`` so the
#   developer can inspect the directory layout.

set -euo pipefail

cd "$(dirname "$0")/.."

# Resolve the Python interpreter. Prefer the in-tree venv if present.
if [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif [ -x "/tmp/ew-venv/bin/python" ]; then
    PYTHON="/tmp/ew-venv/bin/python"
else
    PYTHON="${PYTHON:-python3}"
fi

echo "[build_local] using interpreter: $PYTHON"

# Install build extras if PyInstaller is missing.
if ! "$PYTHON" -c "import PyInstaller" >/dev/null 2>&1; then
    echo "[build_local] installing build extras"
    "$PYTHON" -m pip install --upgrade pip
    "$PYTHON" -m pip install -e ".[build]"
fi

# Clean previous output so stale artifacts don't confuse the smoke step.
rm -rf build dist

echo "[build_local] running pyinstaller exlab_wizard.spec"
"$PYTHON" -m PyInstaller exlab_wizard.spec

echo "[build_local] dist tree:"
ls -la dist/

cat <<MSG

Build complete. Artifact root: dist/ExLab-Wizard/
Smoke-test the tray binary (Linux/macOS):
    ./dist/ExLab-Wizard/ExLab-Wizard-Tray
On macOS the artifact is dist/ExLab-Wizard.app -- right-click + Open the
first time to bypass Gatekeeper (Backend Spec §15.2).
MSG
