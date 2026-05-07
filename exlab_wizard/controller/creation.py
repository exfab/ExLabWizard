"""Creation controller. Backend Spec §4.4.1, §4.7, §4.8.

Composes the validator, template engine, plugin host, cache writers,
README generator, and NAS sync client into the §4.7 state machine,
driving each session from :data:`SessionState.PENDING` through to
:data:`SessionState.DONE` (or :data:`SessionState.FAILED` /
:data:`SessionState.ABORTED` on failure / cancel).

Validation gate (UI-spec §2). Before transitioning out of
:data:`SessionState.VALIDATING` the controller enforces the mandatory
core-field set:

- ``label`` non-empty after trim, ≤ 100 chars.
- ``operator`` non-empty after trim; allowlisted when
  ``config.operators.allowlist`` is non-empty.
- ``objective`` non-empty after trim, ≤ 2000 chars.
- ``equipment_id`` is in ``config.equipment``.
- For runs, ``project_short_id`` matches
  :data:`PROJECT_SHORT_ID_PATTERN`.
- Template-required field ids and config-required field ids are all
  present in ``readme_extra``.

ReadmeGenerator and NASSyncClient are injected as Protocols so this
phase ships before Phase 8 / Phase 10 land their concrete
implementations. :class:`NoOpReadmeGenerator` writes a minimal
``README.md`` that the post-validate pass can scan;
:class:`NoOpNASSync` is a true no-op (the sync queue is built in
Phase 10).
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from exlab_wizard.api.schemas import (
    CreationJson,
    EquipmentJson,
    LimsProjectBlock,
    PathsBlock,
    PluginApplied,
    PluginIsolation,
    TemplateBlock,
)
from exlab_wizard.cache.creation_writer import CreationWriter
from exlab_wizard.cache.equipment import EquipmentCacheWriter
from exlab_wizard.cache.log_writer import append_log_line, format_log_line
from exlab_wizard.config.models import Config
from exlab_wizard.constants import (
    ANSWERS_FILE_NAME,
    CACHE_DIR_NAME,
    CREATION_JSON_NAME,
    CREATION_JSON_VERSION,
    EQUIPMENT_JSON_NAME,
    EQUIPMENT_JSON_VERSION,
    LABEL_MAX_LENGTH,
    LOG_FILE_TEMPLATE,
    OBJECTIVE_MAX_LENGTH,
    PROJECT_SHORT_ID_PATTERN,
    PluginStatus,
    RunKind,
    SyncStatus,
    TemplateType,
    Tier,
)
from exlab_wizard.controller.session_store import Session, SessionStore
from exlab_wizard.controller.state_machine import (
    Phase,
    SessionState,
    state_to_phase,
)
from exlab_wizard.errors import ValidationError
from exlab_wizard.logging import get_logger
from exlab_wizard.paths import canonicalize_equipment_id, compose_project_path, compose_run_path
from exlab_wizard.plugins.base import PluginContext
from exlab_wizard.plugins.host import InputRequiredPayload, PluginHost, PluginPassResult
from exlab_wizard.plugins.logger import HostPluginLogger
from exlab_wizard.template.copier_driver import (
    CORE_README_FIELD_IDS,
    RenderResult,
    ResolvedTemplate,
    TemplateEngine,
)
from exlab_wizard.validator.engine import CreationValidationInput, Validator
from exlab_wizard.validator.findings import Finding

__all__ = [
    "CreationController",
    "NASSyncProtocol",
    "NoOpNASSync",
    "NoOpReadmeGenerator",
    "ProjectCreateRequest",
    "ReadmeContext",
    "ReadmeGeneratorProtocol",
    "RunCreateRequest",
    "SessionHandle",
]


_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectCreateRequest:
    """Inputs for :meth:`CreationController.create_project`.

    Backend Spec §4.6.1 / UI-spec §3.1. ``lims_project`` mirrors the
    §11.3 ``lims_project`` block (``uid``, ``short_id``, ``name_at_creation``,
    ``source``).
    """

    equipment_id: str
    template_path: Path
    lims_project: dict[str, Any]
    variables: dict[str, Any]
    label: str
    operator: str
    objective: str
    readme_extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunCreateRequest:
    """Inputs for :meth:`CreationController.create_run`.

    Backend Spec §4.6.1 / UI-spec §3.2 / §3.3. ``run_kind`` is the core
    mode flag and is immutable mid-session per UI-spec §3.3.
    """

    equipment_id: str
    project_short_id: str
    template_path: Path
    run_kind: RunKind
    variables: dict[str, Any]
    label: str
    operator: str
    objective: str
    readme_extra: dict[str, Any] = field(default_factory=dict)
    run_date: datetime | None = None
    lims_project: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionHandle:
    """Snapshot of session state. Backend Spec §4.4.1."""

    session_id: str
    state: SessionState
    current_phase: Phase | None
    next_action: str


# ---------------------------------------------------------------------------
# Protocols + No-op implementations (Phase 8 / Phase 10 will replace these)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReadmeContext:
    """Inputs handed to the README generator. Phase 8 owns the canonical type;
    this lightweight stand-in lets Phase 7 ship before Phase 8 lands."""

    label: str
    operator: str
    objective: str
    equipment_id: str
    project_short_id: str
    run_kind: str
    variables: dict[str, Any]
    template: ResolvedTemplate
    extra_fields: dict[str, Any] = field(default_factory=dict)


class ReadmeGeneratorProtocol(Protocol):
    """The README generator surface the controller depends on. Phase 8."""

    async def generate(self, dst: Path, ctx: ReadmeContext) -> Path: ...


class NoOpReadmeGenerator:
    """Minimal README generator used until Phase 8 lands the real one.

    Writes a tiny ``README.md`` containing only the core fields so the
    post-validate pass has something to scan.
    """

    async def generate(self, dst: Path, ctx: ReadmeContext) -> Path:
        readme = dst / "README.md"
        body = f"# {ctx.label}\n\nOperator: {ctx.operator}\n\n{ctx.objective}\n"
        readme.write_text(body, encoding="utf-8")
        return readme


class NASSyncProtocol(Protocol):
    """The NAS sync surface the controller depends on. Phase 10."""

    async def enqueue(self, run_path: Path) -> None: ...


class NoOpNASSync:
    """No-op stand-in for :class:`NASSyncClient` until Phase 10 lands."""

    async def enqueue(self, run_path: Path) -> None:
        return None


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------


class CreationController:
    """Drives the §4.7 state machine end-to-end.

    Composes the validator (Phase 4), template engine (Phase 5), plugin
    host (Phase 6), cache writers (Phase 3), README generator (Phase 8 --
    NoOp until then), and NAS sync client (Phase 10 -- NoOp until then).
    """

    def __init__(
        self,
        *,
        config: Config,
        validator: Validator,
        template_engine: TemplateEngine,
        plugin_host: PluginHost | None,
        cache_creation: CreationWriter,
        cache_equipment: EquipmentCacheWriter,
        cache_log: Any | None = None,  # log writer interface; kept loose for v1
        readme_generator: ReadmeGeneratorProtocol | None = None,
        nas_sync: NASSyncProtocol | None = None,
        session_store: SessionStore | None = None,
    ) -> None:
        self._config = config
        self._validator = validator
        self._template_engine = template_engine
        self._plugin_host = plugin_host
        self._cache_creation = cache_creation
        self._cache_equipment = cache_equipment
        self._cache_log = cache_log
        self._readme_generator: ReadmeGeneratorProtocol = (
            readme_generator if readme_generator is not None else NoOpReadmeGenerator()
        )
        self._nas_sync: NASSyncProtocol = nas_sync if nas_sync is not None else NoOpNASSync()
        self._sessions: SessionStore = (
            session_store if session_store is not None else SessionStore()
        )
        # Per-session resume queues: a controller-internal asyncio.Queue
        # the create_* loop awaits while ``INPUT_REQUIRED`` is held. The
        # ``resume`` method puts a payload on the queue (or ``None`` to
        # cancel) and the create_* loop wakes up and continues.
        self._resume_queues: dict[str, asyncio.Queue[dict[str, Any] | None]] = {}
        # Per-session asyncio.Tasks: the running pipeline coroutine for
        # each session. Used by ``cancel`` to interrupt the pipeline.
        self._tasks: dict[str, asyncio.Task[None]] = {}

    @property
    def session_store(self) -> SessionStore:
        """Expose the in-memory session store for the API surface."""
        return self._sessions

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_project(self, req: ProjectCreateRequest) -> SessionHandle:
        """Open a project-creation session and start the pipeline.

        The session is registered with the store immediately and the
        pipeline runs as a background asyncio task; the returned
        :class:`SessionHandle` reflects the post-VALIDATING state.
        Failures from the validation gate transition the session to
        ``FAILED`` synchronously before returning -- so the caller can
        detect them on the very first response.
        """
        session = self._sessions.open("project", req)
        return await self._launch(session)

    async def create_run(self, req: RunCreateRequest) -> SessionHandle:
        """Open a run-creation session and start the pipeline."""
        session = self._sessions.open("run", req)
        return await self._launch(session)

    async def resume(self, session_id: str, extra_inputs: dict[str, Any]) -> SessionHandle:
        """Supply ``extra_inputs`` after a ``PluginInputRequired`` prompt.

        Pushes the payload onto the session's resume queue; the
        suspended pipeline wakes, re-spawns the trigger plugin's worker
        with the new inputs, and continues. Backend Spec §4.7 / §6.4.
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"unknown session_id {session_id!r}")
        if session.state is not SessionState.INPUT_REQUIRED:
            raise ValueError(
                f"session {session_id} is in state {session.state.value!r}; "
                "resume requires INPUT_REQUIRED"
            )
        queue = self._resume_queues.get(session_id)
        if queue is None:
            raise ValueError(f"session {session_id} has no resume queue")
        await queue.put(dict(extra_inputs))
        # Heartbeat refreshed so the GC will not close the session
        # before the resume picks up.
        self._sessions.heartbeat(session_id)
        return self._handle(session)

    async def cancel(self, session_id: str, *, discard_files: bool = False) -> None:
        """Abort an in-flight session.

        Pushes a ``None`` onto the resume queue (so an
        ``INPUT_REQUIRED`` session wakes immediately), cancels the
        pipeline task, and runs the cleanup hook. ``discard_files``
        deletes the partially-created directory; otherwise the directory
        is left in place as an orphan (Backend Spec §4.7 / §4.8).
        """
        session = self._sessions.get(session_id)
        if session is None:
            return
        if session.is_terminal():
            return
        # Wake any INPUT_REQUIRED waiter so it sees the cancel.
        queue = self._resume_queues.get(session_id)
        if queue is not None:
            with contextlib.suppress(Exception):
                queue.put_nowait(None)
        # Mark the operator's intent on the session so the pipeline
        # knows to clean up rather than continuing.
        session.error = {
            "code": "cancelled",
            "discard_files": discard_files,
        }
        # Cancel the pipeline task; the wrapper handles the cleanup.
        task = self._tasks.get(session_id)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        # Defensive: if the session somehow did not transition to a
        # terminal state via the pipeline (e.g. cancelled before the
        # task started), do it here.
        if not session.is_terminal():
            with contextlib.suppress(ValueError):
                self._sessions.transition(session_id, SessionState.ABORTED)
            self._sessions.close(
                session_id,
                {"code": "cancelled", "discard_files": discard_files},
            )
            await self._cleanup(session, discard_files=discard_files)

    async def status(self, session_id: str) -> SessionHandle:
        """Return a snapshot :class:`SessionHandle` for ``session_id``."""
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"unknown session_id {session_id!r}")
        return self._handle(session)

    async def subscribe(self, session_id: str) -> AsyncIterator[dict[str, Any]]:
        """Yield WebSocket-event dicts for the named session.

        Wraps the session's ``event_queue``. The iterator terminates
        when the session reaches a terminal state and the queue drains.
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise ValueError(f"unknown session_id {session_id!r}")
        if session.event_queue is None:
            session.event_queue = asyncio.Queue()
        queue = session.event_queue
        while True:
            event = await queue.get()
            yield event
            if event.get("kind") in ("done", "failed") or (
                event.get("kind") == "phase" and event.get("phase") == Phase.DONE.value
            ):
                break

    # ------------------------------------------------------------------
    # Pipeline launcher
    # ------------------------------------------------------------------

    async def _launch(self, session: Session) -> SessionHandle:
        """Validate the request, then spawn the pipeline as a background task.

        The validation gate runs synchronously so the client gets an
        immediate ``FAILED`` response on bad inputs. On success the
        controller transitions to ``RENDERING`` and the rest of the
        pipeline runs in a background task that publishes events on
        the session's event queue.
        """
        # Ensure the event queue exists before any frame is pushed.
        if session.event_queue is None:
            session.event_queue = asyncio.Queue()
        # Resume queue is created up-front so cancel before the
        # pipeline reaches PLUGIN_PASS still wakes any waiter.
        self._resume_queues[session.session_id] = asyncio.Queue()

        # PENDING -> VALIDATING (always).
        await self._transition(session, SessionState.VALIDATING)
        try:
            self._validate_inputs(session)
        except ValidationError as exc:
            await self._fail(session, error=self._format_error(exc))
            return self._handle(session)
        except Exception as exc:  # pragma: no cover -- defensive
            await self._fail(session, error={"code": "internal_error", "message": str(exc)})
            return self._handle(session)

        # Validation passed; transition to RENDERING and kick off the
        # pipeline as a background task.
        await self._transition(session, SessionState.RENDERING)
        task = asyncio.create_task(self._run_pipeline(session))
        self._tasks[session.session_id] = task
        return self._handle(session)

    # ------------------------------------------------------------------
    # Validation gate (UI-spec §2; §5 validation order)
    # ------------------------------------------------------------------

    def _validate_inputs(self, session: Session) -> None:
        """Run the §2 mandatory-core-field gate.

        Raises :class:`ValidationError` on the first failing field. The
        controller catches the exception and transitions to ``FAILED``.
        """
        req = session.request

        # Equipment must be configured.
        equipment_ids = {entry.id for entry in self._config.equipment}
        if req.equipment_id not in equipment_ids:
            raise ValidationError(
                {
                    "code": "validation_failed",
                    "message": f"equipment_id {req.equipment_id!r} is not in the configured equipment list",
                    "field": "equipment_id",
                }
            )
        # The id format check below also validates equipment id shape;
        # raises ConfigError which we convert to a uniform ValidationError.
        try:
            canonicalize_equipment_id(req.equipment_id)
        except Exception as exc:
            raise ValidationError(
                {
                    "code": "equipment_id_invalid",
                    "message": str(exc),
                    "field": "equipment_id",
                }
            ) from exc

        # Mandatory core fields.
        label = (req.label or "").strip()
        if not label:
            raise ValidationError(
                {
                    "code": "validation_failed",
                    "message": "label must not be empty",
                    "field": "label",
                }
            )
        if len(label) > LABEL_MAX_LENGTH:
            raise ValidationError(
                {
                    "code": "field_too_long",
                    "message": f"label length {len(label)} exceeds max {LABEL_MAX_LENGTH}",
                    "field": "label",
                    "details": {"max_length": LABEL_MAX_LENGTH},
                }
            )

        operator = (req.operator or "").strip()
        if not operator:
            raise ValidationError(
                {
                    "code": "validation_failed",
                    "message": "operator must not be empty",
                    "field": "operator",
                }
            )
        allowlist = list(self._config.operators.allowlist)
        if allowlist and operator not in allowlist:
            raise ValidationError(
                {
                    "code": "validation_failed",
                    "message": (f"operator {operator!r} is not in the configured allowlist"),
                    "field": "operator",
                    "details": {"allowed": list(allowlist)},
                }
            )

        objective = (req.objective or "").strip()
        if not objective:
            raise ValidationError(
                {
                    "code": "validation_failed",
                    "message": "objective must not be empty",
                    "field": "objective",
                }
            )
        if len(objective) > OBJECTIVE_MAX_LENGTH:
            raise ValidationError(
                {
                    "code": "field_too_long",
                    "message": f"objective length {len(objective)} exceeds max {OBJECTIVE_MAX_LENGTH}",
                    "field": "objective",
                    "details": {"max_length": OBJECTIVE_MAX_LENGTH},
                }
            )

        # Run-specific gates.
        if isinstance(req, RunCreateRequest) and not PROJECT_SHORT_ID_PATTERN.fullmatch(
            req.project_short_id
        ):
            raise ValidationError(
                {
                    "code": "validation_failed",
                    "message": (
                        f"project_short_id {req.project_short_id!r} does not match "
                        f"pattern {PROJECT_SHORT_ID_PATTERN.pattern}"
                    ),
                    "field": "project_short_id",
                }
            )

        # Resolve the template so we can read its required-field ids
        # and store the resolved object on the session for downstream
        # reuse.
        resolved = self._resolve_template(req)
        session.request = _attach_resolved(req, resolved)

        # Template- and config-required README fields must all be
        # present in ``readme_extra``.
        template_required = _required_field_ids(resolved.extra_readme_fields)
        config_required = tuple(
            entry.id
            for entry in self._config.readme.defaults
            if entry.required
            # Core fields cannot be re-required by config; they're already
            # mandatory above.
            and entry.id not in CORE_README_FIELD_IDS
        )
        all_required = (*template_required, *config_required)
        for fid in all_required:
            if fid in req.readme_extra and (
                req.readme_extra[fid] is not None and str(req.readme_extra[fid]).strip()
            ):
                continue
            raise ValidationError(
                {
                    "code": "validation_failed",
                    "message": f"required README field {fid!r} is missing or empty",
                    "field": fid,
                }
            )

    # ------------------------------------------------------------------
    # Pipeline body
    # ------------------------------------------------------------------

    async def _run_pipeline(self, session: Session) -> None:
        """Drive the session from RENDERING to DONE / FAILED / ABORTED."""
        try:
            await self._pipeline_states(session)
        except asyncio.CancelledError:
            # Cancel via :meth:`cancel`. The session.error envelope was
            # populated by the cancel call; we just need to transition
            # and run cleanup.
            error = session.error or {"code": "cancelled"}
            discard_files = bool(error.get("discard_files", False))
            with contextlib.suppress(ValueError):
                self._sessions.transition(session.session_id, SessionState.ABORTED)
            self._sessions.close(session.session_id, error)
            await self._cleanup(session, discard_files=discard_files)
            await self._publish(session, {"kind": "failed", "error": error})
            raise
        except Exception as exc:
            error = self._format_error(exc)
            await self._fail(session, error=error)

    async def _pipeline_states(self, session: Session) -> None:
        """Run the RENDERING → DONE pipeline once."""
        req = session.request
        resolved: ResolvedTemplate = req._resolved_template  # type: ignore[attr-defined]
        dst = self._compose_destination_path(req)

        # RENDERING.
        render_result = await self._render(resolved, dst, req)

        # PLUGIN_PASS.
        await self._transition(session, SessionState.PLUGIN_PASS)
        plugin_result = await self._plugin_pass(session, resolved, render_result, req)
        if plugin_result.aborted:
            # The host returned ``aborted=True`` because operator chose to
            # cancel the input-required prompt. Treat as a session-level cancel.
            await self._fail(
                session,
                error={"code": "cancelled", "message": "operator cancelled input"},
            )
            return

        # CACHE_WRITE.
        await self._transition(session, SessionState.CACHE_WRITE)
        creation_payload = await self._write_cache(
            session=session,
            req=req,
            resolved=resolved,
            dst=dst,
            render_result=render_result,
            plugin_result=plugin_result,
        )

        # POST_VALIDATE.
        await self._transition(session, SessionState.POST_VALIDATE)
        findings = self._post_validate(req, dst, render_result)
        post_pass = not _has_hard_finding(findings)

        # SYNC_QUEUED -> NAS enqueue if post_validate passed.
        await self._transition(session, SessionState.SYNC_QUEUED)
        if post_pass:
            with contextlib.suppress(Exception):
                await self._nas_sync.enqueue(dst)
        else:
            # Mutate the on-disk creation.json to reflect the gated state.
            cache_path = dst / CACHE_DIR_NAME / CREATION_JSON_NAME

            def _gate(payload: CreationJson) -> CreationJson:
                payload.sync_status = SyncStatus.BLOCKED_BY_VALIDATION.value
                return payload

            await self._cache_creation.update_creation_atomic(cache_path, _gate)
            creation_payload.sync_status = SyncStatus.BLOCKED_BY_VALIDATION.value
            await self._publish(
                session,
                {
                    "kind": "warning",
                    "phase": Phase.QUEUEING_NAS_SYNC.value,
                    "message": "post-validate found hard findings; sync gated",
                },
            )

        # DONE.
        await self._transition(session, SessionState.DONE)
        result = {
            "path": str(dst),
            "sync_status": creation_payload.sync_status,
            "blocked": not post_pass,
            "findings": [f.to_dict() for f in findings],
        }
        self._sessions.close(session.session_id, result)
        await self._publish(session, {"kind": "done", "result": result})
        # Best-effort log line for §11.5.
        with contextlib.suppress(Exception):
            self._append_log(session, dst, "creation completed")

    # ------------------------------------------------------------------
    # Sub-steps
    # ------------------------------------------------------------------

    def _resolve_template(self, req: ProjectCreateRequest | RunCreateRequest) -> ResolvedTemplate:
        scope = TemplateType.PROJECT if isinstance(req, ProjectCreateRequest) else TemplateType.RUN
        return self._template_engine.resolve(req.template_path, scope)

    @staticmethod
    def _short_id_for(req: ProjectCreateRequest | RunCreateRequest) -> str:
        """Return the LIMS project ``short_id`` for the request."""
        if isinstance(req, RunCreateRequest):
            return req.project_short_id
        return str(req.lims_project.get("short_id", ""))

    @staticmethod
    def _run_kind_value_for(req: ProjectCreateRequest | RunCreateRequest) -> str:
        """Return the ``run_kind`` value to record on ``creation.json``.

        Project-level creations default to ``experimental`` per the
        v1.7 history table (see §11.3); run requests carry the bound
        ``run_kind`` directly.
        """
        if isinstance(req, RunCreateRequest):
            return req.run_kind.value
        return RunKind.EXPERIMENTAL.value

    def _compose_destination_path(self, req: ProjectCreateRequest | RunCreateRequest) -> Path:
        local_root = Path(self._config.paths.local_root)
        if isinstance(req, ProjectCreateRequest):
            return compose_project_path(
                local_root=local_root,
                equipment_id=req.equipment_id,
                project_short_id=self._short_id_for(req),
            )
        run_date = req.run_date or datetime.now(tz=UTC)
        return compose_run_path(
            local_root=local_root,
            equipment_id=req.equipment_id,
            project_short_id=req.project_short_id,
            run_kind=req.run_kind,
            run_date=run_date,
        )

    async def _render(
        self,
        resolved: ResolvedTemplate,
        dst: Path,
        req: ProjectCreateRequest | RunCreateRequest,
    ) -> RenderResult:
        """Render the resolved template into ``dst``.

        Copier receives the request's ``variables`` map plus a small set
        of core defaults (label, operator, objective, project_short_id,
        run_kind) so templates that reference them via Jinja resolve.
        Existing keys in ``variables`` are not overwritten -- the
        operator's explicit values win.
        """
        variables = dict(req.variables)
        variables.setdefault("label", req.label)
        variables.setdefault("operator", req.operator)
        variables.setdefault("objective", req.objective)
        variables.setdefault("project_short_id", self._short_id_for(req))
        if isinstance(req, RunCreateRequest):
            variables.setdefault("run_kind", req.run_kind.value)
        return await self._template_engine.render(resolved, dst, variables)

    async def _plugin_pass(
        self,
        session: Session,
        resolved: ResolvedTemplate,
        render: RenderResult,
        req: ProjectCreateRequest | RunCreateRequest,
    ) -> PluginPassResult:
        if self._plugin_host is None or not resolved.plugin_order:
            # No plugins configured for this template -- skip the pass.
            return PluginPassResult()

        # Build the per-session context for the plugin host. Project
        # creations carry no run_kind; the literal string ``"project"``
        # is used so plugins inspecting ``ctx.run_kind`` can detect the
        # level without misinterpreting it as ``"experimental"``.
        run_kind = req.run_kind.value if isinstance(req, RunCreateRequest) else "project"
        project_short_id = self._short_id_for(req)
        ctx = PluginContext(
            variables=dict(req.variables),
            dst_root=render.dst_path,
            answers_file=render.dst_path / ANSWERS_FILE_NAME,
            template_name=resolved.name,
            template_version=resolved.exlab_version,
            run_kind=run_kind,
            equipment_id=req.equipment_id,
            project=project_short_id,
            dry_run=False,
            log=HostPluginLogger(name="exlab_wizard.plugins"),
        )

        async def on_input_required(payload: InputRequiredPayload) -> dict[str, Any] | None:
            await self._transition(session, SessionState.INPUT_REQUIRED)
            session.pending_input = {
                "plugin": payload.plugin,
                "fields": list(payload.fields),
                "reason": payload.reason,
            }
            await self._publish(
                session,
                {
                    "kind": "input_required",
                    "fields": list(payload.fields),
                    "reason": payload.reason,
                    "plugin": payload.plugin,
                },
            )
            queue = self._resume_queues[session.session_id]
            response = await queue.get()
            session.pending_input = None
            if response is not None:
                # Resume re-enters PLUGIN_PASS for the trigger plugin only.
                await self._transition(session, SessionState.PLUGIN_PASS)
            return response

        return await self._plugin_host.run_pass(
            ctx,
            file_paths=list(render.files_written),
            plugin_order=list(resolved.plugin_order),
            on_input_required=on_input_required,
        )

    async def _write_cache(
        self,
        *,
        session: Session,
        req: ProjectCreateRequest | RunCreateRequest,
        resolved: ResolvedTemplate,
        dst: Path,
        render_result: RenderResult,
        plugin_result: PluginPassResult,
    ) -> CreationJson:
        """Write README + creation.json into the destination tree."""
        # Render README via the (Phase 8) generator.
        readme_ctx = ReadmeContext(
            label=req.label,
            operator=req.operator,
            objective=req.objective,
            equipment_id=req.equipment_id,
            project_short_id=self._short_id_for(req),
            run_kind=(req.run_kind.value if isinstance(req, RunCreateRequest) else "project"),
            variables=dict(req.variables),
            template=resolved,
            extra_fields=dict(req.readme_extra),
        )
        await self._readme_generator.generate(dst, readme_ctx)

        # Build the CreationJson payload.
        cache_dir = dst / CACHE_DIR_NAME
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / CREATION_JSON_NAME

        lims_block_dict = req.lims_project if req.lims_project else {}
        if not lims_block_dict:
            # Run requests may not carry a lims_project block in v1; we
            # still write a stub block so the schema's required fields
            # are present.
            lims_block_dict = {
                "uid": "",
                "short_id": self._short_id_for(req),
                "name_at_creation": req.label,
                "source": "live",
            }
        lims_block = LimsProjectBlock(
            uid=str(lims_block_dict.get("uid", "")),
            short_id=str(lims_block_dict.get("short_id", "")),
            name_at_creation=str(lims_block_dict.get("name_at_creation", req.label)),
            source=str(lims_block_dict.get("source", "live")),
            cache_freshness_at_use=lims_block_dict.get("cache_freshness_at_use"),
        )

        run_kind_value = self._run_kind_value_for(req)
        level_value = "run" if isinstance(req, RunCreateRequest) else "project"

        nas_root = ""
        for entry in self._config.equipment:
            if entry.id == req.equipment_id:
                nas_root = entry.nas_root
                break

        plugins_applied = [
            PluginApplied(
                plugin=entry["plugin"],
                version=entry["version"],
                files_affected=list(entry.get("files_affected", [])),
                status=entry.get("status", PluginStatus.SUCCESS.value),
                isolation=PluginIsolation(
                    duration_ms=int(entry.get("isolation", {}).get("duration_ms", 0)),
                    exit_code=int(entry.get("isolation", {}).get("exit_code", 0)),
                    peak_memory_mb=int(entry.get("isolation", {}).get("peak_memory_mb", 0)),
                )
                if "isolation" in entry
                else None,
            )
            for entry in plugin_result.applied
        ]

        payload = CreationJson(
            schema_version=CREATION_JSON_VERSION,
            created_at=datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            created_by=req.operator,
            level=level_value,
            run_kind=run_kind_value,
            lims_project=lims_block,
            template=TemplateBlock(
                name=resolved.name,
                version=resolved.exlab_version,
                source_path=str(resolved.path),
                run_scope=resolved.run_scope or "",
            ),
            variables=dict(req.variables),
            paths=PathsBlock(
                local=str(dst),
                nas=str(Path(nas_root) / req.equipment_id) if nas_root else "",
            ),
            plugins_applied=plugins_applied,
            sync_status=SyncStatus.PENDING.value,
        )
        await self._cache_creation.write_creation(cache_path, payload)

        # Write or refresh ``equipment.json`` at the equipment root.
        # The writer is idempotent on ``first_seen_at`` and only
        # refreshes ``last_modified_at`` on subsequent writes (§11.4.1).
        await self._write_equipment_json(req, nas_root)

        return payload

    async def _write_equipment_json(
        self,
        req: ProjectCreateRequest | RunCreateRequest,
        nas_root: str,
    ) -> None:
        """Write the per-equipment ``equipment.json`` registry record.

        Backend Spec §11.4.1. Idempotent: the writer preserves the
        original ``first_seen_at`` on subsequent rewrites and updates
        only ``last_modified_at``.
        """
        equipment_label = req.equipment_id
        for entry in self._config.equipment:
            if entry.id == req.equipment_id:
                equipment_label = entry.label or entry.id
                break

        local_root = Path(self._config.paths.local_root)
        equipment_dir = local_root / req.equipment_id
        equipment_cache_path = equipment_dir / CACHE_DIR_NAME / EQUIPMENT_JSON_NAME

        # The ``first_seen_at`` / ``last_modified_at`` fields are
        # stamped by the writer; the values supplied here are
        # placeholders that the writer overwrites.
        now_iso = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        equipment_payload = EquipmentJson(
            schema_version=EQUIPMENT_JSON_VERSION,
            id=req.equipment_id,
            label=equipment_label,
            configured_local_root=str(local_root),
            configured_nas_root=nas_root,
            first_seen_at=now_iso,
            last_modified_at=now_iso,
        )
        await self._cache_equipment.write_equipment(equipment_cache_path, equipment_payload)

    def _post_validate(
        self,
        req: ProjectCreateRequest | RunCreateRequest,
        dst: Path,
        render_result: RenderResult,
    ) -> list[Finding]:
        """Run :meth:`Validator.validate_creation` against the rendered tree.

        Catches plugin-introduced findings (e.g. a renamer that
        accidentally produced a ``<placeholder>`` file name).
        """
        # Walk the rendered tree to collect names + content for the
        # validator. The post-validate pass is driven by the same
        # creation-time engine so we re-use its rule set.
        file_names: list[str] = []
        file_contents: dict[str, str] = {}
        for path in dst.rglob("*"):
            if not path.is_file():
                continue
            file_names.append(path.name)
            # Only scan text-extension files; the validator config caps
            # the size we read.
            if path.suffix.lower() in self._validator.config.content_scan_extensions:
                size_cap = self._validator.config.content_scan_max_mib * 1024 * 1024
                with contextlib.suppress(OSError, UnicodeDecodeError):
                    with path.open("rb") as fh:
                        head = fh.read(size_cap)
                    text = head.decode("utf-8", errors="replace")
                    file_contents[path.name] = text

        # For project-level creation we deliberately pass an empty
        # ``run_kind`` so the §8.1.3 mode-prefix-mismatch rule
        # short-circuits -- that rule is a run-level invariant
        # (Backend Spec §8.1.3) and a project leaf like ``PROJ-0042`` is
        # not a ``Run_*`` / ``TestRun_*`` candidate.
        params = CreationValidationInput(
            proposed_path=str(dst),
            variables=dict(req.variables),
            file_names=tuple(file_names),
            file_contents=dict(file_contents),
            run_kind=self._run_kind_value_for(req) if isinstance(req, RunCreateRequest) else "",
        )
        return self._validator.validate_creation(params)

    # ------------------------------------------------------------------
    # State / event helpers
    # ------------------------------------------------------------------

    async def _transition(self, session: Session, new_state: SessionState) -> None:
        self._sessions.transition(session.session_id, new_state)
        phase = state_to_phase(new_state)
        if phase is None:
            return
        if new_state is SessionState.INPUT_REQUIRED:
            # Pushed by the input_required envelope; no separate phase frame.
            return
        if new_state is SessionState.DONE:
            # The DONE event is emitted as ``kind: "done"`` with the
            # result envelope, not a phase frame.
            return
        await self._publish(
            session,
            {
                "kind": "phase",
                "phase": phase.value,
                "at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )

    async def _publish(self, session: Session, frame: dict[str, Any]) -> None:
        if session.event_queue is None:
            session.event_queue = asyncio.Queue()
        await session.event_queue.put(frame)

    async def _fail(self, session: Session, *, error: dict[str, Any]) -> None:
        with contextlib.suppress(ValueError):
            self._sessions.transition(session.session_id, SessionState.FAILED)
        self._sessions.close(session.session_id, error)
        await self._publish(session, {"kind": "failed", "error": error})
        # Best-effort cleanup of any partial directory; FAILED defaults
        # to leaving the partial directory (operator decides via
        # Problems tab) per §4.8.
        await self._cleanup(session, discard_files=False)

    async def _cleanup(self, session: Session, *, discard_files: bool) -> None:
        """Remove or leave the partial directory per the spec."""
        if not discard_files:
            return
        req = session.request
        # Compose the destination path lazily; on a validation-failure
        # cancel before render the path may not exist yet.
        try:
            dst = self._compose_destination_path(req)
        except Exception:
            return
        if dst.exists():
            with contextlib.suppress(OSError):
                shutil.rmtree(dst)

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def _handle(self, session: Session) -> SessionHandle:
        return SessionHandle(
            session_id=session.session_id,
            state=session.state,
            current_phase=session.current_phase,
            next_action=session.next_action,
        )

    @staticmethod
    def _format_error(exc: BaseException) -> dict[str, Any]:
        if isinstance(exc, ValidationError) and exc.args and isinstance(exc.args[0], dict):
            return dict(exc.args[0])
        return {"code": "internal_error", "message": str(exc)}

    def _append_log(self, session: Session, dst: Path, message: str) -> None:
        """Best-effort append to the equipment-level log. Backend Spec §11.5.

        Computes the equipment directory from the configured local root
        and the request's equipment id (rather than walking up from
        ``dst``) so the path is identical for project- and run-level
        creations.
        """
        equipment_dir = Path(self._config.paths.local_root) / session.request.equipment_id
        log_name = LOG_FILE_TEMPLATE.format(hostname="local")
        log_path = equipment_dir / CACHE_DIR_NAME / log_name
        line = format_log_line(
            timestamp_utc=datetime.now(tz=UTC),
            level="INFO",
            message=message,
            equipment_id=session.request.equipment_id,
        )
        append_log_line(log_path, line)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _required_field_ids(extra_fields: list[dict[str, Any]]) -> tuple[str, ...]:
    """Return the ``id`` of every entry in ``extra_fields`` with ``required: true``."""
    out: list[str] = []
    for entry in extra_fields:
        if not isinstance(entry, dict):
            continue
        if entry.get("required") is True:
            fid = entry.get("id")
            if isinstance(fid, str) and fid:
                out.append(fid)
    return tuple(out)


def _has_hard_finding(findings: list[Finding]) -> bool:
    return any(f.tier == Tier.HARD.value for f in findings)


def _attach_resolved(
    req: ProjectCreateRequest | RunCreateRequest, resolved: ResolvedTemplate
) -> ProjectCreateRequest | RunCreateRequest:
    """Stash the resolved template on the request so the pipeline can re-use it.

    The frozen dataclass blocks attribute mutation; we set the attribute
    on a private alias by going through ``object.__setattr__``. The
    pipeline reads it back via the same name.
    """
    object.__setattr__(req, "_resolved_template", resolved)
    return req
