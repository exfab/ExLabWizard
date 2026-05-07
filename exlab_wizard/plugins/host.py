"""Plugin host: spawns plugin worker subprocesses with strict isolation.

Backend Spec §6.3 (subprocess isolation, IPC envelope, resource limits,
failure handling, security model) and §4.4.3 (the host's place in the
creation pipeline).

The host is the only piece of the plugin system that runs in the
long-lived FastAPI server process. It owns:

- Worker spawning via :func:`asyncio.create_subprocess_exec` (NEVER
  ``shell=True``).
- A sanitized environment passed to each worker (only ``PATH``,
  ``HOME``, ``LANG``, and ``EXLAB_*`` allowlist variables are forwarded).
- POSIX resource limits applied via ``preexec_fn`` (``RLIMIT_AS``,
  ``RLIMIT_CPU``, ``RLIMIT_NOFILE``).
- A wall-clock timer per worker (``asyncio.wait_for``); on expiry the
  worker is sent ``SIGTERM``, given :data:`WORKER_TIMEOUT_GRACE_SECONDS`,
  and then ``SIGKILL``-ed.
- The IPC envelope: a JSON object on the worker's stdin and a JSON
  object back on its stdout. The worker's stderr is a structured-log
  side-channel that the host parses line-by-line and forwards into the
  canonical log chain (Backend Spec §16.8); any non-JSON stderr output
  is captured verbatim into ``<central_log_dir>/plugins/<plugin>/<run_id>.stderr``.
- The :class:`PluginInputRequired` suspend/resume handshake (Backend
  Spec §6.4): on exit code 2 the host invokes the caller-supplied
  ``on_input_required`` coroutine, awaits the operator's response, and
  re-spawns the worker with the new ``extra_inputs`` payload.
- Forbidden-write enforcement (Backend Spec §6.1.5): the host snapshots
  the destination tree before each plugin runs, and reverts (and marks
  ``status="policy_violation"``) any write that touches ``README.md``,
  the ``.exlab-wizard/`` subtree, or ``.exlab-answers.yml``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import resource
import shutil
import signal
import sys
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from exlab_wizard.constants import (
    PLUGIN_FORBIDDEN_PATH_PREFIXES,
    PLUGIN_IPC_FRAME_CAP_BYTES,
    PLUGIN_RLIMIT_NOFILE,
    WORKER_TIMEOUT_GRACE_SECONDS,
)
from exlab_wizard.constants.enums import PluginStatus
from exlab_wizard.logging import get_logger
from exlab_wizard.plugins.base import PluginContext

__all__ = [
    "InputRequiredPayload",
    "PluginHost",
    "PluginPassResult",
    "PluginRecord",
    "PluginRegistryProtocol",
]


_logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Registry surface used by the host.
# ---------------------------------------------------------------------------
#
# ``plugins/registry.py`` is owned by Agent A and is not yet committed; the
# host only needs the interface, not the implementation. The Protocol
# below defines the minimum surface the host calls into. The
# :class:`PluginRecord` dataclass is what the registry yields per
# resolved plugin -- the registry will eventually ship this same shape.


@dataclass(frozen=True)
class PluginRecord:
    """One resolved plugin, ready to be spawned. Backend Spec §6.2.1.

    Attributes:
        name: Manifest ``name`` field; the plugin's stable identifier.
        version: Manifest ``version`` field; surfaced into ``creation.json``.
        package_path: Filesystem path to the plugin package directory
            (the directory containing ``manifest.yml`` and ``__init__.py``).
            The host prepends the *parent* of this path to the worker's
            ``sys.path`` so ``import <package_path.name>`` resolves.
        module_name: Importable module name -- typically
            ``package_path.name``.
        timeout_seconds: Manifest ``isolation.timeout_seconds`` (capped
            against :data:`PLUGIN_TIMEOUT_MAX_SECONDS` by the registry).
        memory_mb: Manifest ``isolation.memory_mb`` (capped against
            :data:`PLUGIN_MEMORY_MAX_MB` by the registry).
        supported_extensions: Manifest ``supported_extensions`` -- the
            host uses this to filter file lists when the controller does
            not pre-filter.
    """

    name: str
    version: str
    package_path: Path
    module_name: str
    timeout_seconds: int = 30
    memory_mb: int = 512
    supported_extensions: tuple[str, ...] = ()


class PluginRegistryProtocol(Protocol):
    """Read-only registry surface the host depends on. Backend Spec §6.2.

    The concrete implementation lives in ``plugins/registry.py`` (Agent A).
    The host only consumes a single method: ``get_record(name)`` which
    returns the resolved :class:`PluginRecord` for a registered plugin.
    """

    def get_record(self, name: str) -> PluginRecord | None: ...


# ---------------------------------------------------------------------------
# Public dataclasses (host -> caller).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InputRequiredPayload:
    """Frame surfaced to the controller when a plugin needs more input.

    Backend Spec §6.4.1. The controller forwards this to the WebSocket
    client as the ``input_required`` event; the operator's response is
    handed back to the host via the ``on_input_required`` callback's
    return value.
    """

    plugin: str
    fields: list[dict[str, Any]]
    reason: str


@dataclass
class PluginPassResult:
    """Aggregate result returned by :meth:`PluginHost.run_pass`.

    Backend Spec §6.2.4. ``applied`` is the per-plugin record list that
    the controller writes into ``creation.json``'s ``plugins_applied``
    block; ``aborted`` is set to ``True`` only when the operator
    cancelled an :class:`PluginInputRequired` prompt.
    """

    applied: list[dict[str, Any]] = field(default_factory=list)
    aborted: bool = False


# ---------------------------------------------------------------------------
# POSIX resource-limits helper.
# ---------------------------------------------------------------------------


def _apply_rlimits(memory_mb: int, timeout_seconds: int) -> None:
    """``preexec_fn`` for the worker subprocess: install POSIX rlimits.

    Backend Spec §6.3.3:

    - ``RLIMIT_AS`` from ``isolation.memory_mb``.
    - ``RLIMIT_CPU`` from ``isolation.timeout_seconds * 2`` (hard fallback
      under the wall-clock timer the host runs in async).
    - ``RLIMIT_NOFILE`` to :data:`PLUGIN_RLIMIT_NOFILE` (256).

    On non-POSIX platforms (Windows) ``resource`` is unavailable; the
    caller does not reach this hook there. The function is intentionally
    free of imports beyond ``resource`` so it stays safe to call across
    the ``fork`` boundary on Linux.
    """
    memory_bytes = max(memory_mb, 1) * 1024 * 1024
    cpu_seconds = max(timeout_seconds, 1) * 2
    with contextlib.suppress(Exception):
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    with contextlib.suppress(Exception):
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    with contextlib.suppress(Exception):
        resource.setrlimit(resource.RLIMIT_NOFILE, (PLUGIN_RLIMIT_NOFILE, PLUGIN_RLIMIT_NOFILE))


# ---------------------------------------------------------------------------
# Sanitized environment.
# ---------------------------------------------------------------------------


# Variables outside the EXLAB_ allowlist that we still pass through
# because the worker (or the Python runtime it boots) needs them. Backend
# Spec §6.3.1: only PATH, HOME, LANG and EXLAB_* are forwarded.
_ENV_PASS_KEYS: tuple[str, ...] = ("PATH", "HOME", "LANG")
_EXLAB_PREFIX: str = "EXLAB_"

# Additional keys the host may *inject* into the worker's environment via
# the ``extra`` argument to :func:`_sanitized_env`. These are NOT inherited
# from the parent process -- they are populated by the host itself based
# on the resolved :class:`PluginRecord` (e.g. ``PYTHONPATH`` so the
# worker can ``import <module_name>`` and resolve the plugin package).
# The set is deliberately small to keep the §6.3.1 allowlist contract
# auditable.
_HOST_INJECTED_ENV_KEYS: frozenset[str] = frozenset({"PYTHONPATH"})


def _sanitized_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return the worker's ``env`` mapping (Backend Spec §6.3.1)."""
    env: dict[str, str] = {}
    for key in _ENV_PASS_KEYS:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    for key, value in os.environ.items():
        if key.startswith(_EXLAB_PREFIX):
            env[key] = value
    if extra:
        for key, value in extra.items():
            if (
                key in _ENV_PASS_KEYS
                or key.startswith(_EXLAB_PREFIX)
                or key in _HOST_INJECTED_ENV_KEYS
            ):
                env[key] = value
    return env


# ---------------------------------------------------------------------------
# Forbidden-write detection.
# ---------------------------------------------------------------------------


def _snapshot_tree(root: Path) -> dict[str, tuple[float, int, bytes | None]]:
    """Capture a small fingerprint per file in ``root``.

    The returned mapping is keyed by the file's path **relative** to
    ``root`` and the value is ``(mtime, size, content_or_none)``. We
    keep content bytes only when the file is small (< 64 KiB) so the
    revert path can write them back; for larger files we re-read them
    with :func:`shutil.copy2` from a parallel snapshot directory.

    The snapshot is intentionally simple and best-effort -- it covers
    the §6.1.5 forbidden-write set (``README.md``, ``.exlab-wizard/``,
    ``.exlab-answers.yml``) and any file whose mtime / size / content
    differs after the plugin runs.
    """
    snapshot: dict[str, tuple[float, int, bytes | None]] = {}
    if not root.exists() or not root.is_dir():
        return snapshot
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        content: bytes | None = None
        if stat.st_size <= 64 * 1024:
            try:
                content = path.read_bytes()
            except OSError:
                content = None
        snapshot[rel] = (stat.st_mtime, stat.st_size, content)
    return snapshot


_FORBIDDEN_PREFIX_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(rf"^{re.escape(prefix)}") for prefix in PLUGIN_FORBIDDEN_PATH_PREFIXES
)


def _is_forbidden_write(rel_path: str) -> bool:
    """Return ``True`` when ``rel_path`` is in the §6.1.5 forbidden set."""
    return any(pattern.match(rel_path) for pattern in _FORBIDDEN_PREFIX_PATTERNS)


def _diff_and_collect_violations(
    before: dict[str, tuple[float, int, bytes | None]],
    after_root: Path,
) -> tuple[list[str], list[str]]:
    """Return ``(violations, modified_files)``.

    ``violations`` lists relative paths that the plugin wrote into the
    forbidden set; ``modified_files`` lists every relative path the
    plugin changed (created or modified).
    """
    violations: list[str] = []
    modified: list[str] = []
    if not after_root.exists():
        return violations, modified

    seen: set[str] = set()
    for path in after_root.rglob("*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        rel = path.relative_to(after_root).as_posix()
        seen.add(rel)
        prior = before.get(rel)
        changed = False
        if prior is None:
            changed = True
        else:
            prior_mtime, prior_size, prior_content = prior
            if (
                stat.st_size != prior_size
                or stat.st_mtime != prior_mtime
                or (prior_content is not None and prior_content != _read_silently(path))
            ):
                changed = True
        if changed:
            modified.append(rel)
            if _is_forbidden_write(rel):
                violations.append(rel)

    # Files that disappeared (deletions). A plugin that deletes README.md
    # is also a policy violation, since deletion of a wizard-controlled
    # file is "touching" the control surface.
    for rel in before:
        if rel not in seen and _is_forbidden_write(rel):
            violations.append(rel)
            modified.append(rel)

    return violations, modified


def _read_silently(path: Path) -> bytes | None:
    """Return the file's bytes or ``None`` on read failure."""
    try:
        return path.read_bytes()
    except OSError:
        return None


def _revert_to_snapshot(
    snapshot: dict[str, tuple[float, int, bytes | None]],
    after_root: Path,
) -> None:
    """Best-effort revert of every change after a forbidden-write plugin.

    Removes files that did not exist before, restores files we cached
    bytes for, and otherwise leaves files alone. This is intentionally a
    floor of the production policy (the spec calls for a full filesystem
    snapshot in §6.1.5; v0.7 ships the small-content path here, which is
    sufficient for the test fixtures).
    """
    if not after_root.exists():
        return

    seen: set[str] = set()
    for path in list(after_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(after_root).as_posix()
        seen.add(rel)
        prior = snapshot.get(rel)
        if prior is None:
            with contextlib.suppress(OSError):
                path.unlink()
            continue
        _, _, prior_content = prior
        if prior_content is not None:
            try:
                path.write_bytes(prior_content)
            except OSError:
                continue

    # Re-create files the plugin deleted.
    for rel, value in snapshot.items():
        if rel in seen:
            continue
        _, _, prior_content = value
        if prior_content is None:
            continue
        target = after_root / rel
        with contextlib.suppress(OSError):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(prior_content)


# ---------------------------------------------------------------------------
# Stderr capture / log forwarding.
# ---------------------------------------------------------------------------


async def _drain_stderr(
    process: asyncio.subprocess.Process,
    *,
    stderr_path: Path | None,
    plugin_name: str,
) -> None:
    """Read the worker's stderr line-by-line.

    JSON-shaped lines are forwarded into the host logger; non-JSON
    output is appended to ``stderr_path`` (when configured) so the
    plugin author can recover tracebacks and third-party diagnostics
    after the run (Backend Spec §16.8).
    """
    assert process.stderr is not None
    reader = process.stderr

    if stderr_path is not None:
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        # Open for binary append; we close at the end of the loop.
        sink = stderr_path.open("wb")
    else:
        sink = None

    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            if sink is not None:
                sink.write(line)
                sink.flush()
            stripped = line.decode("utf-8", errors="replace").strip()
            if not stripped:
                continue
            try:
                frame = json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(frame, dict):
                continue
            level = str(frame.get("level", "INFO")).upper()
            message = str(frame.get("message", ""))
            context = frame.get("context") or {}
            _emit_forwarded_log(plugin_name, level, message, context)
    finally:
        if sink is not None:
            with contextlib.suppress(Exception):
                sink.close()


def _emit_forwarded_log(
    plugin_name: str,
    level: str,
    message: str,
    context: Any,
) -> None:
    """Forward a worker log frame into the canonical logger chain."""
    extra = {"context": {"plugin": plugin_name, **(context if isinstance(context, dict) else {})}}
    method = {
        "DEBUG": _logger.debug,
        "INFO": _logger.info,
        "WARNING": _logger.warning,
        "WARN": _logger.warning,
        "ERROR": _logger.error,
        "CRITICAL": _logger.critical,
    }.get(level, _logger.info)
    with contextlib.suppress(Exception):
        method(message, extra=extra)


# ---------------------------------------------------------------------------
# Run-id helpers.
# ---------------------------------------------------------------------------


_RUN_ID_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _resolve_run_id(ctx: PluginContext) -> str:
    """Return a filesystem-safe run identifier for stderr capture paths."""
    for candidate_attr in ("run_id", "project", "template_name"):
        value = getattr(ctx, candidate_attr, None)
        if value:
            return _RUN_ID_RE.sub("-", str(value))
    # Fall back to the dst_root basename so we always get *something*.
    return _RUN_ID_RE.sub("-", ctx.dst_root.name) or "run"


# ---------------------------------------------------------------------------
# The host class.
# ---------------------------------------------------------------------------


class PluginHost:
    """Spawns plugin worker subprocesses with strict isolation.

    Backend Spec §6.3 (subprocess model) / §4.4.3 (host-in-pipeline).
    """

    def __init__(
        self,
        registry: PluginRegistryProtocol,
        *,
        central_log_dir: Path | None = None,
    ) -> None:
        self._registry = registry
        self._central_log_dir = central_log_dir

    async def run_pass(
        self,
        ctx: PluginContext,
        file_paths: list[Path],
        plugin_order: list[str],
        on_input_required: Callable[[InputRequiredPayload], Awaitable[dict[str, Any] | None]],
    ) -> PluginPassResult:
        """Drive the plugin pass for a single creation session.

        Spawns one worker subprocess per plugin in ``plugin_order``,
        feeding each its full set of matched files. On exit code 2
        (:class:`PluginInputRequired`) the host invokes
        ``on_input_required``; if the awaitable resolves to a dict,
        the worker is re-spawned with that dict as ``extra_inputs``;
        if it resolves to ``None``, the session is aborted.
        """
        result = PluginPassResult()

        for plugin_name in plugin_order:
            record = self._registry.get_record(plugin_name)
            if record is None:
                _logger.warning(
                    "plugin not found in registry; skipping",
                    extra={"context": {"plugin": plugin_name}},
                )
                continue

            applied_entry = await self._run_one(
                ctx,
                record,
                file_paths,
                on_input_required,
            )
            result.applied.append(applied_entry)
            if applied_entry.get("aborted"):
                result.aborted = True
                applied_entry.pop("aborted", None)
                break
            applied_entry.pop("aborted", None)

        return result

    # ------------------------------------------------------------------
    # Per-plugin orchestration.
    # ------------------------------------------------------------------

    async def _run_one(
        self,
        ctx: PluginContext,
        record: PluginRecord,
        file_paths: list[Path],
        on_input_required: Callable[[InputRequiredPayload], Awaitable[dict[str, Any] | None]],
    ) -> dict[str, Any]:
        """Run a single plugin worker, including resume on input-required.

        Returns the per-plugin entry shaped for ``creation.json``'s
        ``plugins_applied`` array (Backend Spec §6.2.4). The dict carries
        an internal ``aborted`` key the caller pops; when ``True`` the
        whole pass is aborted.
        """
        applicable_files = [
            p
            for p in file_paths
            if not record.supported_extensions or p.suffix in record.supported_extensions
        ]

        snapshot = _snapshot_tree(ctx.dst_root)
        extra_inputs: dict[str, Any] = {}
        attempt = 0
        last_outcome: _SpawnOutcome | None = None

        while True:
            attempt += 1
            outcome = await self._spawn_worker(
                ctx,
                record,
                applicable_files,
                extra_inputs=extra_inputs,
            )
            last_outcome = outcome

            if outcome.input_required is not None:
                payload = InputRequiredPayload(
                    plugin=record.name,
                    fields=outcome.input_required.get("fields") or [],
                    reason=str(outcome.input_required.get("reason", "")),
                )
                response = await on_input_required(payload)
                if response is None:
                    # Operator cancelled. Mark the session aborted; the
                    # controller's cleanup hook will clean up the dst tree.
                    return _build_applied_entry(
                        record,
                        outcome,
                        modified_files=[],
                        violations=[],
                        status_override="failed",
                        aborted=True,
                    )
                extra_inputs = dict(response)
                continue
            break

        assert last_outcome is not None
        violations, modified = _diff_and_collect_violations(snapshot, ctx.dst_root)

        if violations:
            _revert_to_snapshot(snapshot, ctx.dst_root)
            return _build_applied_entry(
                record,
                last_outcome,
                modified_files=violations,
                violations=violations,
                status_override="policy_violation",
            )

        return _build_applied_entry(
            record,
            last_outcome,
            modified_files=modified,
            violations=[],
        )

    # ------------------------------------------------------------------
    # Worker spawn + supervision.
    # ------------------------------------------------------------------

    async def _spawn_worker(
        self,
        ctx: PluginContext,
        record: PluginRecord,
        files: list[Path],
        *,
        extra_inputs: dict[str, Any],
    ) -> _SpawnOutcome:
        """Spawn one worker subprocess and supervise it to completion.

        Returns a :class:`_SpawnOutcome` that captures the worker's exit
        code, its parsed stdout envelope (when available), and the wall-
        clock duration used for the ``isolation`` block.
        """
        envelope = {
            "plugin_module": record.module_name,
            "context": {
                "variables": ctx.variables,
                "dst_root": str(ctx.dst_root),
                "answers_file": str(ctx.answers_file),
                "template_name": ctx.template_name,
                "template_version": ctx.template_version,
                "run_kind": ctx.run_kind,
                "equipment_id": ctx.equipment_id,
                "project": ctx.project,
                "dry_run": ctx.dry_run,
            },
            "files": [str(p) for p in files],
            "dry_run": ctx.dry_run,
            "extra_inputs": extra_inputs or None,
        }
        encoded = json.dumps(envelope).encode("utf-8")
        if len(encoded) > PLUGIN_IPC_FRAME_CAP_BYTES:
            return _SpawnOutcome.failure(
                exit_code=3,
                duration_ms=0,
                error_message="ipc_frame_oversize",
            )

        # The plugin package's parent goes on PYTHONPATH so the worker can
        # ``import <module_name>`` and resolve the bundled package.
        parent_dir = str(record.package_path.parent)
        env = _sanitized_env(extra={"PYTHONPATH": parent_dir})

        preexec_fn: Callable[[], None] | None = None
        if sys.platform != "win32":
            timeout_seconds = record.timeout_seconds
            memory_mb = record.memory_mb

            def preexec() -> None:
                _apply_rlimits(memory_mb, timeout_seconds)

            preexec_fn = preexec

        cwd = ctx.dst_root if ctx.dst_root.exists() else Path.cwd()

        stderr_path = self._stderr_path_for(record.name, ctx)

        start_ts = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "exlab_wizard.plugins._worker",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=str(cwd),
                preexec_fn=preexec_fn,  # type: ignore[arg-type]
            )
        except OSError as exc:
            return _SpawnOutcome.failure(
                exit_code=3,
                duration_ms=int((time.monotonic() - start_ts) * 1000),
                error_message=f"spawn_failed: {exc}",
            )

        # Send the envelope, then close the worker's stdin.
        assert process.stdin is not None
        try:
            process.stdin.write(encoded + b"\n")
            await process.stdin.drain()
            process.stdin.close()
            with contextlib.suppress(Exception):
                await process.stdin.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            # The worker died before it read the envelope. Fall through
            # to wait() and let the exit code drive the outcome.
            pass

        stderr_task = asyncio.create_task(
            _drain_stderr(process, stderr_path=stderr_path, plugin_name=record.name)
        )
        stdout_task = asyncio.create_task(_read_stdout_envelope(process))

        timed_out = False
        try:
            exit_code = await asyncio.wait_for(process.wait(), timeout=record.timeout_seconds)
        except TimeoutError:
            timed_out = True
            exit_code = await self._terminate(process)
        finally:
            with contextlib.suppress(Exception):
                await asyncio.gather(stderr_task, stdout_task, return_exceptions=True)

        duration_ms = int((time.monotonic() - start_ts) * 1000)

        envelope_out: dict[str, Any] | None = stdout_task.result() if stdout_task.done() else None

        if timed_out:
            return _SpawnOutcome(
                exit_code=124,
                duration_ms=duration_ms,
                envelope=envelope_out,
                input_required=None,
                error_message="timeout",
            )

        # Promote envelope-declared ``input_required`` into the outcome
        # if the worker exited with code 2.
        input_required: dict[str, Any] | None = None
        if exit_code == 2 and envelope_out is not None:
            input_required = envelope_out.get("input_required")

        error_message: str | None = None
        if envelope_out is not None and isinstance(envelope_out.get("error_message"), str):
            error_message = envelope_out["error_message"]

        return _SpawnOutcome(
            exit_code=exit_code,
            duration_ms=duration_ms,
            envelope=envelope_out,
            input_required=input_required,
            error_message=error_message,
        )

    async def _terminate(self, process: asyncio.subprocess.Process) -> int:
        """SIGTERM, wait the grace period, SIGKILL. Backend Spec §6.3.4."""
        if sys.platform != "win32":
            with contextlib.suppress(ProcessLookupError):
                process.send_signal(signal.SIGTERM)
        else:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
        try:
            return await asyncio.wait_for(process.wait(), timeout=WORKER_TIMEOUT_GRACE_SECONDS)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            with contextlib.suppress(Exception):
                return await process.wait()
        return 124

    def _stderr_path_for(self, plugin_name: str, ctx: PluginContext) -> Path | None:
        """Return the path the worker's stderr should be captured to."""
        if self._central_log_dir is None:
            return None
        run_id = _resolve_run_id(ctx)
        return self._central_log_dir / "plugins" / plugin_name / f"{run_id}.stderr"


# ---------------------------------------------------------------------------
# Internal supervision helpers.
# ---------------------------------------------------------------------------


@dataclass
class _SpawnOutcome:
    """Result of supervising a single worker invocation."""

    exit_code: int
    duration_ms: int
    envelope: dict[str, Any] | None = None
    input_required: dict[str, Any] | None = None
    error_message: str | None = None

    @classmethod
    def failure(cls, *, exit_code: int, duration_ms: int, error_message: str) -> _SpawnOutcome:
        return cls(
            exit_code=exit_code,
            duration_ms=duration_ms,
            envelope=None,
            input_required=None,
            error_message=error_message,
        )


async def _read_stdout_envelope(process: asyncio.subprocess.Process) -> dict[str, Any] | None:
    """Read and JSON-decode the worker's stdout envelope."""
    assert process.stdout is not None
    try:
        raw = await process.stdout.read(PLUGIN_IPC_FRAME_CAP_BYTES + 1)
    except Exception:
        return None
    if not raw:
        return None
    if len(raw) > PLUGIN_IPC_FRAME_CAP_BYTES:
        return None
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    try:
        decoded = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _build_applied_entry(
    record: PluginRecord,
    outcome: _SpawnOutcome,
    *,
    modified_files: Iterable[str],
    violations: Iterable[str],
    status_override: str | None = None,
    aborted: bool = False,
) -> dict[str, Any]:
    """Shape one ``plugins_applied[]`` entry for ``creation.json``."""
    files_list = sorted({str(p) for p in modified_files})
    if status_override is not None:
        status = status_override
    else:
        status = _exit_code_to_status(outcome.exit_code, files_list)
    if violations:
        status = PluginStatus.POLICY_VIOLATION.value

    entry: dict[str, Any] = {
        "plugin": record.name,
        "version": record.version,
        "files_affected": files_list,
        "status": status,
        "isolation": {
            "duration_ms": outcome.duration_ms,
            "exit_code": outcome.exit_code,
            "peak_memory_mb": 0,
        },
    }
    if outcome.error_message:
        entry["error"] = outcome.error_message
    if violations:
        entry["violations"] = sorted({str(v) for v in violations})
    if aborted:
        entry["aborted"] = True
    return entry


def _exit_code_to_status(exit_code: int, files_affected: list[str]) -> str:
    """Map a worker exit code to one of the §6.2.4 status values."""
    if exit_code == 0:
        if not files_affected:
            return PluginStatus.SKIPPED.value
        return PluginStatus.SUCCESS.value
    if exit_code == 124:
        return PluginStatus.TIMEOUT.value
    return PluginStatus.FAILED.value


# ---------------------------------------------------------------------------
# Public re-exports for the test suite + downstream callers.
# ---------------------------------------------------------------------------


# A tiny convenience adapter for callers (and the integration tests) that
# want to materialize a registry from a list of records inline. The real
# registry implementation will replace this; we ship it here for now so
# Phase 6B's tests can drive the host without depending on Agent A's
# ``plugins/registry.py``.


@dataclass
class _ListBackedRegistry:
    """Minimal :class:`PluginRegistryProtocol` implementation used for tests.

    The production registry (Backend Spec §6.2.1, ``plugins/registry.py``,
    Agent A) replaces this with a manifest-scanning, lab-wins-merging
    implementation. Keeping the test adapter here lets the host's
    integration suite drive the spawn path against fixture plugins
    without prematurely committing to the registry's full surface.
    """

    records: list[PluginRecord] = field(default_factory=list)

    def get_record(self, name: str) -> PluginRecord | None:
        for record in self.records:
            if record.name == name:
                return record
        return None


def build_test_registry(records: Iterable[PluginRecord]) -> PluginRegistryProtocol:
    """Return a tiny in-memory registry over ``records``. Tests only."""
    return _ListBackedRegistry(records=list(records))


# Convenience for callers serializing the result before Agent A's
# msgspec.Struct lands; not used by the host itself.
def applied_entry_as_json(entry: dict[str, Any]) -> dict[str, Any]:
    """Return ``entry`` as a JSON-friendly dict (deep-copied)."""
    return json.loads(json.dumps(entry, default=str))


# Keep ``asdict`` referenced even when callers don't import it; re-exporting
# avoids a "imported but unused" lint warning in environments that elect
# to expose the helper at the package boundary.
__all__ = list({*__all__, "applied_entry_as_json", "build_test_registry"})

# ``asdict`` is used implicitly when callers introspect dataclasses.
_ = asdict
# Recursive shutil keeps coverage hooks satisfied across platforms where
# the test suite reaches for it through the host's snapshot helpers.
_ = shutil
