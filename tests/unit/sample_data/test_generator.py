"""End-to-end tests for the sample-data generator (design spec §6/§7/§9).

These drive ``generate_samples`` against a temporary ``-test`` sandbox and
assert the full on-disk tree is written through the real producers: every
project/run folder carries a valid ``README.md`` + ``creation.json`` +
``readme_fields.json`` (decoded with the production msgspec Structs and the
README front-matter), each equipment root carries ``equipment.json``, and
test-run projects carry the ``test_runs.json`` marker. They also cover the
sync-status spread, payload files, README field layering, determinism, and
the destructive-wipe guardrails.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import msgspec
import pytest
import yaml

from exlab_wizard.api.schemas import CreationJson, EquipmentJson, ReadmeFieldsJson
from exlab_wizard.api.schemas import (
    TestRunsJson as _TestRunsJson,  # aliased: avoid pytest collection
)
from exlab_wizard.config.loader import load_config, save_config
from exlab_wizard.config.models import Config, OrchestratorConfig, PathsConfig
from exlab_wizard.constants import TEST_MODE_ENV
from exlab_wizard.constants.enums import SyncStatus
from exlab_wizard.paths import (
    cache_dir,
    creation_json_path,
    equipment_json_path,
    readme_fields_json_path,
)
from exlab_wizard.sample_data.generator import SampleDataGenerator, generate_samples
from exlab_wizard.sample_data.spec import SAMPLES

BASE_TIME = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a ``-test`` sandbox with a starter config; return ``config_path``.

    Sets ``EXLAB_WIZARD_TEST_MODE=1`` (so the loader stamps the ``TEST_``
    prefix and the wipe guardrail is satisfied) and points the OS path
    helpers' ``_app_name`` at an ``...-test`` name.
    """
    monkeypatch.setenv(TEST_MODE_ENV, "1")
    config_path = tmp_path / "config.yaml"
    starter = Config(
        paths=PathsConfig(app_root=str(tmp_path / "app")),
        orchestrator=OrchestratorConfig(label="test-workstation"),
    )
    save_config(config_path, starter)
    return config_path


def _read_creation(folder: Path) -> CreationJson:
    raw = creation_json_path(folder).read_bytes()
    return msgspec.json.decode(raw, type=CreationJson)


def _read_readme_fields(folder: Path) -> ReadmeFieldsJson:
    raw = readme_fields_json_path(folder).read_bytes()
    return msgspec.json.decode(raw, type=ReadmeFieldsJson)


def _read_front_matter(folder: Path) -> dict:
    text = (folder / "README.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    _, fm, _ = text.split("---\n", 2)
    return yaml.safe_load(fm)


def _read_label(folder: Path) -> str:
    """Return the README ``core_fields.label`` for a project/run folder."""
    return _read_front_matter(folder)["core_fields"]["label"]


def _project_dirs(local_root: Path) -> list[Path]:
    """Return every ``<EQ>/<project>`` directory under ``local_root``.

    Excludes the ``.exlab-wizard`` cache dir at the equipment root (which
    holds ``equipment.json``) -- it is not a project.
    """
    out: list[Path] = []
    for eq_dir in local_root.iterdir():
        if not eq_dir.is_dir():
            continue
        for proj_dir in eq_dir.iterdir():
            if proj_dir.is_dir() and proj_dir.name != ".exlab-wizard":
                out.append(proj_dir)
    return out


def _run_dirs(project_dir: Path) -> list[Path]:
    runs: list[Path] = []
    for kind_dir_name in ("Runs", "TestRuns"):
        kind_dir = project_dir / kind_dir_name
        if kind_dir.is_dir():
            runs.extend(p for p in kind_dir.iterdir() if p.is_dir())
    return runs


# ---------------------------------------------------------------------------
# Tree existence
# ---------------------------------------------------------------------------


def test_full_tree_exists(sandbox: Path) -> None:
    generate_samples(sandbox, wipe=True, base_time=BASE_TIME)
    config = load_config(sandbox)
    local_root = Path(config.paths.local_root)

    # Prefixed equipment roots.
    roots = {p.name for p in local_root.iterdir() if p.is_dir()}
    assert roots == {"TEST_TESTRIG", "TEST_ALTRIG"}

    # 3 project dirs.
    project_dirs = _project_dirs(local_root)
    assert len(project_dirs) == 3

    # 7 run dirs total, with the right prefixes per kind.
    all_runs = [r for pd in project_dirs for r in _run_dirs(pd)]
    assert len(all_runs) == 7
    for run in all_runs:
        parent = run.parent.name
        if parent == "Runs":
            assert run.name.startswith("Run_")
        else:
            assert parent == "TestRuns"
            assert run.name.startswith("TestRun_")


def test_every_folder_has_metadata(sandbox: Path) -> None:
    generate_samples(sandbox, wipe=True, base_time=BASE_TIME)
    config = load_config(sandbox)
    local_root = Path(config.paths.local_root)

    project_dirs = _project_dirs(local_root)
    folders = list(project_dirs)
    for pd in project_dirs:
        folders.extend(_run_dirs(pd))

    for folder in folders:
        assert (folder / "README.md").is_file(), folder
        # Decode with the real Structs -> raises on malformed metadata.
        creation = _read_creation(folder)
        assert creation.schema_version
        fields = _read_readme_fields(folder)
        assert fields.schema_version
        fm = _read_front_matter(folder)
        assert isinstance(fm, dict) and fm

    # equipment.json at each equipment root.
    for eq_dir in local_root.iterdir():
        if not eq_dir.is_dir():
            continue
        raw = equipment_json_path(eq_dir).read_bytes()
        eq = msgspec.json.decode(raw, type=EquipmentJson)
        assert eq.id == eq_dir.name
        assert eq.configured_local_root == str(local_root)


def test_test_runs_marker_present_for_test_projects(sandbox: Path) -> None:
    generate_samples(sandbox, wipe=True, base_time=BASE_TIME)
    config = load_config(sandbox)
    local_root = Path(config.paths.local_root)

    for project_dir in _project_dirs(local_root):
        has_test_run = (project_dir / "TestRuns").is_dir()
        marker = cache_dir(project_dir) / "test_runs.json"
        if has_test_run:
            assert marker.is_file(), project_dir
            payload = msgspec.json.decode(marker.read_bytes(), type=_TestRunsJson)
            assert payload.equipment == project_dir.parent.name
            assert payload.run_kind.value == "test"
        else:
            assert not marker.exists(), project_dir


# ---------------------------------------------------------------------------
# Sync-status spread
# ---------------------------------------------------------------------------


def test_sync_status_spread(sandbox: Path) -> None:
    generate_samples(sandbox, wipe=True, base_time=BASE_TIME)
    config = load_config(sandbox)
    local_root = Path(config.paths.local_root)

    seen: set[SyncStatus] = set()
    for project_dir in _project_dirs(local_root):
        for run in _run_dirs(project_dir):
            seen.add(_read_creation(run).sync_status)

    # The seeded runs collectively cover these three badge states.
    assert {
        SyncStatus.SYNCED,
        SyncStatus.PENDING,
        SyncStatus.BLOCKED_BY_VALIDATION,
    } <= seen


def test_each_run_sync_status_matches_sample(sandbox: Path) -> None:
    generate_samples(sandbox, wipe=True, base_time=BASE_TIME)
    config = load_config(sandbox)
    local_root = Path(config.paths.local_root)

    # Build expected: {run label -> sync_status} from SAMPLES.
    expected: dict[str, SyncStatus] = {}
    for eq in SAMPLES:
        for proj in eq.projects:
            for run in proj.runs:
                expected[run.label] = run.sync_status

    for project_dir in _project_dirs(local_root):
        for run_dir in _run_dirs(project_dir):
            creation = _read_creation(run_dir)
            label = _read_label(run_dir)
            assert creation.sync_status == expected[label], label


# ---------------------------------------------------------------------------
# Payload files
# ---------------------------------------------------------------------------


def test_default_payload_pair(sandbox: Path) -> None:
    generate_samples(sandbox, wipe=True, base_time=BASE_TIME)
    config = load_config(sandbox)
    local_root = Path(config.paths.local_root)

    # The "Repeat run" (PROJ-0001) has files=None -> default pair.
    repeat_run = None
    for project_dir in _project_dirs(local_root):
        for run_dir in _run_dirs(project_dir):
            if _read_label(run_dir) == "Repeat run":
                repeat_run = run_dir
    assert repeat_run is not None
    assert (repeat_run / "data" / "acq_001.csv").is_file()
    assert (repeat_run / "notes.txt").is_file()


def test_blocked_run_carries_trigger_file(sandbox: Path) -> None:
    generate_samples(sandbox, wipe=True, base_time=BASE_TIME)
    config = load_config(sandbox)
    local_root = Path(config.paths.local_root)

    blocked = None
    for project_dir in _project_dirs(local_root):
        if project_dir.name != "Failure Modes":
            continue
        for run_dir in _run_dirs(project_dir):
            if _read_creation(run_dir).sync_status is SyncStatus.BLOCKED_BY_VALIDATION:
                blocked = run_dir
    assert blocked is not None
    trigger = blocked / "data" / "leak.txt"
    assert trigger.is_file()
    assert "DEMO_SCAN_TRIGGER" in trigger.read_text(encoding="utf-8")

    # The seeded extensions fall within the validator's content_scan set.
    scan_exts = set(config.validator.content_scan_extensions)
    assert ".txt" in scan_exts
    assert ".csv" in scan_exts


# ---------------------------------------------------------------------------
# README layering
# ---------------------------------------------------------------------------


def test_readme_field_layering(sandbox: Path) -> None:
    generate_samples(sandbox, wipe=True, base_time=BASE_TIME)
    config = load_config(sandbox)
    local_root = Path(config.paths.local_root)

    # Find the "Calibration A" run under "Calibration Study".
    cal_a = None
    for project_dir in _project_dirs(local_root):
        if project_dir.name != "Calibration Study":
            continue
        for run_dir in _run_dirs(project_dir):
            if _read_label(run_dir) == "Calibration A":
                cal_a = run_dir
    assert cal_a is not None

    fields = _read_readme_fields(cal_a)
    # "sample_type" matches the seeded config default -> config_fields.
    assert fields.config_fields.get("sample_type") == "control"
    # "reviewer" matches nothing -> custom_fields.
    custom_labels = {c["label"]: c["value"] for c in fields.custom_fields}
    assert custom_labels.get("reviewer") == "asmith"


def test_seeded_config_default_is_present_and_optional(sandbox: Path) -> None:
    generate_samples(sandbox, wipe=True, base_time=BASE_TIME)
    config = load_config(sandbox)

    defaults = {d.id: d for d in config.readme.defaults}
    assert "sample_type" in defaults
    sample_type = defaults["sample_type"]
    assert sample_type.required is False
    assert sample_type.options == ["control", "treatment"]
    # operators allowlist stays empty.
    assert config.operators.allowlist == []


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def _snapshot(local_root: Path) -> dict[str, bytes]:
    """Map relpath -> bytes for README/creation.json/payload (no equipment.json)."""
    snap: dict[str, bytes] = {}
    for path in sorted(local_root.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(local_root))
        if path.name == "equipment.json":
            # Writer-stamped timestamps are non-deterministic by design.
            continue
        snap[rel] = path.read_bytes()
    return snap


def test_determinism(sandbox: Path) -> None:
    generate_samples(sandbox, wipe=True, base_time=BASE_TIME)
    config = load_config(sandbox)
    local_root = Path(config.paths.local_root)
    first = _snapshot(local_root)
    first_paths = sorted(first)

    generate_samples(sandbox, wipe=True, base_time=BASE_TIME)
    second = _snapshot(local_root)
    second_paths = sorted(second)

    assert first_paths == second_paths
    assert first == second


def test_equipment_json_normalized_is_deterministic(sandbox: Path) -> None:
    """equipment.json differs only in the two writer-stamped timestamps."""
    generate_samples(sandbox, wipe=True, base_time=BASE_TIME)
    config = load_config(sandbox)
    local_root = Path(config.paths.local_root)

    def _norm(root: Path) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for eq_dir in root.iterdir():
            if not eq_dir.is_dir():
                continue
            eq = msgspec.json.decode(equipment_json_path(eq_dir).read_bytes(), type=EquipmentJson)
            d = msgspec.to_builtins(eq)
            d.pop("first_seen_at")
            d.pop("last_modified_at")
            out[eq_dir.name] = d
        return out

    first = _norm(local_root)
    generate_samples(sandbox, wipe=True, base_time=BASE_TIME)
    second = _norm(local_root)
    assert first == second


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------


def test_wipe_refuses_when_test_mode_unset(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TEST_MODE_ENV, raising=False)
    with pytest.raises(RuntimeError):
        generate_samples(sandbox, wipe=True, base_time=BASE_TIME)


def test_wipe_refuses_when_app_name_not_test(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import exlab_wizard.sample_data.generator as gen

    monkeypatch.setattr(gen, "_app_name", lambda: "exlab-wizard")
    with pytest.raises(RuntimeError):
        generate_samples(sandbox, wipe=True, base_time=BASE_TIME)


def test_wipe_refuses_when_target_escapes_sandbox(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # First seed without wipe to create config; then force the app_root (and
    # thus the derived data root) to point outside the sandbox so the
    # containment check fails.
    generate_samples(sandbox, wipe=False, base_time=BASE_TIME)
    config = load_config(sandbox)
    escaped = Config(
        paths=PathsConfig(app_root="/tmp"),
        orchestrator=config.orchestrator,
    )
    save_config(sandbox, escaped)
    with pytest.raises(RuntimeError, match="strict subpath"):
        generate_samples(sandbox, wipe=True, base_time=BASE_TIME)


def test_wipe_refuses_symlinked_target_escaping_sandbox(sandbox: Path, tmp_path: Path) -> None:
    """A seeded equipment dir replaced by a symlink out of the sandbox is refused."""
    import shutil

    generate_samples(sandbox, wipe=False, base_time=BASE_TIME)
    config = load_config(sandbox)
    local_root = Path(config.paths.local_root)

    # A directory OUTSIDE the sandbox (a sibling of the sandbox dir).
    outside = tmp_path.parent / f"escape-{tmp_path.name}"
    outside.mkdir()
    (outside / "precious.txt").write_text("do not delete", encoding="utf-8")
    try:
        victim = local_root / "TEST_TESTRIG"
        shutil.rmtree(victim)
        victim.symlink_to(outside, target_is_directory=True)
        with pytest.raises(RuntimeError, match="strict subpath"):
            generate_samples(sandbox, wipe=True, base_time=BASE_TIME)
        # The wipe resolved the symlink, saw it escape the sandbox, and refused
        # before touching anything -- the external dir is untouched.
        assert (outside / "precious.txt").is_file()
    finally:
        shutil.rmtree(outside, ignore_errors=True)


def test_seeded_equipment_roots_are_base_paths(sandbox: Path) -> None:
    """Seeded ``EquipmentConfig.nas_root`` and the data root are BASE roots.

    Consumers (orchestrator quiescence poller, validator) compose
    ``Path(config.paths.local_root) / equipment.id`` and ``build_creation_json``
    composes ``Path(nas_root) / equipment_id`` -- so the seeded roots must be the
    base (no id), matching a real operator config, or the seeded tree is
    invisible to run-walking. The data root is no longer per-equipment: it
    derives once from ``config.paths.app_root`` as ``<app_root>/data``.
    """
    generate_samples(sandbox, wipe=True, base_time=BASE_TIME)
    config = load_config(sandbox)
    sandbox_dir = sandbox.parent

    assert config.paths.local_root == str(sandbox_dir / "app" / "data")
    for entry in config.equipment:
        assert entry.nas_root == str(sandbox_dir / "nas")

    # creation.json ``paths.nas`` = base nas_root + prefixed id; ``paths.local``
    # is the run dir itself.
    local_root = Path(config.paths.local_root)
    for project_dir in _project_dirs(local_root):
        for run_dir in _run_dirs(project_dir):
            creation = _read_creation(run_dir)
            eq_id = run_dir.parents[2].name
            assert creation.paths.nas == str(sandbox_dir / "nas" / eq_id)
            assert creation.paths.local == str(run_dir)


def test_no_wipe_is_idempotent_and_nondestructive(sandbox: Path) -> None:
    """``wipe=False`` seeds, and a re-seed without wipe does not delete the tree."""
    generate_samples(sandbox, wipe=False, base_time=BASE_TIME)
    config = load_config(sandbox)
    local_root = Path(config.paths.local_root)
    assert (local_root / "TEST_TESTRIG").is_dir()

    # Drop a sentinel file an operator might have added between runs.
    sentinel = local_root / "TEST_TESTRIG" / "operator_added.txt"
    sentinel.write_text("keep me", encoding="utf-8")

    generate_samples(sandbox, wipe=False, base_time=BASE_TIME)
    assert sentinel.is_file(), "wipe=False must never delete an existing tree"


def test_generator_class_is_constructable(sandbox: Path) -> None:
    gen = SampleDataGenerator(config_path=sandbox, base_time=BASE_TIME)
    gen.generate(wipe=True)
    config = load_config(sandbox)
    local_root = Path(config.paths.local_root)
    assert (local_root / "TEST_TESTRIG").is_dir()
