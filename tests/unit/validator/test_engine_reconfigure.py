"""Unit tests for ``Validator.reconfigure`` (live config reload).

The settings dialog's Save now reconfigures the running validator in
place (no tray relaunch), so the content-scan limits, the equipment-roots
map, and the staging root must all refresh from the new config.
"""

from __future__ import annotations

from pathlib import Path

from exlab_wizard.config.models import ValidatorConfig
from exlab_wizard.validator.engine import Validator


def test_reconfigure_refreshes_scan_limits_and_roots() -> None:
    validator = Validator(ValidatorConfig(content_scan_max_mib=1, content_scan_extensions=[".txt"]))
    assert validator._content_scan_max_bytes == 1 * 1024 * 1024
    assert validator._equipment_roots == {}
    assert validator._staging_root is None

    validator.reconfigure(
        ValidatorConfig(content_scan_max_mib=4, content_scan_extensions=[".MD", ".Txt"]),
        equipment_roots={"EQ1": Path("/data/EQ1")},
        staging_root=Path("/staging"),
    )

    assert validator._content_scan_max_bytes == 4 * 1024 * 1024
    # Extensions are lower-cased, mirroring __init__.
    assert validator._content_scan_extensions == frozenset({".md", ".txt"})
    assert validator._equipment_roots == {"EQ1": Path("/data/EQ1")}
    assert validator._staging_root == Path("/staging")


def test_reconfigure_defaults_to_empty_roots() -> None:
    validator = Validator(
        ValidatorConfig(),
        equipment_roots={"EQ1": Path("/old")},
        staging_root=Path("/old-staging"),
    )
    validator.reconfigure(ValidatorConfig())
    assert validator._equipment_roots == {}
    assert validator._staging_root is None
