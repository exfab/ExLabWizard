"""Tests for the top-level exception hierarchy in ``exlab_wizard.errors``.

These tests pin the inheritance graph and the public attribute surface of each
custom exception. The hierarchy is wire-stable because ``except`` clauses
elsewhere in the codebase (and in plugin authors' code) rely on the parent
relationships -- e.g. ``except TemplateLoadError`` MUST also catch
``TemplateCoreFieldRedeclaredError``.
"""

from __future__ import annotations

import pytest

from exlab_wizard import errors
from exlab_wizard.errors import (
    ConfigError,
    ExLabError,
    KeyringUnavailableError,
    PluginError,
    PluginInputRequired,
    SchemaMajorMismatchError,
    SetupIncompleteError,
    TemplateCoreFieldRedeclaredError,
    TemplateLoadError,
    ValidationError,
)

# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


def test_exlab_error_is_subclass_of_exception() -> None:
    assert issubclass(ExLabError, Exception)


def test_exlab_error_can_be_raised_and_caught() -> None:
    with pytest.raises(ExLabError) as info:
        raise ExLabError("test")
    assert str(info.value) == "test"


# ---------------------------------------------------------------------------
# Direct ExLabError subclasses
# ---------------------------------------------------------------------------


def test_config_error_is_subclass_of_exlab_error() -> None:
    assert issubclass(ConfigError, ExLabError)


def test_config_error_can_be_raised_and_caught() -> None:
    with pytest.raises(ExLabError):
        raise ConfigError("bad config")


def test_validation_error_is_subclass_of_exlab_error() -> None:
    assert issubclass(ValidationError, ExLabError)


def test_validation_error_can_be_raised_and_caught() -> None:
    with pytest.raises(ExLabError):
        raise ValidationError("invalid")


def test_template_load_error_is_subclass_of_exlab_error() -> None:
    assert issubclass(TemplateLoadError, ExLabError)


def test_template_load_error_can_be_raised_and_caught() -> None:
    with pytest.raises(ExLabError):
        raise TemplateLoadError("template failed")


def test_plugin_error_is_subclass_of_exlab_error() -> None:
    assert issubclass(PluginError, ExLabError)


def test_plugin_error_can_be_raised_and_caught() -> None:
    with pytest.raises(ExLabError):
        raise PluginError("plugin failed")


def test_keyring_unavailable_error_is_subclass_of_exlab_error() -> None:
    assert issubclass(KeyringUnavailableError, ExLabError)


def test_keyring_unavailable_error_can_be_raised_and_caught() -> None:
    with pytest.raises(ExLabError):
        raise KeyringUnavailableError("no keyring")


def test_setup_incomplete_error_is_subclass_of_exlab_error() -> None:
    assert issubclass(SetupIncompleteError, ExLabError)


def test_setup_incomplete_error_can_be_raised_and_caught() -> None:
    with pytest.raises(ExLabError):
        raise SetupIncompleteError("not ready")


# ---------------------------------------------------------------------------
# Two-level subclass: TemplateCoreFieldRedeclaredError -> TemplateLoadError
# ---------------------------------------------------------------------------


def test_template_core_field_redeclared_is_subclass_of_template_load_error() -> None:
    # Backend Spec §10.3 -- catching TemplateLoadError MUST also catch this.
    assert issubclass(TemplateCoreFieldRedeclaredError, TemplateLoadError)


def test_template_core_field_redeclared_is_also_subclass_of_exlab_error() -> None:
    assert issubclass(TemplateCoreFieldRedeclaredError, ExLabError)


def test_template_core_field_redeclared_can_be_caught_as_template_load_error() -> None:
    with pytest.raises(TemplateLoadError):
        raise TemplateCoreFieldRedeclaredError("label redeclared")


def test_template_core_field_redeclared_can_be_caught_as_exlab_error() -> None:
    with pytest.raises(ExLabError):
        raise TemplateCoreFieldRedeclaredError("label redeclared")


# ---------------------------------------------------------------------------
# PluginInputRequired -- custom __init__ + reason property
# ---------------------------------------------------------------------------


def test_plugin_input_required_is_subclass_of_exlab_error() -> None:
    assert issubclass(PluginInputRequired, ExLabError)


def test_plugin_input_required_sets_fields_attribute() -> None:
    fields = [{"name": "foo", "label": "Foo"}]
    err = PluginInputRequired(fields=fields, reason="needs input")
    assert err.fields is fields


def test_plugin_input_required_accepts_empty_fields_list() -> None:
    err = PluginInputRequired(fields=[], reason="reason text")
    assert err.fields == []


def test_plugin_input_required_reason_property_returns_message_string() -> None:
    err = PluginInputRequired(fields=[], reason="missing operator")
    assert err.reason == "missing operator"


def test_plugin_input_required_str_equals_reason() -> None:
    reason = "operator name is required"
    err = PluginInputRequired(fields=[], reason=reason)
    assert str(err) == reason


def test_plugin_input_required_can_be_raised_and_caught() -> None:
    with pytest.raises(PluginInputRequired) as info:
        raise PluginInputRequired(fields=[{"name": "x"}], reason="r")
    assert info.value.fields == [{"name": "x"}]
    assert info.value.reason == "r"


def test_plugin_input_required_can_be_caught_as_exlab_error() -> None:
    with pytest.raises(ExLabError):
        raise PluginInputRequired(fields=[], reason="r")


# ---------------------------------------------------------------------------
# SchemaMajorMismatchError -- custom __init__ with formatted message
# ---------------------------------------------------------------------------


def test_schema_major_mismatch_is_subclass_of_exlab_error() -> None:
    assert issubclass(SchemaMajorMismatchError, ExLabError)


def test_schema_major_mismatch_sets_expected_major_attribute() -> None:
    err = SchemaMajorMismatchError(expected_major=1, found="2.0")
    assert err.expected_major == 1


def test_schema_major_mismatch_sets_found_attribute() -> None:
    err = SchemaMajorMismatchError(expected_major=1, found="2.0")
    assert err.found == "2.0"


def test_schema_major_mismatch_str_contains_expected_and_found() -> None:
    err = SchemaMajorMismatchError(expected_major=1, found="2.0")
    text = str(err)
    assert "1" in text
    assert "2.0" in text


def test_schema_major_mismatch_can_be_raised_and_caught() -> None:
    with pytest.raises(SchemaMajorMismatchError) as info:
        raise SchemaMajorMismatchError(expected_major=3, found="4.5")
    assert info.value.expected_major == 3
    assert info.value.found == "4.5"


def test_schema_major_mismatch_can_be_caught_as_exlab_error() -> None:
    with pytest.raises(ExLabError):
        raise SchemaMajorMismatchError(expected_major=1, found="2.0")


# ---------------------------------------------------------------------------
# Module __all__ surface
# ---------------------------------------------------------------------------


def test_all_lists_every_public_exception_class() -> None:
    # Each public exception class defined in this module must appear in __all__.
    expected = {
        "ConfigError",
        "ExLabError",
        "KeyringUnavailableError",
        "PluginError",
        "PluginInputRequired",
        "SchemaMajorMismatchError",
        "SetupIncompleteError",
        "TemplateCoreFieldRedeclaredError",
        "TemplateLoadError",
        "ValidationError",
    }
    assert set(errors.__all__) == expected


def test_all_entries_resolve_to_module_attributes() -> None:
    # Every name in __all__ must be importable from the module.
    for name in errors.__all__:
        assert hasattr(errors, name), f"{name} missing from exlab_wizard.errors"
        assert isinstance(getattr(errors, name), type)
