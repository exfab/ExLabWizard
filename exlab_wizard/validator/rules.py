"""Validator rule functions. Backend Spec §8.1.

This module is a pure-function library: one function per §8.1 rule. Each
function takes the rule-specific inputs and returns a list of finding-shaped
dictionaries (the §11.8 wire shape, partially populated -- the engine layer
fills in ``run_path``, ``synced_under_prior_policy`` and ``override_active``
once it knows the run context).

The rule functions are intentionally stateless and side-effect free so they
can be exercised in isolation by the unit-test suite and re-used by both
creation-time mode (single proposed path) and audit mode (whole subtree
walk). The engine layer (``validator/engine.py``) orchestrates which rules
fire on which inputs.

Each finding dict has the shape::

    {
        "rule": "<rule_name>",
        "tier": "hard" | "soft",
        "matched_token": "<token>" | None,
        "rule_detail": "<human description>",
        "offending_kind": "directory_segment" | "file_name" | "file_content",
        "offending_path": "<path or filename>",
    }
"""

from __future__ import annotations

from typing import Any

import yaml

from exlab_wizard.constants import (
    PLACEHOLDER_ANGLE_BRACKET_PATTERN,
    PLACEHOLDER_JINJA_BLOCK_PATTERN,
    PLACEHOLDER_JINJA_VAR_PATTERN,
    TEST_RUNS_DIR_NAME,
    WINDOWS_ILLEGAL_CHARS,
    WINDOWS_RESERVED_NAMES,
)
from exlab_wizard.logging import get_logger

__all__ = [
    "check_illegal_filesystem_character",
    "check_malformed_yaml_front_matter",
    "check_missing_required_field",
    "check_mode_prefix_mismatch",
    "check_orphan",
    "check_reserved_filesystem_name",
    "check_unresolved_placeholder",
]

logger = get_logger(__name__)

# Maximum file content size, in bytes, that a rule will scan. Backend Spec
# §8.1.1 commits a default of 5 MiB via ``validator.content_scan_max_mib``.
# The engine layer applies the configured override; this module enforces
# the default as a defensive ceiling on inputs the engine forwards.
_CONTENT_SCAN_MAX_BYTES: int = 5 * 1024 * 1024

# Maximum number of leading lines inspected when looking for a YAML
# front-matter terminator. Backend Spec §8.1.1 commits a "first 200 lines"
# bound so unterminated front matter is detected quickly without reading
# arbitrarily large prose bodies.
_FRONT_MATTER_MAX_LINES: int = 200


# ---------------------------------------------------------------------------
# §8.1.1 Unresolved-placeholder rule
# ---------------------------------------------------------------------------


def check_unresolved_placeholder(
    *,
    path_segments: list[str],
    file_names: list[str],
    file_contents: dict[str, str],
) -> list[dict[str, Any]]:
    """§8.1.1: detect angle-bracket and Jinja placeholder tokens.

    Hard-tier. Uses ``PLACEHOLDER_ANGLE_BRACKET_PATTERN`` and the two Jinja
    patterns from constants. Returns one finding per match with rule
    ``unresolved_placeholder_token`` (angle-bracket) or
    ``leftover_jinja_marker`` (Jinja).

    ``file_contents`` may be empty for path-only checks. Files larger than
    ``_CONTENT_SCAN_MAX_BYTES`` are skipped per §8.1.1.
    """
    findings: list[dict[str, Any]] = []

    for segment in path_segments:
        findings.extend(_scan_for_placeholders(segment, "directory_segment", segment))

    for name in file_names:
        findings.extend(_scan_for_placeholders(name, "file_name", name))

    for filename, content in file_contents.items():
        if len(content.encode("utf-8", errors="replace")) > _CONTENT_SCAN_MAX_BYTES:
            logger.debug(
                "skipping content scan for %s: exceeds %d bytes",
                filename,
                _CONTENT_SCAN_MAX_BYTES,
            )
            continue
        findings.extend(_scan_for_placeholders(content, "file_content", filename))

    return findings


def _scan_for_placeholders(
    text: str,
    offending_kind: str,
    offending_path: str,
) -> list[dict[str, Any]]:
    """Return one finding per placeholder match in ``text``."""
    findings: list[dict[str, Any]] = []

    for match in PLACEHOLDER_ANGLE_BRACKET_PATTERN.finditer(text):
        token = match.group(0)
        findings.append(
            {
                "rule": "unresolved_placeholder_token",
                "tier": "hard",
                "matched_token": token,
                "rule_detail": (
                    f"Angle-bracket identifier token {token} was not resolved by the renderer."
                ),
                "offending_kind": offending_kind,
                "offending_path": offending_path,
            }
        )

    for match in PLACEHOLDER_JINJA_VAR_PATTERN.finditer(text):
        token = match.group(0)
        findings.append(
            {
                "rule": "leftover_jinja_marker",
                "tier": "hard",
                "matched_token": token,
                "rule_detail": (
                    f"Leftover Jinja variable marker {token} -- the "
                    f"renderer was bypassed or the file was not processed."
                ),
                "offending_kind": offending_kind,
                "offending_path": offending_path,
            }
        )

    for match in PLACEHOLDER_JINJA_BLOCK_PATTERN.finditer(text):
        token = match.group(0)
        findings.append(
            {
                "rule": "leftover_jinja_marker",
                "tier": "hard",
                "matched_token": token,
                "rule_detail": (
                    f"Leftover Jinja block marker {token} -- the renderer "
                    f"was bypassed or the file was not processed."
                ),
                "offending_kind": offending_kind,
                "offending_path": offending_path,
            }
        )

    return findings


# ---------------------------------------------------------------------------
# §8.1.2 Illegal-filesystem-character rule
# ---------------------------------------------------------------------------


def check_illegal_filesystem_character(
    *,
    path_segments: list[str],
    file_names: list[str],
) -> list[dict[str, Any]]:
    """§8.1.2: detect Windows-illegal characters in any segment / file name.

    Illegal set: NUL, ``<``, ``>``, ``:``, ``"``, ``/``, ``\\``, ``|``,
    ``?``, ``*``, ASCII 0-31, trailing dot or trailing space.

    The spec's POSIX exception allows ``<`` / ``>`` in non-token positions
    on POSIX -- but our app composes paths cross-platform, so we ALWAYS
    reject. Returns rule ``illegal_filesystem_character`` findings.
    """
    findings: list[dict[str, Any]] = []

    for segment in path_segments:
        findings.extend(_scan_for_illegal_chars(segment, "directory_segment"))

    for name in file_names:
        findings.extend(_scan_for_illegal_chars(name, "file_name"))

    return findings


def _scan_for_illegal_chars(name: str, offending_kind: str) -> list[dict[str, Any]]:
    """Return one finding per illegal character / trailing-rule violation in ``name``."""
    findings: list[dict[str, Any]] = []

    seen: set[str] = set()

    for ch in name:
        if ch in seen:
            continue
        is_illegal_windows = ch in WINDOWS_ILLEGAL_CHARS
        is_control = ord(ch) < 32 or ch == "\x00"
        if is_illegal_windows or is_control:
            seen.add(ch)
            display = repr(ch)
            findings.append(
                {
                    "rule": "illegal_filesystem_character",
                    "tier": "hard",
                    "matched_token": ch,
                    "rule_detail": (
                        f"Name {name!r} contains illegal filesystem character {display}."
                    ),
                    "offending_kind": offending_kind,
                    "offending_path": name,
                }
            )

    if name.endswith("."):
        findings.append(
            {
                "rule": "illegal_filesystem_character",
                "tier": "hard",
                "matched_token": ".",
                "rule_detail": (
                    f"Name {name!r} ends with a trailing dot, which is illegal on Windows targets."
                ),
                "offending_kind": offending_kind,
                "offending_path": name,
            }
        )

    if name.endswith(" "):
        findings.append(
            {
                "rule": "illegal_filesystem_character",
                "tier": "hard",
                "matched_token": " ",
                "rule_detail": (
                    f"Name {name!r} ends with a trailing space, which is "
                    f"illegal on Windows targets."
                ),
                "offending_kind": offending_kind,
                "offending_path": name,
            }
        )

    return findings


# ---------------------------------------------------------------------------
# §8.1.2 Reserved-filesystem-name rule
# ---------------------------------------------------------------------------


def check_reserved_filesystem_name(*, file_names: list[str]) -> list[dict[str, Any]]:
    """§8.1.2: detect Windows reserved names (``CON``, ``PRN``, ``AUX``,
    ``NUL``, ``COM1..COM9``, ``LPT1..LPT9``).

    Case-insensitive; with or without extension. Uses
    ``WINDOWS_RESERVED_NAMES`` from constants. Returns rule
    ``reserved_filesystem_name`` findings.
    """
    findings: list[dict[str, Any]] = []

    for name in file_names:
        # Strip the extension (everything after the first dot is treated as
        # an extension here; Windows applies the reserved-name rule to the
        # base stem regardless of suffix).
        stem = name.split(".", 1)[0] if "." in name else name
        if stem.upper() in WINDOWS_RESERVED_NAMES:
            findings.append(
                {
                    "rule": "reserved_filesystem_name",
                    "tier": "hard",
                    "matched_token": stem.upper(),
                    "rule_detail": (f"Name {name!r} matches Windows reserved name {stem.upper()}."),
                    "offending_kind": "file_name",
                    "offending_path": name,
                }
            )

    return findings


# ---------------------------------------------------------------------------
# §8.1.3 Mode-prefix mismatch rule
# ---------------------------------------------------------------------------


def check_mode_prefix_mismatch(
    *,
    leaf_dir_name: str,
    parent_dir_name: str | None,
    creation_run_kind: str | None,
) -> list[dict[str, Any]]:
    """§8.1.3: detect three-way disagreement between ``run_kind``, leaf
    prefix, and parent folder.

    Hard-tier. Triple-agreement contract:

    - ``run_kind="experimental"`` <=> leaf prefix ``Run_`` <=> parent
      != ``TestRuns/``
    - ``run_kind="test"`` <=> leaf prefix ``TestRun_`` <=> parent
      == ``TestRuns/``

    Returns rule ``mode_prefix_mismatch`` findings naming the conflict.
    """
    findings: list[dict[str, Any]] = []

    if creation_run_kind is None:
        return findings

    leaf_says_test = leaf_dir_name.startswith("TestRun_")
    leaf_says_experimental = leaf_dir_name.startswith("Run_") and not leaf_says_test
    parent_is_test_runs = parent_dir_name == TEST_RUNS_DIR_NAME

    if creation_run_kind == "experimental":
        if not leaf_says_experimental:
            findings.append(
                {
                    "rule": "mode_prefix_mismatch",
                    "tier": "hard",
                    "matched_token": leaf_dir_name,
                    "rule_detail": (
                        f"creation.json run_kind='experimental' requires "
                        f"leaf prefix 'Run_' but leaf is "
                        f"{leaf_dir_name!r}."
                    ),
                    "offending_kind": "directory_segment",
                    "offending_path": leaf_dir_name,
                }
            )
        if parent_is_test_runs:
            findings.append(
                {
                    "rule": "mode_prefix_mismatch",
                    "tier": "hard",
                    "matched_token": TEST_RUNS_DIR_NAME,
                    "rule_detail": (
                        f"creation.json run_kind='experimental' requires "
                        f"parent != 'TestRuns/' but parent is "
                        f"{parent_dir_name!r}."
                    ),
                    "offending_kind": "directory_segment",
                    "offending_path": leaf_dir_name,
                }
            )
    elif creation_run_kind == "test":
        if not leaf_says_test:
            findings.append(
                {
                    "rule": "mode_prefix_mismatch",
                    "tier": "hard",
                    "matched_token": leaf_dir_name,
                    "rule_detail": (
                        f"creation.json run_kind='test' requires leaf "
                        f"prefix 'TestRun_' but leaf is "
                        f"{leaf_dir_name!r}."
                    ),
                    "offending_kind": "directory_segment",
                    "offending_path": leaf_dir_name,
                }
            )
        if not parent_is_test_runs:
            findings.append(
                {
                    "rule": "mode_prefix_mismatch",
                    "tier": "hard",
                    "matched_token": str(parent_dir_name),
                    "rule_detail": (
                        f"creation.json run_kind='test' requires parent "
                        f"== 'TestRuns/' but parent is "
                        f"{parent_dir_name!r}."
                    ),
                    "offending_kind": "directory_segment",
                    "offending_path": leaf_dir_name,
                }
            )

    return findings


# ---------------------------------------------------------------------------
# §8.1.4 Orphan rule
# ---------------------------------------------------------------------------


def check_orphan(*, level: str, has_creation_json: bool) -> list[dict[str, Any]]:
    """§8.1.4: detect missing ``creation.json`` at project / run level
    (NOT equipment).

    Soft-tier. Returns one rule ``orphan`` finding when ``level`` is in
    ``("project", "run")`` and ``has_creation_json`` is ``False``.
    """
    if level not in ("project", "run"):
        return []
    if has_creation_json:
        return []

    return [
        {
            "rule": "orphan",
            "tier": "soft",
            "matched_token": None,
            "rule_detail": (
                f"{level.capitalize()}-level directory has no "
                f"creation.json -- the cache file is expected at this "
                f"level but is missing."
            ),
            "offending_kind": "directory_segment",
            "offending_path": "",
        }
    ]


# ---------------------------------------------------------------------------
# §8.1.5 Missing-required-field rule
# ---------------------------------------------------------------------------


def check_missing_required_field(
    *,
    readme_fields: dict[str, Any] | None,
    required_field_ids: list[str],
) -> list[dict[str, Any]]:
    """§8.1.5: detect required README fields that are absent or empty.

    Soft-tier. Walks the ``readme_fields_json`` layer dicts
    (``core_fields``, ``template_fields``, ``config_fields``) for each id
    in ``required_field_ids``. Returns rule ``missing_required_field``
    findings.
    """
    findings: list[dict[str, Any]] = []

    layers: list[dict[str, Any]] = []
    if readme_fields is not None:
        for layer_name in ("core_fields", "template_fields", "config_fields"):
            layer = readme_fields.get(layer_name)
            if isinstance(layer, dict):
                layers.append(layer)

    for field_id in required_field_ids:
        value = _lookup_field_value(layers, field_id)
        if value is None or (isinstance(value, str) and value == ""):
            findings.append(
                {
                    "rule": "missing_required_field",
                    "tier": "soft",
                    "matched_token": field_id,
                    "rule_detail": (f"Required README field {field_id!r} is absent or empty."),
                    "offending_kind": "file_content",
                    "offending_path": field_id,
                }
            )

    return findings


def _lookup_field_value(layers: list[dict[str, Any]], field_id: str) -> Any:
    """Return the first non-missing value for ``field_id`` across layers.

    A missing key returns ``None``. An explicit ``None`` value also returns
    ``None`` so the caller treats both as "absent".
    """
    for layer in layers:
        if field_id in layer:
            return layer[field_id]
    return None


# ---------------------------------------------------------------------------
# §8.1 Malformed YAML front matter rule
# ---------------------------------------------------------------------------


def check_malformed_yaml_front_matter(*, content: str) -> list[dict[str, Any]]:
    """§8.1: detect malformed YAML front matter at the head of a Markdown
    file.

    Soft-tier. Returns rule ``malformed_yaml_front_matter`` finding when
    the first ``---`` opens a block but no second ``---`` closes it within
    the first 200 lines, OR when ``yaml.safe_load`` fails on the block.
    """
    lines = content.splitlines()
    if not lines or lines[0].rstrip() != "---":
        return []

    closing_index: int | None = None
    for index in range(1, min(len(lines), _FRONT_MATTER_MAX_LINES)):
        if lines[index].rstrip() == "---":
            closing_index = index
            break

    if closing_index is None:
        return [
            {
                "rule": "malformed_yaml_front_matter",
                "tier": "soft",
                "matched_token": None,
                "rule_detail": (
                    "Markdown file opens with '---' but no closing '---' "
                    f"was found within the first {_FRONT_MATTER_MAX_LINES} "
                    f"lines."
                ),
                "offending_kind": "file_content",
                "offending_path": "",
            }
        ]

    block = "\n".join(lines[1:closing_index])
    try:
        yaml.safe_load(block)
    except yaml.YAMLError as exc:
        return [
            {
                "rule": "malformed_yaml_front_matter",
                "tier": "soft",
                "matched_token": None,
                "rule_detail": (f"YAML front matter failed to parse: {exc!s}"),
                "offending_kind": "file_content",
                "offending_path": "",
            }
        ]

    return []
