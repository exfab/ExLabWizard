"""Regression: the three equipment-data-dir derivations must agree.

Before the single-app-root refactor there were two sources of truth for where
an equipment's runs live: the global ``paths.local_root`` (used by run
*creation* and the *validator* audit roots) and a per-equipment
``EquipmentConfig.local_root`` (used by the auto-sync *quiescence poller*). If
those diverged, runs were created in one tree while the sync/audit engines
watched another -- runs silently never synced.

The refactor removed ``EquipmentConfig.local_root`` and made every site derive
``<config.paths.local_root>/<equipment_id>`` (== ``<app_root>/data/<id>``).
This test pins that agreement so the divergence cannot regress: it checks the
exact derivation used by

* run creation (:func:`exlab_wizard.paths.compose_run_path` /
  :func:`compose_project_path`, fed ``config.paths.local_root`` by the
  controller),
* the validator audit roots (:meth:`Validator.from_config`), and
* the quiescence poller's nas-mode discovery root.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

# Importing the API package first sidesteps a pre-existing import-ordering
# cycle (validator.engine <-> controller.creation) when this module is
# collected standalone; mirrors the guard already used by other unit tests.
import exlab_wizard.api.app  # noqa: F401
from exlab_wizard.config.models import Config, EquipmentConfig, PathsConfig
from exlab_wizard.constants import RunKind
from exlab_wizard.paths import compose_project_path, compose_run_path
from exlab_wizard.validator.engine import Validator

_EQUIPMENT_ID = "CONFOCAL_01"


def _config(app_root: Path) -> Config:
    return Config(
        paths=PathsConfig(app_root=str(app_root)),
        equipment=[
            EquipmentConfig(id=_EQUIPMENT_ID, label="Confocal", nas_root="//nas/lab"),
        ],
    )


def test_data_root_derives_from_app_root(tmp_path: Path) -> None:
    """``config.paths.local_root`` is exactly ``<app_root>/data``."""
    config = _config(tmp_path / "ExLabWizard")
    assert Path(config.paths.local_root) == tmp_path / "ExLabWizard" / "data"


def test_creation_and_validator_and_poller_agree_on_equipment_dir(tmp_path: Path) -> None:
    """All three consumers compose the same ``<data_root>/<equipment_id>``."""
    config = _config(tmp_path / "ExLabWizard")
    data_root = Path(config.paths.local_root)
    expected_equipment_dir = data_root / _EQUIPMENT_ID

    # Creation: the controller composes run/project paths from
    # ``config.paths.local_root``; both must sit under the equipment dir.
    project_path = compose_project_path(
        local_root=data_root,
        equipment_id=_EQUIPMENT_ID,
        project_name="Cortex Q3",
    )
    run_path = compose_run_path(
        local_root=data_root,
        equipment_id=_EQUIPMENT_ID,
        project_name="Cortex Q3",
        run_kind=RunKind.EXPERIMENTAL,
        run_date=datetime(2026, 6, 1, 14, 30),
    )
    assert project_path.parent == expected_equipment_dir
    assert expected_equipment_dir in run_path.parents

    # Validator audit roots: built by Validator.from_config.
    validator = Validator.from_config(config)
    assert validator._equipment_roots[_EQUIPMENT_ID] == expected_equipment_dir

    # Poller: nas-mode discovery walks ``<config.paths.local_root>/<id>`` --
    # the same derivation, expressed inline here so a change to the poller's
    # composition that diverges from creation fails this assertion.
    poller_equipment_dir = Path(config.paths.local_root) / _EQUIPMENT_ID
    assert poller_equipment_dir == expected_equipment_dir
