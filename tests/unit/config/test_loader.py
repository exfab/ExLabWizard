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
    apply_test_mode_prefix,
    dump_config,
    load_config,
    load_config_from_text,
    save_config,
)
from exlab_wizard.config.models import Config
from exlab_wizard.constants import TEST_MODE_ENV, TEST_MODE_PREFIX
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
    assert confocal.transport.type == "rclone_sftp"
    assert confocal.transport.host == "nas01.lab.example"

    flow = cfg.equipment[1]
    assert flow.id == "FLOW_01"
    assert flow.transport.type == "rclone_smb"
    assert flow.transport.share == "lab"

    # Operators allowlist (one entry per the prompt).
    assert cfg.operators.allowlist == ["alex.nguyen"]

    # Top-level toggles per the prompt. Redesign §3.1: orchestrator
    # ``enabled`` is gone; label + staging_root are always required.
    assert cfg.sync.enabled is True
    assert cfg.orchestrator.label == "Lab Acquisition Station 01"
    assert cfg.orchestrator.staging_root == "/staging"


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
        "    transport:\n"
        "      type: rclone_sftp\n"
        "      host: nas.lab.example\n"
        "      user: testuser\n"
        "      remote_path: p\n",
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


def test_load_config_unreadable_path_raises_config_error(tmp_path: Path) -> None:
    # Hitting the non-FileNotFoundError OSError branch in load_config:
    # passing a directory (instead of a file) makes Path.read_text raise
    # IsADirectoryError, which is an OSError but not FileNotFoundError. The
    # loader must wrap this in ConfigError with the offending path mentioned.
    target = tmp_path / "is-a-dir"
    target.mkdir()
    with pytest.raises(ConfigError) as info:
        load_config(target)
    assert str(target) in str(info.value)
    # The error chain preserves the original OSError as __cause__ for
    # callers that want to introspect.
    assert isinstance(info.value.__cause__, OSError)
    # The branch we hit must NOT be the FileNotFoundError branch.
    assert not isinstance(info.value.__cause__, FileNotFoundError)


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
                "transport": {
                    "type": "rclone_sftp",
                    "host": "nas.lab.example",
                    "user": "testuser",
                    "remote_path": "lab/CONFOCAL_01",
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


# ---------------------------------------------------------------------------
# EXLAB_WIZARD_TEST_MODE prefix
# ---------------------------------------------------------------------------


# Minimal two-equipment YAML used by the test-mode flavour tests. Kept inline
# (rather than as a fixture file) so the original-vs-prefixed comparison is
# obvious to a reader and so the test stays robust if the shared
# ``complete.yaml`` adds an equipment entry later.
_TWO_EQUIPMENT_YAML = (
    "equipment:\n"
    "  - id: EQ1\n"
    "    label: First\n"
    "    local_root: /data/eq1\n"
    "    nas_root: /mnt/eq1\n"
    "    transport:\n"
    "      type: rclone_sftp\n"
    "      host: nas.lab.example\n"
    "      user: testuser\n"
    "      remote_path: p\n"
    "  - id: EQ2\n"
    "    label: Second\n"
    "    local_root: /data/eq2\n"
    "    nas_root: /mnt/eq2\n"
    "    transport:\n"
    "      type: rclone_sftp\n"
    "      host: nas.lab.example\n"
    "      user: testuser\n"
    "      remote_path: q\n"
)


def test_test_mode_unset_leaves_equipment_ids_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default code path is a no-op: no env var, no rewrite."""
    monkeypatch.delenv(TEST_MODE_ENV, raising=False)
    cfg = load_config_from_text(_TWO_EQUIPMENT_YAML)
    assert [e.id for e in cfg.equipment] == ["EQ1", "EQ2"]


def test_test_mode_enabled_prefixes_every_equipment_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """A truthy env value rewrites every id with the canonical prefix."""
    monkeypatch.setenv(TEST_MODE_ENV, "1")
    cfg = load_config_from_text(_TWO_EQUIPMENT_YAML)
    assert [e.id for e in cfg.equipment] == [
        f"{TEST_MODE_PREFIX}EQ1",
        f"{TEST_MODE_PREFIX}EQ2",
    ]
    # Non-id fields are untouched.
    assert cfg.equipment[0].label == "First"
    assert cfg.equipment[1].local_root == "/data/eq2"


def test_test_mode_is_idempotent_on_already_prefixed_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An already-``TEST_``-prefixed id must NOT be re-prefixed."""
    monkeypatch.setenv(TEST_MODE_ENV, "true")
    seeded = (
        "equipment:\n"
        "  - id: TEST_EQ1\n"
        "    label: Already prefixed\n"
        "    local_root: /data/eq1\n"
        "    nas_root: /mnt/eq1\n"
        "    transport:\n"
        "      type: rclone_sftp\n"
        "      host: nas.lab.example\n"
        "      user: testuser\n"
        "      remote_path: p\n"
        "  - id: EQ2\n"
        "    label: Plain\n"
        "    local_root: /data/eq2\n"
        "    nas_root: /mnt/eq2\n"
        "    transport:\n"
        "      type: rclone_sftp\n"
        "      host: nas.lab.example\n"
        "      user: testuser\n"
        "      remote_path: q\n"
    )
    cfg = load_config_from_text(seeded)
    # The first id is unchanged (no ``TEST_TEST_…`` doubling); the
    # second picks up the prefix exactly once.
    assert [e.id for e in cfg.equipment] == ["TEST_EQ1", "TEST_EQ2"]


def test_test_mode_preserves_unique_id_invariant(monkeypatch: pytest.MonkeyPatch) -> None:
    """The transformed config still passes Config.model_validate."""
    monkeypatch.setenv(TEST_MODE_ENV, "1")
    cfg = load_config_from_text(_TWO_EQUIPMENT_YAML)
    # Re-validate the dumped form so duplicate ids or pattern violations
    # would surface here too. (The loader already does this internally;
    # the explicit re-validation guards against future regressions in
    # apply_test_mode_prefix.)
    revalidated = Config.model_validate(cfg.model_dump(mode="python"))
    seen: set[str] = set()
    for entry in revalidated.equipment:
        assert entry.id not in seen, f"duplicate id {entry.id!r} after test-mode rewrite"
        seen.add(entry.id)


@pytest.mark.parametrize("truthy", ["1", "true", "TRUE", "True", "yes", "on", "ON"])
def test_test_mode_truthy_values_trigger_prefix(
    monkeypatch: pytest.MonkeyPatch, truthy: str
) -> None:
    """Every accepted spelling flips the loader on (case-insensitive)."""
    monkeypatch.setenv(TEST_MODE_ENV, truthy)
    cfg = load_config_from_text(_TWO_EQUIPMENT_YAML)
    assert all(e.id.startswith(TEST_MODE_PREFIX) for e in cfg.equipment), (
        f"{truthy!r} should have enabled test mode"
    )


@pytest.mark.parametrize("falsy", ["", "0", "false", "FALSE", "no", "off", "  "])
def test_test_mode_falsy_values_do_not_trigger_prefix(
    monkeypatch: pytest.MonkeyPatch, falsy: str
) -> None:
    """Falsy / unrecognized values leave the loaded config untouched."""
    monkeypatch.setenv(TEST_MODE_ENV, falsy)
    cfg = load_config_from_text(_TWO_EQUIPMENT_YAML)
    assert [e.id for e in cfg.equipment] == ["EQ1", "EQ2"]


def test_test_mode_unset_via_delenv_does_not_trigger_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An absent env var (not just empty) leaves the config untouched."""
    monkeypatch.delenv(TEST_MODE_ENV, raising=False)
    cfg = load_config_from_text(_TWO_EQUIPMENT_YAML)
    assert [e.id for e in cfg.equipment] == ["EQ1", "EQ2"]


def test_apply_test_mode_prefix_helper_is_a_pure_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``apply_test_mode_prefix`` itself ignores the env var.

    The env-var check is the loader's responsibility; the helper is
    the deterministic transformation tests can call directly.
    """
    monkeypatch.delenv(TEST_MODE_ENV, raising=False)
    cfg = load_config_from_text(_TWO_EQUIPMENT_YAML)
    prefixed = apply_test_mode_prefix(cfg)
    assert [e.id for e in prefixed.equipment] == [
        f"{TEST_MODE_PREFIX}EQ1",
        f"{TEST_MODE_PREFIX}EQ2",
    ]
    # The input config is not mutated.
    assert [e.id for e in cfg.equipment] == ["EQ1", "EQ2"]


def test_loader_round_trips_nas_block(tmp_path):
    from exlab_wizard.config.loader import load_config, save_config

    text = (
        "paths:\n"
        "  templates_dir: /t\n  plugin_dir: /p\n  local_root: /l\n"
        "orchestrator:\n  label: ws-1\n"
        "nas:\n"
        "  remote: nas01\n"
        "  base_root: /srv/lab\n"
        "  perf:\n    transfers: 2\n    checkers: 3\n"
    )
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    cfg = load_config(p)
    assert cfg.nas.remote == "nas01"
    assert cfg.nas.perf.transfers == 2
    save_config(p, cfg, original_text=text)
    assert "nas01" in p.read_text(encoding="utf-8")


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
