"""Machine-readable catalog of the wizard's UX interactions.

Each :class:`UXInteraction` pins one operator-facing affordance: the
flow it belongs to, the route it lives on, its ``data-testid``, the
widget kind, the action the operator takes, and the expected outcome.

This catalog is the single source of truth for two automated checks in
``test_ux_documentation.py``:

1. **Documentation** -- ``docs/UX_INTERACTIONS.md`` is regenerated from
   this list, so the human-readable interaction reference can never
   drift from the catalog.
2. **Coverage** -- every entry's ``testid`` must appear both in the
   ``exlab_wizard/ui`` source (the affordance really exists) and in a
   ``tests/e2e/test_flow_*.py`` file (an e2e test really drives it).

Add an entry here when you add an operator-facing affordance; the
checks then force you to wire its source + test.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["UX_INTERACTIONS", "UXInteraction"]


@dataclass(frozen=True)
class UXInteraction:
    """One operator-facing affordance in the wizard UI."""

    flow: str
    route: str
    testid: str
    element: str
    action: str
    outcome: str


UX_INTERACTIONS: tuple[UXInteraction, ...] = (
    # -- First-launch / welcome ---------------------------------------------
    UXInteraction(
        flow="First-launch setup",
        route="/welcome",
        testid="welcome-get-started",
        element="button",
        action="Click 'Get started'",
        outcome="Navigates to /settings to begin configuration.",
    ),
    UXInteraction(
        flow="First-launch setup",
        route="/welcome",
        testid="welcome-skip-for-now",
        element="button",
        action="Click 'Skip for now'",
        outcome="Navigates straight to /main, bypassing guided setup.",
    ),
    UXInteraction(
        flow="First-launch setup",
        route="/welcome",
        testid="welcome-autostart-toggle",
        element="toggle",
        action="Toggle 'start at login'",
        outcome="Sets the autostart preference applied on get-started / skip.",
    ),
    # -- Settings: paths ----------------------------------------------------
    UXInteraction(
        flow="Settings",
        route="/settings",
        testid="settings-nav-paths",
        element="nav row",
        action="Click the 'Paths' sidebar row",
        outcome="Shows the Paths section (client-side; edits are preserved).",
    ),
    UXInteraction(
        flow="Settings",
        route="/settings",
        testid="settings-paths-app-root",
        element="input",
        action="Type the data folder (app root)",
        outcome=(
            "Binds config.paths.app_root on the draft; templates/, plugins/ and "
            "data/ are derived and shown as read-only labels."
        ),
    ),
    # -- Settings: LIMS -----------------------------------------------------
    UXInteraction(
        flow="Settings",
        route="/settings",
        testid="settings-nav-lims",
        element="nav row",
        action="Click the 'LIMS' sidebar row",
        outcome="Shows the LIMS section.",
    ),
    UXInteraction(
        flow="Settings",
        route="/settings",
        testid="settings-lims-endpoint",
        element="input",
        action="Type the LIMS endpoint URL",
        outcome="Binds config.lims.endpoint on the draft.",
    ),
    UXInteraction(
        flow="Settings",
        route="/settings",
        testid="settings-lims-email",
        element="input",
        action="Type the operator email",
        outcome="Binds config.lims.email on the draft.",
    ),
    # -- Settings: equipment editor -----------------------------------------
    UXInteraction(
        flow="Equipment",
        route="/settings",
        testid="settings-nav-equipment",
        element="nav row",
        action="Click the 'Equipment List' sidebar row",
        outcome="Shows the equipment list and the add-equipment sub-form.",
    ),
    UXInteraction(
        flow="Equipment",
        route="/settings",
        testid="settings-equipment-id",
        element="input",
        action="Type the equipment ID (^[A-Z][A-Z0-9_]*$)",
        outcome="Provides the EquipmentConfig.id for the new entry.",
    ),
    UXInteraction(
        flow="Equipment",
        route="/settings",
        testid="settings-equipment-label",
        element="input",
        action="Type the equipment label",
        outcome="Provides the EquipmentConfig.label for the new entry.",
    ),
    UXInteraction(
        flow="Equipment",
        route="/settings",
        testid="settings-equipment-nas-root",
        element="input",
        action="Type the equipment NAS root",
        outcome="Provides the EquipmentConfig.nas_root for the new entry.",
    ),
    # rclone.conf migration (Phase 8): the per-equipment SFTP/SMB transport
    # radio + connection fields were removed from the Settings equipment form.
    # A nas-mode device now carries only id/label/nas_root (its data dir is
    # derived from the single app root); the NAS connection is the single nas:
    # remote configured in Settings -> NAS Remote.
    # -- Settings: NAS Remote (rclone.conf migration) ----------------------
    UXInteraction(
        flow="NAS Remote",
        route="/settings",
        testid="settings-nav-nas_remote",
        element="nav row",
        action="Click the 'NAS Remote' sidebar row",
        outcome="Shows the configured rclone remote name, base root, and found/not-found badge.",
    ),
    UXInteraction(
        flow="NAS Remote",
        route="/settings",
        testid="settings-nas-remote-name",
        element="label",
        action="View the configured rclone remote name",
        outcome="Displays the remote name from config.nas.remote (or '(not configured)').",
    ),
    UXInteraction(
        flow="NAS Remote",
        route="/settings",
        testid="settings-nas-test-connection",
        element="button",
        action="Click 'Test connection'",
        outcome="Runs the rclone remote probe and renders the result inline.",
    ),
    UXInteraction(
        flow="Equipment",
        route="/settings",
        testid="settings-equipment-add",
        element="button",
        action="Click 'Add equipment'",
        outcome="Validates and appends an EquipmentConfig to the draft; row appears.",
    ),
    # -- Settings: save (applied live, no restart) --------------------------
    UXInteraction(
        flow="Settings",
        route="/settings",
        testid="settings-save",
        element="button",
        action="Click 'Save all'",
        outcome="Persists config.yaml, applies it to the running components "
        "in-process, and shows a 'Settings saved' toast (no relaunch).",
    ),
    UXInteraction(
        flow="Settings",
        route="/settings",
        testid="settings-discard",
        element="button",
        action="Click 'Discard all'",
        outcome="Drops the in-memory draft edits.",
    ),
    # -- Template manager ---------------------------------------------------
    UXInteraction(
        flow="New template",
        route="/templates",
        testid="template-name",
        element="input",
        action="Type a new template name",
        outcome="Names the template directory to scaffold.",
    ),
    UXInteraction(
        flow="New template",
        route="/templates",
        testid="template-type",
        element="select",
        action="Pick the template type (project / equipment / run)",
        outcome="Sets _exlab_type in the scaffolded copier.yml.",
    ),
    UXInteraction(
        flow="New template",
        route="/templates",
        testid="template-run-scope",
        element="select",
        action="Pick the run scope (experimental / test / both)",
        outcome="Sets _exlab_run_scope when the type is 'run'.",
    ),
    UXInteraction(
        flow="New template",
        route="/templates",
        testid="template-description",
        element="input",
        action="Type the template description",
        outcome="Sets _exlab_description in the scaffolded copier.yml.",
    ),
    UXInteraction(
        flow="New template",
        route="/templates",
        testid="template-create",
        element="button",
        action="Click 'Create template'",
        outcome="Scaffolds a valid Copier template under the templates dir.",
    ),
    UXInteraction(
        flow="New template",
        route="/templates",
        testid="templates-back",
        element="button",
        action="Click 'Back'",
        outcome="Returns to /main.",
    ),
    # -- New Project wizard -------------------------------------------------
    UXInteraction(
        flow="New project",
        route="/wizard/project",
        testid="wizard-project-lims-picker",
        element="select",
        action="Pick a LIMS project from the live LIMS / offline catalogue",
        outcome="Fills the project short_id + name + source from the picked row. "
        "Shown only when a project list could be loaded.",
    ),
    UXInteraction(
        flow="New project",
        route="/wizard/project",
        testid="wizard-project-lims-gate",
        element="button",
        action="Click 'Enter project details manually'",
        outcome="Reveals the manual short-ID / name inputs. Shown only when "
        "neither the live LIMS nor an offline catalogue produced any projects.",
    ),
    UXInteraction(
        flow="New project",
        route="/wizard/project",
        testid="wizard-project-lims-id",
        element="input",
        action="Type the LIMS project short ID (PROJ-NNNN)",
        outcome="Sets the project short_id. Hidden until the manual-entry gate is clicked.",
    ),
    UXInteraction(
        flow="New project",
        route="/wizard/project",
        testid="wizard-project-lims-name",
        element="input",
        action="Type the project name",
        outcome="Sets the LIMS project name on the wizard state. Hidden until "
        "the manual-entry gate is clicked.",
    ),
    UXInteraction(
        flow="New project",
        route="/wizard/project",
        testid="wizard-project-template",
        element="select",
        action="Pick a project template (load from a template)",
        outcome="Selects the Copier template the project is scaffolded from.",
    ),
    UXInteraction(
        flow="New project",
        route="/wizard/project",
        testid="wizard-project-var-sample_id",
        element="dynamic field",
        action="Fill a copier.yml-declared variable (e.g. sample_id)",
        outcome="Binds the value into state.template_variables for Copier render.",
    ),
    UXInteraction(
        flow="New project",
        route="/wizard/project",
        testid="wizard-project-equipment",
        element="select",
        action="Pick the host equipment",
        outcome="Selects the equipment_id for the new project.",
    ),
    UXInteraction(
        flow="New project",
        route="/wizard/project",
        testid="wizard-project-readme-label",
        element="input",
        action="Type the README label",
        outcome="Sets the mandatory core README 'label' field.",
    ),
    UXInteraction(
        flow="New project",
        route="/wizard/project",
        testid="wizard-project-readme-operator",
        element="input",
        action="Type the README operator",
        outcome="Sets the mandatory core README 'operator' field.",
    ),
    UXInteraction(
        flow="New project",
        route="/wizard/project",
        testid="wizard-project-readme-objective",
        element="input",
        action="Type the README objective",
        outcome="Sets the mandatory core README 'objective' field.",
    ),
    UXInteraction(
        flow="New project",
        route="/wizard/project",
        testid="wizard-next",
        element="button",
        action="Click 'Next' on a wizard step",
        outcome="Advances the stepper to the next step.",
    ),
    UXInteraction(
        flow="New project",
        route="/wizard/project",
        testid="wizard-back",
        element="button",
        action="Click 'Back' on a wizard step",
        outcome="Returns the stepper to the previous step. Absent on the first "
        "step, which exits via 'Cancel'.",
    ),
    UXInteraction(
        flow="New project",
        route="/wizard/project",
        testid="wizard-cancel",
        element="button",
        action="Click 'Cancel' on a wizard step",
        outcome="Discards the wizard and returns to /main. Present on every step.",
    ),
    UXInteraction(
        flow="New project",
        route="/wizard/project",
        testid="wizard-submit",
        element="button",
        action="Click 'Create' on the confirm step",
        outcome="Runs controller.create_project; writes the project dir + creation.json.",
    ),
    # -- New Run / Test Run wizard -----------------------------------------
    UXInteraction(
        flow="New run / test run",
        route="/wizard/run, /wizard/test-run",
        testid="wizard-run-project-name",
        element="input",
        action="Type the parent project name",
        outcome="Sets the parent project's folder name (the human-readable LIMS "
        "name used verbatim, §3.2) on the run wizard state.",
    ),
    UXInteraction(
        flow="New run / test run",
        route="/wizard/run, /wizard/test-run",
        testid="wizard-run-equipment",
        element="select",
        action="Pick the host equipment",
        outcome="Selects the equipment_id for the new run.",
    ),
    UXInteraction(
        flow="New run / test run",
        route="/wizard/run, /wizard/test-run",
        testid="wizard-run-template",
        element="select",
        action="Pick a run template (load from a template)",
        outcome="Selects the Copier template the run is scaffolded from.",
    ),
    UXInteraction(
        flow="New run / test run",
        route="/wizard/run, /wizard/test-run",
        testid="wizard-run-var-gain",
        element="dynamic field",
        action="Fill a copier.yml-declared variable (e.g. gain)",
        outcome="Binds the value into state.template_variables for Copier render.",
    ),
    UXInteraction(
        flow="New run / test run",
        route="/wizard/run, /wizard/test-run",
        testid="wizard-run-readme-label",
        element="input",
        action="Type the README label",
        outcome="Sets the mandatory core README 'label' field.",
    ),
    UXInteraction(
        flow="New run / test run",
        route="/wizard/run, /wizard/test-run",
        testid="wizard-run-readme-operator",
        element="input",
        action="Type the README operator",
        outcome="Sets the mandatory core README 'operator' field.",
    ),
    UXInteraction(
        flow="New run / test run",
        route="/wizard/run, /wizard/test-run",
        testid="wizard-run-readme-objective",
        element="input",
        action="Type the README objective",
        outcome="Sets the mandatory core README 'objective' field.",
    ),
    UXInteraction(
        flow="New run / test run",
        route="/wizard/run, /wizard/test-run",
        testid="wizard-run-next",
        element="button",
        action="Click 'Next' on a run-wizard step",
        outcome="Advances the run-wizard stepper to the next step.",
    ),
    UXInteraction(
        flow="New run / test run",
        route="/wizard/run, /wizard/test-run",
        testid="wizard-run-back",
        element="button",
        action="Click 'Back' on a run-wizard step",
        outcome="Returns the run-wizard stepper to the previous step. Absent on "
        "the first step, which exits via 'Cancel'.",
    ),
    UXInteraction(
        flow="New run / test run",
        route="/wizard/run, /wizard/test-run",
        testid="wizard-run-cancel",
        element="button",
        action="Click 'Cancel' on a run-wizard step",
        outcome="Discards the run wizard and returns to /main. Present on every step.",
    ),
    UXInteraction(
        flow="New run / test run",
        route="/wizard/run, /wizard/test-run",
        testid="wizard-run-submit",
        element="button",
        action="Click 'Create run' on the confirm step",
        outcome="Runs controller.create_run; writes the run dir + creation.json.",
    ),
    # -- Add Equipment wizard (Redesign §6) ------------------------------
    UXInteraction(
        flow="Add equipment",
        route="/main",
        testid="toolbar-add-equipment",
        element="button",
        action="Click 'Add Equipment' on the main-window toolbar",
        outcome="Navigates to /wizard/equipment for the 5-step Add-Equipment wizard.",
    ),
    UXInteraction(
        flow="Add equipment",
        route="/wizard/equipment",
        testid="wizard-equipment-id",
        element="input",
        action="Type the equipment ID (^[A-Z][A-Z0-9_]*$)",
        outcome="Sets the canonical equipment id used by paths + sync_mode validation.",
    ),
    UXInteraction(
        flow="Add equipment",
        route="/wizard/equipment",
        testid="wizard-equipment-label",
        element="input",
        action="Type the equipment label",
        outcome="Sets the human-readable equipment label.",
    ),
    UXInteraction(
        flow="Add equipment",
        route="/wizard/equipment",
        testid="wizard-equipment-sync-mode",
        element="radio",
        action="Pick 'nas' or 'stage' sync mode",
        outcome="Swaps the transport sub-form between NAS-direct and stage-push.",
    ),
    UXInteraction(
        flow="Add equipment",
        route="/wizard/equipment",
        testid="wizard-equipment-confirm",
        element="button",
        action="Click 'Confirm' on the review step",
        outcome="Posts the assembled EquipmentConfig via POST /config/equipment.",
    ),
    UXInteraction(
        flow="Add equipment",
        route="/wizard/equipment",
        testid="wizard-equipment-cancel",
        element="button",
        action="Click 'Cancel' on any wizard step",
        outcome="Discards the wizard and returns to /main.",
    ),
    # -- File-explorer context menus (Redesign §4.6 / decision 4A) -------
    UXInteraction(
        flow="File explorer",
        route="/main?view=explorer",
        testid="tree-context-edit-equipment",
        element="menu item",
        action="Right-click an owned-equipment tree node and choose 'Edit equipment…'",
        outcome="Deep-links into Settings → Equipment List with the equipment pre-selected.",
    ),
    UXInteraction(
        flow="File explorer",
        route="/main?view=explorer",
        testid="tree-context-remove-equipment",
        element="menu item",
        action="Right-click an owned-equipment tree node and choose 'Remove…'",
        outcome="Deep-links into Settings → Equipment List with the equipment pre-selected.",
    ),
    UXInteraction(
        flow="File explorer",
        route="/main?view=explorer",
        testid="run-context-force-sync",
        element="menu item",
        action="Right-click a run tree node and choose 'Force sync'",
        outcome="Re-enqueues the run for NAS sync via the operations router.",
    ),
    UXInteraction(
        flow="File explorer",
        route="/main?view=explorer",
        testid="run-context-clear-verified",
        element="menu item",
        action="Right-click a run tree node and choose 'Clear verified'",
        outcome="Clears the run from the local cache after the orchestrator verifies the NAS copy.",
    ),
    UXInteraction(
        flow="File explorer",
        route="/main?view=explorer",
        testid="run-context-view-log",
        element="menu item",
        action="Right-click a run tree node and choose 'View log'",
        outcome="Opens the per-run log viewer.",
    ),
    UXInteraction(
        flow="File explorer",
        route="/main?view=explorer",
        testid="file-context-open-in-os",
        element="menu item",
        action="Right-click a file-list row and choose 'Open in OS'",
        outcome="Asks the OS to open the file in its default application.",
    ),
    UXInteraction(
        flow="File explorer",
        route="/main?view=explorer",
        testid="file-context-copy-path",
        element="menu item",
        action="Right-click a file-list row and choose 'Copy path'",
        outcome="Copies the file's absolute path to the system clipboard.",
    ),
    # -- File explorer: selection / search / density / legend (Phase 4/5) ---
    UXInteraction(
        flow="File explorer",
        route="/main",
        testid="file-list-row",
        element="table row",
        action="Single-click a file row in the centre list",
        outcome="Selects the row (highlight + accent bar) and threads ?file= so "
        "the metadata popover shows the file's sub-card (Phase 4 / Option B).",
    ),
    UXInteraction(
        flow="File explorer",
        route="/main",
        testid="toggle-right-pane",
        element="button",
        action="Click the vertical 'Metadata' tab",
        outcome="Opens / collapses the metadata popover over the Files pane via ?right_pane=.",
    ),
    UXInteraction(
        flow="File explorer",
        route="/main",
        testid="main-search",
        element="input",
        action="Type in the Explorer search box",
        outcome="Filters the tree (debounced ?q=) and shows a result-count / "
        "no-matches pill below the box (OQ-2).",
    ),
    UXInteraction(
        flow="File explorer",
        route="/main",
        testid="files-density-toggle",
        element="button",
        action="Click the Files-header density toggle",
        outcome="Flips the file list between comfortable and compact row padding via ?density= (§4.8).",
    ),
    UXInteraction(
        flow="File explorer",
        route="/main",
        testid="files-legend",
        element="button",
        action="Click the Files-header '?' legend button",
        outcome="Opens a popover listing every sync-status icon and its meaning.",
    ),
)
