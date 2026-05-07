"""Plugin worker entry point. Backend Spec §6.3.1, §6.3.2.

Invoked as ``python -m exlab_wizard.plugins._worker``. Reads a single JSON
envelope from stdin, imports the plugin module, instantiates the
``Plugin`` class, runs its lifecycle against the supplied files, and
writes a single JSON envelope to stdout. Exits with the spec-defined
exit code:

==== ====================================================================
0    success (every file processed without raising)
1    :class:`exlab_wizard.errors.PluginError` raised
2    :class:`exlab_wizard.errors.PluginInputRequired` raised
3    uncaught exception or malformed envelope
124  reserved for the host (host-side wall-clock timeout, never set here)
==== ====================================================================

The worker writes structured log frames to ``stderr`` via
:class:`exlab_wizard.plugins.logger.WorkerPluginLogger`; the host parses
those frames in real time and forwards them to its canonical logger
chain (Backend Spec §16.8). ``stdout`` is reserved for the IPC envelope
and any stray write to it from a plugin will corrupt the protocol.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import traceback
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from exlab_wizard.errors import PluginError, PluginInputRequired
from exlab_wizard.plugins.base import FileChange, Plugin, PluginContext
from exlab_wizard.plugins.logger import WorkerPluginLogger

# ---------------------------------------------------------------------------
# Exit codes (Backend Spec §6.3.1).
# ---------------------------------------------------------------------------

EXIT_SUCCESS: int = 0
EXIT_PLUGIN_ERROR: int = 1
EXIT_INPUT_REQUIRED: int = 2
EXIT_UNCAUGHT: int = 3
EXIT_TIMEOUT: int = 124  # set by the host, never by the worker itself.


# ---------------------------------------------------------------------------
# In-worker state.
# ---------------------------------------------------------------------------


@dataclass
class _WorkerOutcome:
    """Aggregated worker-side result before stdout serialization."""

    result: str = "success"  # one of "success" / "failed" / "input_required"
    per_file: list[dict[str, Any]] = field(default_factory=list)
    log_records: list[dict[str, Any]] = field(default_factory=list)
    input_required: dict[str, Any] | None = None
    error_message: str | None = None
    exit_code: int = EXIT_SUCCESS


# ---------------------------------------------------------------------------
# Envelope decoding.
# ---------------------------------------------------------------------------


def _read_stdin_envelope() -> dict[str, Any]:
    """Read a single JSON object from stdin and return it as a dict.

    The host writes one JSON object terminated by ``\\n`` (Backend Spec
    §6.3.2) and then closes its end of the pipe; we therefore drain
    everything until EOF rather than trying to read line-by-line, which
    works with arbitrarily large envelopes (within the 1 MiB cap the
    host enforces) and tolerates pretty-printed input.
    """
    raw = sys.stdin.read()
    if not raw:
        raise ValueError("plugin worker received empty stdin envelope")
    return json.loads(raw)


def _decode_files(files: Iterable[Any], dst_root: Path) -> list[Path]:
    """Resolve incoming file path strings against ``dst_root``.

    Paths are accepted as either absolute or relative to ``dst_root``
    (the host normally sends absolute paths but tests may send relative
    ones). Relative paths are resolved against the worker's CWD which
    the host sets to ``dst_root``.
    """
    out: list[Path] = []
    for entry in files:
        candidate = Path(str(entry))
        if not candidate.is_absolute():
            candidate = dst_root / candidate
        out.append(candidate)
    return out


def _build_context(
    payload: dict[str, Any],
    *,
    log: WorkerPluginLogger,
) -> tuple[PluginContext, Path, list[Path], bool, dict[str, Any]]:
    """Materialize a :class:`PluginContext` from the stdin envelope.

    Returns the context plus the destination root, the file list, the
    dry-run flag, and the optional ``extra_inputs`` payload (only
    populated on a resume after :class:`PluginInputRequired`).
    """
    context_blob = payload.get("context", {}) or {}
    dst_root = Path(str(context_blob.get("dst_root", os.getcwd())))
    answers_file = Path(str(context_blob.get("answers_file", str(dst_root / ".exlab-answers.yml"))))

    ctx = PluginContext(
        variables=dict(context_blob.get("variables", {})),
        dst_root=dst_root,
        answers_file=answers_file,
        template_name=str(context_blob.get("template_name", "")),
        template_version=str(context_blob.get("template_version", "")),
        run_kind=str(context_blob.get("run_kind", "")),
        equipment_id=str(context_blob.get("equipment_id", "")),
        project=str(context_blob.get("project", "")),
        dry_run=bool(payload.get("dry_run", False)),
        log=log,
    )

    files = _decode_files(payload.get("files", []) or [], dst_root)
    dry_run = bool(payload.get("dry_run", False))
    extra_inputs = dict(payload.get("extra_inputs") or {})
    return ctx, dst_root, files, dry_run, extra_inputs


# ---------------------------------------------------------------------------
# Plugin import + instantiation.
# ---------------------------------------------------------------------------


def _load_plugin_class(module_name: str) -> type[Plugin]:
    """Import the plugin module and return the exported ``Plugin`` symbol.

    The host names the module via ``plugin_module`` in the envelope. The
    worker has the plugin's package directory prepended to ``sys.path``
    so the import resolves to the plugin's own ``__init__.py``, which
    must export ``Plugin`` (Backend Spec §6.1.1).
    """
    module = importlib.import_module(module_name)
    plugin_cls = getattr(module, "Plugin", None)
    if plugin_cls is None:
        raise ImportError(
            f"plugin module {module_name!r} does not export a 'Plugin' symbol",
        )
    if not isinstance(plugin_cls, type) or not issubclass(plugin_cls, Plugin):
        raise TypeError(
            f"plugin module {module_name!r} 'Plugin' export is not a Plugin subclass",
        )
    return plugin_cls


# ---------------------------------------------------------------------------
# Lifecycle execution.
# ---------------------------------------------------------------------------


def _run_lifecycle(
    plugin: Plugin,
    ctx: PluginContext,
    files: list[Path],
    *,
    extra_inputs: dict[str, Any],
) -> _WorkerOutcome:
    """Execute the plugin's transform lifecycle against ``files``.

    The lifecycle is:

    1. ``pre_transform_all(ctx)``
    2. for each file: ``can_handle`` -> ``transform`` (or
       ``describe_changes`` in dry-run)
    3. ``post_transform_all(ctx)``

    On :class:`PluginInputRequired` we capture the payload, invoke
    ``on_plugin_failure``, and return early -- the host re-spawns the
    worker on resume.

    On :class:`PluginError` we record the failure, invoke
    ``on_plugin_failure``, and return early.

    Any other exception is re-raised so the outer ``main`` can catch it
    and return the EXIT_UNCAUGHT code with a traceback.
    """
    outcome = _WorkerOutcome()

    # Stash extra_inputs onto the context's variables surface so a
    # resumed plugin can read them via ``ctx.variables`` if it wants.
    # Spec §6.4.2 also calls them ``ctx.extra_inputs``; we expose them
    # under that name through a sentinel key the plugin author can read.
    if extra_inputs:
        # The PluginContext is a frozen dataclass; we cannot rebind
        # attributes. The variable map is mutable (dict) so we widen it.
        ctx.variables.setdefault("__extra_inputs__", {}).update(extra_inputs)

    try:
        plugin.pre_transform_all(ctx)
    except PluginInputRequired as exc:
        outcome.result = "input_required"
        outcome.input_required = {"fields": exc.fields, "reason": exc.reason}
        outcome.exit_code = EXIT_INPUT_REQUIRED
        with _SwallowCallbackErrors():
            plugin.on_plugin_failure(exc, ctx)
        return outcome
    except PluginError as exc:
        outcome.result = "failed"
        outcome.error_message = str(exc)
        outcome.exit_code = EXIT_PLUGIN_ERROR
        with _SwallowCallbackErrors():
            plugin.on_plugin_failure(exc, ctx)
        return outcome

    for file_path in files:
        try:
            if not plugin.can_handle(file_path, ctx.variables):
                outcome.per_file.append(
                    {"path": str(file_path), "status": "skipped", "changes": None}
                )
                continue
        except Exception as exc:
            outcome.per_file.append(
                {
                    "path": str(file_path),
                    "status": "failed",
                    "changes": None,
                    "error": f"can_handle raised {type(exc).__name__}: {exc}",
                }
            )
            continue

        try:
            if ctx.dry_run:
                changes = plugin.describe_changes(file_path, ctx)
                outcome.per_file.append(
                    {
                        "path": str(file_path),
                        "status": "described",
                        "changes": [_serialize_change(c) for c in changes],
                    }
                )
            else:
                plugin.transform(file_path, ctx)
                outcome.per_file.append(
                    {"path": str(file_path), "status": "modified", "changes": None}
                )
        except PluginInputRequired as exc:
            outcome.result = "input_required"
            outcome.input_required = {"fields": exc.fields, "reason": exc.reason}
            outcome.exit_code = EXIT_INPUT_REQUIRED
            outcome.per_file.append(
                {"path": str(file_path), "status": "input_required", "changes": None}
            )
            with _SwallowCallbackErrors():
                plugin.on_plugin_failure(exc, ctx)
            return outcome
        except PluginError as exc:
            outcome.result = "failed"
            outcome.error_message = str(exc)
            outcome.exit_code = EXIT_PLUGIN_ERROR
            outcome.per_file.append(
                {
                    "path": str(file_path),
                    "status": "failed",
                    "changes": None,
                    "error": str(exc),
                }
            )
            with _SwallowCallbackErrors():
                plugin.on_plugin_failure(exc, ctx)
            return outcome

    try:
        plugin.post_transform_all(ctx)
    except PluginError as exc:
        outcome.result = "failed"
        outcome.error_message = str(exc)
        outcome.exit_code = EXIT_PLUGIN_ERROR
        with _SwallowCallbackErrors():
            plugin.on_plugin_failure(exc, ctx)
        return outcome

    return outcome


def _serialize_change(change: FileChange) -> dict[str, Any]:
    """Convert a :class:`FileChange` to its IPC dict shape."""
    return {
        "path": str(change.path),
        "kind": change.kind,
        "summary": change.summary,
        "detail": dict(change.detail),
    }


# ---------------------------------------------------------------------------
# Misc helpers.
# ---------------------------------------------------------------------------


class _SwallowCallbackErrors:
    """Context manager that suppresses errors from ``on_plugin_failure``.

    Per Backend Spec §6.1.3 the failure-callback contract is "MUST NOT
    re-raise; raising means the cleanup itself failed and will be logged
    separately." We log via stderr so the host sees it and continue.
    """

    def __enter__(self) -> _SwallowCallbackErrors:
        return self

    def __exit__(self, exc_type: object, exc: BaseException | None, tb: object) -> bool:
        if exc is not None:
            try:
                sys.stderr.write(
                    json.dumps(
                        {
                            "level": "ERROR",
                            "message": "on_plugin_failure raised; cleanup may be incomplete",
                            "context": {
                                "exception": type(exc).__name__,
                                "detail": str(exc),
                            },
                        }
                    )
                    + "\n"
                )
                sys.stderr.flush()
            except Exception:
                pass
            return True
        return False


# ---------------------------------------------------------------------------
# Entry point.
# ---------------------------------------------------------------------------


def main() -> int:
    """Worker entry point. Returns the process exit code."""
    log = WorkerPluginLogger()

    try:
        payload = _read_stdin_envelope()
    except Exception as exc:
        sys.stderr.write(
            json.dumps(
                {
                    "level": "ERROR",
                    "message": "failed to decode stdin envelope",
                    "context": {"exception": type(exc).__name__, "detail": str(exc)},
                }
            )
            + "\n"
        )
        sys.stderr.flush()
        return EXIT_UNCAUGHT

    plugin_module = str(payload.get("plugin_module", "")).strip()
    if not plugin_module:
        sys.stderr.write(
            json.dumps(
                {
                    "level": "ERROR",
                    "message": "stdin envelope is missing 'plugin_module'",
                    "context": {},
                }
            )
            + "\n"
        )
        sys.stderr.flush()
        return EXIT_UNCAUGHT

    try:
        ctx, _dst_root, files, _dry_run, extra_inputs = _build_context(payload, log=log)
    except Exception as exc:
        sys.stderr.write(
            json.dumps(
                {
                    "level": "ERROR",
                    "message": "failed to build PluginContext",
                    "context": {"exception": type(exc).__name__, "detail": str(exc)},
                }
            )
            + "\n"
        )
        sys.stderr.flush()
        return EXIT_UNCAUGHT

    try:
        plugin_cls = _load_plugin_class(plugin_module)
        plugin = plugin_cls()
    except Exception as exc:
        sys.stderr.write(
            json.dumps(
                {
                    "level": "ERROR",
                    "message": "failed to import or instantiate plugin",
                    "context": {
                        "module": plugin_module,
                        "exception": type(exc).__name__,
                        "detail": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                }
            )
            + "\n"
        )
        sys.stderr.flush()
        return EXIT_UNCAUGHT

    try:
        outcome = _run_lifecycle(plugin, ctx, files, extra_inputs=extra_inputs)
    except Exception as exc:
        sys.stderr.write(
            json.dumps(
                {
                    "level": "ERROR",
                    "message": "plugin lifecycle raised an unhandled exception",
                    "context": {
                        "exception": type(exc).__name__,
                        "detail": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                }
            )
            + "\n"
        )
        sys.stderr.flush()
        return EXIT_UNCAUGHT

    envelope = {
        "result": outcome.result,
        "per_file": outcome.per_file,
        "log_records": outcome.log_records,
        "input_required": outcome.input_required,
        "error_message": outcome.error_message,
    }
    sys.stdout.write(json.dumps(envelope) + "\n")
    sys.stdout.flush()
    return outcome.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
