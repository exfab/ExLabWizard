"""Typed, round-trippable model of a Copier ``copier.yml`` manifest.

This module owns the two primitives the GUI authoring form and the
wizard consume questions through:

* :class:`TemplateQuestion` -- one Copier question normalised to the
  widget family the wizard renders (``str`` / ``int`` / ``float`` /
  ``bool`` / ``choice``).
* :func:`template_questions` -- parse the operator-answerable questions
  out of a raw ``copier.yml`` body (both Copier long- and short-form).

These two used to live in :mod:`exlab_wizard.ui.pages.templates`. They
were moved here so the (non-UI) :class:`TemplateManifest` model can
reuse them without importing a NiceGUI page module (an import cycle).
``ui.pages.templates`` re-exports them so existing callers keep working.

:class:`TemplateManifest` mirrors the ``_exlab_*`` metadata keys plus
the parsed questions and round-trips ``copier.yml`` so the structured
authoring form never hand-writes YAML:
``TemplateManifest.from_yaml(m.to_yaml()) == m`` holds for manifests
built from every supported question kind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

__all__ = [
    "TemplateManifest",
    "TemplateQuestion",
    "template_questions",
]


@dataclass(frozen=True)
class TemplateQuestion:
    """One Copier question parsed from a template's ``copier.yml``.

    ``kind`` is normalised to the widget family the wizard renders:
    ``str`` / ``int`` / ``float`` / ``bool`` / ``choice``. ``choices``
    is populated only for ``choice`` questions. ``secret`` flags a
    password-style ``str`` input.
    """

    key: str
    kind: str
    default: Any = None
    choices: tuple[Any, ...] = ()
    help: str = ""
    secret: bool = False


# Copier reserves ``_``-prefixed manifest keys for itself; everything
# else under the top level is an operator-answerable question.
_COPIER_TYPE_TO_KIND: dict[str, str] = {
    "str": "str",
    "int": "int",
    "float": "float",
    "bool": "bool",
    "yaml": "str",
    "json": "str",
}

# Reverse map for emitting a Copier ``type`` string from a normalised
# :class:`TemplateQuestion.kind`. ``choice`` is special-cased (emits
# ``type: str`` + a ``choices`` block) in :meth:`TemplateManifest.to_yaml`.
_KIND_TO_COPIER_TYPE: dict[str, str] = {
    "str": "str",
    "int": "int",
    "float": "float",
    "bool": "bool",
    "choice": "str",
}


def template_questions(raw_manifest: dict[str, Any]) -> list[TemplateQuestion]:
    """Parse the operator-answerable questions out of a ``copier.yml`` body.

    Handles both Copier question forms:

    * **long form** -- ``key: {type: ..., default: ..., choices: ...}``
    * **short form** -- ``key: <scalar>`` (the scalar is the default;
      the type is inferred from it)

    ``_``-prefixed keys (Copier / ``_exlab_*`` metadata) are skipped.
    Questions carrying a ``when`` clause are still returned -- the
    wizard renders them unconditionally for v1.
    """
    questions: list[TemplateQuestion] = []
    for key, spec in raw_manifest.items():
        if key.startswith("_"):
            continue
        if isinstance(spec, dict):
            raw_type = str(spec.get("type", "str"))
            raw_choices = spec.get("choices")
            choices: tuple[Any, ...] = ()
            if isinstance(raw_choices, dict):
                choices = tuple(raw_choices.values())
            elif isinstance(raw_choices, list):
                choices = tuple(raw_choices)
            kind = "choice" if choices else _COPIER_TYPE_TO_KIND.get(raw_type, "str")
            questions.append(
                TemplateQuestion(
                    key=key,
                    kind=kind,
                    default=spec.get("default"),
                    choices=choices,
                    help=str(spec.get("help", "")),
                    secret=bool(spec.get("secret", False)),
                )
            )
        else:
            # Short form: the scalar is the default; infer the kind.
            if isinstance(spec, bool):
                kind = "bool"
            elif isinstance(spec, int):
                kind = "int"
            elif isinstance(spec, float):
                kind = "float"
            else:
                kind = "str"
            questions.append(TemplateQuestion(key=key, kind=kind, default=spec))
    return questions


@dataclass(frozen=True)
class TemplateManifest:
    """A typed, round-trippable model of a Copier ``copier.yml``.

    Mirrors the ``_exlab_*`` metadata keys plus the parsed
    :class:`TemplateQuestion` list. Built so the structured authoring
    form never hand-writes YAML: :meth:`from_yaml` parses a manifest
    (string or already-parsed dict) and :meth:`to_yaml` emits a
    deterministic, long-form ``copier.yml`` such that
    ``TemplateManifest.from_yaml(m.to_yaml()) == m``.

    Attributes:
        exlab_type: One of ``"project"`` / ``"equipment"`` / ``"run"``
            (``_exlab_type``). Not validated here -- see
            :mod:`exlab_wizard.template.lint`.
        exlab_version: The required ``_exlab_version`` string (§5.7).
        exlab_run_scope: ``_exlab_run_scope`` for run templates; ``None``
            otherwise (and then never emitted by :meth:`to_yaml`).
        description: ``_exlab_description`` free-form text.
        plugins: Ordered ``_exlab_plugins`` slug list (§6.2.3); emitted
            only when non-empty.
        readme_fields: ``_exlab_readme.fields`` field-extension list
            (§10.3), each a free-form dict.
        questions: Parsed operator-answerable questions, emitted in
            long form.
        min_copier_version: ``_min_copier_version`` (defaults ``"9.0"``).
        answers_file: ``_answers_file`` (defaults ``".exlab-answers.yml"``).
    """

    exlab_type: str
    exlab_version: str
    exlab_run_scope: str | None = None
    description: str = ""
    plugins: list[str] = field(default_factory=list)
    readme_fields: list[dict[str, Any]] = field(default_factory=list)
    questions: list[TemplateQuestion] = field(default_factory=list)
    min_copier_version: str = "9.0"
    answers_file: str = ".exlab-answers.yml"

    @classmethod
    def from_yaml(cls, raw: str | dict[str, Any]) -> TemplateManifest:
        """Build a :class:`TemplateManifest` from a manifest body.

        Args:
            raw: Either a raw ``copier.yml`` string (parsed with
                :func:`yaml.safe_load`) or an already-parsed mapping.

        Returns:
            A :class:`TemplateManifest`. Missing ``_exlab_*`` / ``_*``
            keys fall back to the dataclass defaults; questions are
            parsed via :func:`template_questions`.
        """
        data: dict[str, Any]
        if isinstance(raw, str):
            parsed = yaml.safe_load(raw)
            data = parsed if isinstance(parsed, dict) else {}
        else:
            data = raw

        raw_type = data.get("_exlab_type")
        exlab_type = raw_type if isinstance(raw_type, str) else ""

        raw_version = data.get("_exlab_version")
        exlab_version = raw_version if isinstance(raw_version, str) else ""

        raw_scope = data.get("_exlab_run_scope")
        exlab_run_scope = raw_scope if isinstance(raw_scope, str) else None

        raw_description = data.get("_exlab_description")
        description = raw_description if isinstance(raw_description, str) else ""

        raw_plugins = data.get("_exlab_plugins")
        plugins = list(raw_plugins) if isinstance(raw_plugins, list) else []

        readme_fields: list[dict[str, Any]] = []
        readme_block = data.get("_exlab_readme")
        if isinstance(readme_block, dict):
            raw_fields = readme_block.get("fields")
            if isinstance(raw_fields, list):
                readme_fields = [e for e in raw_fields if isinstance(e, dict)]

        raw_min = data.get("_min_copier_version")
        min_copier_version = raw_min if isinstance(raw_min, str) else "9.0"

        raw_answers = data.get("_answers_file")
        answers_file = raw_answers if isinstance(raw_answers, str) else ".exlab-answers.yml"

        return cls(
            exlab_type=exlab_type,
            exlab_version=exlab_version,
            exlab_run_scope=exlab_run_scope,
            description=description,
            plugins=plugins,
            readme_fields=readme_fields,
            questions=template_questions(data),
            min_copier_version=min_copier_version,
            answers_file=answers_file,
        )

    def to_yaml(self) -> str:
        """Emit a deterministic, long-form ``copier.yml`` string.

        Keys are emitted in a fixed order (Copier metadata first, then
        each question in long form) with ``sort_keys=False`` so the
        ordering is preserved. ``_exlab_run_scope`` is emitted only when
        not ``None`` and ``_exlab_plugins`` only when non-empty. Each
        question emits ``{type, help, default, choices, secret}`` with
        empty / ``None`` sub-keys omitted, so
        :meth:`from_yaml` reconstructs the identical model.
        """
        body: dict[str, Any] = {
            "_min_copier_version": self.min_copier_version,
            "_answers_file": self.answers_file,
            "_exlab_type": self.exlab_type,
            "_exlab_version": self.exlab_version,
        }
        if self.exlab_run_scope is not None:
            body["_exlab_run_scope"] = self.exlab_run_scope
        body["_exlab_description"] = self.description
        body["_exlab_readme"] = {"fields": self.readme_fields}
        if self.plugins:
            body["_exlab_plugins"] = list(self.plugins)

        for question in self.questions:
            body[question.key] = _question_to_long_form(question)

        return yaml.safe_dump(body, sort_keys=False)


def _question_to_long_form(question: TemplateQuestion) -> dict[str, Any]:
    """Render one :class:`TemplateQuestion` as a Copier long-form spec.

    Maps ``kind`` back to a Copier ``type`` string; ``choice`` questions
    emit ``type: str`` plus a ``choices`` list. Empty / ``None`` sub-keys
    (``help``, ``default``, ``choices``, ``secret``) are omitted so the
    round-trip through :func:`template_questions` is lossless.
    """
    spec: dict[str, Any] = {"type": _KIND_TO_COPIER_TYPE.get(question.kind, "str")}
    if question.help:
        spec["help"] = question.help
    if question.default is not None:
        spec["default"] = question.default
    if question.choices:
        spec["choices"] = list(question.choices)
    if question.secret:
        spec["secret"] = True
    return spec
