"""Tests for :class:`exlab_wizard.readme.generator.ReadmeGenerator`.

Pins the contract laid out in Backend Spec §10 and §11.4 and the
mandatory core gate from User Interaction Spec §2:

* both files (README.md and readme_fields.json) are written.
* README.md starts with ``---\\n`` and the front matter parses via
  ``yaml.safe_load``.
* README.md body has a ``# {label}`` heading and the objective.
* ``readme_fields.json`` round-trips through
  :class:`~exlab_wizard.api.schemas.ReadmeFieldsJson`.
* core gate: empty / over-length core values are rejected.
* required template / config fields are enforced.
* duplicate field ids across layers are rejected.
* custom-field labels do not collide with declared ids.
* core ids redeclared by the template raise
  :class:`~exlab_wizard.errors.TemplateCoreFieldRedeclaredError`.
* system run name is correct for every level (project, run, test).
* every field type is validated (string, text, choice, date, boolean).
* generation is byte-identical when called twice with the same context.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from msgspec import json as msgspec_json

from exlab_wizard.api.schemas import ReadmeFieldsJson
from exlab_wizard.constants import (
    CACHE_DIR_NAME,
    LABEL_MAX_LENGTH,
    OBJECTIVE_MAX_LENGTH,
    README_FIELDS_JSON_NAME,
    README_FIELDS_JSON_VERSION,
    README_FILE_NAME,
    README_FRONT_MATTER_SCHEMA_VERSION,
)
from exlab_wizard.errors import TemplateCoreFieldRedeclaredError
from exlab_wizard.readme import (
    CoreFields,
    CustomField,
    ReadmeContext,
    ReadmeGenerator,
    SystemFields,
    TemplateFieldDecl,
)

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _system_fields(
    *,
    run: str | None = "Run_2026-04-17T14-32-00",
    run_kind: str = "experimental",
) -> SystemFields:
    return SystemFields(
        created=datetime(2026, 4, 17, 14, 32, 0, tzinfo=UTC),
        created_by="asmith",
        equipment={"id": "CONFOCAL_01", "label": "Confocal Microscope 1"},
        template={"name": "confocal_run_v2", "version": "2.1"},
        project="PROJ-0042",
        run=run,
        run_kind=run_kind,
    )


def _ctx(
    *,
    level: str = "run",
    label: str = "Cortex Q3 calibration sweep",
    operator: str = "asmith",
    objective: str = "Validate laser power settings before production acquisitions.",
    template_fields: dict | None = None,
    config_fields: dict | None = None,
    custom_fields: list[CustomField] | None = None,
    system: SystemFields | None = None,
    template_field_decls: list[TemplateFieldDecl] | None = None,
    config_field_decls: list[TemplateFieldDecl] | None = None,
) -> ReadmeContext:
    return ReadmeContext(
        level=level,  # type: ignore[arg-type]
        core=CoreFields(label=label, operator=operator, objective=objective),
        template_fields=template_fields or {},
        config_fields=config_fields or {},
        custom_fields=custom_fields or [],
        system=system or _system_fields(),
        template_field_decls=template_field_decls or [],
        config_field_decls=config_field_decls or [],
    )


@pytest.fixture()
def generator() -> ReadmeGenerator:
    return ReadmeGenerator()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_writes_both_files(generator: ReadmeGenerator, tmp_path: Path) -> None:
    readme, cache = await generator.generate(tmp_path, _ctx())
    assert readme == tmp_path / README_FILE_NAME
    assert cache == tmp_path / CACHE_DIR_NAME / README_FIELDS_JSON_NAME
    assert readme.is_file()
    assert cache.is_file()


@pytest.mark.asyncio
async def test_readme_starts_with_yaml_front_matter(
    generator: ReadmeGenerator, tmp_path: Path
) -> None:
    readme, _ = await generator.generate(tmp_path, _ctx())
    text = readme.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    # The first ``---`` opens the block; the second closes it. Anything after
    # is the prose body. ``yaml.safe_load`` should accept the inner block.
    head, _, tail = text.partition("\n---\n")
    fm_text = head[len("---\n") :]
    parsed = yaml.safe_load(fm_text)
    assert parsed["schema_version"] == README_FRONT_MATTER_SCHEMA_VERSION
    assert parsed["core_fields"]["operator"] == "asmith"
    assert tail.lstrip().startswith("# Cortex Q3 calibration sweep")


@pytest.mark.asyncio
async def test_readme_body_includes_label_and_objective(
    generator: ReadmeGenerator, tmp_path: Path
) -> None:
    readme, _ = await generator.generate(
        tmp_path,
        _ctx(label="My label", objective="My objective"),
    )
    text = readme.read_text(encoding="utf-8")
    _, _, body = text.partition("\n---\n\n")
    assert body.startswith("# My label\n\nMy objective\n")


@pytest.mark.asyncio
async def test_readme_fields_json_round_trips(generator: ReadmeGenerator, tmp_path: Path) -> None:
    _, cache = await generator.generate(
        tmp_path,
        _ctx(
            template_fields={"sample_type": "Fixed tissue"},
            config_fields={"irb_protocol": "IRB-2026-0042"},
            custom_fields=[CustomField(label="Collaborator", value="Dr. J. Lee")],
            template_field_decls=[
                TemplateFieldDecl(
                    id="sample_type",
                    label="Sample Type",
                    type="choice",
                    options=["Fixed tissue", "Live cell"],
                )
            ],
            config_field_decls=[
                TemplateFieldDecl(
                    id="irb_protocol",
                    label="IRB",
                    type="string",
                    required=True,
                )
            ],
        ),
    )
    decoded = msgspec_json.decode(cache.read_bytes(), type=ReadmeFieldsJson)
    assert decoded.schema_version == README_FIELDS_JSON_VERSION
    assert decoded.core_fields["label"] == "Cortex Q3 calibration sweep"
    assert decoded.template_fields == {"sample_type": "Fixed tissue"}
    assert decoded.config_fields == {"irb_protocol": "IRB-2026-0042"}
    assert decoded.custom_fields == [{"label": "Collaborator", "value": "Dr. J. Lee"}]
    assert decoded.system_fields["run_kind"] == "experimental"


# ---------------------------------------------------------------------------
# Core-field validation (User Interaction Spec §2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field_name,value",
    [("label", ""), ("label", "   "), ("operator", ""), ("objective", "  ")],
)
@pytest.mark.asyncio
async def test_empty_core_field_rejected(
    generator: ReadmeGenerator, tmp_path: Path, field_name: str, value: str
) -> None:
    kwargs = {field_name: value}
    with pytest.raises(ValueError, match=f"core_fields.{field_name}"):
        await generator.generate(tmp_path, _ctx(**kwargs))


@pytest.mark.asyncio
async def test_label_over_max_length_rejected(generator: ReadmeGenerator, tmp_path: Path) -> None:
    too_long = "x" * (LABEL_MAX_LENGTH + 1)
    with pytest.raises(ValueError, match=r"core_fields.label exceeds"):
        await generator.generate(tmp_path, _ctx(label=too_long))


@pytest.mark.asyncio
async def test_objective_over_max_length_rejected(
    generator: ReadmeGenerator, tmp_path: Path
) -> None:
    too_long = "y" * (OBJECTIVE_MAX_LENGTH + 1)
    with pytest.raises(ValueError, match=r"core_fields.objective exceeds"):
        await generator.generate(tmp_path, _ctx(objective=too_long))


# ---------------------------------------------------------------------------
# Required-field gates (Backend §10.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_required_template_field_missing_raises(
    generator: ReadmeGenerator, tmp_path: Path
) -> None:
    decls = [TemplateFieldDecl(id="protocol", label="Protocol", type="string", required=True)]
    with pytest.raises(ValueError, match=r"template_fields\['protocol'\]"):
        await generator.generate(tmp_path, _ctx(template_fields={}, template_field_decls=decls))


@pytest.mark.asyncio
async def test_required_config_field_missing_raises(
    generator: ReadmeGenerator, tmp_path: Path
) -> None:
    decls = [TemplateFieldDecl(id="irb", label="IRB", type="string", required=True)]
    with pytest.raises(ValueError, match=r"config_fields\['irb'\]"):
        await generator.generate(
            tmp_path, _ctx(config_fields={"irb": "  "}, config_field_decls=decls)
        )


# ---------------------------------------------------------------------------
# Field-id uniqueness across layers + custom-field collision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_field_id_across_layers_raises(
    generator: ReadmeGenerator, tmp_path: Path
) -> None:
    template_decls = [TemplateFieldDecl(id="shared", label="t", type="string")]
    config_decls = [TemplateFieldDecl(id="shared", label="c", type="string")]
    with pytest.raises(ValueError, match=r"declared in both"):
        await generator.generate(
            tmp_path,
            _ctx(
                template_field_decls=template_decls,
                config_field_decls=config_decls,
            ),
        )


@pytest.mark.asyncio
async def test_custom_field_label_collides_with_template_id(
    generator: ReadmeGenerator, tmp_path: Path
) -> None:
    decls = [TemplateFieldDecl(id="sample_type", label="Sample Type", type="string")]
    custom = [CustomField(label="sample_type", value="oops")]
    with pytest.raises(ValueError, match=r"custom field label"):
        await generator.generate(
            tmp_path,
            _ctx(template_field_decls=decls, custom_fields=custom),
        )


# ---------------------------------------------------------------------------
# Core-redeclaration gate (Backend §10.3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_core_field_redeclared_in_template_raises(
    generator: ReadmeGenerator, tmp_path: Path
) -> None:
    decls = [TemplateFieldDecl(id="objective", label="Objective", type="text")]
    with pytest.raises(TemplateCoreFieldRedeclaredError):
        await generator.generate(tmp_path, _ctx(template_field_decls=decls))


@pytest.mark.asyncio
async def test_core_field_redeclared_in_config_raises(
    generator: ReadmeGenerator, tmp_path: Path
) -> None:
    decls = [TemplateFieldDecl(id="label", label="Label", type="string")]
    with pytest.raises(TemplateCoreFieldRedeclaredError):
        await generator.generate(tmp_path, _ctx(config_field_decls=decls))


# ---------------------------------------------------------------------------
# system_fields.run varies with level / run kind (Backend §10.6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_level_system_run_is_none(generator: ReadmeGenerator, tmp_path: Path) -> None:
    sys_fields = _system_fields(run=None, run_kind="project")
    _, cache = await generator.generate(tmp_path, _ctx(level="project", system=sys_fields))
    decoded = msgspec_json.decode(cache.read_bytes(), type=ReadmeFieldsJson)
    assert decoded.system_fields["run"] is None
    assert decoded.system_fields["run_kind"] == "project"


@pytest.mark.asyncio
async def test_run_level_experimental_run_name(generator: ReadmeGenerator, tmp_path: Path) -> None:
    sys_fields = _system_fields(run="Run_2026-04-17T14-32-00", run_kind="experimental")
    _, cache = await generator.generate(tmp_path, _ctx(level="run", system=sys_fields))
    decoded = msgspec_json.decode(cache.read_bytes(), type=ReadmeFieldsJson)
    assert decoded.system_fields["run"] == "Run_2026-04-17T14-32-00"
    assert decoded.system_fields["run_kind"] == "experimental"


@pytest.mark.asyncio
async def test_run_level_test_run_name(generator: ReadmeGenerator, tmp_path: Path) -> None:
    sys_fields = _system_fields(run="TestRun_2026-04-17T14-32-00", run_kind="test")
    _, cache = await generator.generate(tmp_path, _ctx(level="run", system=sys_fields))
    decoded = msgspec_json.decode(cache.read_bytes(), type=ReadmeFieldsJson)
    assert decoded.system_fields["run"] == "TestRun_2026-04-17T14-32-00"
    assert decoded.system_fields["run_kind"] == "test"


# ---------------------------------------------------------------------------
# Per-type validation (Backend §10.3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_string_type_accepts_string_rejects_int(
    generator: ReadmeGenerator, tmp_path: Path
) -> None:
    decls = [TemplateFieldDecl(id="x", label="X", type="string")]
    # Accept
    await generator.generate(
        tmp_path,
        _ctx(template_fields={"x": "ok"}, template_field_decls=decls),
    )
    # Reject
    with pytest.raises(ValueError, match=r"expects string"):
        await generator.generate(
            tmp_path,
            _ctx(template_fields={"x": 1}, template_field_decls=decls),
        )


@pytest.mark.asyncio
async def test_text_type_accepts_multiline_rejects_list(
    generator: ReadmeGenerator, tmp_path: Path
) -> None:
    decls = [TemplateFieldDecl(id="notes", label="Notes", type="text")]
    await generator.generate(
        tmp_path,
        _ctx(template_fields={"notes": "line1\nline2"}, template_field_decls=decls),
    )
    with pytest.raises(ValueError, match=r"expects text"):
        await generator.generate(
            tmp_path,
            _ctx(template_fields={"notes": ["a", "b"]}, template_field_decls=decls),
        )


@pytest.mark.asyncio
async def test_choice_type_accepts_listed_rejects_other(
    generator: ReadmeGenerator, tmp_path: Path
) -> None:
    decls = [TemplateFieldDecl(id="x", label="X", type="choice", options=["a", "b"])]
    await generator.generate(
        tmp_path,
        _ctx(template_fields={"x": "a"}, template_field_decls=decls),
    )
    with pytest.raises(ValueError, match=r"is not in options"):
        await generator.generate(
            tmp_path,
            _ctx(template_fields={"x": "c"}, template_field_decls=decls),
        )


@pytest.mark.asyncio
async def test_choice_without_options_raises(generator: ReadmeGenerator, tmp_path: Path) -> None:
    decls = [TemplateFieldDecl(id="x", label="X", type="choice", options=None)]
    with pytest.raises(ValueError, match=r"declares no options"):
        await generator.generate(
            tmp_path,
            _ctx(template_fields={"x": "a"}, template_field_decls=decls),
        )


@pytest.mark.asyncio
async def test_date_type_accepts_iso8601_rejects_garbage(
    generator: ReadmeGenerator, tmp_path: Path
) -> None:
    decls = [TemplateFieldDecl(id="when", label="When", type="date")]
    await generator.generate(
        tmp_path,
        _ctx(template_fields={"when": "2026-04-17"}, template_field_decls=decls),
    )
    await generator.generate(
        tmp_path,
        _ctx(
            template_fields={"when": "2026-04-17T14:32:00Z"},
            template_field_decls=decls,
        ),
    )
    with pytest.raises(ValueError, match=r"not a valid ISO 8601 date"):
        await generator.generate(
            tmp_path,
            _ctx(template_fields={"when": "not-a-date"}, template_field_decls=decls),
        )


@pytest.mark.asyncio
async def test_date_type_rejects_non_string(generator: ReadmeGenerator, tmp_path: Path) -> None:
    decls = [TemplateFieldDecl(id="when", label="When", type="date")]
    with pytest.raises(ValueError, match=r"expects ISO 8601 date string"):
        await generator.generate(
            tmp_path,
            _ctx(template_fields={"when": 20260417}, template_field_decls=decls),
        )


@pytest.mark.asyncio
async def test_boolean_type_accepts_bool_rejects_string(
    generator: ReadmeGenerator, tmp_path: Path
) -> None:
    decls = [TemplateFieldDecl(id="flag", label="Flag", type="boolean")]
    await generator.generate(
        tmp_path,
        _ctx(template_fields={"flag": True}, template_field_decls=decls),
    )
    await generator.generate(
        tmp_path,
        _ctx(template_fields={"flag": False}, template_field_decls=decls),
    )
    with pytest.raises(ValueError, match=r"expects bool"):
        await generator.generate(
            tmp_path,
            _ctx(template_fields={"flag": "yes"}, template_field_decls=decls),
        )


@pytest.mark.asyncio
async def test_unknown_field_type_raises(generator: ReadmeGenerator, tmp_path: Path) -> None:
    decls = [TemplateFieldDecl(id="x", label="X", type="bogus")]  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=r"unknown type"):
        await generator.generate(
            tmp_path,
            _ctx(template_fields={"x": "v"}, template_field_decls=decls),
        )


@pytest.mark.asyncio
async def test_choice_type_rejects_non_string_value(
    generator: ReadmeGenerator, tmp_path: Path
) -> None:
    decls = [TemplateFieldDecl(id="x", label="X", type="choice", options=["a", "b"])]
    with pytest.raises(ValueError, match=r"expects choice"):
        await generator.generate(
            tmp_path,
            _ctx(template_fields={"x": 1}, template_field_decls=decls),
        )


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_is_idempotent_byte_identical(
    generator: ReadmeGenerator, tmp_path: Path
) -> None:
    """Calling generate twice with the same context produces identical bytes."""
    ctx = _ctx(
        template_fields={"sample_type": "Fixed tissue"},
        config_fields={"irb_protocol": "IRB-2026-0042"},
        custom_fields=[CustomField(label="Collaborator", value="Dr. J. Lee")],
        template_field_decls=[
            TemplateFieldDecl(
                id="sample_type",
                label="Sample Type",
                type="choice",
                options=["Fixed tissue", "Live cell"],
            )
        ],
        config_field_decls=[
            TemplateFieldDecl(id="irb_protocol", label="IRB", type="string", required=True)
        ],
    )
    readme1, cache1 = await generator.generate(tmp_path, ctx)
    bytes_readme_1 = readme1.read_bytes()
    bytes_cache_1 = cache1.read_bytes()

    # Second invocation overwrites; the rendered bytes must match exactly.
    readme2, cache2 = await generator.generate(tmp_path, ctx)
    assert readme2.read_bytes() == bytes_readme_1
    assert cache2.read_bytes() == bytes_cache_1


# ---------------------------------------------------------------------------
# Misc: timestamp formatting + .exlab-wizard creation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generated_at_uses_z_suffix(generator: ReadmeGenerator, tmp_path: Path) -> None:
    _, cache = await generator.generate(tmp_path, _ctx())
    decoded = msgspec_json.decode(cache.read_bytes(), type=ReadmeFieldsJson)
    assert decoded.generated_at.endswith("Z")
    assert decoded.system_fields["created"].endswith("Z")


@pytest.mark.asyncio
async def test_cache_dir_created_on_demand(generator: ReadmeGenerator, tmp_path: Path) -> None:
    target = tmp_path / "fresh"
    target.mkdir()
    _, cache = await generator.generate(target, _ctx())
    assert cache.parent.is_dir()
    assert cache.parent.name == CACHE_DIR_NAME


@pytest.mark.asyncio
async def test_value_without_declaration_skips_typecheck(
    generator: ReadmeGenerator, tmp_path: Path
) -> None:
    """A value whose id has no declaration is permitted (controller-side merge)."""
    # The dict carries an extra id ("rogue") that the decl list does not
    # mention; the generator skips type checking for it rather than raising.
    decls = [TemplateFieldDecl(id="known", label="K", type="string")]
    await generator.generate(
        tmp_path,
        _ctx(
            template_fields={"known": "ok", "rogue": 42},
            template_field_decls=decls,
        ),
    )


@pytest.mark.asyncio
async def test_optional_empty_field_is_skipped(generator: ReadmeGenerator, tmp_path: Path) -> None:
    """An optional field with an empty value passes; type check is skipped."""
    decls = [TemplateFieldDecl(id="x", label="X", type="string", required=False)]
    await generator.generate(
        tmp_path,
        _ctx(template_fields={"x": ""}, template_field_decls=decls),
    )


@pytest.mark.asyncio
async def test_required_field_present_with_none_raises(
    generator: ReadmeGenerator, tmp_path: Path
) -> None:
    """``None`` is treated as absent for required fields."""
    decls = [TemplateFieldDecl(id="x", label="X", type="string", required=True)]
    with pytest.raises(ValueError, match=r"required but empty"):
        await generator.generate(
            tmp_path,
            _ctx(template_fields={"x": None}, template_field_decls=decls),
        )


@pytest.mark.asyncio
async def test_generated_at_uses_z_for_naive_datetime(
    generator: ReadmeGenerator, tmp_path: Path
) -> None:
    """Naive datetimes are treated as already-UTC (no timezone gymnastics)."""
    sys_fields = SystemFields(
        created=datetime(2026, 4, 17, 14, 32, 0),
        created_by="asmith",
        equipment={"id": "E1", "label": "L"},
        template={"name": "n", "version": "v"},
        project="PROJ-0001",
        run=None,
        run_kind="project",
    )
    _, cache = await generator.generate(tmp_path, _ctx(level="project", system=sys_fields))
    decoded = msgspec_json.decode(cache.read_bytes(), type=ReadmeFieldsJson)
    assert decoded.system_fields["created"] == "2026-04-17T14:32:00Z"
