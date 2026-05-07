"""Keyboard-shortcut registry (Frontend Spec §3.7).

The bindings are intentionally small and central. Adding a new shortcut is
a deliberate spec change to §3.7 plus a registry entry here; bypassing the
registry to bind directly on a NiceGUI element is a code-review reject.

The registry exposes the canonical macOS and Windows / Linux key combos for
each :class:`Shortcut`. Per-shortcut handlers are looked up at runtime so
the pages don't need to import the registry's binding helper to register.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from exlab_wizard.logging import get_logger

_log = get_logger(__name__)


class Shortcut(StrEnum):
    """Identifiers for the app-level shortcut set (Frontend §3.7)."""

    NEW_PROJECT = "new_project"
    NEW_RUN = "new_run"
    NEW_TEST_RUN = "new_test_run"
    OPEN_SETTINGS = "open_settings"
    REFRESH_TREE = "refresh_tree"
    OPEN_PROBLEMS = "open_problems"
    FOCUS_TREE_SEARCH = "focus_tree_search"
    WIZARD_NEXT = "wizard_next"
    WIZARD_CANCEL = "wizard_cancel"


@dataclass(frozen=True)
class KeyCombo:
    """A modifier-and-key combination."""

    key: str
    cmd: bool = False
    ctrl: bool = False
    shift: bool = False
    alt: bool = False

    def matches(self, *, key: str, cmd: bool, ctrl: bool, shift: bool, alt: bool) -> bool:
        """Return True if a NiceGUI ``KeyEventArguments`` matches this combo."""

        return (
            self.key.lower() == key.lower()
            and self.cmd == cmd
            and self.ctrl == ctrl
            and self.shift == shift
            and self.alt == alt
        )


@dataclass(frozen=True)
class ShortcutBinding:
    """One row in the §3.7 shortcut table."""

    shortcut: Shortcut
    macos: KeyCombo
    other: KeyCombo
    description: str


# Canonical bindings (Frontend §3.7).
_BINDINGS: tuple[ShortcutBinding, ...] = (
    ShortcutBinding(
        shortcut=Shortcut.NEW_PROJECT,
        macos=KeyCombo(key="n", cmd=True),
        other=KeyCombo(key="n", ctrl=True),
        description="Open the New Project Wizard",
    ),
    ShortcutBinding(
        shortcut=Shortcut.NEW_RUN,
        macos=KeyCombo(key="n", cmd=True, shift=True),
        other=KeyCombo(key="n", ctrl=True, shift=True),
        description="Open the New Experimental Run Wizard",
    ),
    ShortcutBinding(
        shortcut=Shortcut.NEW_TEST_RUN,
        macos=KeyCombo(key="t", cmd=True, shift=True),
        other=KeyCombo(key="t", ctrl=True, shift=True),
        description="Open the New Test Run Wizard",
    ),
    ShortcutBinding(
        shortcut=Shortcut.OPEN_SETTINGS,
        macos=KeyCombo(key=",", cmd=True),
        other=KeyCombo(key=",", ctrl=True),
        description="Open the Settings dialog",
    ),
    ShortcutBinding(
        shortcut=Shortcut.REFRESH_TREE,
        macos=KeyCombo(key="r", cmd=True),
        other=KeyCombo(key="r", ctrl=True),
        description="Refresh the tree",
    ),
    ShortcutBinding(
        shortcut=Shortcut.OPEN_PROBLEMS,
        macos=KeyCombo(key="p", cmd=True, shift=True),
        other=KeyCombo(key="p", ctrl=True, shift=True),
        description="Switch right panel to Problems tab",
    ),
    ShortcutBinding(
        shortcut=Shortcut.FOCUS_TREE_SEARCH,
        macos=KeyCombo(key="/"),
        other=KeyCombo(key="/"),
        description="Focus the tree search box",
    ),
    ShortcutBinding(
        shortcut=Shortcut.WIZARD_NEXT,
        macos=KeyCombo(key="enter", cmd=True),
        other=KeyCombo(key="enter", ctrl=True),
        description="Advance to the next wizard step",
    ),
    ShortcutBinding(
        shortcut=Shortcut.WIZARD_CANCEL,
        macos=KeyCombo(key="escape"),
        other=KeyCombo(key="escape"),
        description="Cancel the active wizard step",
    ),
)


@dataclass
class ShortcutRegistry:
    """A populated registry of shortcut handlers.

    Pages instantiate one registry, register handlers for the actions they
    care about, and pass the registry to :func:`bind_global_shortcuts` to
    install a single keyboard listener.
    """

    handlers: dict[Shortcut, Callable[[], None]] = field(default_factory=dict)

    def register(self, shortcut: Shortcut, handler: Callable[[], None]) -> None:
        """Attach a handler. Only one handler per shortcut.

        Raises:
            ValueError: if a handler is already registered for ``shortcut``.
        """

        if shortcut in self.handlers:
            raise ValueError(
                f"shortcut {shortcut.value!r} already has a handler; "
                "only one handler per shortcut is allowed",
            )
        self.handlers[shortcut] = handler

    def dispatch(self, shortcut: Shortcut) -> bool:
        """Invoke the handler for ``shortcut`` if registered.

        Returns ``True`` if a handler ran, ``False`` if the shortcut had no
        registered handler.
        """

        handler = self.handlers.get(shortcut)
        if handler is None:
            return False
        try:
            handler()
        except Exception as exc:
            _log.exception(
                "shortcut_handler_failed",
                extra={"event": "ui.shortcut.failed", "shortcut": shortcut.value},
            )
            raise exc
        return True


def list_bindings() -> tuple[ShortcutBinding, ...]:
    """Return the canonical bindings table (Frontend §3.7)."""

    return _BINDINGS


def get_binding(shortcut: Shortcut) -> ShortcutBinding:
    """Return the :class:`ShortcutBinding` for the given shortcut id.

    Raises:
        KeyError: if the shortcut is not in the canonical table.
    """

    for entry in _BINDINGS:
        if entry.shortcut is shortcut or entry.shortcut == shortcut:
            return entry
    raise KeyError(f"unknown shortcut {shortcut!r}")


def is_macos() -> bool:
    """Return ``True`` when running on macOS (used for combo selection)."""

    return sys.platform == "darwin"


def combo_for_current_os(shortcut: Shortcut) -> KeyCombo:
    """Resolve the :class:`KeyCombo` for the current OS."""

    binding = get_binding(shortcut)
    return binding.macos if is_macos() else binding.other


def resolve(
    *,
    key: str,
    cmd: bool = False,
    ctrl: bool = False,
    shift: bool = False,
    alt: bool = False,
) -> Shortcut | None:
    """Find the shortcut id matching the given key event, if any."""

    for entry in _BINDINGS:
        combo = entry.macos if is_macos() else entry.other
        if combo.matches(key=key, cmd=cmd, ctrl=ctrl, shift=shift, alt=alt):
            return entry.shortcut
    return None


def bind_global_shortcuts(registry: ShortcutRegistry) -> None:
    """Install a NiceGUI keyboard listener for the registry.

    NiceGUI is imported lazily so unit tests can exercise the registry
    surface without spinning up an app.
    """

    from nicegui import ui

    def _handle(event) -> None:  # type: ignore[no-untyped-def]
        action = event.action
        if not getattr(action, "keydown", False):
            return
        modifiers = getattr(event, "modifiers", None)
        cmd = bool(getattr(modifiers, "meta", False))
        ctrl = bool(getattr(modifiers, "ctrl", False))
        shift = bool(getattr(modifiers, "shift", False))
        alt = bool(getattr(modifiers, "alt", False))
        key = getattr(event.key, "name", "") or ""

        shortcut = resolve(key=key, cmd=cmd, ctrl=ctrl, shift=shift, alt=alt)
        if shortcut is None:
            return
        registry.dispatch(shortcut)

    ui.keyboard(on_key=_handle)
    _log.info(
        "global_shortcuts_bound",
        extra={
            "event": "ui.keyboard.bound",
            "count": len(registry.handlers),
        },
    )
