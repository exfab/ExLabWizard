"""Unit tests for :mod:`exlab_wizard.ui.keyboard`.

The registry is intentionally small (Frontend §3.7); we assert each
documented binding has the right OS-specific key combo, that the resolver
maps key events back to shortcut ids, and that the ShortcutRegistry
dispatches handlers properly.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from exlab_wizard.ui.keyboard import (
    KeyCombo,
    Shortcut,
    ShortcutRegistry,
    combo_for_current_os,
    get_binding,
    list_bindings,
    resolve,
)


def test_all_documented_shortcuts_in_registry() -> None:
    """Every Frontend §3.7 row has a ShortcutBinding."""

    bindings = {b.shortcut for b in list_bindings()}
    assert bindings == {
        Shortcut.NEW_PROJECT,
        Shortcut.NEW_RUN,
        Shortcut.NEW_TEST_RUN,
        Shortcut.OPEN_SETTINGS,
        Shortcut.REFRESH_TREE,
        Shortcut.OPEN_PROBLEMS,
        Shortcut.FOCUS_TREE_SEARCH,
        Shortcut.WIZARD_NEXT,
        Shortcut.WIZARD_CANCEL,
    }


def test_new_project_combo_macos_and_other() -> None:
    """``Cmd+N`` on macOS, ``Ctrl+N`` elsewhere."""

    binding = get_binding(Shortcut.NEW_PROJECT)
    assert binding.macos == KeyCombo(key="n", cmd=True)
    assert binding.other == KeyCombo(key="n", ctrl=True)


def test_new_run_combo_macos_and_other() -> None:
    """``Cmd+Shift+N`` / ``Ctrl+Shift+N``."""

    binding = get_binding(Shortcut.NEW_RUN)
    assert binding.macos == KeyCombo(key="n", cmd=True, shift=True)
    assert binding.other == KeyCombo(key="n", ctrl=True, shift=True)


def test_new_test_run_combo_macos_and_other() -> None:
    """``Cmd+Shift+T`` / ``Ctrl+Shift+T``."""

    binding = get_binding(Shortcut.NEW_TEST_RUN)
    assert binding.macos == KeyCombo(key="t", cmd=True, shift=True)
    assert binding.other == KeyCombo(key="t", ctrl=True, shift=True)


def test_open_settings_uses_comma_key() -> None:
    """``Cmd+,`` / ``Ctrl+,``."""

    binding = get_binding(Shortcut.OPEN_SETTINGS)
    assert binding.macos.key == ","
    assert binding.other.key == ","


def test_refresh_tree_combo() -> None:
    """``Cmd+R`` / ``Ctrl+R``."""

    binding = get_binding(Shortcut.REFRESH_TREE)
    assert binding.macos == KeyCombo(key="r", cmd=True)
    assert binding.other == KeyCombo(key="r", ctrl=True)


def test_open_problems_combo() -> None:
    """``Cmd+Shift+P`` / ``Ctrl+Shift+P``."""

    binding = get_binding(Shortcut.OPEN_PROBLEMS)
    assert binding.macos == KeyCombo(key="p", cmd=True, shift=True)
    assert binding.other == KeyCombo(key="p", ctrl=True, shift=True)


def test_focus_tree_search_is_unmodified_slash() -> None:
    """``/`` focuses the tree search box."""

    binding = get_binding(Shortcut.FOCUS_TREE_SEARCH)
    assert binding.macos == KeyCombo(key="/")
    assert binding.other == KeyCombo(key="/")


def test_wizard_next_uses_enter_with_modifier() -> None:
    """``Cmd+Enter`` / ``Ctrl+Enter`` advances the wizard."""

    binding = get_binding(Shortcut.WIZARD_NEXT)
    assert binding.macos == KeyCombo(key="enter", cmd=True)
    assert binding.other == KeyCombo(key="enter", ctrl=True)


def test_wizard_cancel_uses_escape() -> None:
    """Esc cancels the wizard."""

    binding = get_binding(Shortcut.WIZARD_CANCEL)
    assert binding.macos == KeyCombo(key="escape")
    assert binding.other == KeyCombo(key="escape")


def test_combo_for_current_os_returns_other_on_linux() -> None:
    """On non-darwin we return ``other``."""

    with patch("exlab_wizard.ui.keyboard.is_macos", return_value=False):
        combo = combo_for_current_os(Shortcut.NEW_PROJECT)
    assert combo == KeyCombo(key="n", ctrl=True)


def test_combo_for_current_os_returns_macos_on_darwin() -> None:
    """On darwin we return ``macos``."""

    with patch("exlab_wizard.ui.keyboard.is_macos", return_value=True):
        combo = combo_for_current_os(Shortcut.NEW_PROJECT)
    assert combo == KeyCombo(key="n", cmd=True)


def test_resolve_finds_known_combo_on_linux() -> None:
    """Resolver returns the right shortcut id on non-darwin."""

    with patch("exlab_wizard.ui.keyboard.is_macos", return_value=False):
        sid = resolve(key="n", ctrl=True)
    assert sid is Shortcut.NEW_PROJECT


def test_resolve_returns_none_for_unknown_combo() -> None:
    """Unknown combos resolve to ``None``."""

    assert resolve(key="z", cmd=True, ctrl=True, shift=True, alt=True) is None


def test_get_binding_raises_for_unknown() -> None:
    """``get_binding`` raises ``KeyError`` for an unknown shortcut id."""

    with pytest.raises(KeyError):
        # Force a bogus enum value via a synthetic StrEnum lookup; we just
        # bypass the type check with a raw string for the purposes of this
        # test.
        get_binding("not_a_real_shortcut")  # type: ignore[arg-type]


def test_registry_register_and_dispatch() -> None:
    """``ShortcutRegistry`` dispatches the registered handler exactly once."""

    registry = ShortcutRegistry()
    calls: list[str] = []
    registry.register(Shortcut.NEW_PROJECT, lambda: calls.append("p"))
    assert registry.dispatch(Shortcut.NEW_PROJECT)
    assert calls == ["p"]


def test_registry_dispatch_returns_false_when_unbound() -> None:
    """Dispatching an unbound shortcut returns ``False`` and runs no handler."""

    registry = ShortcutRegistry()
    assert registry.dispatch(Shortcut.NEW_PROJECT) is False


def test_registry_double_register_raises() -> None:
    """Two handlers per shortcut is forbidden."""

    registry = ShortcutRegistry()
    registry.register(Shortcut.NEW_PROJECT, lambda: None)
    with pytest.raises(ValueError):
        registry.register(Shortcut.NEW_PROJECT, lambda: None)


def test_keycombo_matches_returns_true_for_same_modifiers() -> None:
    """``KeyCombo.matches`` is exact-match across modifiers and key."""

    combo = KeyCombo(key="n", ctrl=True, shift=True)
    assert combo.matches(key="n", cmd=False, ctrl=True, shift=True, alt=False)
    assert not combo.matches(key="n", cmd=True, ctrl=False, shift=True, alt=False)


def test_descriptions_match_spec() -> None:
    """Each binding has a non-empty description (Frontend §3.7)."""

    for binding in list_bindings():
        assert binding.description, binding.shortcut
