"""Unit tests for the pure file-type icon map."""

from __future__ import annotations

import pytest

from exlab_wizard.ui.components.file_type_icon import (
    file_type_legend_entries,
    file_type_props,
)


@pytest.mark.parametrize(
    ("name", "icon", "color", "category"),
    [
        ("scan.pdf", "mdi-file-pdf-box", "--oi-vermilion", "pdf"),
        ("table.csv", "mdi-file-delimited", "--oi-green", "tabular"),
        ("sheet.xlsx", "mdi-microsoft-excel", "--oi-green", "tabular"),
        ("img.PNG", "mdi-file-image", "--oi-sky", "image"),
        ("notes.txt", "mdi-file-document-outline", "--oi-grey", "document"),
        ("conf.json", "mdi-code-json", "--oi-blue", "structured"),
        ("run.py", "mdi-language-python", "--oi-purple", "code"),
        ("bundle.zip", "mdi-folder-zip", "--oi-orange", "archive"),
        ("data.h5", "mdi-microscope", "--oi-yellow", "scientific"),
    ],
)
def test_known_extensions(name: str, icon: str, color: str, category: str) -> None:
    props = file_type_props(name, is_dir=False)
    assert props["icon_name"] == icon
    assert props["color_var"] == color
    assert props["category"] == category


def test_double_suffix_archive() -> None:
    props = file_type_props("logs.tar.gz", is_dir=False)
    assert props["category"] == "archive"
    assert props["icon_name"] == "mdi-folder-zip"


def test_directory_uses_folder_glyph() -> None:
    props = file_type_props("Runs", is_dir=True)
    assert props["icon_name"] == "mdi-folder"
    assert props["category"] == "folder"


def test_unknown_extension_falls_through() -> None:
    props = file_type_props("mystery.zzz", is_dir=False)
    assert props["icon_name"] == "mdi-file-outline"
    assert props["color_var"] == "--color-muted"
    assert props["category"] == "unknown"


def test_no_extension_falls_through() -> None:
    props = file_type_props("README", is_dir=False)
    assert props["category"] == "unknown"


def test_legend_covers_every_category() -> None:
    cats = {e["category"] for e in file_type_legend_entries()}
    assert {
        "document",
        "pdf",
        "tabular",
        "image",
        "structured",
        "code",
        "archive",
        "scientific",
    } <= cats
