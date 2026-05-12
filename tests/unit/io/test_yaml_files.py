"""Tests for ``exlab_wizard.io.yaml_files``."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from exlab_wizard.io.yaml_files import load_yaml_manifest


def test_load_yaml_manifest_returns_dict(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yml"
    path.write_text("name: example\nversion: 1\n", encoding="utf-8")
    assert load_yaml_manifest(path) == {"name": "example", "version": 1}


def test_load_yaml_manifest_empty_file_returns_empty_dict(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yml"
    path.write_text("", encoding="utf-8")
    assert load_yaml_manifest(path) == {}


def test_load_yaml_manifest_non_dict_returns_empty_dict(tmp_path: Path) -> None:
    """A YAML file with a top-level scalar/list is not a manifest."""
    path = tmp_path / "manifest.yml"
    path.write_text("- a\n- b\n", encoding="utf-8")
    assert load_yaml_manifest(path) == {}


def test_load_yaml_manifest_propagates_yaml_error(tmp_path: Path) -> None:
    path = tmp_path / "manifest.yml"
    path.write_text("name: [unterminated\n", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        load_yaml_manifest(path)


def test_load_yaml_manifest_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_yaml_manifest(tmp_path / "missing.yml")
