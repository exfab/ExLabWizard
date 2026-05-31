"""Tests for the author-time template service.

The authoring service is the non-UI backend the GUI template editor
drives. The load-bearing assertions here are the security and durability
invariants: every path flows through :func:`_safe_target` (no traversal,
no escape), every gated write refuses an invalid manifest / Jinja before
it touches disk, uploads honour the size / count caps, and an
optimistic-concurrency write loses the stat race rather than clobbering a
concurrent edit.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from exlab_wizard.constants import COPIER_MANIFEST_NAME, TemplateType
from exlab_wizard.template import authoring
from exlab_wizard.template.authoring import (
    StaleEditError,
    TemplateAuthoringError,
    UnsafePathError,
    create_template_dir,
    delete_path,
    is_editable,
    list_files,
    read_content,
    read_manifest,
    rename_path,
    upload_file,
    write_content_file,
    write_manifest,
)
from exlab_wizard.template.manifest import TemplateManifest


@pytest.fixture
def template(tmp_path: Path) -> Path:
    """Return a freshly scaffolded project template directory."""
    return create_template_dir(tmp_path, name="tpl", template_type="project")


# ---------------------------------------------------------------------------
# _safe_target -- the path chokepoint attack table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel",
    [
        "../escape",
        "/abs/path",
        "a/../../b",
        "CON",
        "foo.",  # trailing dot
        "a\x00b",  # control char
        "café.md",  # non-ASCII
        "",  # empty
        "a//b",  # empty segment
    ],
)
def test_safe_target_rejects_unsafe(template: Path, rel: str) -> None:
    with pytest.raises(UnsafePathError):
        authoring._safe_target(template, rel)


def test_safe_target_accepts_legit_nested(template: Path) -> None:
    resolved = authoring._safe_target(template, "sub/dir/file.md")
    assert resolved == (template / "sub" / "dir" / "file.md").resolve()
    assert template.resolve() in resolved.parents


# ---------------------------------------------------------------------------
# create_template_dir -- scaffold + name validation
# ---------------------------------------------------------------------------


def test_create_template_dir_scaffolds_manifest_and_content(tmp_path: Path) -> None:
    root = create_template_dir(tmp_path, name="proj", template_type="project")
    assert root == tmp_path / "proj"
    assert (root / COPIER_MANIFEST_NAME).is_file()
    content = [p for p in root.iterdir() if p.name != COPIER_MANIFEST_NAME]
    assert len(content) == 1
    assert content[0].read_text(encoding="utf-8")


def test_create_template_dir_rejects_unsafe_name(tmp_path: Path) -> None:
    with pytest.raises(UnsafePathError):
        create_template_dir(tmp_path, name="bad/name", template_type="project")


def test_create_template_dir_rejects_duplicate(tmp_path: Path) -> None:
    create_template_dir(tmp_path, name="dup", template_type="project")
    with pytest.raises(ValueError, match="already exists"):
        create_template_dir(tmp_path, name="dup", template_type="project")


def test_create_run_template_requires_scope(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="run_scope"):
        create_template_dir(tmp_path, name="r", template_type="run")


# ---------------------------------------------------------------------------
# manifest round-trip + lint gate
# ---------------------------------------------------------------------------


def test_manifest_round_trip(template: Path) -> None:
    manifest, stat = read_manifest(template)
    assert manifest.exlab_type == TemplateType.PROJECT.value
    assert isinstance(stat, tuple) and len(stat) == 2


def test_write_manifest_lint_error_not_written(template: Path) -> None:
    copier_path = template / COPIER_MANIFEST_NAME
    before = copier_path.read_bytes()
    # An empty _exlab_type is a lint ERROR (template_type_missing).
    bad = TemplateManifest(exlab_type="", exlab_version="1.0")
    with pytest.raises(TemplateAuthoringError, match="lint errors"):
        write_manifest(template, bad)
    # The original file is unchanged -- the bad manifest never landed.
    assert copier_path.read_bytes() == before


def test_write_manifest_valid_persists(template: Path) -> None:
    manifest, stat = read_manifest(template)
    updated = TemplateManifest(
        exlab_type=manifest.exlab_type,
        exlab_version=manifest.exlab_version,
        description="now described",
    )
    write_manifest(template, updated, expected_stat=stat)
    reread, _ = read_manifest(template)
    assert reread.description == "now described"


# ---------------------------------------------------------------------------
# content read / write -- atomic round-trip + jinja gate
# ---------------------------------------------------------------------------


def test_content_round_trip(template: Path) -> None:
    written = write_content_file(template, "doc.md", "hello\nworld\n")
    assert written == (template / "doc.md").resolve()
    text, stat = read_content(written)
    assert text == "hello\nworld\n"
    assert isinstance(stat, tuple) and len(stat) == 2


def test_write_content_nested_creates_dirs(template: Path) -> None:
    written = write_content_file(template, "a/b/c.txt", "x")
    assert written.is_file()
    assert written.read_text(encoding="utf-8") == "x"


def test_write_content_rejects_unsafe(template: Path) -> None:
    with pytest.raises(UnsafePathError):
        write_content_file(template, "../escape.md", "x")


def test_write_content_jinja_syntax_error_refused(template: Path) -> None:
    with pytest.raises(TemplateAuthoringError, match="Jinja syntax error"):
        write_content_file(template, "broken.md.jinja", "{% if %}")
    assert not (template / "broken.md.jinja").exists()


def test_write_content_jinja_valid_persists(template: Path) -> None:
    written = write_content_file(template, "ok.md.jinja", "{{ name }}\n")
    assert written.read_text(encoding="utf-8") == "{{ name }}\n"


# ---------------------------------------------------------------------------
# upload -- verbatim, render-as-template, caps
# ---------------------------------------------------------------------------


def test_upload_verbatim_keeps_bytes(template: Path) -> None:
    data = b"\x00\x01binary\xff"
    written = upload_file(template, "data.bin", data)
    assert written.read_bytes() == data
    assert written.suffix == ".bin"


def test_upload_render_as_template_appends_jinja(template: Path) -> None:
    written = upload_file(template, "report.md", b"# Report\n", render_as_template=True)
    assert written.name == "report.md.jinja"


def test_upload_render_as_template_idempotent_on_jinja(template: Path) -> None:
    written = upload_file(template, "x.md.jinja", b"{{ a }}", render_as_template=True)
    assert written.name == "x.md.jinja"


def test_upload_oversize_rejected(template: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(authoring, "TEMPLATE_UPLOAD_MAX_BYTES", 8)
    with pytest.raises(TemplateAuthoringError, match="cap"):
        upload_file(template, "big.bin", b"0123456789")


def test_upload_count_cap_rejected(template: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(authoring, "TEMPLATE_MAX_FILES", 1)
    # The scaffold already holds copier.yml + notes.md.jinja (>= 1).
    with pytest.raises(TemplateAuthoringError, match="cap"):
        upload_file(template, "another.txt", b"x")


def test_upload_traversal_rejected(template: Path) -> None:
    with pytest.raises(UnsafePathError):
        upload_file(template, "../evil.txt", b"x")


def test_upload_jinja_bad_syntax_rejected(template: Path) -> None:
    with pytest.raises(TemplateAuthoringError, match="Jinja syntax error"):
        upload_file(template, "t.txt", b"{% for %}", render_as_template=True)


def test_upload_binary_jinja_skips_parse(template: Path) -> None:
    # Non-UTF-8 bytes in a .jinja upload: the parse is skipped, bytes land.
    data = b"\xff\xfe\x00binary"
    written = upload_file(template, "blob", data, render_as_template=True)
    assert written.name == "blob.jinja"
    assert written.read_bytes() == data


# ---------------------------------------------------------------------------
# stale-edit guard
# ---------------------------------------------------------------------------


def test_stale_edit_content_raises(template: Path) -> None:
    write_content_file(template, "note.md", "v1\n")
    _, stat = read_content(template / "note.md")
    # Modify out-of-band with a different size + bumped mtime.
    target = template / "note.md"
    target.write_text("a much longer second version\n", encoding="utf-8")
    new_mtime = stat[0] + 5
    os.utime(target, (new_mtime, new_mtime))
    with pytest.raises(StaleEditError):
        write_content_file(template, "note.md", "v3\n", expected_stat=stat)


def test_stale_edit_manifest_raises(template: Path) -> None:
    manifest, stat = read_manifest(template)
    # Mutate copier.yml out-of-band so the stat no longer matches.
    copier_path = template / COPIER_MANIFEST_NAME
    time.sleep(0.01)
    copier_path.write_text(
        copier_path.read_text(encoding="utf-8") + "\n# touched\n", encoding="utf-8"
    )
    new = stat[0] + 5
    os.utime(copier_path, (new, new))
    with pytest.raises(StaleEditError):
        write_manifest(template, manifest, expected_stat=stat)


def test_stale_edit_none_skips_guard(template: Path) -> None:
    # No expected_stat -> no guard, even after an out-of-band change.
    write_content_file(template, "f.md", "x")
    written = write_content_file(template, "f.md", "y", expected_stat=None)
    assert written.read_text(encoding="utf-8") == "y"


# ---------------------------------------------------------------------------
# rename / delete
# ---------------------------------------------------------------------------


def test_rename_moves_file(template: Path) -> None:
    write_content_file(template, "old.md", "x")
    dst = rename_path(template, "old.md", "new.md")
    assert dst.name == "new.md"
    assert not (template / "old.md").exists()
    assert (template / "new.md").read_text(encoding="utf-8") == "x"


def test_rename_copier_yml_refused(template: Path) -> None:
    with pytest.raises(TemplateAuthoringError, match=r"copier\.yml"):
        rename_path(template, COPIER_MANIFEST_NAME, "elsewhere.yml")


def test_delete_file(template: Path) -> None:
    write_content_file(template, "gone.md", "x")
    delete_path(template, "gone.md")
    assert not (template / "gone.md").exists()


def test_delete_dir_recursive(template: Path) -> None:
    write_content_file(template, "d/inner.md", "x")
    delete_path(template, "d")
    assert not (template / "d").exists()


def test_delete_copier_yml_refused(template: Path) -> None:
    with pytest.raises(TemplateAuthoringError, match=r"copier\.yml"):
        delete_path(template, COPIER_MANIFEST_NAME)


# ---------------------------------------------------------------------------
# editable classification + list_files
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a.md", True),
        ("a.jinja", True),
        ("a.md.jinja", True),
        ("a.csv", True),
        ("a.json", True),
        ("a.xlsx", False),
        ("a.png", False),
        ("a", False),
    ],
)
def test_is_editable(name: str, expected: bool) -> None:
    assert is_editable(Path(name)) is expected


def test_list_files_shape_and_sort(template: Path) -> None:
    write_content_file(template, "sub/b.md", "b")
    write_content_file(template, "a.txt", "a")
    entries = list_files(template)
    rels = [e["rel"] for e in entries]
    assert rels == sorted(rels)
    by_rel = {e["rel"]: e for e in entries}
    assert by_rel["a.txt"]["editable"] is True
    assert by_rel["a.txt"]["is_dir"] is False
    assert by_rel["a.txt"]["size"] == 1
    assert by_rel["sub"]["is_dir"] is True
    assert by_rel["sub"]["editable"] is False


def test_list_files_empty_for_missing_dir(tmp_path: Path) -> None:
    assert list_files(tmp_path / "nope") == []


# ---------------------------------------------------------------------------
# read_content rejects non-editable suffix
# ---------------------------------------------------------------------------


def test_read_content_rejects_binary_suffix(template: Path) -> None:
    blob = template / "sheet.xlsx"
    blob.write_bytes(b"\x00\x01")
    with pytest.raises(TemplateAuthoringError, match="editable"):
        read_content(blob)
