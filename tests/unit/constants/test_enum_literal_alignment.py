"""Assert that ``StrEnum`` value sets match their sibling ``Literal`` aliases.

Per the project's closed-set typing rule, whenever both a ``StrEnum`` and a
``Literal[...]`` exist for the same closed value set, a test must pin them to
the same value set so a future rename in one place is caught immediately.

This module covers the value-set comparison statically (against frozen sets
of the expected wire strings). Pydantic/TypedDict pairs that keep a
``Literal`` discriminator alongside an enum (``_ProjectSessionBody.kind`` vs
:class:`SessionKind`, ``AuditScope*.kind`` vs :class:`AuditScopeKind`) are
covered separately in the test modules of those subsystems, where the
runtime ``typing.get_args(...)`` lookup is performed.
"""

from __future__ import annotations

import pytest

from exlab_wizard.constants import enums


@pytest.mark.parametrize(
    ("enum_cls", "expected_values"),
    [
        # New enums introduced for closed-set cleanup.
        (enums.CreationLevel, frozenset({"project", "run"})),
        (
            enums.OrchestratorTransportType,
            frozenset({"smb_mount", "file_transfer"}),
        ),
        (
            enums.FieldType,
            frozenset({"string", "text", "choice", "date", "boolean"}),
        ),
        (
            enums.BandwidthDay,
            frozenset({"mon", "tue", "wed", "thu", "fri", "sat", "sun"}),
        ),
        (enums.SessionKind, frozenset({"project", "run"})),
        (enums.NextAction, frozenset({"none", "awaiting_input"})),
        (
            enums.AuditScopeKind,
            frozenset({"equipment_id", "project_path", "all"}),
        ),
        (
            enums.DirectoryLevel,
            frozenset(
                {"equipment", "project", "run", "test_run", "test_runs", "other"}
            ),
        ),
        (enums.Platform, frozenset({"macos", "windows", "linux"})),
        (
            enums.SetupNextAction,
            frozenset({"set_paths", "add_equipment", "configure_lims", "test_lims"}),
        ),
        (enums.SyncHandleState, frozenset({"queued", "blocked"})),
        (enums.PluginSourceRoot, frozenset({"bundled", "lab"})),
        (enums.TreeProjectStatus, frozenset({"active", "archived", "deleted"})),
    ],
)
def test_enum_values_match_expected_literal_set(
    enum_cls: type[enums.StrEnum], expected_values: frozenset[str]
) -> None:
    actual = {m.value for m in enum_cls}
    assert actual == expected_values, (
        f"{enum_cls.__name__} member values {actual!r} drifted from the "
        f"expected closed set {expected_values!r}; update the enum or the "
        f"corresponding ``Literal[...]`` annotation in lockstep."
    )
