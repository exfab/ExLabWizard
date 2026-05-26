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
from exlab_wizard.constants import TEST_MODE_ENV, TEST_MODE_PREFIX
from exlab_wizard.errors import ConfigError
from exlab_wizard.io import atomic_write_bytes
from exlab_wizard.logging import get_logger

_log = get_logger(__name__)

# Values that flip ``EXLAB_WIZARD_TEST_MODE`` on. Matched case-insensitively
# against the env var's stripped value; anything else (including unset, "",
# "0", "false", "no") leaves the loaded config untouched.
_TEST_MODE_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on"})


def _test_mode_enabled() -> bool:
    """Return True when the env var :data:`TEST_MODE_ENV` is truthy.

    Centralized here so callers don't have to know the truthy/falsy
    spellings; see :data:`_TEST_MODE_TRUTHY` for the accepted strings.
    """
    raw = os.environ.get(TEST_MODE_ENV, "")
    return raw.strip().lower() in _TEST_MODE_TRUTHY


def apply_test_mode_prefix(config: Config) -> Config:
    """Return ``config`` with each equipment id prefixed by :data:`TEST_MODE_PREFIX`.

    Idempotent: an id that already begins with the prefix is left
    unchanged so repeated applications (or already-prefixed inputs)
    never grow a ``TEST_TEST_…`` chain. The rewritten config is re-run
    through :meth:`Config.model_validate` so unique-id and pattern
    invariants are re-checked against the new ids -- if a rewrite ever
    produces a duplicate or an over-length id, the loader raises the
    same :class:`ConfigError` shape a hand-edited config would.
    """
    if not config.equipment:
        return config

    needs_rewrite = any(not entry.id.startswith(TEST_MODE_PREFIX) for entry in config.equipment)
    if not needs_rewrite:
        return config

    # Round-trip through ``model_dump`` so the rewritten dict is valid
    # input for ``Config.model_validate`` (which re-runs every field-,
    # model-, and cross-field validator, including the equipment-id
    # regex, the max-length check, and the unique-id invariant).
    dumped: dict[str, Any] = config.model_dump(mode="python")
    for entry in dumped.get("equipment", []):
        current_id = entry.get("id", "")
        if isinstance(current_id, str) and not current_id.startswith(TEST_MODE_PREFIX):
            entry["id"] = f"{TEST_MODE_PREFIX}{current_id}"

    try:
        return Config.model_validate(dumped)
    except ValidationError as exc:
        raise ConfigError(
            f"applying {TEST_MODE_PREFIX!r} prefix to equipment ids failed validation:\n{exc}"
        ) from exc


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
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"config.yaml not found at {path}") from exc
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
        config = Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"config.yaml failed validation:\n{exc}") from exc
    # Apply the ``EXLAB_WIZARD_TEST_MODE`` opt-in *after* validation so the
    # base config is verified once on its own, and the prefix step's
    # error path (duplicate ids, over-length) is reported separately.
    if _test_mode_enabled():
        config = apply_test_mode_prefix(config)
    return config


def save_config(path: Path, config: Config, *, original_text: str | None = None) -> None:
    """Atomically write `config` back to `path`.

    If `original_text` is supplied, ruamel.yaml round-trips it so existing
    comments and key order are preserved; only the modified values change.
    If `original_text` is None, write a fresh document with no preserved
    formatting.

    Atomicity: write to ``<path>.tmp``, fsync, then ``Path.replace`` to ``<path>``.
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

    text_buf = io.StringIO()
    yaml.dump(out, text_buf)
    encoded = text_buf.getvalue().encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(path, encoded)
    _log.info("saved config.yaml [path=%s] [keys=%d]", str(path), len(out))


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
