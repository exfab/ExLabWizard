"""Tests for ``exlab_wizard.config.loader``. Backend Spec §9.

The loader is the single boundary that the rest of the app uses to read and
write ``config.yaml``. These tests cover:

- Validation: filesystem absence, malformed YAML, non-mapping top level, and
  Pydantic ``ValidationError`` are all surfaced as ``ConfigError``.
- Round-trip: comments and key order survive a load/save cycle when the caller
  passes the original text in (the Settings UI flow).
- Atomicity: ``save_config`` does not leave a stray ``.tmp`` next to the
  destination, and creates parent directories on demand.
- Library identity: the loader uses ``ruamel.yaml`` (not PyYAML); we assert
  this indirectly by exercising a ruamel-only behaviour (preserved quoting on
  dump).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError
from ruamel.yaml import YAML

from exlab_wizard.config.loader import (
    dump_config,
    load_config,
    load_config_from_text,
    save_config,
)
from exlab_wizard.config.models import Config
from exlab_wizard.errors import ConfigError

# Fixtures are read-only inputs committed to the repo. Resolve from this
# file's location so the tests work regardless of the pytest invocation cwd.
FIXTURES_DIR = Path(__file__).parents[2] / "fixtures" / "configs"


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


def test_load_config_complete_yaml() -> None:
    cfg = load_config(FIXTURES_DIR / "complete.yaml")
    assert isinstance(cfg, Config)

    # Paths block.
    assert cfg.paths.templates_dir == "/opt/exlab-wizard/templates"
    assert cfg.paths.plugin_dir == "/opt/exlab-wizard/plugins"
    assert cfg.paths.local_root == "/data/lab"

    # LIMS block.
    assert cfg.lims.endpoint == "https://lims.lab.example/api/v1"
    assert cfg.lims.email == "alex.nguyen@lab.example"
    assert cfg.lims.cache_ttl_hours == 24
    assert cfg.lims.offline_catalogue_path == ""

    # Equipment list.
    assert len(cfg.equipment) == 2
    confocal = cfg.equipment[0]
    assert confocal.id == "CONFOCAL_01"
    assert confocal.completeness_signal == "sentinel_file"
    assert confocal.sentinel_filename == "acquisition_complete.flag"
    assert confocal.transport.type == "rclone"
    assert confocal.transport.rclone_remote == "lab-nas"

    flow = cfg.equipment[1]
    assert flow.id == "FLOW_01"
    assert flow.completeness_signal == "manifest"
    assert flow.manifest_filename == "run_manifest.json"
    assert flow.transport.type == "rsync_ssh"
    assert flow.transport.ssh_target == "labuser@nas01.lab.example"

    # Operators allowlist (one entry per the prompt).
    assert cfg.operators.allowlist == ["alex.nguyen"]

    # Top-level toggles per the prompt.
    assert cfg.sync.enabled is True
    assert cfg.orchestrator.enabled is False


def test_load_config_missing_file_raises(tmp_path: Path) -> None:
    target = tmp_path / "missing" / "config.yaml"
    with pytest.raises(ConfigError) as info:
        load_config(target)
    # Error must name the offending path so the operator can fix it.
    assert str(target) in str(info.value)


def test_load_config_invalid_yaml_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    # Unbalanced brackets are a definitive YAML parse error in any backend.
    bad.write_text("paths: {templates_dir: 'unterminated\n", encoding="utf-8")
    with pytest.raises(ConfigError) as info:
        load_config(bad)
    assert "not valid YAML" in str(info.value)


def test_load_config_top_level_not_mapping_raises(tmp_path: Path) -> None:
    bad = tmp_path / "not-mapping.yaml"
    bad.write_text('"just a string"\n', encoding="utf-8")
    with pytest.raises(ConfigError) as info:
        load_config(bad)
    assert "mapping" in str(info.value)


def test_load_config_validation_error_raises_config_error(tmp_path: Path) -> None:
    # A lowercase equipment ID violates EQUIPMENT_ID_PATTERN; Pydantic raises
    # ValidationError, the loader must catch and re-raise as ConfigError with
    # the original error chained as __cause__.
    bad = tmp_path / "validation.yaml"
    bad.write_text(
        "equipment:\n"
        "  - id: lowercase\n"
        "    label: x\n"
        "    local_root: /tmp\n"
        "    nas_root: /mnt\n"
        "    completeness_signal: sentinel_file\n"
        "    sentinel_filename: done.flag\n"
        "    transport:\n"
        "      type: rclone\n"
        "      rclone_remote: r\n"
        "      rclone_remote_path: p\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as info:
        load_config(bad)
    assert isinstance(info.value.__cause__, PydanticValidationError)
    assert "validation" in str(info.value).lower()


def test_load_config_from_text_empty_returns_empty_config() -> None:
    # Empty YAML text loads to None which we coerce to {}; every Config field
    # has a default factory, so the result is an all-defaults Config.
    cfg = load_config_from_text("")
    assert isinstance(cfg, Config)
    assert cfg.paths.local_root == ""
    assert cfg.equipment == []


# ---------------------------------------------------------------------------
# save_config
# ---------------------------------------------------------------------------


def test_save_config_atomic(tmp_path: Path) -> None:
    cfg = load_config(FIXTURES_DIR / "complete.yaml")
    target = tmp_path / "config.yaml"

    save_config(target, cfg)

    assert target.exists()
    # The atomic-write tmp file must not survive a successful save.
    leftover = tmp_path / "config.yaml.tmp"
    assert not leftover.exists()


def test_save_config_preserves_comments_round_trip(tmp_path: Path) -> None:
    src = FIXTURES_DIR / "complete.yaml"
    original_text = src.read_text(encoding="utf-8")
    cfg = load_config(src)

    target = tmp_path / "config.yaml"
    save_config(target, cfg, original_text=original_text)

    saved = target.read_text(encoding="utf-8")
    # At least one operator-readable comment from complete.yaml must survive
    # the round-trip; this is the whole point of using ruamel.yaml.
    assert "# directory containing Copier template subdirectories" in saved


def test_save_config_preserves_key_order(tmp_path: Path) -> None:
    src = FIXTURES_DIR / "complete.yaml"
    original_text = src.read_text(encoding="utf-8")
    cfg = load_config(src)

    target = tmp_path / "config.yaml"
    save_config(target, cfg, original_text=original_text)

    yaml = YAML(typ="rt")
    original_loaded = yaml.load(original_text)
    saved_loaded = yaml.load(target.read_text(encoding="utf-8"))

    # Top-level keys appear in the same sequence as the original document.
    assert list(saved_loaded.keys()) == list(original_loaded.keys())


def test_save_config_creates_parent_dirs(tmp_path: Path) -> None:
    cfg = load_config(FIXTURES_DIR / "complete.yaml")
    target = tmp_path / "subdir" / "more" / "config.yaml"

    save_config(target, cfg)

    assert target.exists()
    assert target.parent.is_dir()


def test_save_config_without_original_text_writes_fresh(tmp_path: Path) -> None:
    cfg = load_config(FIXTURES_DIR / "complete.yaml")
    target = tmp_path / "fresh.yaml"

    save_config(target, cfg)

    # Reload and confirm we get an equivalent Config back. We compare via
    # model_dump rather than __eq__ so the assertion message is readable on
    # mismatch and so anonymous list-vs-CommentedSeq differences don't trip
    # us up.
    reloaded = load_config(target)
    assert reloaded.model_dump(mode="python") == cfg.model_dump(mode="python")


# ---------------------------------------------------------------------------
# dump_config
# ---------------------------------------------------------------------------


def test_dump_config_round_trip() -> None:
    # Build a Config from a minimal dict, dump to text, load back, assert
    # equivalence under model_dump.
    seed = {
        "paths": {
            "templates_dir": "/t",
            "plugin_dir": "/p",
            "local_root": "/l",
        },
        "lims": {
            "endpoint": "https://lims.example/api",
            "email": "op@example.com",
        },
        "equipment": [
            {
                "id": "CONFOCAL_01",
                "label": "Confocal",
                "local_root": "/l",
                "nas_root": "/n",
                "completeness_signal": "sentinel_file",
                "sentinel_filename": "done.flag",
                "transport": {
                    "type": "rclone",
                    "rclone_remote": "lab-nas",
                    "rclone_remote_path": "lab/CONFOCAL_01",
                },
            },
        ],
    }
    cfg = Config.model_validate(seed)

    text = dump_config(cfg)
    reloaded = load_config_from_text(text)
    assert reloaded.model_dump(mode="python") == cfg.model_dump(mode="python")


# ---------------------------------------------------------------------------
# library identity
# ---------------------------------------------------------------------------


def test_load_config_uses_ruamel_round_trip(tmp_path: Path) -> None:
    # ruamel.yaml in round-trip mode preserves the original quoting style on
    # dump; PyYAML's safe_load + safe_dump path strips it. We assert the
    # ruamel behaviour: a value originally written with double quotes round-
    # trips out with double quotes still applied.
    src = FIXTURES_DIR / "complete.yaml"
    original_text = src.read_text(encoding="utf-8")
    cfg = load_config(src)

    target = tmp_path / "config.yaml"
    save_config(target, cfg, original_text=original_text)
    saved = target.read_text(encoding="utf-8")

    # The original keeps templates_dir as `"/opt/exlab-wizard/templates"`
    # (double-quoted). PyYAML's default dumper would emit it unquoted; ruamel
    # in round-trip mode keeps the quotes.
    assert '"/opt/exlab-wizard/templates"' in saved
