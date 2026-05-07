"""WebSocket frame envelope types. Backend Spec §4.6.2.

These ``msgspec.Struct`` types are the typed shape of every frame
emitted on the two WebSocket channels:

* ``WS /api/v1/sessions/{id}/events`` -- per-session pipeline events
  (``PhaseEvent``, ``ProgressEvent``, ``InputRequiredEvent``,
  ``WarningEvent``, ``DoneEvent``, ``FailedEvent``).
* ``WS /api/v1/problems/events`` -- audit pub-sub channel
  (``SnapshotEvent``, ``DeltaEvent``).

Each Struct uses a string tag that drops onto the wire as the ``kind``
discriminator (§4.6.2 example payloads). Encode via
``msgspec.json.encode`` to keep the same hot-path serializer the cache
files use (§4.4.5 rationale).

The structs are intentionally permissive: ``fields``/``added``/etc.
carry ``list[dict]`` rather than typed sub-structs because the field
specs and finding shapes are defined elsewhere (PluginInputRequired
fields are spec'd by the plugin; findings by §11.8). We round-trip the
dict shape so the outbound frame is the literal example payload.
"""

from __future__ import annotations

from typing import Any

import msgspec
from msgspec import Struct
from msgspec import json as msgspec_json

__all__ = [
    "DeltaEvent",
    "DoneEvent",
    "FailedEvent",
    "InputRequiredEvent",
    "PhaseEvent",
    "ProgressEvent",
    "SessionEvent",
    "SnapshotEvent",
    "WarningEvent",
    "encode_event",
]


class PhaseEvent(
    Struct,
    kw_only=True,
    tag_field="kind",
    tag="phase",
):
    """Per-state phase frame. Backend Spec §4.6.2.

    Emitted on every state transition that has a corresponding phase
    enum value (see §4.7.1 mapping table). ``at`` is a UTC ISO-8601
    timestamp stamped at transition time.
    """

    phase: str
    at: str


class ProgressEvent(
    Struct,
    kw_only=True,
    tag_field="kind",
    tag="progress",
):
    """Progress frame for the long-running plugin pass. Backend Spec §4.6.2."""

    phase: str
    current: int
    total: int


class InputRequiredEvent(
    Struct,
    kw_only=True,
    tag_field="kind",
    tag="input_required",
):
    """Plugin escalation frame. Backend Spec §4.6.2 / §6.4.

    ``fields`` is the plugin-supplied list of field-spec dicts
    (id/label/type/required/default/options/hint) that the wizard's
    escalation dialog renders. ``reason`` is the plugin's prompt
    string. ``plugin`` is the plugin name so the UI can attribute the
    prompt.
    """

    fields: list[dict[str, Any]]
    reason: str
    plugin: str


class WarningEvent(
    Struct,
    kw_only=True,
    tag_field="kind",
    tag="warning",
):
    """Non-fatal warning frame. Backend Spec §4.6.2.

    Emitted by the controller when a phase produces a warning that
    should be surfaced in the UI without aborting the session
    (e.g. post-validate hard-tier finding gating sync).
    """

    phase: str
    message: str


class DoneEvent(
    Struct,
    kw_only=True,
    tag_field="kind",
    tag="done",
):
    """Terminal success frame. Backend Spec §4.6.2.

    ``result`` carries the final state envelope: ``path`` (the new
    directory), ``sync_status``, ``blocked``, and any other fields the
    pipeline chooses to surface.
    """

    result: dict[str, Any]


class FailedEvent(
    Struct,
    kw_only=True,
    tag_field="kind",
    tag="failed",
):
    """Terminal failure frame. Backend Spec §4.6.2.

    ``error`` follows the §4.6.3 error envelope shape (``code``,
    ``message`` minimum) so client code can reuse the same dispatch
    table it uses for HTTP-level errors.
    """

    phase: str
    error: dict[str, Any]


class SnapshotEvent(
    Struct,
    kw_only=True,
    tag_field="kind",
    tag="snapshot",
):
    """Full audit snapshot frame for the Problems pub-sub channel.

    Sent immediately after the WebSocket connects so the client can
    paint the initial Problems-tab state without a separate REST call.
    Backend Spec §4.6.2 example.
    """

    findings: list[dict[str, Any]]
    audit_at: str


class DeltaEvent(
    Struct,
    kw_only=True,
    tag_field="kind",
    tag="delta",
):
    """Delta frame for the Problems pub-sub channel. Backend Spec §4.6.2.

    Sent on every audit pass after the first. The three lists carry the
    ``rule + offending_path`` pairs that were added, removed, or
    changed compared to the previous snapshot.
    """

    added: list[dict[str, Any]]
    removed: list[dict[str, Any]]
    changed: list[dict[str, Any]]
    audit_at: str


# Closed union of session-channel frame types. Used for documentation
# and to make ``encode_event`` callers explicit.
SessionEvent = (
    PhaseEvent
    | ProgressEvent
    | InputRequiredEvent
    | WarningEvent
    | DoneEvent
    | FailedEvent
)


def encode_event(event: Struct) -> bytes:
    """Encode a typed WebSocket frame to JSON bytes via ``msgspec.json``.

    Single dispatch site so callers don't repeat the encoder choice;
    keeps the §4.6.2 wire format consistent with the cache hot path.
    """
    return msgspec_json.encode(event)


def event_from_dict(payload: dict[str, Any]) -> Struct:
    """Convert a plain dict (e.g. from the controller's event queue)
    into the matching typed Struct.

    The controller's pipeline pushes dicts onto the session event queue
    (see ``CreationController._publish``). The WebSocket handler reads
    those dicts and round-trips them through ``msgspec.convert`` so
    every outgoing frame is shape-checked.

    The mapping table is keyed by the ``kind`` discriminator. Unknown
    kinds raise :class:`ValueError` -- adding a new kind is a wire
    change and must be reflected here.
    """
    kind = payload.get("kind")
    table: dict[str, type[Struct]] = {
        "phase": PhaseEvent,
        "progress": ProgressEvent,
        "input_required": InputRequiredEvent,
        "warning": WarningEvent,
        "done": DoneEvent,
        "failed": FailedEvent,
        "snapshot": SnapshotEvent,
        "delta": DeltaEvent,
    }
    target = table.get(kind) if isinstance(kind, str) else None
    if target is None:
        msg = f"unknown event kind: {kind!r}"
        raise ValueError(msg)
    return msgspec.convert(payload, type=target)
