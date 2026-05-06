"""Integration test: concurrent ``creation.json`` writers serialize correctly.

Spec §4.4.5 requires the per-file lock to be held for the *entire*
read-mutate-write cycle so that two writers cannot lost-update each
other. This test spawns N=10 asyncio tasks each appending a unique
entry to ``plugins_applied`` and asserts that all 10 entries survive
in the final file.

The fixture is the canonical concurrent-write check referenced from
§4.4.5 and is the integration-suite counterpart to the small-N variant
in ``tests/unit/cache/test_creation_writer.py``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from exlab_wizard.api.schemas import (
    CreationJson,
    LimsProjectBlock,
    PathsBlock,
    PluginApplied,
    TemplateBlock,
    msgspec_json,
)
from exlab_wizard.cache.creation_writer import CreationWriter
from exlab_wizard.constants import CREATION_JSON_VERSION


def _build_minimal_payload() -> CreationJson:
    return CreationJson(
        schema_version=CREATION_JSON_VERSION,
        created_at="2026-04-17T14:32:00Z",
        created_by="asmith",
        level="run",
        run_kind="experimental",
        lims_project=LimsProjectBlock(
            uid="8c7e9d2f-1a4b-4e6c-9b3d-7f2a1e5d8c4b",
            short_id="PROJ-0042",
            name_at_creation="Cortex Q3 Pilot",
        ),
        template=TemplateBlock(
            name="confocal_run_v2",
            version="2.1",
            source_path="templates/confocal_run_v2",
            run_scope="both",
        ),
        variables={"project_name": "Cortex Q3 Pilot"},
        paths=PathsBlock(local="/x", nas="//y"),
    )


@pytest.mark.asyncio
async def test_ten_concurrent_mutations_all_land(tmp_path: Path) -> None:
    """Run 10 ``update_creation_atomic`` calls concurrently against the same
    file. Every appended ``plugins_applied`` entry must be present afterwards."""
    creation_path = tmp_path / "creation.json"
    writer = CreationWriter(lock_timeout_seconds=30.0)
    await writer.write_creation(creation_path, _build_minimal_payload())

    n_tasks = 10

    def make_mutator(token: str):
        def mutator(payload: CreationJson) -> CreationJson:
            payload.plugins_applied.append(
                PluginApplied(
                    plugin=f"plugin-{token}",
                    version="1.0",
                    files_affected=[f"file-{token}.txt"],
                    status="success",
                )
            )
            return payload

        return mutator

    await asyncio.gather(
        *[
            writer.update_creation_atomic(creation_path, make_mutator(str(idx)))
            for idx in range(n_tasks)
        ]
    )

    on_disk = msgspec_json.decode(creation_path.read_bytes(), type=CreationJson)
    plugin_names = {entry.plugin for entry in on_disk.plugins_applied}
    assert plugin_names == {f"plugin-{idx}" for idx in range(n_tasks)}
    assert len(on_disk.plugins_applied) == n_tasks


@pytest.mark.asyncio
async def test_concurrent_mutations_keep_schema_at_current_version(
    tmp_path: Path,
) -> None:
    """The writer pins ``schema_version`` to the current version on every
    write. Concurrent mutations must not race the version pin."""
    creation_path = tmp_path / "creation.json"
    writer = CreationWriter(lock_timeout_seconds=30.0)
    await writer.write_creation(creation_path, _build_minimal_payload())

    n_tasks = 10

    def make_mutator(token: str):
        def mutator(payload: CreationJson) -> CreationJson:
            payload.plugins_applied.append(
                PluginApplied(
                    plugin=f"plugin-{token}",
                    version="1.0",
                    files_affected=[],
                    status="success",
                )
            )
            return payload

        return mutator

    await asyncio.gather(
        *[
            writer.update_creation_atomic(creation_path, make_mutator(str(idx)))
            for idx in range(n_tasks)
        ]
    )

    on_disk = msgspec_json.decode(creation_path.read_bytes(), type=dict[str, object])
    assert on_disk["schema_version"] == CREATION_JSON_VERSION


@pytest.mark.asyncio
async def test_concurrent_mutations_keep_file_valid_json(tmp_path: Path) -> None:
    """An interleaved write must never produce a half-written file. The
    ``os.replace``-of-tempfile pattern guarantees atomicity at the FS level;
    this test verifies the post-condition: the file decodes cleanly via
    msgspec at the end."""
    creation_path = tmp_path / "creation.json"
    writer = CreationWriter(lock_timeout_seconds=30.0)
    await writer.write_creation(creation_path, _build_minimal_payload())

    def make_mutator(token: str):
        def mutator(payload: CreationJson) -> CreationJson:
            payload.plugins_applied.append(
                PluginApplied(
                    plugin=f"plugin-{token}",
                    version="1.0",
                    files_affected=[],
                    status="success",
                )
            )
            return payload

        return mutator

    await asyncio.gather(
        *[writer.update_creation_atomic(creation_path, make_mutator(str(idx))) for idx in range(10)]
    )

    # If the file is corrupt, this raises msgspec.DecodeError (a subclass of
    # ValueError). The decode is the assertion.
    parsed = msgspec_json.decode(creation_path.read_bytes(), type=CreationJson)
    assert parsed.schema_version == CREATION_JSON_VERSION
