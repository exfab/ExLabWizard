"""Shared pytest fixtures for the ExLabWizard test suite.

Centralizes the one cross-cutting seam introduced by the single-app-root
refactor: the setup-state paths gate now probes the real filesystem
(``paths.app_root_writable`` does an ``ensure_app_dirs`` + ``os.access``),
where it used to be a pure string-emptiness check. The vast majority of
unit/integration tests build configs with placeholder roots (``/srv/...``,
``/data/...``) and only care about the *other* setup gates (equipment, NAS
remote, LIMS) or about reaching ``READY`` -- they should not depend on those
fake paths being writable on the test host.

The :func:`_assume_app_root_writable` autouse fixture therefore stubs the
*imported* ``app_root_writable`` reference inside the two API call sites
(``api.setup`` and ``api.routers.config``) to return ``True``. It deliberately
leaves :func:`exlab_wizard.paths.app_root_writable` itself untouched, so the
unit tests in ``tests/unit/test_paths.py`` still exercise the real
mkdir/``os.access`` behavior. A test that needs the unwritable branch (e.g. to
assert the ``paths`` section/banner appears) overrides this per-test via
``monkeypatch.setattr("exlab_wizard.api.setup.app_root_writable", lambda _c: False)``.
"""

from __future__ import annotations

import contextlib

import pytest


@pytest.fixture(autouse=True)
def _assume_app_root_writable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treat the app root as writable in the API setup-state call sites.

    See the module docstring. Patches the names where they are *used*, not the
    definition, so direct tests of ``paths.app_root_writable`` are unaffected.
    """
    for module in ("exlab_wizard.api.setup", "exlab_wizard.api.routers.config"):
        # The module may not expose the name in a given test environment
        # (e.g. optional API extras); the patch is simply skipped there.
        with contextlib.suppress(ImportError, AttributeError):
            monkeypatch.setattr(f"{module}.app_root_writable", lambda _config: True)
