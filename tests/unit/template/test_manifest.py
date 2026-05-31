"""Tests for the typed, round-trippable :class:`TemplateManifest` model.

The load-bearing invariant is round-trip stability:
``TemplateManifest.from_yaml(m.to_yaml()) == m`` for manifests built
from every supported question kind. Frozen-dataclass equality (field by
field, ``TemplateQuestion`` is itself frozen) makes the comparison exact.
"""

from __future__ import annotations

import yaml

from exlab_wizard.template.manifest import (
    TemplateManifest,
    TemplateQuestion,
    template_questions,
)


def _roundtrip(manifest: TemplateManifest) -> TemplateManifest:
    return TemplateManifest.from_yaml(manifest.to_yaml())


# ---------------------------------------------------------------------------
# round-trip per question kind
# ---------------------------------------------------------------------------


def test_roundtrip_str_question() -> None:
    m = TemplateManifest(
        exlab_type="project",
        exlab_version="1.0",
        questions=[TemplateQuestion(key="sample_id", kind="str", default="abc", help="ID")],
    )
    assert _roundtrip(m) == m


def test_roundtrip_int_question() -> None:
    m = TemplateManifest(
        exlab_type="project",
        exlab_version="1.0",
        questions=[TemplateQuestion(key="count", kind="int", default=7)],
    )
    assert _roundtrip(m) == m


def test_roundtrip_float_question() -> None:
    m = TemplateManifest(
        exlab_type="project",
        exlab_version="1.0",
        questions=[TemplateQuestion(key="ratio", kind="float", default=1.5)],
    )
    assert _roundtrip(m) == m


def test_roundtrip_bool_question() -> None:
    m = TemplateManifest(
        exlab_type="project",
        exlab_version="1.0",
        questions=[TemplateQuestion(key="enabled", kind="bool", default=True)],
    )
    assert _roundtrip(m) == m


def test_roundtrip_choice_question() -> None:
    m = TemplateManifest(
        exlab_type="project",
        exlab_version="1.0",
        questions=[
            TemplateQuestion(
                key="mode",
                kind="choice",
                default="fast",
                choices=("fast", "slow"),
            )
        ],
    )
    assert _roundtrip(m) == m


def test_roundtrip_secret_question() -> None:
    m = TemplateManifest(
        exlab_type="project",
        exlab_version="1.0",
        questions=[TemplateQuestion(key="token", kind="str", secret=True)],
    )
    assert _roundtrip(m) == m


def test_roundtrip_all_kinds_together() -> None:
    m = TemplateManifest(
        exlab_type="run",
        exlab_version="3.2",
        exlab_run_scope="experimental",
        description="A mixed-question template",
        plugins=["plugin_a", "plugin_b"],
        readme_fields=[{"id": "instrument", "label": "Instrument"}],
        questions=[
            TemplateQuestion(key="sample_id", kind="str", default="s1", help="Sample"),
            TemplateQuestion(key="count", kind="int", default=3),
            TemplateQuestion(key="ratio", kind="float", default=0.25),
            TemplateQuestion(key="enabled", kind="bool", default=False),
            TemplateQuestion(key="mode", kind="choice", default="b", choices=("a", "b")),
            TemplateQuestion(key="token", kind="str", secret=True),
        ],
    )
    assert _roundtrip(m) == m


# ---------------------------------------------------------------------------
# _-prefixed metadata keys preserved
# ---------------------------------------------------------------------------


def test_underscore_prefixed_keys_preserved() -> None:
    m = TemplateManifest(
        exlab_type="project",
        exlab_version="2.0",
        description="desc",
        plugins=["p1"],
        min_copier_version="9.0",
        answers_file=".exlab-answers.yml",
    )
    body = yaml.safe_load(m.to_yaml())
    assert body["_min_copier_version"] == "9.0"
    assert body["_answers_file"] == ".exlab-answers.yml"
    assert body["_exlab_type"] == "project"
    assert body["_exlab_version"] == "2.0"
    assert body["_exlab_description"] == "desc"
    assert body["_exlab_plugins"] == ["p1"]
    assert body["_exlab_readme"] == {"fields": []}
    assert _roundtrip(m) == m


def test_emitted_key_order_metadata_first() -> None:
    m = TemplateManifest(
        exlab_type="project",
        exlab_version="1.0",
        questions=[TemplateQuestion(key="sample_id", kind="str")],
    )
    keys = list(yaml.safe_load(m.to_yaml()).keys())
    # Metadata keys come before the question key, in the documented order.
    assert keys[:5] == [
        "_min_copier_version",
        "_answers_file",
        "_exlab_type",
        "_exlab_version",
        "_exlab_description",
    ]
    assert keys[-1] == "sample_id"


# ---------------------------------------------------------------------------
# run-scope emission
# ---------------------------------------------------------------------------


def test_run_scope_emitted_only_for_run_type() -> None:
    run_m = TemplateManifest(
        exlab_type="run",
        exlab_version="1.0",
        exlab_run_scope="test",
    )
    project_m = TemplateManifest(exlab_type="project", exlab_version="1.0")

    assert "_exlab_run_scope" in yaml.safe_load(run_m.to_yaml())
    assert "_exlab_run_scope" not in yaml.safe_load(project_m.to_yaml())
    assert _roundtrip(run_m) == run_m
    assert _roundtrip(project_m) == project_m


def test_plugins_omitted_when_empty() -> None:
    m = TemplateManifest(exlab_type="project", exlab_version="1.0")
    assert "_exlab_plugins" not in yaml.safe_load(m.to_yaml())


# ---------------------------------------------------------------------------
# from_yaml accepts string OR dict; tolerates missing keys
# ---------------------------------------------------------------------------


def test_from_yaml_accepts_string() -> None:
    m = TemplateManifest.from_yaml("_exlab_type: project\n_exlab_version: '1.0'\n")
    assert m.exlab_type == "project"
    assert m.exlab_version == "1.0"
    # Defaults filled for missing keys.
    assert m.min_copier_version == "9.0"
    assert m.answers_file == ".exlab-answers.yml"
    assert m.questions == []


def test_from_yaml_accepts_dict() -> None:
    m = TemplateManifest.from_yaml({"_exlab_type": "equipment", "_exlab_version": "9"})
    assert m.exlab_type == "equipment"
    assert m.exlab_version == "9"


def test_from_yaml_tolerates_empty_and_non_mapping() -> None:
    assert TemplateManifest.from_yaml("").exlab_type == ""
    assert TemplateManifest.from_yaml("just a string").exlab_type == ""


def test_template_questions_reexport_parses_questions() -> None:
    questions = template_questions(
        {"_exlab_type": "project", "name": {"type": "str", "default": "x"}}
    )
    assert [q.key for q in questions] == ["name"]
    assert questions[0].kind == "str"
