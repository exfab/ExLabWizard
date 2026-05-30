"""File-type icon component (materials file-type icons design, 2026-05-30).

Mirrors :mod:`sync_status_icon`: one source-of-truth map from file extension
-> {Material Design Icons glyph, Okabe-Ito color token, tooltip, category},
plus pure prop functions and a thin NiceGUI render wrapper. The file list,
the metadata pane, and any legend all read from this map so the iconography
cannot drift.

Colours reference the theme's categorical ``--oi-*`` (Okabe-Ito) tokens, not
the semantic ``--color-*`` status tokens; the file-type icon lives in the
Name column while sync status lives in the Status column, so the hue overlap
between the two palettes is disambiguated by position and glyph.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Final

# category -> rendered props (icon glyph + colour token + tooltip).
_CATEGORY_PROPS: Final[dict[str, dict[str, str]]] = {
    "document": {
        "icon_name": "mdi-file-document-outline",
        "color_var": "--oi-grey",
        "tooltip": "Document",
    },
    "pdf": {"icon_name": "mdi-file-pdf-box", "color_var": "--oi-vermilion", "tooltip": "PDF"},
    "tabular": {
        "icon_name": "mdi-file-delimited",
        "color_var": "--oi-green",
        "tooltip": "Tabular data",
    },
    "image": {"icon_name": "mdi-file-image", "color_var": "--oi-sky", "tooltip": "Image"},
    "structured": {
        "icon_name": "mdi-code-json",
        "color_var": "--oi-blue",
        "tooltip": "Structured data",
    },
    "code": {"icon_name": "mdi-file-code", "color_var": "--oi-purple", "tooltip": "Source code"},
    "archive": {"icon_name": "mdi-folder-zip", "color_var": "--oi-orange", "tooltip": "Archive"},
    "scientific": {
        "icon_name": "mdi-microscope",
        "color_var": "--oi-yellow",
        "tooltip": "Instrument / scientific data",
    },
}

# Per-extension glyph overrides where a single category glyph would be too
# generic (e.g. spreadsheets, common languages).
_EXT_ICON_OVERRIDE: Final[dict[str, str]] = {
    "xlsx": "mdi-microsoft-excel",
    "xls": "mdi-microsoft-excel",
    "py": "mdi-language-python",
}

# extension token (lowercased, no dot) -> category.
_EXT_TO_CATEGORY: Final[dict[str, str]] = {
    # documents / text
    "txt": "document",
    "md": "document",
    "rtf": "document",
    "log": "document",
    "doc": "document",
    "docx": "document",
    "odt": "document",
    # pdf
    "pdf": "pdf",
    # tabular
    "csv": "tabular",
    "tsv": "tabular",
    "xlsx": "tabular",
    "xls": "tabular",
    "parquet": "tabular",
    # images
    "png": "image",
    "jpg": "image",
    "jpeg": "image",
    "gif": "image",
    "bmp": "image",
    "tif": "image",
    "tiff": "image",
    "svg": "image",
    "webp": "image",
    # structured / markup
    "json": "structured",
    "yaml": "structured",
    "yml": "structured",
    "xml": "structured",
    "toml": "structured",
    "ini": "structured",
    # code / scripts
    "py": "code",
    "js": "code",
    "ts": "code",
    "sh": "code",
    "r": "code",
    "m": "code",
    "c": "code",
    "cpp": "code",
    "h": "code",
    "java": "code",
    # archives (single + common double suffixes)
    "zip": "archive",
    "tar": "archive",
    "gz": "archive",
    "7z": "archive",
    "rar": "archive",
    "bz2": "archive",
    "xz": "archive",
    "tar.gz": "archive",
    "tar.bz2": "archive",
    "tar.xz": "archive",
    # scientific / instrument
    "h5": "scientific",
    "hdf5": "scientific",
    "fits": "scientific",
    "nd2": "scientific",
    "czi": "scientific",
    "dm3": "scientific",
    "mrc": "scientific",
    "raw": "scientific",
}

_DOUBLE_SUFFIXES: Final[tuple[str, ...]] = ("tar.gz", "tar.bz2", "tar.xz")

_FOLDER_PROPS: Final[dict[str, str]] = {
    "icon_name": "mdi-folder",
    "color_var": "--oi-grey",
    "tooltip": "Folder",
    "category": "folder",
}
_UNKNOWN_PROPS: Final[dict[str, str]] = {
    "icon_name": "mdi-file-outline",
    "color_var": "--color-muted",
    "tooltip": "File",
    "category": "unknown",
}


def _extension(name: str) -> str:
    """Return the lowercased extension token (no leading dot) for ``name``.

    Recognises a small set of double suffixes (``.tar.gz`` etc.) so archives
    map correctly; otherwise returns the final suffix.
    """
    lower = name.lower()
    for double in _DOUBLE_SUFFIXES:
        if lower.endswith("." + double):
            return double
    return PurePosixPath(lower).suffix.lstrip(".")


def file_type_props(name: str, *, is_dir: bool) -> dict[str, str]:
    """Return ``{icon_name, color_var, tooltip, category}`` for a file/folder.

    Pure; no NiceGUI import. Directories return the folder glyph; an unknown
    or absent extension returns the muted fallthrough.
    """
    if is_dir:
        return dict(_FOLDER_PROPS)
    ext = _extension(name)
    category = _EXT_TO_CATEGORY.get(ext)
    if category is None:
        return dict(_UNKNOWN_PROPS)
    props = dict(_CATEGORY_PROPS[category])
    props["category"] = category
    override = _EXT_ICON_OVERRIDE.get(ext)
    if override is not None:
        props["icon_name"] = override
    return props


def file_type_legend_entries() -> list[dict[str, str]]:
    """Return one props dict per file category (for an optional legend)."""
    return [{"category": cat, **props} for cat, props in _CATEGORY_PROPS.items()]


def file_type_icon(name: str, *, is_dir: bool = False) -> Any:
    """Build a NiceGUI ``<q-icon>`` for ``name`` (or return props in tests)."""
    props = file_type_props(name, is_dir=is_dir)
    try:
        from nicegui import ui
    except Exception:
        return props
    return (
        ui.icon(props["icon_name"])
        .style(f"color: var({props['color_var']}); font-size: 1rem; flex-shrink: 0;")
        .tooltip(props["tooltip"])
    )
