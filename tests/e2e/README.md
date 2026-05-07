# E2E test suite

Driven by Playwright against a real uvicorn server.

## Setup

```
pip install -e .[test]
playwright install chromium
```

The harness never installs browsers itself -- if `playwright install
chromium` has not been run, the entire suite skips.

## Run

```
pytest tests/e2e -q                   # full suite (headless, default)
pytest tests/e2e --headed --slowmo 250 # debug locally
```

The session-scoped `server_url` fixture spawns a real `uvicorn` process
running `exlab_wizard.api.app:create_app` (`--factory`) on a free port,
waits up to 15 s for `/api/v1/health` to respond 200, then yields the
base URL. The session-scoped `browser` fixture launches headless
chromium; the function-scoped `page` fixture builds a fresh context
per test so cookies / local storage do not leak.

## Status of the 15-flow merged plan

| #     | Flow                                  | Status     | Notes                                                           |
| ----- | ------------------------------------- | ---------- | --------------------------------------------------------------- |
| smoke | Server boots, root URL renders        | active     | The e2e harness sanity check (passes when chromium installed).  |
| 01    | First-launch onboarding               | skipped    | Needs `data-testid` on welcome card + autostart toggle.         |
| 02    | Project wizard 7-step happy path      | skipped    | Needs `data-testid` on stepper + per-step inputs.               |
| 03    | Experimental run wizard end-to-end    | skipped    | Needs `data-testid` on run wizard + plugin step.                |
| 04    | Test run wizard end-to-end            | skipped    | Needs `data-testid` on test mode toggle + run wizard.           |
| 05    | Browse view -- open project + session | skipped    | Needs `data-testid` on tree + browse panes.                     |
| 06    | Problems live updates over WebSocket  | skipped    | Needs `data-testid` on problems table.                          |
| 07    | Plugin input-required round trip      | skipped    | Needs `data-testid` on plugin reply form.                       |
| 08    | Settings dialog round-trip            | skipped    | Needs `data-testid` on settings dialog inputs.                  |
| 09    | Orchestrator staging panel + ingest   | skipped    | Needs `data-testid` on staging panel.                           |
| 10    | Schema-mismatch / migration prompt    | skipped    | Needs `data-testid` on the schema-mismatch dialog.              |
| 11    | Crash-recovery prompt on relaunch     | skipped    | Needs `data-testid` on recovery dialog.                         |
| 12    | Quit coordinator drain                | skipped    | Needs `data-testid` on quit-confirm dialog.                     |
| 13    | Keyboard shortcuts + a11y focus       | skipped    | Needs stable focus-trap landmarks + ARIA roles.                 |
| 14    | Tray notifications + in-app toasts    | skipped    | Needs `data-testid` on toast surface.                           |
| 15    | WebSocket reconnect / degraded banner | skipped    | Needs `data-testid` on degraded-mode banner.                    |

`active` flows execute when chromium is installed; `skipped` flows are
collected with `pytest.mark.skip` and their reason is reported. The
suite never fails just because the placeholders are empty -- skip is
green.

## Page objects

The `tests/e2e/page_objects/` package wraps stable selectors so the
flows do not duplicate `page.get_by_test_id(...)` calls. The Phase 16
initial cut ships:

* `MainPage` -- `tree`, `setup_incomplete_banner`, `toolbar_new_project`,
  `toolbar_settings`.
* `WelcomePage` -- `headline`, `autostart_toggle`, `get_started`,
  `skip_for_now`.

Additional page objects for the wizards / dialogs land in the Phase 16
follow-up alongside the `data-testid` retrofit.

## Phase 16 follow-up

The remaining 14 flows require Phase 12's NiceGUI components to carry
`data-testid="..."` attributes for stable selectors. Adding them is
mechanical -- one prop per component -- and a clean follow-up commit.
The follow-up will:

1. Retrofit `data-testid` on every NiceGUI component referenced from
   the page objects above plus the wizards / dialogs.
2. Replace each placeholder body with the real flow.
3. Flip the table entries from `skipped` to `active`.

## Why placeholders are skipped

The skip is intentional: shipping skipped placeholders documents the
plan, prevents the harness from being merged with hidden gaps, and
lets the follow-up land as small reviewable commits (one per flow)
without churn on the harness itself.
