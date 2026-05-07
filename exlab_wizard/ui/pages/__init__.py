"""UI pages package.

Each module exposes a render function plus its state dataclass for unit
testing.
"""

from exlab_wizard.ui.pages import (
    main,
    problems,
    settings,
    welcome,
    wizard_project,
    wizard_run,
)

__all__ = (
    "main",
    "problems",
    "settings",
    "welcome",
    "wizard_project",
    "wizard_run",
)
