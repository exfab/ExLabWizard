# Getting Started

## Overview

On first launch ExLab-Wizard shows a welcome card -- the operator's
entry point into the application. From here the operator either begins
guided setup or skips straight to the main window. This page is an
orientation aid; it is not one of the eight numbered capabilities, and
the guide proper begins with {doc}`01_settings`.

## Walkthrough

1. **Launch ExLab-Wizard.** The tray application shows the welcome card
   on a fresh install, or whenever required configuration is still
   missing.
2. **Choose a start path.** *Get started*
   (`data-testid="welcome-get-started"`) opens the Settings dialog in
   setup-incomplete mode to walk through configuration; *Skip for now*
   (`data-testid="welcome-skip-for-now"`) jumps straight to the main
   window, leaving setup for later.
3. **Set the autostart preference.** The *start at login* toggle
   (`data-testid="welcome-autostart-toggle"`) records whether the tray
   should launch automatically; the preference is applied whichever
   start path is chosen.

## Screenshots

```{image} ../_static/screenshots/00_getting_started/01_initial.png
:alt: First-launch welcome card
:align: center
```

## Related material

- {doc}`01_settings` -- the guided setup that *Get started* opens.
- {doc}`02_browse` -- the main window that *Skip for now* opens.
- Design spec section 02 (User Interaction) -- the capability set this
  guide documents.
