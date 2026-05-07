"""Page object package for the Phase 16 e2e suite.

Each module wraps the stable ``data-testid`` selectors for one logical
surface so the flow tests do not duplicate ``page.locator(...)`` calls.
"""

from tests.e2e.page_objects.main_page import MainPage
from tests.e2e.page_objects.problems_page import ProblemsPage
from tests.e2e.page_objects.settings_page import SettingsPage
from tests.e2e.page_objects.staging_page import StagingPage
from tests.e2e.page_objects.welcome_page import WelcomePage
from tests.e2e.page_objects.wizard_project_page import WizardProjectPage
from tests.e2e.page_objects.wizard_run_page import WizardRunPage

__all__ = (
    "MainPage",
    "ProblemsPage",
    "SettingsPage",
    "StagingPage",
    "WelcomePage",
    "WizardProjectPage",
    "WizardRunPage",
)
