"""Tests for ``exlab_wizard.plugins.registry`` -- manifest-driven discovery.

Pin the registry's behavior against a tmpfs-built scaffold:

- valid manifest --> plugin loads.
- missing / malformed manifest --> rejected.
- invalid api_version, oversized timeout/memory, network without opt-in --> rejected.
- lab plugin shadows bundled plugin on name collision.
- ``candidates_for`` matches by ``supported_extensions``.
- ``get`` / ``list_all`` expose the loaded records.

Backend Spec §6.1.2, §6.2.1.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from exlab_wizard.constants import PLUGIN_MANIFEST_NAME
from exlab_wizard.plugins.registry import (
    PluginManifest,
    PluginPlan,
    PluginRecord,
    PluginRegistry,
    RegistryReport,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_plugin(
    root: Path,
    name: str,
    *,
    version: str = "0.1.0",
    extensions: list[str] | None = None,
    api_version: str = "1",
    required_variables: list[str] | None = None,
    optional_variables: list[str] | None = None,
    timeout_seconds: int = 30,
    memory_mb: int = 512,
    network: bool = False,
    omit_field: str | None = None,
    extra_yaml: str = "",
) -> Path:
    """Write a minimal plugin scaffold and return the plugin directory."""
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    extensions = extensions if extensions is not None else [".txt"]
    required_variables = required_variables if required_variables is not None else []
    optional_variables = optional_variables if optional_variables is not None else []

    fields: dict[str, str] = {
        "name": f'"{name}"',
        "version": f'"{version}"',
        "author": '"Test"',
        "description": '"Test plugin"',
        "supported_extensions": "[" + ", ".join(f'"{e}"' for e in extensions) + "]",
        "api_version": f'"{api_version}"',
        "required_variables": (
            "[" + ", ".join(f'"{v}"' for v in required_variables) + "]"
        ),
        "optional_variables": (
            "[" + ", ".join(f'"{v}"' for v in optional_variables) + "]"
        ),
    }
    if omit_field is not None:
        fields.pop(omit_field, None)

    body = "\n".join(f"{k}: {v}" for k, v in fields.items())
    isolation = (
        "isolation:\n"
        f"  timeout_seconds: {timeout_seconds}\n"
        f"  memory_mb: {memory_mb}\n"
        f"  network: {'true' if network else 'false'}\n"
    )
    manifest_text = body + "\n" + isolation
    if extra_yaml:
        manifest_text = manifest_text + "\n" + extra_yaml
    (plugin_dir / PLUGIN_MANIFEST_NAME).write_text(manifest_text, encoding="utf-8")
    return plugin_dir


# ---------------------------------------------------------------------------
# Loading: happy path
# ---------------------------------------------------------------------------


def test_loads_a_valid_plugin(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    _write_plugin(bundled, "valid_plugin")
    registry = PluginRegistry(bundled_dir=bundled, lab_dir=None)

    report = registry.reload()

    assert "valid_plugin" in report.loaded
    assert report.rejected == []
    record = registry.get("valid_plugin")
    assert record is not None
    assert isinstance(record.manifest, PluginManifest)
    assert record.manifest.name == "valid_plugin"
    assert record.manifest.supported_extensions == [".txt"]
    assert record.source_root == "bundled"


def test_loaded_record_carries_isolation_block(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    _write_plugin(bundled, "iso_plugin", timeout_seconds=42, memory_mb=128)
    registry = PluginRegistry(bundled_dir=bundled, lab_dir=None)
    registry.reload()

    record = registry.get("iso_plugin")
    assert record is not None
    iso = record.manifest.isolation
    assert iso == {"timeout_seconds": 42, "memory_mb": 128, "network": False}


def test_required_and_optional_variables_round_trip(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    _write_plugin(
        bundled,
        "vars_plugin",
        required_variables=["operator", "run_date"],
        optional_variables=["sample_type"],
    )
    registry = PluginRegistry(bundled_dir=bundled, lab_dir=None)
    registry.reload()

    record = registry.get("vars_plugin")
    assert record is not None
    assert record.manifest.required_variables == ["operator", "run_date"]
    assert record.manifest.optional_variables == ["sample_type"]


# ---------------------------------------------------------------------------
# Rejections
# ---------------------------------------------------------------------------


def test_rejects_plugin_directory_without_manifest(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    plugin_dir = bundled / "no_manifest"
    plugin_dir.mkdir(parents=True)
    registry = PluginRegistry(bundled_dir=bundled, lab_dir=None)

    report = registry.reload()

    assert report.loaded == []
    assert any(name == "no_manifest" for name, _ in report.rejected)


def test_rejects_plugin_with_malformed_yaml(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    plugin_dir = bundled / "broken"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / PLUGIN_MANIFEST_NAME).write_text(": : :\n  bad: [\n", encoding="utf-8")
    registry = PluginRegistry(bundled_dir=bundled, lab_dir=None)

    report = registry.reload()
    assert report.loaded == []
    assert any(name == "broken" for name, _ in report.rejected)


def test_rejects_plugin_missing_required_field(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    _write_plugin(bundled, "no_version", omit_field="version")
    registry = PluginRegistry(bundled_dir=bundled, lab_dir=None)

    report = registry.reload()
    assert report.loaded == []
    assert any("missing required field" in reason for _, reason in report.rejected)


def test_rejects_plugin_with_unsupported_api_version(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    _write_plugin(bundled, "future_plugin", api_version="999")
    registry = PluginRegistry(bundled_dir=bundled, lab_dir=None)

    report = registry.reload()
    assert "future_plugin" not in report.loaded
    assert any("api_version" in reason for _, reason in report.rejected)


def test_rejects_plugin_with_invalid_name(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    plugin_dir = bundled / "bad_name"
    plugin_dir.mkdir(parents=True)
    # Write manifest with an internally-invalid name (contains a space).
    (plugin_dir / PLUGIN_MANIFEST_NAME).write_text(
        'name: "bad name with space"\n'
        'version: "0.1.0"\n'
        'supported_extensions: [".txt"]\n'
        'api_version: "1"\n',
        encoding="utf-8",
    )
    registry = PluginRegistry(bundled_dir=bundled, lab_dir=None)
    report = registry.reload()
    assert report.loaded == []
    assert report.rejected != []


def test_rejects_plugin_with_timeout_above_cap(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    _write_plugin(bundled, "slow_plugin", timeout_seconds=999)
    registry = PluginRegistry(bundled_dir=bundled, lab_dir=None)
    report = registry.reload()
    assert "slow_plugin" not in report.loaded
    assert any("timeout_seconds" in reason for _, reason in report.rejected)


def test_rejects_plugin_with_memory_above_cap(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    _write_plugin(bundled, "fat_plugin", memory_mb=99_999)
    registry = PluginRegistry(bundled_dir=bundled, lab_dir=None)
    report = registry.reload()
    assert "fat_plugin" not in report.loaded
    assert any("memory_mb" in reason for _, reason in report.rejected)


def test_rejects_supported_extensions_when_not_a_list_of_strings(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    plugin_dir = bundled / "bad_exts"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / PLUGIN_MANIFEST_NAME).write_text(
        'name: "bad_exts"\n'
        'version: "0.1.0"\n'
        "supported_extensions: 42\n"
        'api_version: "1"\n',
        encoding="utf-8",
    )
    registry = PluginRegistry(bundled_dir=bundled, lab_dir=None)
    report = registry.reload()
    assert report.loaded == []


# ---------------------------------------------------------------------------
# Network-opt-in gate (§6.3.3)
# ---------------------------------------------------------------------------


def test_rejects_network_plugin_when_allow_network_false(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    _write_plugin(bundled, "online_plugin", network=True)
    registry = PluginRegistry(bundled_dir=bundled, lab_dir=None, allow_network=False)
    report = registry.reload()
    assert "online_plugin" not in report.loaded
    assert any("network" in reason.lower() for _, reason in report.rejected)


def test_loads_network_plugin_when_allow_network_true(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    _write_plugin(bundled, "online_plugin", network=True)
    registry = PluginRegistry(bundled_dir=bundled, lab_dir=None, allow_network=True)
    report = registry.reload()
    assert "online_plugin" in report.loaded
    record = registry.get("online_plugin")
    assert record is not None
    assert record.manifest.isolation["network"] is True


# ---------------------------------------------------------------------------
# Bundled vs. lab precedence (§6.2.1.4)
# ---------------------------------------------------------------------------


def test_lab_dir_wins_on_name_collision(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    lab = tmp_path / "lab"
    _write_plugin(bundled, "shared", version="0.1.0")
    _write_plugin(lab, "shared", version="9.9.9")

    registry = PluginRegistry(bundled_dir=bundled, lab_dir=lab)
    registry.reload()

    record = registry.get("shared")
    assert record is not None
    assert record.manifest.version == "9.9.9"
    assert record.source_root == "lab"


def test_bundled_only_when_no_lab_collision(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    lab = tmp_path / "lab"
    lab.mkdir()
    _write_plugin(bundled, "bundled_only")

    registry = PluginRegistry(bundled_dir=bundled, lab_dir=lab)
    registry.reload()

    record = registry.get("bundled_only")
    assert record is not None
    assert record.source_root == "bundled"


def test_lab_only_plugins_are_loaded_too(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    lab = tmp_path / "lab"
    _write_plugin(lab, "lab_specific")

    registry = PluginRegistry(bundled_dir=bundled, lab_dir=lab)
    registry.reload()

    record = registry.get("lab_specific")
    assert record is not None
    assert record.source_root == "lab"


# ---------------------------------------------------------------------------
# Reload reset behavior
# ---------------------------------------------------------------------------


def test_reload_replaces_previous_state(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    _write_plugin(bundled, "first")
    registry = PluginRegistry(bundled_dir=bundled, lab_dir=None)
    registry.reload()
    assert registry.get("first") is not None

    # Remove the plugin directory and confirm reload drops the record.
    import shutil

    shutil.rmtree(bundled / "first")
    registry.reload()
    assert registry.get("first") is None


def test_reload_returns_registry_report_dataclass(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    _write_plugin(bundled, "alpha")
    registry = PluginRegistry(bundled_dir=bundled, lab_dir=None)
    report = registry.reload()
    assert isinstance(report, RegistryReport)
    assert "alpha" in report.loaded


# ---------------------------------------------------------------------------
# get / list_all
# ---------------------------------------------------------------------------


def test_get_returns_none_for_unknown_plugin(tmp_path: Path) -> None:
    registry = PluginRegistry(bundled_dir=None, lab_dir=None)
    registry.reload()
    assert registry.get("does_not_exist") is None


def test_list_all_returns_records_sorted_by_name(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    _write_plugin(bundled, "zeta")
    _write_plugin(bundled, "alpha")
    _write_plugin(bundled, "mu")
    registry = PluginRegistry(bundled_dir=bundled, lab_dir=None)
    registry.reload()
    names = [r.manifest.name for r in registry.list_all()]
    assert names == ["alpha", "mu", "zeta"]


def test_list_all_returns_record_instances(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    _write_plugin(bundled, "one")
    registry = PluginRegistry(bundled_dir=bundled, lab_dir=None)
    registry.reload()
    records = registry.list_all()
    assert len(records) == 1
    assert isinstance(records[0], PluginRecord)


# ---------------------------------------------------------------------------
# candidates_for: dispatch matching by extension
# ---------------------------------------------------------------------------


def test_candidates_for_matches_by_extension(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    _write_plugin(bundled, "txt_handler", extensions=[".txt"])
    _write_plugin(bundled, "xlsx_handler", extensions=[".xlsx"])
    registry = PluginRegistry(bundled_dir=bundled, lab_dir=None)
    registry.reload()

    files = [
        tmp_path / "data.txt",
        tmp_path / "metadata.xlsx",
        tmp_path / "notes.txt",
    ]
    plans = registry.candidates_for(files)
    by_name = {p.record.manifest.name: p for p in plans}
    assert "txt_handler" in by_name
    assert "xlsx_handler" in by_name
    assert {p.name for p in by_name["txt_handler"].matching_files} == {"data.txt", "notes.txt"}
    assert {p.name for p in by_name["xlsx_handler"].matching_files} == {"metadata.xlsx"}


def test_candidates_for_excludes_plugins_with_zero_matches(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    _write_plugin(bundled, "csv_handler", extensions=[".csv"])
    registry = PluginRegistry(bundled_dir=bundled, lab_dir=None)
    registry.reload()

    plans = registry.candidates_for([tmp_path / "data.txt"])
    assert plans == []


def test_candidates_for_returns_pluginplan_instances(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    _write_plugin(bundled, "h", extensions=[".txt"])
    registry = PluginRegistry(bundled_dir=bundled, lab_dir=None)
    registry.reload()

    plans = registry.candidates_for([tmp_path / "x.txt"])
    assert len(plans) == 1
    assert isinstance(plans[0], PluginPlan)
    assert plans[0].record.manifest.name == "h"


def test_candidates_for_supports_multi_extension_plugin(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    _write_plugin(bundled, "multi", extensions=[".txt", ".md"])
    registry = PluginRegistry(bundled_dir=bundled, lab_dir=None)
    registry.reload()

    plans = registry.candidates_for([tmp_path / "a.txt", tmp_path / "b.md", tmp_path / "c.csv"])
    assert len(plans) == 1
    assert {p.name for p in plans[0].matching_files} == {"a.txt", "b.md"}


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_registry_with_no_dirs_loads_nothing(tmp_path: Path) -> None:
    registry = PluginRegistry(bundled_dir=None, lab_dir=None)
    report = registry.reload()
    assert report.loaded == []
    assert report.rejected == []
    assert registry.list_all() == []


def test_registry_skips_files_in_root(tmp_path: Path) -> None:
    """A non-directory entry inside the plugin root must be ignored, not crashed on."""
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    (bundled / "stray_file.txt").write_text("not a plugin", encoding="utf-8")
    _write_plugin(bundled, "real_plugin")

    registry = PluginRegistry(bundled_dir=bundled, lab_dir=None)
    report = registry.reload()
    assert "real_plugin" in report.loaded


def test_registry_handles_nonexistent_dir(tmp_path: Path) -> None:
    """A configured plugin root that doesn't exist is treated as empty (no crash)."""
    registry = PluginRegistry(bundled_dir=tmp_path / "ghost", lab_dir=None)
    report = registry.reload()
    assert report.loaded == []
    assert report.rejected == []


@pytest.mark.parametrize(
    ("ts", "mb"),
    [
        (300, 2048),  # exactly at the caps -- allowed
        (1, 1),  # well under
    ],
)
def test_registry_accepts_isolation_at_cap_boundary(tmp_path: Path, ts: int, mb: int) -> None:
    bundled = tmp_path / "bundled"
    _write_plugin(bundled, "edge", timeout_seconds=ts, memory_mb=mb)
    registry = PluginRegistry(bundled_dir=bundled, lab_dir=None)
    report = registry.reload()
    assert "edge" in report.loaded


def test_pluginplan_dataclass_shape(tmp_path: Path) -> None:
    """PluginPlan exposes ``record`` and ``matching_files`` as named attributes."""
    bundled = tmp_path / "bundled"
    _write_plugin(bundled, "shape", extensions=[".txt"])
    registry = PluginRegistry(bundled_dir=bundled, lab_dir=None)
    registry.reload()
    plan = registry.candidates_for([tmp_path / "a.txt"])[0]
    assert hasattr(plan, "record")
    assert hasattr(plan, "matching_files")


def test_pluginrecord_dataclass_shape(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    _write_plugin(bundled, "rec_shape")
    registry = PluginRegistry(bundled_dir=bundled, lab_dir=None)
    registry.reload()
    rec = registry.get("rec_shape")
    assert rec is not None
    assert isinstance(rec, PluginRecord)
    assert rec.plugin_class is None  # host-side scan: no Python import
    assert rec.source_path.is_dir()
