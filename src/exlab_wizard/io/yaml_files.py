"""Shared YAML manifest reader (PyYAML safe-load).

Used for read-only manifests where the wizard does NOT need to preserve
formatting or comments: copier templates (``copier.yml``), plugin
manifests (``manifest.yml``). The config loader uses ``ruamel.yaml`` for
round-trip preservation and is intentionally NOT routed through this
helper.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

__all__ = ["load_yaml_manifest"]


def load_yaml_manifest(path: Path) -> dict[str, Any]:
    """Read and ``safe_load`` a YAML manifest into a plain dict.

    Raises ``FileNotFoundError`` when the manifest does not exist and
    ``yaml.YAMLError`` (subclass of ``Exception``) on parse failure --
    callers are expected to translate those into their own typed errors.
    An empty file decodes to an empty dict.
    """
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}
