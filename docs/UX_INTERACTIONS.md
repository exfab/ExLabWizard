# UX Interaction Reference

<!-- GENERATED FILE -- do not edit by hand.
     Regenerated from tests/e2e/ux_catalog.py by
     tests/e2e/test_ux_documentation.py. Edit the catalog and re-run
     the test suite to update this file. -->

Every operator-facing affordance in the ExLab-Wizard UI, grouped by
flow. Each row is verified by the e2e suite: the `data-testid` exists
in the `exlab_wizard/ui` source and is driven by a `tests/e2e`
flow test.


## First-launch setup

| Route | Test ID | Element | Action | Outcome |
|---|---|---|---|---|
| `/welcome` | `welcome-get-started` | button | Click 'Get started' | Navigates to /settings to begin configuration. |
| `/welcome` | `welcome-skip-for-now` | button | Click 'Skip for now' | Navigates straight to /main, bypassing guided setup. |
| `/welcome` | `welcome-autostart-toggle` | toggle | Toggle 'start at login' | Sets the autostart preference applied on get-started / skip. |
| `/restart-required` | `restart-required` | screen | Observe the restart-required gate | Terminal screen instructing the operator to relaunch the tray. |

## Settings

| Route | Test ID | Element | Action | Outcome |
|---|---|---|---|---|
| `/settings` | `settings-nav-paths` | nav row | Click the 'Paths' sidebar row | Shows the Paths section (client-side; edits are preserved). |
| `/settings` | `settings-paths-templates` | input | Type the templates directory | Binds config.paths.templates_dir on the draft. |
| `/settings` | `settings-paths-plugin` | input | Type the plugin directory | Binds config.paths.plugin_dir on the draft. |
| `/settings` | `settings-paths-local-root` | input | Type the local data root | Binds config.paths.local_root on the draft. |
| `/settings` | `settings-nav-lims` | nav row | Click the 'LIMS' sidebar row | Shows the LIMS section. |
| `/settings` | `settings-lims-endpoint` | input | Type the LIMS endpoint URL | Binds config.lims.endpoint on the draft. |
| `/settings` | `settings-lims-email` | input | Type the operator email | Binds config.lims.email on the draft. |
| `/settings` | `settings-save` | button | Click 'Save all' | Persists config.yaml and routes to the restart-required gate. |
| `/settings` | `settings-discard` | button | Click 'Discard all' | Drops the in-memory draft edits. |

## Equipment

| Route | Test ID | Element | Action | Outcome |
|---|---|---|---|---|
| `/settings` | `settings-nav-equipment` | nav row | Click the 'Equipment List' sidebar row | Shows the equipment list and the add-equipment sub-form. |
| `/settings` | `settings-equipment-id` | input | Type the equipment ID (^[A-Z][A-Z0-9_]*$) | Provides the EquipmentConfig.id for the new entry. |
| `/settings` | `settings-equipment-label` | input | Type the equipment label | Provides the EquipmentConfig.label for the new entry. |
| `/settings` | `settings-equipment-local-root` | input | Type the equipment local root | Provides the EquipmentConfig.local_root for the new entry. |
| `/settings` | `settings-equipment-nas-root` | input | Type the equipment NAS root | Provides the EquipmentConfig.nas_root for the new entry. |
| `/settings` | `settings-equipment-sentinel` | input | Type the sentinel filename | Sets the sentinel_file completeness signal filename. |
| `/settings` | `settings-equipment-rclone-remote` | input | Type the rclone remote | Sets the rclone transport remote for the new entry. |
| `/settings` | `settings-equipment-rclone-path` | input | Type the rclone remote path | Sets the rclone transport remote path for the new entry. |
| `/settings` | `settings-equipment-add` | button | Click 'Add equipment' | Validates and appends an EquipmentConfig to the draft; row appears. |

## New template

| Route | Test ID | Element | Action | Outcome |
|---|---|---|---|---|
| `/templates` | `template-name` | input | Type a new template name | Names the template directory to scaffold. |
| `/templates` | `template-type` | select | Pick the template type (project / equipment / run) | Sets _exlab_type in the scaffolded copier.yml. |
| `/templates` | `template-run-scope` | select | Pick the run scope (experimental / test / both) | Sets _exlab_run_scope when the type is 'run'. |
| `/templates` | `template-description` | input | Type the template description | Sets _exlab_description in the scaffolded copier.yml. |
| `/templates` | `template-create` | button | Click 'Create template' | Scaffolds a valid Copier template under the templates dir. |
| `/templates` | `templates-back` | button | Click 'Back' | Returns to /main. |

## New project

| Route | Test ID | Element | Action | Outcome |
|---|---|---|---|---|
| `/wizard/project` | `wizard-project-lims-id` | input | Type the LIMS project short ID (PROJ-NNNN) | Sets the project short_id on the wizard state. |
| `/wizard/project` | `wizard-project-lims-name` | input | Type the project name | Sets the LIMS project name on the wizard state. |
| `/wizard/project` | `wizard-project-template` | select | Pick a project template (load from a template) | Selects the Copier template the project is scaffolded from. |
| `/wizard/project` | `wizard-project-equipment` | select | Pick the host equipment | Selects the equipment_id for the new project. |
| `/wizard/project` | `wizard-project-readme-label` | input | Type the README label | Sets the mandatory core README 'label' field. |
| `/wizard/project` | `wizard-project-readme-operator` | input | Type the README operator | Sets the mandatory core README 'operator' field. |
| `/wizard/project` | `wizard-project-readme-objective` | input | Type the README objective | Sets the mandatory core README 'objective' field. |
| `/wizard/project` | `wizard-next` | button | Click 'Next' on a wizard step | Advances the stepper to the next step. |
| `/wizard/project` | `wizard-back` | button | Click 'Back' on a wizard step | Returns the stepper to the previous step. |
| `/wizard/project` | `wizard-submit` | button | Click 'Create' on the confirm step | Runs controller.create_project; writes the project dir + creation.json. |

## New run / test run

| Route | Test ID | Element | Action | Outcome |
|---|---|---|---|---|
| `/wizard/run, /wizard/test-run` | `wizard-run-project-id` | input | Type the parent project short ID | Sets the project_short_id on the run wizard state. |
| `/wizard/run, /wizard/test-run` | `wizard-run-equipment` | select | Pick the host equipment | Selects the equipment_id for the new run. |
| `/wizard/run, /wizard/test-run` | `wizard-run-template` | select | Pick a run template (load from a template) | Selects the Copier template the run is scaffolded from. |
| `/wizard/run, /wizard/test-run` | `wizard-run-readme-label` | input | Type the README label | Sets the mandatory core README 'label' field. |
| `/wizard/run, /wizard/test-run` | `wizard-run-readme-operator` | input | Type the README operator | Sets the mandatory core README 'operator' field. |
| `/wizard/run, /wizard/test-run` | `wizard-run-readme-objective` | input | Type the README objective | Sets the mandatory core README 'objective' field. |
| `/wizard/run, /wizard/test-run` | `wizard-run-next` | button | Click 'Next' on a run-wizard step | Advances the run-wizard stepper to the next step. |
| `/wizard/run, /wizard/test-run` | `wizard-run-back` | button | Click 'Back' on a run-wizard step | Returns the run-wizard stepper to the previous step. |
| `/wizard/run, /wizard/test-run` | `wizard-run-submit` | button | Click 'Create run' on the confirm step | Runs controller.create_run; writes the run dir + creation.json. |
