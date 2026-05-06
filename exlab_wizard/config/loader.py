"""config.yaml round-trip loader. Backend Spec §9.

Uses ruamel.yaml in round-trip mode so saving back to disk preserves
operator-readable comments and key order. The Settings UI's Save action
goes through this module so config.yaml stays human-friendly across
edit cycles.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from ruamel.yaml import YAML

from exlab_wizard.config.models import Config
from exlab_wizard.errors import ConfigError
from exlab_wizard.logging import get_logger

_log = get_logger(__name__)


def _yaml() -> YAML:
    """Build a configured ruamel.yaml instance.

    typ='rt' is round-trip mode (preserves comments, anchors, key order).
    indent settings match the §9 example layout.
    """
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 120
    return yaml


def load_config(path: Path) -> Config:
    """Load a config.yaml from disk and validate against the Pydantic model.

    Raises ConfigError on filesystem error, YAML parse error, or model
    validation failure. The original ValidationError is chained as the
    cause so the caller can introspect per-field errors.
    """
    if not path.exists():
        raise ConfigError(f"config.yaml not found at {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"could not read config.yaml at {path}: {exc}") from exc
    return load_config_from_text(text)


def load_config_from_text(text: str) -> Config:
    """Load a config from YAML text. Same semantics as load_config but for in-memory input."""
    try:
        data = _yaml().load(text) or {}
    except Exception as exc:  # ruamel.yaml has multiple exception types; catch broadly
        raise ConfigError(f"config.yaml is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("config.yaml top level must be a mapping")
    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"config.yaml failed validation:\n{exc}") from exc


def save_config(path: Path, config: Config, *, original_text: str | None = None) -> None:
    """Atomically write `config` back to `path`.

    If `original_text` is supplied, ruamel.yaml round-trips it so existing
    comments and key order are preserved; only the modified values change.
    If `original_text` is None, write a fresh document with no preserved
    formatting.

    Atomicity: write to <path>.tmp, fsync, then os.replace to <path>.
    """
    yaml = _yaml()
    new_dict = config.model_dump(mode="python", exclude_none=False)

    if original_text is not None:
        # Round-trip merge: load original, mutate values in place, dump.
        original = yaml.load(original_text) or {}
        _deep_merge(original, new_dict)
        out = original
    else:
        out = new_dict

    tmp = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as fh:
        yaml.dump(out, fh)
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)
    _log.info(
        "saved config.yaml [path=%s] [keys=%d]",
        str(path),
        len(out) if hasattr(out, "__len__") else 0,
    )


def _deep_merge(target: Any, source: dict[str, Any]) -> None:
    """In-place deep-merge source into target.

    Used by save_config to overlay new values onto a ruamel-loaded
    document so comments/key order survive. Lists are replaced wholesale
    (the Settings UI hands us the full list to write).
    """
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value


def dump_config(config: Config) -> str:
    """Serialize config to a YAML string. No comment preservation; tests use this."""
    yaml = _yaml()
    buf = io.StringIO()
    yaml.dump(config.model_dump(mode="python", exclude_none=False), buf)
    return buf.getvalue()
