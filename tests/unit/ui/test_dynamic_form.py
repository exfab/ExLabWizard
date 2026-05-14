"""Unit tests for the frontend-polish helpers.

Covers the pure logic behind the three polish features:

* ``template_questions`` -- the Copier-question parser that drives the
  wizard's dynamic Variables step.
* ``render_question_field`` -- seeds the answers dict with each
  question's default (the only headlessly-assertable behaviour).
* ``build_equipment_config`` -- the equipment-editor builder, across
  both completeness signals and both transports.
"""

from __future__ import annotations

# Prime the api package before importing ui.pages (import-cycle workaround).
import exlab_wizard.api.app  # noqa: F401

import pytest
from pydantic import ValidationError

from exlab_wizard.config.models import RcloneTransport, RsyncSshTransport
from exlab_wizard.constants import CompletenessSignal
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
        "completeness_signal": "sentinel_file",
        "sentinel_filename": "done.flag",
        "manifest_filename": "",
        "transport_type": "rclone",
        "rclone_remote": "lab-nas",
        "rclone_remote_path": "lab/microscope1",
        "ssh_target": "",
        "ssh_key_path": "",
        "rsync_remote_path": "",
    }
    base.update(overrides)
    return base


def test_build_equipment_rclone_sentinel() -> None:
    entry = build_equipment_config(**_equipment_kwargs())  # type: ignore[arg-type]
    assert entry.id == "MICROSCOPE1"
    assert entry.completeness_signal is CompletenessSignal.SENTINEL_FILE
    assert entry.sentinel_filename == "done.flag"
    assert entry.manifest_filename is None
    assert isinstance(entry.transport, RcloneTransport)
    assert entry.transport.rclone_remote == "lab-nas"


def test_build_equipment_rsync_manifest() -> None:
    entry = build_equipment_config(
        **_equipment_kwargs(  # type: ignore[arg-type]
            completeness_signal="manifest",
            sentinel_filename="",
            manifest_filename="manifest.json",
            transport_type="rsync_ssh",
            rclone_remote="",
            rclone_remote_path="",
            ssh_target="operator@host",
            ssh_key_path="~/.ssh/id_ed25519",
            rsync_remote_path="/remote/microscope1",
        )
    )
    assert entry.completeness_signal is CompletenessSignal.MANIFEST
    assert entry.manifest_filename == "manifest.json"
    assert entry.sentinel_filename is None
    assert isinstance(entry.transport, RsyncSshTransport)
    assert entry.transport.ssh_target == "operator@host"
    assert entry.transport.remote_path == "/remote/microscope1"


def test_build_equipment_rejects_bad_id() -> None:
    with pytest.raises(ValidationError):
        build_equipment_config(**_equipment_kwargs(equipment_id="lower_case"))  # type: ignore[arg-type]


def test_build_equipment_rejects_sentinel_without_filename() -> None:
    with pytest.raises(ValidationError):
        build_equipment_config(
            **_equipment_kwargs(sentinel_filename="")  # type: ignore[arg-type]
        )
