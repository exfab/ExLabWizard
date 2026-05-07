"""Tests for ``exlab_wizard.plugins.base`` -- the plugin contract base class.

Pin the ABC shape so plugin authors get a stable signature: required
methods (``can_handle``, ``transform``) cannot be skipped, optional
hooks default to no-ops, and the data-class context payload is frozen.

Backend Spec §6.1.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import ClassVar

import pytest

from exlab_wizard.errors import PluginError as CanonicalPluginError
from exlab_wizard.errors import PluginInputRequired as CanonicalPluginInputRequired
from exlab_wizard.plugins.base import (
    FileChange,
    Plugin,
    PluginContext,
    PluginError,
    PluginInputRequired,
)
from exlab_wizard.plugins.logger import HostPluginLogger

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MinimalPlugin(Plugin):
    """Concrete subclass with only the two abstract methods implemented."""

    name = "minimal"
    version = "0.1.0"
    supported_extensions: ClassVar[list[str]] = [".txt"]
    api_version = "1"

    def can_handle(self, file_path, variables):  # type: ignore[override]
        return True

    def transform(self, file_path, ctx):  # type: ignore[override]
        return None


def _make_ctx(tmp_path: Path, **overrides) -> PluginContext:
    defaults = {
        "variables": {"operator": "asmith"},
        "dst_root": tmp_path,
        "answers_file": tmp_path / ".exlab-answers.yml",
        "template_name": "tpl",
        "template_version": "0.1.0",
        "run_kind": "experimental",
        "equipment_id": "EQ1",
        "project": "PROJ-1",
        "dry_run": False,
        "log": HostPluginLogger(name="test.plugin"),
    }
    defaults.update(overrides)
    return PluginContext(**defaults)


# ---------------------------------------------------------------------------
# Plugin ABC enforcement
# ---------------------------------------------------------------------------


def test_plugin_abc_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        Plugin()  # type: ignore[abstract]


def test_plugin_subclass_missing_can_handle_is_abstract() -> None:
    class Partial(Plugin):
        name = "partial"
        version = "0.1.0"
        supported_extensions: ClassVar[list[str]] = [".txt"]

        def transform(self, file_path, ctx):  # type: ignore[override]
            return None

    with pytest.raises(TypeError):
        Partial()  # type: ignore[abstract]


def test_plugin_subclass_missing_transform_is_abstract() -> None:
    class Partial(Plugin):
        name = "partial"
        version = "0.1.0"
        supported_extensions: ClassVar[list[str]] = [".txt"]

        def can_handle(self, file_path, variables):  # type: ignore[override]
            return True

    with pytest.raises(TypeError):
        Partial()  # type: ignore[abstract]


def test_minimal_plugin_can_be_instantiated() -> None:
    """A subclass with both abstract methods implemented is instantiable."""
    plugin = _MinimalPlugin()
    assert plugin.name == "minimal"


# ---------------------------------------------------------------------------
# Default validate_variables behavior (§6.1.3)
# ---------------------------------------------------------------------------


def test_default_validate_variables_returns_empty_when_no_required() -> None:
    plugin = _MinimalPlugin()
    assert plugin.validate_variables({"operator": "asmith"}) == []


def test_default_validate_variables_reports_missing_required() -> None:
    class WithRequired(_MinimalPlugin):
        required_variables: ClassVar[list[str]] = ["operator", "run_date"]

    plugin = WithRequired()
    errors = plugin.validate_variables({"operator": "asmith"})
    assert len(errors) == 1
    assert "run_date" in errors[0]


def test_default_validate_variables_reports_empty_string_as_missing() -> None:
    """An empty string is treated as a missing value (per spec: 'missing or empty')."""

    class WithRequired(_MinimalPlugin):
        required_variables: ClassVar[list[str]] = ["project_name"]

    plugin = WithRequired()
    errors = plugin.validate_variables({"project_name": ""})
    assert len(errors) == 1
    assert "project_name" in errors[0]


def test_default_validate_variables_lists_all_missing() -> None:
    class WithRequired(_MinimalPlugin):
        required_variables: ClassVar[list[str]] = ["a", "b", "c"]

    plugin = WithRequired()
    errors = plugin.validate_variables({})
    assert len(errors) == 3


# ---------------------------------------------------------------------------
# Default lifecycle hooks are no-ops
# ---------------------------------------------------------------------------


def test_pre_transform_all_default_is_noop(tmp_path: Path) -> None:
    plugin = _MinimalPlugin()
    ctx = _make_ctx(tmp_path)
    assert plugin.pre_transform_all(ctx) is None


def test_post_transform_all_default_is_noop(tmp_path: Path) -> None:
    plugin = _MinimalPlugin()
    ctx = _make_ctx(tmp_path)
    assert plugin.post_transform_all(ctx) is None


def test_on_plugin_failure_default_is_noop(tmp_path: Path) -> None:
    plugin = _MinimalPlugin()
    ctx = _make_ctx(tmp_path)
    # Default returns None and does not re-raise the input exception.
    assert plugin.on_plugin_failure(RuntimeError("boom"), ctx) is None


def test_describe_changes_default_returns_single_modify_change(tmp_path: Path) -> None:
    plugin = _MinimalPlugin()
    ctx = _make_ctx(tmp_path)
    target = tmp_path / "data.txt"
    changes = plugin.describe_changes(target, ctx)
    assert len(changes) == 1
    assert changes[0].path == target
    assert changes[0].kind == "modify"
    assert plugin.name in changes[0].summary


# ---------------------------------------------------------------------------
# FileChange dataclass
# ---------------------------------------------------------------------------


def test_filechange_is_frozen_dataclass(tmp_path: Path) -> None:
    fc = FileChange(path=tmp_path / "x.txt", kind="modify", summary="touched")
    with pytest.raises(dataclasses.FrozenInstanceError):
        fc.kind = "create"  # type: ignore[misc]


def test_filechange_default_detail_is_empty_dict(tmp_path: Path) -> None:
    fc = FileChange(path=tmp_path / "x.txt", kind="modify", summary="touched")
    assert fc.detail == {}


def test_filechange_accepts_detail_payload(tmp_path: Path) -> None:
    fc = FileChange(
        path=tmp_path / "x.txt",
        kind="modify",
        summary="wrote 2 cells",
        detail={"writes": [{"cell": "B7", "value": "asmith"}]},
    )
    assert fc.detail["writes"][0]["cell"] == "B7"


# ---------------------------------------------------------------------------
# PluginContext dataclass
# ---------------------------------------------------------------------------


def test_plugincontext_is_frozen_dataclass(tmp_path: Path) -> None:
    ctx = _make_ctx(tmp_path)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.dry_run = True  # type: ignore[misc]


def test_plugincontext_carries_full_session_metadata(tmp_path: Path) -> None:
    ctx = _make_ctx(
        tmp_path,
        template_name="my-tpl",
        template_version="2.3.4",
        run_kind="test",
        equipment_id="EQ-X",
        project="PROJ-42",
        dry_run=True,
    )
    assert ctx.template_name == "my-tpl"
    assert ctx.template_version == "2.3.4"
    assert ctx.run_kind == "test"
    assert ctx.equipment_id == "EQ-X"
    assert ctx.project == "PROJ-42"
    assert ctx.dry_run is True


# ---------------------------------------------------------------------------
# Error class re-exports
# ---------------------------------------------------------------------------


def test_pluginerror_reexport_is_canonical_class() -> None:
    """The plugins package re-exports the same class object as ``errors``."""
    assert PluginError is CanonicalPluginError


def test_plugininputrequired_reexport_is_canonical_class() -> None:
    assert PluginInputRequired is CanonicalPluginInputRequired


def test_plugininputrequired_carries_fields_and_reason() -> None:
    """PluginInputRequired stores both the field list and the operator-readable reason."""
    fields = [{"id": "calibration_id", "label": "Calibration ID", "type": "string"}]
    exc = PluginInputRequired(fields=fields, reason="missing calibration ID")
    assert exc.fields == fields
    assert exc.reason == "missing calibration ID"


# ---------------------------------------------------------------------------
# Class attributes / api_version default
# ---------------------------------------------------------------------------


def test_plugin_api_version_defaults_to_one() -> None:
    """The class-level api_version default matches the host's current version."""
    assert _MinimalPlugin.api_version == "1"


def test_plugin_required_variables_default_is_empty_list() -> None:
    assert _MinimalPlugin.required_variables == []


def test_plugin_optional_variables_default_is_empty_list() -> None:
    assert _MinimalPlugin.optional_variables == []
