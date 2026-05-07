"""UI package re-exports (Backend Spec §4.3, Frontend Spec §2).

Public surface:

* :mod:`exlab_wizard.ui.design` -- design tokens (DESIGN.md §07).
* :mod:`exlab_wizard.ui.theme` -- ``register_theme`` + ``build_root_css``.
* :mod:`exlab_wizard.ui.notifications` -- toast / banner / inline helpers.
* :mod:`exlab_wizard.ui.keyboard` -- shortcut registry.
* :mod:`exlab_wizard.ui.components` -- reusable UI primitives.
* :mod:`exlab_wizard.ui.pages` -- page factories.
"""

from exlab_wizard.ui import design, keyboard, notifications, theme

__all__ = (
    "design",
    "keyboard",
    "notifications",
    "theme",
)
