"""Frozen template provenance copy. Design spec (Phase 3b).

After a template renders into a destination folder, the exact template
source is copied -- verbatim, including ``copier.yml`` and ``.jinja``
files -- into that instance's own typed template store under
``<dst>/.exlab-wizard/templates/<own_type>/<name>/``. This freezes the
provenance so a later reader can see precisely which template produced
the instance, even if the shared templates directory drifts.

The copy path is recorded in ``creation.json`` (the ``template`` block's
``provenance_path``). This module is intentionally pure and dependency
light: it reads only ``resolved.name`` / ``resolved.path`` and performs a
single ``copytree``.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from exlab_wizard.constants import TEMPLATES_SUBDIR
from exlab_wizard.paths import cache_dir

if TYPE_CHECKING:
    from pathlib import Path

    from exlab_wizard.template.copier_driver import ResolvedTemplate

__all__ = ["copy_template_into_instance"]


def copy_template_into_instance(
    resolved: ResolvedTemplate,
    dst: Path,
    own_type: str,
) -> str:
    """Copy the resolved template root into ``dst``'s own typed provenance store.

    Writes ``<dst>/.exlab-wizard/templates/<own_type>/<resolved.name>/`` as a
    verbatim copy (incl. ``copier.yml`` and ``.jinja`` files). The copy is a
    frozen snapshot of the exact template source that produced this instance.

    Args:
        resolved: The resolved template whose ``name`` / ``path`` (template
            root directory) are copied. Only these two attributes are read.
        dst: The instance destination directory the template rendered into.
        own_type: The instance's own template type segment -- ``"run"`` for a
            run, ``"project"`` for a project.

    Returns:
        The provenance copy's path RELATIVE to ``dst`` as a POSIX string,
        e.g. ``".exlab-wizard/templates/run/confocal_run"``.

    Raises:
        FileNotFoundError: ``resolved.path`` does not exist (should not happen
            after a successful render). The caller wraps this best-effort.
    """
    source = resolved.path
    if not source.is_dir():
        raise FileNotFoundError(
            f"template source {source} is missing; cannot copy provenance",
        )

    target = cache_dir(dst) / TEMPLATES_SUBDIR / own_type / resolved.name
    shutil.copytree(source, target, dirs_exist_ok=True)
    return target.relative_to(dst).as_posix()
