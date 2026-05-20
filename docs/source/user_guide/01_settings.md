# 3.6 Configure Equipment, Paths, and Integrations

## Capability summary

The Settings dialog is the only operator-facing surface for editing
`config.yaml`. It groups the editable surface into nine sections,
including paths (templates, plugins, local data root, NAS sync root),
equipment registry, LIMS endpoint, orchestrator toggle and staging
root, and notification preferences. On save, validation runs first --
paths must be resolvable, the DB connection string must parse,
equipment IDs must be unique and filesystem-safe -- and then the
config is written via `ruamel.yaml` to preserve comments and key
order. See section 02 §3.6 for the authoritative contract and section
09 of the design spec for the configuration-file schema.

## Walkthrough

1. **Open Settings.** From the toolbar, click the Settings button
   (`data-testid="toolbar-settings"`).
2. **Navigate sections.** The left rail lists each section
   (`data-testid="settings-nav-<section>"`). The body
   (`data-testid="settings-section-<section>"`) updates as the
   operator selects.
3. **Edit, then save.** The Save button
   (`data-testid="settings-save"`) runs validation; on success the
   dialog renders the saved-marker (`data-testid="settings-saved"`).
   The Discard button (`data-testid="settings-discard"`) abandons
   pending changes.

## Add Equipment wizard

The Settings dialog edits equipment that already exists; registering a
**new** device uses the dedicated Add-Equipment wizard, opened from the
main-window toolbar's *Add Equipment* button
(`data-testid="toolbar-add-equipment"`). The wizard walks five steps --
identity, paths, sync mode, completeness signal, and a review step --
then posts the assembled `EquipmentConfig` to the configuration router.
Editing or removing a registered device stays in the Settings dialog's
Equipment List section, which the file-explorer tree context menus
deep-link into (Redesign decision 4A).

1. **Open the wizard.** Click *Add Equipment* on the main-window
   toolbar to navigate to `/wizard/equipment`.
2. **Step through the wizard.** Supply the equipment identity
   (`data-testid="wizard-equipment-id"`), the local and NAS paths, the
   sync mode (`nas` or `stage`), and the completeness signal
   (`sentinel_file` or `manifest`).
3. **Confirm.** The Confirm button on the review step
   (`data-testid="wizard-equipment-confirm"`) registers the device and
   returns to the main window.

## Screenshots

```{image} ../_static/screenshots/01_settings/01_initial.png
:alt: Settings dialog with the Paths section active
:align: center
```

```{image} ../_static/screenshots/01_settings/02_add_equipment.png
:alt: Add-Equipment wizard, identity step
:align: center
```

## Related material

- Design spec section 09 (Configuration File) -- the authoritative
  `config.yaml` schema.
- Design spec section 06 §6.2 (Plugin Registry) -- the plugin
  directory configured here.
- Design spec section 07 (Sync and Database Integration) -- the LIMS
  endpoint and NAS sync root configured here.
