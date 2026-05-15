"""Per-task structured-tag context vars for the logging system. Backend Spec §16.2.3.

The logger format string includes structured tags (``[host:..]``, ``[equip:..]``,
``[proj:..]``, ``[kind:..]``, ``[run:..]``) whose values are pulled from
``contextvars.ContextVar`` snapshots at log-emit time. ``contextvars`` are
async-safe: a ``set_run_context`` call inside one asyncio task does not bleed
into another concurrent task. This is what lets the orchestrator mode (§13)
run multiple equipment sessions concurrently and still emit cleanly tagged
log lines.

The tags are deliberately scoped to the creation lifecycle (host / equipment /
project / run-kind / run-id). Component tags (``[component:tray]``,
``[plugin:..]``) are emitted directly via ``logger.info("...", extra={...})``
calls in the relevant component code; they do not flow through this module.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar, Token

__all__ = [
    "clear_run_context",
    "equipment_id_var",
    "get_run_context",
    "host_var",
    "project_short_id_var",
    "run_id_var",
    "run_kind_var",
    "set_run_context",
]

# ---------------------------------------------------------------------------
# Context vars (one per structured tag)
# ---------------------------------------------------------------------------
#
# Each var defaults to None so absence-of-tag is the natural pre-context state.
# The formatter consults these via ``ContextVar.get()`` and renders the
# matching ``[tag:value]`` segment only when the value is not None.

host_var: ContextVar[str | None] = ContextVar("exlab_log_host", default=None)
equipment_id_var: ContextVar[str | None] = ContextVar("exlab_log_equipment_id", default=None)
project_short_id_var: ContextVar[str | None] = ContextVar(
    "exlab_log_project_short_id", default=None
)
run_kind_var: ContextVar[str | None] = ContextVar("exlab_log_run_kind", default=None)
run_id_var: ContextVar[str | None] = ContextVar("exlab_log_run_id", default=None)


# Tuple of (var, kwarg-name) pairs the helpers iterate over so a future tag
# only requires editing this single registry.
_VARS: tuple[tuple[ContextVar[str | None], str], ...] = (
    (host_var, "host"),
    (equipment_id_var, "equipment_id"),
    (project_short_id_var, "project_short_id"),
    (run_kind_var, "run_kind"),
    (run_id_var, "run_id"),
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def set_run_context(
    *,
    host: str | None = None,
    equipment_id: str | None = None,
    project_short_id: str | None = None,
    run_kind: str | None = None,
    run_id: str | None = None,
) -> Iterator[None]:
    """Push the supplied context vars on entry and reset them on exit.

    Vars whose argument is ``None`` (the default) are left unchanged. This
    lets a caller incrementally widen the context (e.g. set ``host`` and
    ``equipment_id`` in an outer ``with`` block, then add ``run_id`` in
    a nested inner block) without destroying the outer values.

    The context manager is implemented via :class:`contextvars.Token` so
    nested entries are restored to their *prior* value on exit (not to
    ``None``). Each token corresponds to exactly one ``ContextVar.set``
    call.

    The caller is responsible for ensuring values are non-empty strings;
    this helper does no validation. Passing an empty string ``""`` is
    treated as a real value (it suppresses the default-None suppression
    in the formatter), which is almost certainly a caller bug -- so callers
    should pass ``None`` explicitly when they mean "don't touch this var".
    """
    tokens: list[Token[str | None]] = []
    supplied: dict[str, str | None] = {
        "host": host,
        "equipment_id": equipment_id,
        "project_short_id": project_short_id,
        "run_kind": run_kind,
        "run_id": run_id,
    }
    try:
        for var, kwarg_name in _VARS:
            value = supplied[kwarg_name]
            if value is not None:
                tokens.append(var.set(value))
        yield
    finally:
        # Reset in reverse order so each Token is paired with its own .set call.
        for token in reversed(tokens):
            token.var.reset(token)


def get_run_context() -> dict[str, str | None]:
    """Return a snapshot of the active context as a plain ``dict``.

    Used by the formatter to render structured tags. Keys match the kwarg
    names of :func:`set_run_context`; values are ``None`` when the var is
    unset.
    """
    return {kwarg_name: var.get() for var, kwarg_name in _VARS}


def clear_run_context() -> None:
    """Reset every context var to ``None``.

    Provided for test fixtures that want to ensure a clean slate between
    cases. Production code should rely on :func:`set_run_context`'s exit
    semantics; calling ``clear_run_context`` mid-run would mask a nested
    ``with`` block's prior values, which is almost always wrong.
    """
    for var, _ in _VARS:
        var.set(None)
