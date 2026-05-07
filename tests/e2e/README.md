# E2E test suite

Driven by Playwright against a real uvicorn server.

## Setup

```
pip install -e .[test]
playwright install chromium
```

The harness never installs browsers itself. It probes a small set of
locations to find a chromium executable; if none can be launched the
entire suite skips with a precise reason.

Browser discovery (in priority order):

1. `EXLAB_E2E_CHROMIUM` env var -- explicit path to the executable.
2. `PLAYWRIGHT_BROWSERS_PATH/chromium-*/chrome-linux/chrome` (the
   playwright-managed cache).
3. `/opt/pw-browsers/chromium-*/chrome-linux/chrome` (sandbox /
   prebuilt-image default).
4. `~/.cache/ms-playwright/chromium-*/chrome-linux/chrome` (default
   playwright cache).

If `playwright install chromium` is blocked by your network
allowlist, set `PLAYWRIGHT_BROWSERS_PATH` to a directory containing a
prebuilt chromium.

## Run

```
pytest tests/e2e -q                   # full suite (headless, default)
pytest tests/e2e --headed --slowmo 250 # debug locally
```

The session-scoped `server_url` fixture spawns a real `uvicorn` process
running `tests.e2e._test_app:create_app_factory` (`--factory`) on a
free port. The factory wraps `exlab_wizard.api.create_app` and mounts
NiceGUI test pages on `/`, `/main`, `/wizard/project`, `/wizard/run`,
`/wizard/test-run`, `/settings`, `/problems`, `/staging`,
`/plugin-input`, `/notifications`, `/keyboard`, and `/reconnect`. The
fixture waits up to 30 s for `/api/v1/health` to respond 200, then
yields the base URL. The session-scoped `browser` fixture launches
headless chromium; the function-scoped `page` fixture builds a fresh
context per test so cookies / local storage do not leak.

## Status of the 15-flow merged plan

| #     | Flow                                  | Status     | Notes                                                           |
| ----- | ------------------------------------- | ---------- | --------------------------------------------------------------- |
| smoke | Server boots, root URL renders        | active     | The e2e harness sanity check.                                   |
| 01    | First-launch onboarding               | active     | Welcome card -> autostart toggle -> settings -> READY.          |
| 02    | Project wizard 7-step happy path      | active     | Walks every step; asserts the success card.                     |
| 03    | Experimental run wizard end-to-end    | active     | Asserts EXPERIMENTAL mode badge throughout the 6 steps.         |
| 04    | Test run wizard end-to-end            | active     | Asserts TEST mode badge survives back-navigation.               |
| 05    | Browse view -- open project + session | active     | Tree + toolbar + tabs all render with seeded hierarchy.         |
| 06    | Problems live updates over WebSocket  | active     | Override flips state to `Override active`; revoke flips back.   |
| 07    | Plugin input-required round trip      | active     | Submit returns operator value embedded in the resume URL.       |
| 08    | Settings dialog round-trip            | active     | Walks all 9 sections; saves paths + verifies saved marker.      |
| 09    | Orchestrator staging panel + ingest   | active     | Force-sync advances row state to `sync_queued`.                 |
| 10    | Schema-mismatch / migration prompt    | active     | Seeded finding renders with the right rule_class + state.       |
| 11    | Crash-recovery prompt on relaunch     | active     | Orphan finding renders with the right path.                     |
| 12    | Quit coordinator drain                | skipped    | Tray subprocess + pywebview quit handshake; out of e2e scope.   |
| 13    | Keyboard shortcuts + a11y focus       | active     | Cmd/Ctrl+N + Esc each flip the data-action marker.              |
| 14    | Tray notifications + in-app toasts    | active     | Each of the 5 BannerId variants renders its data-testid.        |
| 15    | WebSocket reconnect / degraded banner | active     | Reconnecting banner renders with the right data-testid.         |

`active` flows execute when chromium is installed; `skipped` flows are
collected with `pytest.mark.skip` and their reason is reported. Flow 12
is intentionally skipped: it exercises tray + window subprocesses that
sit outside the e2e surface; the behaviour is fully covered by the
Phase 13 unit tests and `tests/integration/test_tray_lifecycle.py`.

## Page objects

The `tests/e2e/page_objects/` package wraps stable selectors so the
flows do not duplicate `page.locator(...)` calls:

* `MainPage` -- tree, toolbar (new project / new run / new test run /
  settings / refresh), search box, tabs, setup-incomplete banner.
* `WelcomePage` -- card, headline, autostart toggle, get-started, skip.
* `WizardProjectPage` -- card, stepper, per-step containers, back /
  next / submit buttons, success card.
* `WizardRunPage` -- stepper, title, mode badges, per-step containers,
  back / next / submit buttons, success card.
* `SettingsPage` -- dialog, incomplete banner, sidebar nav rows, body
  containers, paths inputs, equipment inputs, save / discard buttons.
* `ProblemsPage` -- table, empty state, per-row state label, override
  / revoke buttons.
* `StagingPage` -- dock, per-row force-sync / clear / view-log
  buttons, clear-verified toolbar action.

## Test app routes (`tests/e2e/_test_app.py`)

The test surface is a thin NiceGUI wrapper around the production page
factories with stable test routes. Each route forwards to the existing
`exlab_wizard.ui.pages.*` `render_*` functions; the only divergence
from production is that the wizards skip the `ui.dialog` wrapper so
headless chromium can interact with the elements directly (a Quasar
modal backdrop intercepts pointer events that Playwright cannot
bypass without significant per-locator boilerplate).

The tests do **not** drive a real ``CreationController`` /
``Validator`` / ``CacheWriter`` -- those surfaces are exercised by
the integration suite. The e2e suite focuses on UI interactions,
state-machine invariants surfaced via the DOM (mode badge stays TEST
across navigation, override flips state, etc.), and ``data-testid``
contract stability.

## Adding a new flow

1. Decide whether the flow needs new ``data-testid`` attributes; add
   them via ``.props('data-testid="..."')`` on the relevant NiceGUI
   element in ``exlab_wizard/ui/{pages,components}/``. Use a stable
   ``kebab-case-by-section`` naming convention (the existing IDs are
   the reference set).
2. Add a route to ``tests/e2e/_test_app.py`` that drives the page
   factory with whatever fixtures the flow needs.
3. Extend the relevant page object in ``tests/e2e/page_objects/`` with
   any new locators.
4. Write the flow under ``tests/e2e/test_flow_NN_*.py`` -- one
   ``test_flow_NN_*`` function per file (parameterise rather than add
   sibling tests, the way Flow 14 does for the 5 BannerId variants).
5. Run ``pytest tests/e2e -q`` and confirm green within the
   per-test 30 s default Playwright timeout.
