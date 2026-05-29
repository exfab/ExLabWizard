"""Unit tests for the frontend-polish helpers.

Covers the pure logic behind the three polish features:

* ``template_questions`` -- the Copier-question parser that drives the
  wizard's dynamic Variables step.
* ``render_question_field`` -- seeds the answers dict with each
  question's default (the only headlessly-assertable behaviour).
* ``build_equipment_config`` -- the equipment-editor builder. rclone.conf
  migration: nas-mode no longer carries a per-equipment transport.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

# Prime the api package before importing ui.pages (import-cycle workaround).
import exlab_wizard.api.app  # noqa: F401
from exlab_wizard.ui.pages.settings import build_equipment_config
from exlab_wizard.ui.pages.templates import (
    TemplateQuestion,
    render_question_field,
    template_questions,
)

# ---------------------------------------------------------------------------
# template_questions
# ---------------------------------------------------------------------------


def test_template_questions_skips_underscore_keys() -> None:
    manifest = {
        "_exlab_type": "project",
        "_exlab_version": "1.0",
        "_min_copier_version": "9.0",
    }
    assert template_questions(manifest) == []


def test_template_questions_long_form_types() -> None:
    manifest = {
        "_exlab_type": "run",
        "name": {"type": "str", "default": "x", "help": "the name"},
        "count": {"type": "int", "default": 3},
        "ratio": {"type": "float", "default": 0.5},
        "enabled": {"type": "bool", "default": True},
        "secret_token": {"type": "str", "secret": True},
    }
    by_key = {q.key: q for q in template_questions(manifest)}
    assert by_key["name"].kind == "str"
    assert by_key["name"].help == "the name"
    assert by_key["count"].kind == "int"
    assert by_key["ratio"].kind == "float"
    assert by_key["enabled"].kind == "bool"
    assert by_key["secret_token"].secret is True


def test_template_questions_choice_from_list_and_dict() -> None:
    manifest = {
        "_exlab_type": "run",
        "mode": {"type": "str", "choices": ["fast", "slow"], "default": "fast"},
        "tier": {"choices": {"Low": "low", "High": "high"}},
    }
    by_key = {q.key: q for q in template_questions(manifest)}
    assert by_key["mode"].kind == "choice"
    assert by_key["mode"].choices == ("fast", "slow")
    assert by_key["tier"].kind == "choice"
    assert by_key["tier"].choices == ("low", "high")


def test_template_questions_short_form_infers_kind() -> None:
    manifest = {
        "_exlab_type": "project",
        "label": "default-label",
        "replicas": 4,
        "flag": True,
        "scale": 1.25,
    }
    by_key = {q.key: q for q in template_questions(manifest)}
    assert by_key["label"].kind == "str"
    assert by_key["label"].default == "default-label"
    assert by_key["replicas"].kind == "int"
    assert by_key["flag"].kind == "bool"
    assert by_key["scale"].kind == "float"


# ---------------------------------------------------------------------------
# render_question_field
# ---------------------------------------------------------------------------


def test_render_question_field_seeds_default_into_answers() -> None:
    answers: dict[str, object] = {}
    render_question_field(
        TemplateQuestion(key="count", kind="int", default=7),
        answers,
        testid_prefix="wizard-project-var",
    )
    assert answers["count"] == 7


def test_render_question_field_preserves_existing_answer() -> None:
    answers: dict[str, object] = {"mode": "slow"}
    render_question_field(
        TemplateQuestion(key="mode", kind="choice", default="fast", choices=("fast", "slow")),
        answers,
        testid_prefix="wizard-run-var",
    )
    assert answers["mode"] == "slow"


# ---------------------------------------------------------------------------
# build_equipment_config
# ---------------------------------------------------------------------------


def _equipment_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "equipment_id": "MICROSCOPE1",
        "label": "Confocal 1",
        "local_root": "/data/microscope1",
        "nas_root": "/nas/microscope1",
        "sync_mode": "nas",
    }
    base.update(overrides)
    return base


def test_build_equipment_nas_has_no_transport() -> None:
    # rclone.conf migration: nas-mode carries no per-equipment transport;
    # the ``nas:`` remote defines the connection.
    entry = build_equipment_config(**_equipment_kwargs())  # type: ignore[arg-type]
    assert entry.id == "MICROSCOPE1"
    assert entry.sync_mode.value == "nas"
    assert not hasattr(entry, "transport")


def test_build_equipment_stage_has_staging_transport() -> None:
    entry = build_equipment_config(
        **_equipment_kwargs(  # type: ignore[arg-type]
            sync_mode="stage",
            staging_transport_type="smb_mount",
            staging_mount_point="/mnt/staging",
            staging_subpath="in/microscope1",
        )
    )
    assert entry.sync_mode.value == "stage"
    assert not hasattr(entry, "transport")
    assert entry.orchestrator_staging_transport is not None
    assert entry.orchestrator_staging_transport.mount_point == "/mnt/staging"


def test_build_equipment_rejects_bad_id() -> None:
    with pytest.raises(ValidationError):
        build_equipment_config(**_equipment_kwargs(equipment_id="lower_case"))  # type: ignore[arg-type]
