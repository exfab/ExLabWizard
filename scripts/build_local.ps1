# Local build wrapper for ExLab-Wizard on Windows. Backend Spec §15.1.
#
# Mirrors the CI matrix's Windows job (``.github/workflows/build.yml``)
# so a developer can reproduce the win-x64 artifact on their workstation.
#
# Usage (PowerShell, from the repo root):
#   ./scripts/build_local.ps1
#
# Output lands in ``dist\`` (PyInstaller default).
#
# Notes:
# * Requires Python 3.12 on PATH and the ``[build]`` extras installed
#   (``pip install -e .[build]``).
# * Does NOT package the artifact (the CI job zips ``dist\ExLab-Wizard``);
#   the local script stops at ``dist\`` so you can inspect the layout.

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

# Resolve the Python interpreter. Prefer the in-tree venv if present.
if (Test-Path ".venv\Scripts\python.exe") {
    $Python = ".venv\Scripts\python.exe"
} elseif ($env:PYTHON) {
    $Python = $env:PYTHON
} else {
    $Python = "python"
}

Write-Host "[build_local] using interpreter: $Python"

# Install build extras if PyInstaller is missing.
$has_pyinstaller = & $Python -c "import PyInstaller" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "[build_local] installing build extras"
    & $Python -m pip install --upgrade pip
    & $Python -m pip install -e ".[build]"
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
}

# Clean previous output so stale artifacts don't confuse the smoke step.
if (Test-Path build) { Remove-Item -Recurse -Force build }
if (Test-Path dist) { Remove-Item -Recurse -Force dist }

Write-Host "[build_local] running pyinstaller exlab_wizard.spec"
& $Python -m PyInstaller exlab_wizard.spec
if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }

Write-Host "[build_local] dist tree:"
Get-ChildItem dist | Format-Table -AutoSize

Write-Host @"

Build complete. Artifact root: dist\ExLab-Wizard\
Smoke-test the tray binary:
    .\dist\ExLab-Wizard\ExLab-Wizard-Tray.exe
First-launch SmartScreen: click "More info" -> "Run anyway" (Backend Spec §15.2).
"@
