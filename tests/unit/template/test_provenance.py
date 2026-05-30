"""Unit tests for :func:`copy_template_into_instance`.

Verify the frozen verbatim template copy lands under the instance's typed
provenance store (``<dst>/.exlab-wizard/templates/<own_type>/<name>/``),
that ``.jinja`` / ``copier.yml`` / nested files are copied unchanged, and
that the returned path is the instance-relative POSIX string.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from exlab_wizard.template.provenance import copy_template_into_instance


@dataclass(frozen=True)
class _StubResolved:
    """Lightweight stand-in -- the function only reads ``name`` / ``path``."""

    name: str
    path: Path


def _make_template(root: Path) -> None:
    """Write a minimal template tree (copier.yml + .jinja + nested file)."""
    root.mkdir(parents=True)
    (root / "copier.yml").write_text(
        '_exlab_type: "run"\n_exlab_version: "1.0"\n',
        encoding="utf-8",
    )
    (root / "body.txt.jinja").write_text("hello {{ run_id }}\n", encoding="utf-8")
    sub = root / "sub"
    sub.mkdir()
    (sub / "nested.txt").write_text("nested\n", encoding="utf-8")


def test_copy_template_into_instance_copies_tree_and_returns_rel_path(
    tmp_path: Path,
) -> None:
    src = tmp_path / "templates" / "confocal_run"
    _make_template(src)
    dst = tmp_path / "instance"
    dst.mkdir()
    resolved = _StubResolved(name="confocal_run", path=src)

    rel = copy_template_into_instance(resolved, dst, "run")

    assert rel == ".exlab-wizard/templates/run/confocal_run"

    copy_root = dst / ".exlab-wizard" / "templates" / "run" / "confocal_run"
    assert (copy_root / "copier.yml").is_file()
    # The .jinja source is copied verbatim (not rendered).
    jinja = copy_root / "body.txt.jinja"
    assert jinja.is_file()
    assert jinja.read_text(encoding="utf-8") == "hello {{ run_id }}\n"
    # Nested subdirectory files come along too.
    assert (copy_root / "sub" / "nested.txt").read_text(encoding="utf-8") == "nested\n"


def test_copy_template_into_instance_uses_project_segment(tmp_path: Path) -> None:
    src = tmp_path / "templates" / "project_basic"
    _make_template(src)
    dst = tmp_path / "instance"
    dst.mkdir()
    resolved = _StubResolved(name="project_basic", path=src)

    rel = copy_template_into_instance(resolved, dst, "project")

    assert rel == ".exlab-wizard/templates/project/project_basic"
    assert (
        dst / ".exlab-wizard" / "templates" / "project" / "project_basic" / "copier.yml"
    ).is_file()


def test_copy_template_into_instance_is_idempotent(tmp_path: Path) -> None:
    """A second copy over an existing target succeeds (dirs_exist_ok)."""
    src = tmp_path / "templates" / "run_a"
    _make_template(src)
    dst = tmp_path / "instance"
    dst.mkdir()
    resolved = _StubResolved(name="run_a", path=src)

    first = copy_template_into_instance(resolved, dst, "run")
    second = copy_template_into_instance(resolved, dst, "run")

    assert first == second == ".exlab-wizard/templates/run/run_a"


def test_copy_template_into_instance_missing_source_raises(tmp_path: Path) -> None:
    dst = tmp_path / "instance"
    dst.mkdir()
    resolved = _StubResolved(name="ghost", path=tmp_path / "does-not-exist")

    with pytest.raises(FileNotFoundError):
        copy_template_into_instance(resolved, dst, "run")
