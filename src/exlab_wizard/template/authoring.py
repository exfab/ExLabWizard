"""Author-time template service: scaffold, edit, and validate templates.

This is the non-UI backend the GUI template authoring form (Frontend Spec
§5) drives. It owns every mutation of a template directory under
``config.paths.templates_dir`` so the NiceGUI page stays a thin view:

* :func:`create_template_dir` -- scaffold a new minimal Copier template
  (the same shape :func:`exlab_wizard.ui.pages.templates.create_template`
  historically produced, now routed through here so the page can delegate).
* :func:`read_manifest` / :func:`write_manifest` -- round-trip the
  structured :class:`~exlab_wizard.template.manifest.TemplateManifest`
  through ``copier.yml``, gating every write on the shared
  :mod:`exlab_wizard.template.lint` rule set.
* :func:`read_content` / :func:`write_content_file` / :func:`upload_file`
  -- read and write the template's content files (``*.jinja`` and the
  editable text allowlist), with a Jinja2 parse gate on ``*.jinja`` saves
  and size / file-count caps on uploads.
* :func:`rename_path` / :func:`delete_path` -- move and remove files
  within the template, never touching ``copier.yml`` on delete.
* :func:`list_files` -- a pure directory walk the GUI tree consumes.

Two invariants run through the whole module:

* **Every** new or edited path is resolved through :func:`_safe_target`,
  the single chokepoint that rejects traversal (``..``), absolute paths,
  path separators inside a segment, Windows-reserved / control / non-ASCII
  segment names, and any resolved path that escapes the template root.
  The per-segment rule reuses
  :func:`exlab_wizard.paths.project_name_violations` so author-time
  filenames obey the same filesystem-safety contract as project names.
* **Every** write goes through :func:`exlab_wizard.io.atomic_write_bytes`
  -- the temp-file + ``fsync`` + ``os.replace`` recipe -- so a crash
  mid-write never leaves a half-written ``copier.yml`` or content file.

The :func:`write_manifest` / :func:`write_content_file` family also take an
optional ``expected_stat`` tuple ``(st_mtime, st_size)`` captured at read
time; if the on-disk file changed since, the write raises
:class:`StaleEditError` rather than clobbering a concurrent edit (the
optimistic-concurrency guard the GUI surfaces as "reload, your copy is
stale").
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from jinja2 import Environment, TemplateSyntaxError

from exlab_wizard.constants import (
    COPIER_MANIFEST_NAME,
    TEMPLATE_MAX_FILES,
    TEMPLATE_UPLOAD_MAX_BYTES,
    RunScope,
    TemplateType,
)
from exlab_wizard.errors import ExLabError
from exlab_wizard.io.atomic_write import atomic_write_bytes
from exlab_wizard.logging import get_logger
from exlab_wizard.paths import project_name_violations
from exlab_wizard.template import lint
from exlab_wizard.template.manifest import TemplateManifest

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "EDITABLE_SUFFIXES",
    "StaleEditError",
    "TemplateAuthoringError",
    "UnsafePathError",
    "create_template_dir",
    "delete_path",
    "is_editable",
    "list_files",
    "read_content",
    "read_manifest",
    "rename_path",
    "upload_file",
    "write_content_file",
    "write_manifest",
]

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Exceptions (subclass the repo base so callers can catch ExLabError)
# ---------------------------------------------------------------------------


class TemplateAuthoringError(ExLabError):
    """Raised on an author-time template-edit failure.

    Covers lint-rejected manifest saves, Jinja-syntax-rejected content
    saves, over-cap uploads, and edits to non-editable / disallowed
    paths. The two narrower failures below subclass this so callers can
    catch the specific case or the whole family.
    """


class StaleEditError(TemplateAuthoringError):
    """Raised when an optimistic-concurrency write loses a stat race.

    The file changed on disk between the caller's read (which captured
    ``expected_stat``) and the write, so applying the edit would clobber
    a concurrent change. The GUI surfaces this as "reload -- your copy is
    stale" rather than silently overwriting.
    """


class UnsafePathError(TemplateAuthoringError):
    """Raised when a requested path is not a safe in-template location.

    Covers traversal (``..``), absolute paths, path separators or
    Windows-reserved / control / non-ASCII characters inside a segment,
    and any resolved target that escapes the template root.
    """


# ---------------------------------------------------------------------------
# Editable-suffix classification
# ---------------------------------------------------------------------------

# Suffixes the GUI may open in its text editor. Anything else (``.xlsx``,
# ``.png``, ...) is treated as opaque binary the operator can upload /
# rename / delete but not edit inline.
EDITABLE_SUFFIXES: frozenset[str] = frozenset(
    {".md", ".txt", ".csv", ".yml", ".yaml", ".jinja", ".json"}
)

# Content file every scaffolded template carries. ``.jinja`` so Copier
# renders it; the body has no variables so it renders verbatim.
_SCAFFOLD_CONTENT_NAME = "notes.md.jinja"
_SCAFFOLD_CONTENT_BODY = "# Notes\n\nScaffolded by ExLab-Wizard.\n"

_JINJA_SUFFIX = ".jinja"


def is_editable(path: Path) -> bool:
    """Return ``True`` if ``path`` is text the GUI may edit inline.

    The decision is purely by suffix against :data:`EDITABLE_SUFFIXES`
    (case-insensitive); the file need not exist. A ``foo.md.jinja`` is
    editable (its final suffix ``.jinja`` is in the set), as is a bare
    ``.md`` / ``.csv`` / ``.json``; a ``.xlsx`` / ``.png`` is not.
    """
    return path.suffix.lower() in EDITABLE_SUFFIXES


# ---------------------------------------------------------------------------
# The single path chokepoint
# ---------------------------------------------------------------------------


def _safe_target(template_dir: Path, rel: str) -> Path:
    """Resolve ``rel`` to an absolute path proven to live inside ``template_dir``.

    Every new or edited path in this module flows through here. The
    relative path is rejected outright if empty; otherwise it is split on
    both ``/`` and ``\\`` and each non-empty segment is validated with
    :func:`exlab_wizard.paths.project_name_violations` -- the same
    filesystem-safety rule project names obey (no separators, no ``..`` /
    trailing dot, no Windows-reserved name, no control / non-ASCII, not
    over-length). The composed target is then ``resolve()``-d and required
    to equal the resolved template root or sit beneath it, so a path that
    escapes via symlink or a residual ``..`` is caught even though the
    per-segment check already rejects literal ``..``.

    Args:
        template_dir: The template root the path must stay within.
        rel: A relative, in-template path (POSIX or Windows separators).

    Returns:
        The resolved absolute path inside ``template_dir``.

    Raises:
        UnsafePathError: ``rel`` is empty, contains an empty / unsafe
            segment, or resolves outside ``template_dir``.
    """
    if not rel:
        msg = "empty path is not a valid template target"
        raise UnsafePathError(msg)

    segments = rel.replace("\\", "/").split("/")
    if any(seg == "" for seg in segments):
        msg = f"path {rel!r} contains an empty segment"
        raise UnsafePathError(msg)

    for seg in segments:
        violations = project_name_violations(seg)
        if violations:
            _token, detail = violations[0]
            msg = f"unsafe path segment {seg!r} in {rel!r}: {detail}"
            raise UnsafePathError(msg)

    target = template_dir / rel
    resolved = target.resolve()
    base = template_dir.resolve()
    if resolved != base and base not in resolved.parents:
        msg = f"path {rel!r} escapes the template directory"
        raise UnsafePathError(msg)
    return resolved


# ---------------------------------------------------------------------------
# Scaffold
# ---------------------------------------------------------------------------


def create_template_dir(
    base_dir: Path,
    *,
    name: str,
    template_type: str,
    description: str = "",
    run_scope: str | None = None,
) -> Path:
    """Scaffold a new minimal Copier template under ``base_dir``.

    Writes ``<base_dir>/<name>/copier.yml`` (serialised from a
    :class:`~exlab_wizard.template.manifest.TemplateManifest` so the
    author-time and structured-edit paths emit byte-identical YAML) plus
    one ``notes.md.jinja`` content file. Both writes go through
    :func:`atomic_write_bytes`. The result is immediately loadable by
    :class:`~exlab_wizard.template.copier_driver.TemplateEngine`.

    Args:
        base_dir: The ``templates_dir`` the new template is created under.
        name: The template directory name. Stripped of surrounding
            whitespace, then validated as a single safe filesystem
            segment via :func:`project_name_violations`.
        template_type: One of :class:`TemplateType` values.
        description: Free-form ``_exlab_description`` text (stripped).
        run_scope: Required for ``run`` templates; one of
            :class:`RunScope` values. Must be ``None`` / unused otherwise.

    Returns:
        The new template's root directory.

    Raises:
        ValueError: Empty / duplicate ``name``, unknown ``template_type``,
            or a run template missing / with an invalid ``run_scope``.
        UnsafePathError: ``name`` is not a safe single filesystem segment.
    """
    clean_name = name.strip()
    if not clean_name:
        msg = "template name must not be empty"
        raise ValueError(msg)
    if template_type not in {t.value for t in TemplateType}:
        msg = f"unknown template type {template_type!r}"
        raise ValueError(msg)
    if template_type == TemplateType.RUN.value:
        if run_scope is None:
            msg = "run templates require a run_scope"
            raise ValueError(msg)
        if run_scope not in {s.value for s in RunScope}:
            msg = f"unknown run_scope {run_scope!r}"
            raise ValueError(msg)

    violations = project_name_violations(clean_name)
    if violations:
        _token, detail = violations[0]
        msg = f"unsafe template name {clean_name!r}: {detail}"
        raise UnsafePathError(msg)

    root = Path(base_dir) / clean_name
    if root.exists():
        msg = f"a template named {clean_name!r} already exists"
        raise ValueError(msg)
    root.mkdir(parents=True)

    manifest = TemplateManifest(
        exlab_type=template_type,
        exlab_version="1.0",
        exlab_run_scope=run_scope if template_type == TemplateType.RUN.value else None,
        description=description.strip(),
    )
    atomic_write_bytes(root / COPIER_MANIFEST_NAME, manifest.to_yaml().encode("utf-8"))
    atomic_write_bytes(root / _SCAFFOLD_CONTENT_NAME, _SCAFFOLD_CONTENT_BODY.encode("utf-8"))
    _log.info("scaffolded %s template %r at %s", template_type, clean_name, root)
    return root


# ---------------------------------------------------------------------------
# Manifest round-trip
# ---------------------------------------------------------------------------


def _stat_tuple(path: Path) -> tuple[float, int]:
    """Return the ``(st_mtime, st_size)`` stale-edit signature of ``path``."""
    st = path.stat()
    return (st.st_mtime, st.st_size)


def _check_stale(path: Path, expected_stat: tuple[float, int] | None) -> None:
    """Raise :class:`StaleEditError` if ``path``'s stat differs from expected.

    A ``None`` ``expected_stat`` skips the guard (the caller opted out of
    optimistic concurrency). A missing file with a non-``None`` expectation
    is itself a stale condition (the file the caller read is gone).
    """
    if expected_stat is None:
        return
    try:
        current = _stat_tuple(path)
    except OSError as exc:
        msg = f"{path} changed on disk since it was read (now missing): {exc}"
        raise StaleEditError(msg) from exc
    if current != expected_stat:
        msg = (
            f"{path} changed on disk since it was read "
            f"(expected stat {expected_stat}, found {current})"
        )
        raise StaleEditError(msg)


def read_manifest(template_dir: Path) -> tuple[TemplateManifest, tuple[float, int]]:
    """Read ``copier.yml`` and return the parsed manifest + its stat signature.

    The returned ``(st_mtime, st_size)`` tuple is passed back to
    :func:`write_manifest` as ``expected_stat`` to detect a concurrent
    edit. The manifest is parsed via
    :meth:`TemplateManifest.from_yaml`, which is tolerant of missing
    ``_exlab_*`` keys.

    Args:
        template_dir: The template root.

    Returns:
        ``(manifest, (st_mtime, st_size))``.

    Raises:
        TemplateAuthoringError: ``copier.yml`` is missing or unreadable.
    """
    copier_path = Path(template_dir) / COPIER_MANIFEST_NAME
    try:
        text = copier_path.read_text(encoding="utf-8")
        stat = _stat_tuple(copier_path)
    except OSError as exc:
        msg = f"failed to read {copier_path}: {exc}"
        raise TemplateAuthoringError(msg) from exc
    return TemplateManifest.from_yaml(text), stat


def write_manifest(
    template_dir: Path,
    manifest: TemplateManifest,
    *,
    expected_stat: tuple[float, int] | None = None,
) -> None:
    """Serialise ``manifest`` to ``copier.yml``, gated on the lint rule set.

    The manifest is rendered with :meth:`TemplateManifest.to_yaml`, then
    re-parsed and run through :func:`exlab_wizard.template.lint.lint_manifest_dict`.
    If any finding is an ERROR the file is **not** written and a
    :class:`TemplateAuthoringError` carrying the joined error messages is
    raised, so a manifest that ``TemplateEngine.resolve`` would reject can
    never be saved. WARN findings do not block the save. On success the
    bytes are written through :func:`atomic_write_bytes`.

    Args:
        template_dir: The template root.
        manifest: The manifest to serialise.
        expected_stat: Optional ``(st_mtime, st_size)`` from
            :func:`read_manifest`; if given and the on-disk file differs,
            :class:`StaleEditError` is raised before any write.

    Raises:
        StaleEditError: ``expected_stat`` given and the file changed.
        TemplateAuthoringError: The serialised manifest has lint ERRORs.
    """
    copier_path = Path(template_dir) / COPIER_MANIFEST_NAME
    _check_stale(copier_path, expected_stat)

    text = manifest.to_yaml()
    parsed = yaml.safe_load(text)
    manifest_dict = parsed if isinstance(parsed, dict) else {}
    findings = lint.lint_manifest_dict(manifest_dict, copier_path)
    if lint.has_errors(findings):
        errors = "; ".join(f.message for f in findings if f.severity == "error")
        msg = f"manifest has lint errors, not written: {errors}"
        raise TemplateAuthoringError(msg)

    atomic_write_bytes(copier_path, text.encode("utf-8"))


# ---------------------------------------------------------------------------
# Content read / write
# ---------------------------------------------------------------------------


def read_content(path: Path) -> tuple[str, tuple[float, int]]:
    """Read an editable text file as UTF-8 and return its content + stat.

    Args:
        path: The file to read. Its suffix must be in
            :data:`EDITABLE_SUFFIXES`.

    Returns:
        ``(text, (st_mtime, st_size))`` -- the stat is the
        optimistic-concurrency signature for a later
        :func:`write_content_file`.

    Raises:
        TemplateAuthoringError: The suffix is not editable, or the file
            is missing / unreadable / not valid UTF-8.
    """
    p = Path(path)
    if not is_editable(p):
        msg = f"{p} is not an editable text file (suffix {p.suffix!r})"
        raise TemplateAuthoringError(msg)
    try:
        text = p.read_text(encoding="utf-8")
        stat = _stat_tuple(p)
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"failed to read {p}: {exc}"
        raise TemplateAuthoringError(msg) from exc
    return text, stat


def write_content_file(
    template_dir: Path,
    rel: str,
    text: str,
    *,
    expected_stat: tuple[float, int] | None = None,
) -> Path:
    """Write ``text`` to an in-template content file at ``rel``.

    The target is resolved through :func:`_safe_target`. When ``rel`` ends
    in ``.jinja`` the text is parsed with Jinja2 first; a syntax error
    refuses the save with a :class:`TemplateAuthoringError` (so a broken
    template never lands on disk). The write itself goes through
    :func:`atomic_write_bytes`.

    Args:
        template_dir: The template root.
        rel: The in-template relative path to write.
        text: The UTF-8 content to write.
        expected_stat: Optional ``(st_mtime, st_size)`` from
            :func:`read_content`; if given and the on-disk file differs,
            :class:`StaleEditError` is raised before any write.

    Returns:
        The resolved absolute path written.

    Raises:
        UnsafePathError: ``rel`` is not a safe in-template path.
        StaleEditError: ``expected_stat`` given and the file changed.
        TemplateAuthoringError: A ``.jinja`` target with a syntax error.
    """
    target = _safe_target(Path(template_dir), rel)
    _check_stale(target, expected_stat)

    if target.suffix.lower() == _JINJA_SUFFIX:
        _validate_jinja(text, rel)

    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(target, text.encode("utf-8"))
    return target


def _validate_jinja(text: str, rel: str) -> None:
    """Parse ``text`` as Jinja2; raise :class:`TemplateAuthoringError` on error.

    Parse-only (never renders), mirroring
    :func:`exlab_wizard.template.lint._lint_jinja_files`.
    """
    try:
        Environment().parse(text)  # parse-only; never renders untrusted input
    except TemplateSyntaxError as exc:
        msg = f"{rel}: Jinja syntax error on line {exc.lineno}: {exc.message}"
        raise TemplateAuthoringError(msg) from exc


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


def upload_file(
    template_dir: Path,
    filename: str,
    data: bytes,
    *,
    render_as_template: bool = False,
) -> Path:
    """Write uploaded ``data`` into the template as ``filename``.

    The filename is resolved through :func:`_safe_target` (so a traversal
    or absolute path is rejected). When ``render_as_template`` is set and
    the name is not already ``*.jinja``, a ``.jinja`` suffix is appended so
    Copier renders the file. Two caps gate the write:

    * the upload may not exceed :data:`TEMPLATE_UPLOAD_MAX_BYTES`;
    * the template may not already hold :data:`TEMPLATE_MAX_FILES` files.

    A ``.jinja`` upload that decodes as UTF-8 text is Jinja-parse-checked
    (a binary ``.jinja`` -- unusual but possible -- skips the parse). The
    write goes through :func:`atomic_write_bytes`.

    Args:
        template_dir: The template root.
        filename: The upload's in-template name (single path, may nest).
        data: The raw bytes to write.
        render_as_template: Append ``.jinja`` so Copier renders the file.

    Returns:
        The resolved absolute path written.

    Raises:
        UnsafePathError: ``filename`` is not a safe in-template path.
        TemplateAuthoringError: The upload exceeds the size cap, the
            template is at the file-count cap, or a UTF-8 ``.jinja`` upload
            has a Jinja syntax error.
    """
    if len(data) > TEMPLATE_UPLOAD_MAX_BYTES:
        msg = (
            f"upload {filename!r} is {len(data)} bytes, exceeds the "
            f"{TEMPLATE_UPLOAD_MAX_BYTES}-byte cap"
        )
        raise TemplateAuthoringError(msg)

    root = Path(template_dir)
    existing = _count_files(root)
    if existing >= TEMPLATE_MAX_FILES:
        msg = (
            f"template already holds {existing} files, at the "
            f"{TEMPLATE_MAX_FILES}-file cap; cannot upload {filename!r}"
        )
        raise TemplateAuthoringError(msg)

    name = filename
    if render_as_template and not name.lower().endswith(_JINJA_SUFFIX):
        name = f"{name}{_JINJA_SUFFIX}"

    target = _safe_target(root, name)
    if target.suffix.lower() == _JINJA_SUFFIX:
        try:
            decoded = data.decode("utf-8")
        except UnicodeDecodeError:
            decoded = None  # Binary .jinja: skip the parse, write verbatim.
        if decoded is not None:
            _validate_jinja(decoded, name)

    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(target, data)
    return target


def _count_files(template_dir: Path) -> int:
    """Count regular files (not directories) under ``template_dir``."""
    root = Path(template_dir)
    if not root.is_dir():
        return 0
    return sum(1 for p in root.rglob("*") if p.is_file())


# ---------------------------------------------------------------------------
# Rename / delete
# ---------------------------------------------------------------------------


def rename_path(template_dir: Path, src_rel: str, dst_rel: str) -> Path:
    """Move an in-template path from ``src_rel`` to ``dst_rel``.

    Both ends are resolved through :func:`_safe_target`, so neither may
    escape the template root. The move is ``os.replace`` (atomic on the
    same filesystem); ``copier.yml`` may not be renamed away.

    Args:
        template_dir: The template root.
        src_rel: The existing in-template path.
        dst_rel: The new in-template path.

    Returns:
        The resolved absolute destination path.

    Raises:
        UnsafePathError: Either end is not a safe in-template path.
        TemplateAuthoringError: ``src_rel`` is ``copier.yml`` or does not
            exist.
    """
    root = Path(template_dir)
    src = _safe_target(root, src_rel)
    dst = _safe_target(root, dst_rel)
    if src == (root / COPIER_MANIFEST_NAME).resolve():
        msg = "copier.yml cannot be renamed"
        raise TemplateAuthoringError(msg)
    if not src.exists():
        msg = f"cannot rename {src_rel!r}: it does not exist"
        raise TemplateAuthoringError(msg)
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.replace(src, dst)
    return dst


def delete_path(template_dir: Path, rel: str) -> None:
    """Delete an in-template file or directory at ``rel``.

    Resolved through :func:`_safe_target`. A file is ``unlink``-ed, a
    directory is removed recursively with :func:`shutil.rmtree` (only ever
    within the template root). ``copier.yml`` may not be deleted.

    Args:
        template_dir: The template root.
        rel: The in-template path to remove.

    Raises:
        UnsafePathError: ``rel`` is not a safe in-template path.
        TemplateAuthoringError: ``rel`` is ``copier.yml`` or does not
            exist.
    """
    root = Path(template_dir)
    target = _safe_target(root, rel)
    if target == (root / COPIER_MANIFEST_NAME).resolve():
        msg = "copier.yml cannot be deleted"
        raise TemplateAuthoringError(msg)
    if not target.exists():
        msg = f"cannot delete {rel!r}: it does not exist"
        raise TemplateAuthoringError(msg)
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()


# ---------------------------------------------------------------------------
# Directory listing (pure, GUI tree)
# ---------------------------------------------------------------------------


def list_files(template_dir: Path) -> list[dict]:
    """Walk ``template_dir`` and return a sorted entry list for the GUI tree.

    Each entry is ``{"rel": str, "is_dir": bool, "editable": bool,
    "size": int}`` -- ``rel`` is the POSIX-style path relative to the
    template root, ``editable`` is :func:`is_editable` (always ``False``
    for directories), and ``size`` is the file size in bytes (``0`` for
    directories). Entries are sorted by ``rel`` for a stable tree.

    Args:
        template_dir: The template root.

    Returns:
        The sorted entry list (empty if ``template_dir`` is not a
        directory).
    """
    root = Path(template_dir)
    if not root.is_dir():
        return []
    entries: list[dict] = []
    for p in _iter_paths(root):
        rel = p.relative_to(root).as_posix()
        is_dir = p.is_dir()
        entries.append(
            {
                "rel": rel,
                "is_dir": is_dir,
                "editable": (not is_dir) and is_editable(p),
                "size": 0 if is_dir else p.stat().st_size,
            }
        )
    entries.sort(key=lambda e: e["rel"])
    return entries


def _iter_paths(root: Path) -> Iterable[Path]:
    """Yield every path under ``root`` (files and directories)."""
    yield from root.rglob("*")
