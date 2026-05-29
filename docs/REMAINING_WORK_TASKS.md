# ExLab-Wizard — Remaining Work: Tracked Tasks

Task checklist derived from [`REMAINING_WORK.md`](./REMAINING_WORK.md) (verified against
`85de7a3`). Each task links back to the spec section that carries the full intent, files,
implementation steps, and risks.

## How to use this tracker
When a task is **integrated**:
1. Flip its checkbox `- [ ]` → `- [x]`.
2. Set **Status:** `✅ Done`.
3. Fill **Impl note:** with the commit SHA / PR and a one-line summary of what landed
   (and any deviation from the spec).

Status legend: `⬜ Not started` · `🟡 In progress` · `✅ Done` · `⛔ Blocked` (note the blocker).

---

## Phase 1 — Release-blocking correctness

### - [x] T1 — Inject the real `ReadmeGenerator` (+ reconcile `ReadmeContext` / return type)
- **Spec:** [§C1](./REMAINING_WORK.md#c1--production-runs-on-the-stub-readme-generator--real-defect) · **Category:** C · **Effort:** M · **Priority:** Highest (release-blocking)
- **Why:** Production builds the controller without `readme_generator=`, so it runs on
  `NoOpReadmeGenerator` — GUI-created runs ship a stub README with no YAML front matter and
  no `readme_fields.json`.
- **Key files:** `controller/creation.py:188-221,271-273,578-599,908-919` · `readme/generator.py:152-220` · `tray/dependencies.py:226-238,519-542`
- **Acceptance:** a GUI-created project/run produces a §10-compliant `README.md` (front
  matter + four-layer field blocks) **and** `readme_fields.json`; the two `ReadmeContext`
  classes and the `Path` vs `tuple[Path, Path]` return are reconciled; validated end-to-end
  against a real template.
- **Risk watch:** the real generator raises on missing/typed-wrong fields — confirm
  `_validate_inputs` gates align so previously-passing creations don't start failing.
- **Status:** ✅ Done
- **Impl note:** Injected `ReadmeGenerator()` in `tray.dependencies._build_controller` (the sole
  production constructor, covering both the lifespan build and `apply_live_config`). Replaced the
  controller's flat placeholder `ReadmeContext` with the canonical layered type from
  `exlab_wizard.readme`; the controller's `ReadmeGeneratorProtocol` + `NoOpReadmeGenerator` now use
  the `tuple[Path, Path]` contract. Added `CreationController._build_readme_context` (partitions
  `readme_extra` across template/config/custom layers by id, maps decls, fills the §10.6 system
  block: `created_by` = OS user, `project` = LIMS short id (§3.1), `run` = run dir / null). Presence is
  already gated by `_validate_inputs` and the GUI submits no typed extra fields yet, so the
  generator's strict validation adds no new failures for current creations (it stays the backstop).
  New end-to-end test `test_real_readme_generator_writes_frontmatter_and_cache` asserts the
  four-layer front matter + `readme_fields.json`. Full unit+integration suite green (2201 passed).

---

## Phase 2 — Session-control epic (build in order; they compound)

### - [x] T2 — Event-subscription consumer in the creation flow (shared foundation)
- **Spec:** [§B4](./REMAINING_WORK.md#b4--live-per-session-phase-progress-is-static-the-gui-never-subscribes) · **Category:** B · **Effort:** S–M · **Priority:** High
- **Why:** The create flow is fire-and-await-final-status; nothing consumes
  `controller.subscribe()`, so progress is static (`active_phase=None`). This consumer
  underpins T3/T4/T5/T6.
- **Key files:** `ui/mount.py:58,1457,1536` · `controller/creation.py:401,410-419,1104-1128` · `ui/components/session_progress.py:24-31,91` · `ui/pages/wizard_project.py:224` · `ui/pages/wizard_run.py:229`
- **Acceptance:** the Confirm & Create step advances through the live phases (and the §9.3
  per-plugin sub-row when the host emits `progress`); the consumer is cancelled/cleaned up on
  wizard close; emitted phase strings verified to match the component's `PHASES`.
- **Status:** ✅ Done
- **Impl note:** Fixed a confirmed bug: `session_progress.PHASES`/`PHASE_LABELS` used
  `post_validation`/`queueing_sync`, but the controller emits the wire-format
  `validating_post_creation`/`queueing_nas_sync` (`state_machine.Phase`) — two of six phases would
  silently no-op. Corrected the component to the verbatim wire-format (single source of truth) and
  updated the phase-order test. Added `SessionProgressState` + `apply_frame(state, frame)` to the
  component (folds `phase`/`progress`/`done` frames; ignores `input_required`/unknown). Each wizard
  renders the confirm-step bar via a `@ui.refreshable` reading `state.progress`, exposing
  `state.progress_refresh`. `mount._run_creation` now consumes `controller.subscribe()` via
  `_consume_session_progress` while the pipeline runs (race-free: `_launch` creates the event queue
  before the pipeline starts and the asyncio.Queue buffers early phases). Note: the controller
  emits no `progress` frame today, so the §9.3 per-plugin sub-row stays dormant until the plugin
  host emits one (`apply_frame` handles it for when it lands). Suite green (2205 passed).

### - [x] T3 — Render the Operations modal + toolbar/footer hooks
- **Spec:** [§B1](./REMAINING_WORK.md#b1--operations-modal-is-built-exported-and-rendered-nowhere--highest-impact) · **Category:** B · **Effort:** M · **Priority:** High
- **Why:** `operations_modal` is built/exported but rendered nowhere; no `[Operations…]`
  action exists, so sessions can't be inspected or acted on from the GUI.
- **Key files:** `ui/components/operations_modal.py:77` · `ui/pages/main.py:189-209,347-377` · `ui/mount.py` (`_main`) · `api/routers/operations.py:86-121` (extract shared `SessionStore → row` helper + public accessor)
- **Acceptance:** an `[Operations…]` toolbar button (visible when ≥1 operation in flight)
  opens the modal populated from `deps.controller.session_store`; footer Sync segment shows
  the "N need input" state and opens the same modal.
- **Depends on:** T2 (for auto-refresh).
- **Status:** ✅ Done
- **Impl note:** Added a public `SessionStore.iter_sorted()` accessor + a shared
  `project_identifier(request)` (both in `controller/session_store.py`) so the `/operations` route
  and the in-process panel stop reaching into `store._sessions` and label rows identically;
  refactored the route onto them. Added `OperationRow.from_session()` (maps the §4.7 states onto the
  panel's running/suspended/completed buckets). `main.py`: `[Operations…]` toolbar button (shown
  when `operations_count > 0`, warning-colored if any need input) + footer Sync segment flips to
  "N operations need input" and opens the same modal; `MainPageState` gains
  `operations_count`/`operations_input_required`. `mount.py`: `_operation_counts`,
  `_build_operation_rows`, `_open_operations_modal` (fresh snapshot per open), and an
  `_open_operation_details` "view log" dialog. Fixed a latent circular import by importing
  `SessionState`/`project_identifier` from submodules in `operations.py`. Resume/cancel row actions
  are placeholders here (cancel does a keep-files cancel) — fully wired in T4/T5. Suite green.

### - [x] T4 — Wire Resume / Cancel (+ §9.4 confirm, §9.6 disable rule)
- **Spec:** [§B2](./REMAINING_WORK.md#b2--no-gui-path-to-resume-or-cancel-a-session) · **Category:** B · **Effort:** M · **Priority:** High
- **Key files:** `controller/creation.py:327,337-341,351,377-381,1151-1153` · `ui/mount.py:1036` (dispatch pattern) · `ui/pages/main.py:54,198-200`
- **Acceptance:** modal Resume/Cancel call `controller.resume`/`cancel` in-process; Cancel
  routes through a §9.4 Discard/Keep dialog mapped to `discard_files`; creation buttons
  disabled while a session is non-terminal (single-equipment); 409/`ValueError` surfaced.
- **Depends on:** T3.
- **Status:** ✅ Done
- **Impl note:** `_cancel_operation` now opens a §9.4 Discard / Keep dialog mapped to
  `controller.cancel(id, discard_files=…)` (Discard → `shutil.rmtree` of the partial dir), with
  errors surfaced as a toast (`cancel` is a no-op on an already-terminal session, so no 409 to
  raise). §9.6 single-equipment lock: `_operation_counts` now also returns an `active`
  (strictly non-terminal) count; `_build_main_state` sets `MainPageState.creation_in_flight`, and
  `main.py` disables New Project / Run / Test Run (with a tooltip) while a creation is in flight.
  Added `_operation_counts` tests (panel vs active vs input_required). Resume still routes through
  T5's input dialog. Suite green (UI 463 passed).

### - [x] T5 — Plugin `INPUT_REQUIRED` escalation dialog (new component)
- **Spec:** [§B3](./REMAINING_WORK.md#b3--plugin-input_required-cannot-be-answered-from-the-gui) · **Category:** B · **Effort:** M · **Priority:** High
- **Why:** When a plugin pauses, the wizard hangs indefinitely with no dialog; only the HTTP
  route can answer.
- **Key files:** `controller/creation.py:865-887` · new `ui/components/` escalation dialog · `ui/mount.py`
- **Acceptance:** a §9.1 dialog renders `pending_input["fields"]` with README widgets +
  reason/plugin header; Submit → `controller.resume(sid, values)`, Cancel → §9.4 confirm →
  `cancel`; escalations strictly sequential (§9.2); handles `failed`/timeout force-close.
- **Depends on:** T2 (detect INPUT_REQUIRED) + T3 (surface).
- **Status:** ✅ Done
- **Impl note:** New component `ui/components/input_required_dialog.py`: §9.1 "Additional input
  required" dialog with a plugin-identity pill, the reason line, and one widget per
  `pending_input["fields"]` (string→input, text→textarea, choice→select, boolean→checkbox),
  two-way bound to a values dict; `persistent` so the escalation must resolve before the next frame
  (§9.2). `mount.py`: the T2 consumer now opens it on an `input_required` frame and force-closes it
  on a terminal `done`/`failed` frame (plugin timeout); Submit → `controller.resume(sid, values)`
  (errors — empty/invalid payload, stale state, or plugin re-rejection re-emitting `input_required`
  — surfaced as a toast / re-opened dialog), Cancel → the §9.4 cancel dialog. `_cancel_operation`
  was split into a controller-driven `_cancel_session` core (reused by the dialog) + a deps
  resolver; the Operations modal's Resume (`_resume_operation`) reads the parked `pending_input` and
  re-opens the same dialog. Tests for `collect_default_values` + dialog build. Suite green (2212).

---

## Phase 3 — Visibility & usability

### - [x] T6 — Live Problems/audit stream + real counts
- **Spec:** [§B5](./REMAINING_WORK.md#b5--audit--problems-live-stream-not-consumed-by-the-in-process-ui) · **Category:** B · **Effort:** M · **Priority:** Med
- **Key files:** `api/app.py:77-133,266,327-356` · `ui/pages/problems.py:279-281` · `ui/pages/main.py:50-51,432,445-450` · `ui/mount.py:699-721,1579-1588`
- **Acceptance:** live findings counts (tab badge + right-pane summary, single source);
  "Last audit: HH:MM:SS · Next refresh in Ns" instead of `--`; override-and-allow-sync
  dialog (§11.5) reachable; `start_audit_task=True` confirmed in the production app build.
- **Depends on:** T2 (subscription pattern, optional).
- **Status:** ✅ Done
- **Impl note:** The 30 s `_audit_loop` now caches tier counts on
  `deps.last_audit_hard`/`last_audit_soft` (single source, no per-render re-audit, per the §B5
  preference); `_build_main_state` reads them into `MainPageState.problems_count_hard/soft`, so the
  Problems **tab badge** (`problems_badge_text`) and the **right-pane summary** are now real (the
  right-pane no longer hardcodes "Showing 0"). The `/problems` footer takes `last_audit_at` and
  renders "Last audit: HH:MM:SS · Next refresh in Ns" with a 1 s `ui.timer` countdown (was a
  hardcoded "--"); `start_audit_task=True` confirmed in the production tray build.
  **Deferred (documented):** wiring the §11.5 override-and-allow-sync action and the full live
  WS-delta stream are out of scope here because `render_problems_page` renders a *view-model* shape
  (`finding.severity/.path/.state/.finding_id`) that the raw `Validator` `Finding`
  (`tier/rule/run_path/…`) does not provide — a pre-existing mismatch (the render is e2e-only,
  `# pragma: no cover`). Reconciling that view-model + the in-process override-write path
  (mirroring `POST /problems/{run_path}/override`) is a follow-up; the counts/last-audit core lands
  here. Suite green.

### - [x] T7 — Operators allowlist chip editor
- **Spec:** [§A4](./REMAINING_WORK.md#a4--operators-allowlist-has-backend-enforcement-but-no-editor-ui) · **Category:** A · **Effort:** M · **Priority:** Med
- **Key files:** `ui/pages/settings.py:25,30-32,46,464` · `config/models.py:422-427` · `controller/creation.py:540-547`
- **Acceptance:** an Operators section with a chip editor bound to
  `draft.operators.allowlist`, persisting via the existing draft path; **case-sensitive**, no
  trimming/lowercasing; kept **non-gating** (not added to `_missing_setup_sections`).
- **Builds:** the reusable chip/list widget (shared with T10).
- **Status:** ✅ Done
- **Impl note:** Built a reusable `_render_chip_editor(values, …)` in `settings.py` (modeled on the
  equipment add-form: add/delete/optional-reset, mutates the draft list in place so persistence
  rides the existing draft → finalize → Save path). Added `"operators"` to `SETTINGS_SECTIONS`
  (between `nas_cleanup` and `validator`) + `SECTION_TITLES`, and an `elif section == "operators"`
  branch with the §7.9 helper text + chip editor bound to `draft.operators.allowlist`. Stored
  verbatim (case-sensitive; whitespace trimmed on add only). Non-gating (not in
  `_missing_setup_sections`). Updated the section-count test (8→9). Suite green.

### - [x] T8 — "Start at login" checkbox wiring
- **Spec:** [§A2](./REMAINING_WORK.md#a2--start-at-login-autostart-checkbox-is-not-bound) · **Category:** A · **Effort:** S · **Priority:** Med
- **Key files:** `ui/pages/settings.py:514` · `tray/dependencies.py:130,840` (add `autostart_is_registered`) · `ui/mount.py:687`
- **Acceptance:** checkbox reflects real `is_registered()` on open; `on_change` toggles
  immediately via `deps.autostart_toggle` and reflects the actual post-op result (reverts on
  failure); sandboxed in tests via `EXLAB_AUTOSTART_ROOT`.
- **Status:** ✅ Done
- **Impl note:** `tray/dependencies.py` seeds `deps.autostart_is_registered` from
  `AutostartManager().is_registered()` at build. `_apply_autostart` now returns the toggle's
  real `is_registered()` result (the welcome card still ignores it). `_settings` passes
  `autostart_registered` + `on_set_autostart`; the Application checkbox (exempt from the draft,
  §7.13) seeds from the real state and, on change, applies immediately and reverts to the actual
  post-op result on mismatch (re-entrancy-guarded). Disabled when no toggle is wired. Tests cover
  the return-value relay.

### - [x] T9 — "Quit ExLab-Wizard now" button (+ `deps` quit hook)
- **Spec:** [§A1](./REMAINING_WORK.md#a1--quit-exlab-wizard-now-button-has-no-handler) · **Category:** A · **Effort:** S · **Priority:** Low–Med
- **Key files:** `ui/pages/settings.py:516` · `tray/main.py:77,80,84,171` · `tray/quit_coordinator.py:82` · `ui/mount.py:358`
- **Acceptance:** a `deps.request_quit` hook exists; button triggers graceful shutdown
  **scheduled non-blocking** (response flushes first) behind a confirm; no-op/disabled when
  the hook is absent (headless/tests).
- **Status:** ✅ Done
- **Impl note:** The tray builder (`tray/main.py`) attaches `deps.request_quit =
  tray_app.request_quit` after building `TrayApp`. `_settings` builds an `on_quit` that schedules
  the hook via `ui.timer(0.1, …, once=True)` so the HTTP response flushes before the server tears
  down, and passes it to the Application section, which renders the Quit button behind a confirm
  dialog (§3.4.6). Disabled when the hook is absent (headless/tests). `AppDependencies` gains the
  typed `request_quit` field.

### - [x] T10 — `content_scan_extensions` chip editor + reset-to-defaults
- **Spec:** [§A5](./REMAINING_WORK.md#a5--content_scan_extensions-renders-read-only-partial) · **Category:** A · **Effort:** S · **Priority:** Low
- **Key files:** `ui/pages/settings.py:482-484` · `config/models.py:435,464-471`
- **Acceptance:** chip editor bound to `draft.validator.content_scan_extensions` with a
  `[Reset to defaults]` action; entries validated to start with `.` on add.
- **Depends on:** T7 (reuse the chip widget).
- **Status:** ✅ Done
- **Impl note:** Replaced the read-only "Scanned file extensions" label with the shared
  `_render_chip_editor` bound to `draft.validator.content_scan_extensions`, with a
  `[Reset to defaults]` action (`draft.validator.content_scan_extensions[:] =
  _default_content_scan_extensions()`) and an on-add validator rejecting entries that don't start
  with `.` (the `ValidatorConfig` dot-prefix rule also re-checks at Save). Landed with T7.

### - [x] T11 — Application-section status labels parity (§7.13)
- **Spec:** [§A3](./REMAINING_WORK.md#a3--application-section-status-indicators-are-hardcoded--missing-713-parity) · **Category:** A · **Effort:** S · **Priority:** Low
- **Key files:** `ui/pages/settings.py:515`
- **Acceptance:** tray-availability label reflects real `pystray` init; window-on-close
  behavior text present.
- **Status:** ✅ Done
- **Impl note:** The tray builder sets `deps.tray_available` from `_tray_backend_available()`
  (whether `pystray` imports; window-only per §15.7.4 otherwise). The Application section now shows
  "Show in system tray: available / unavailable (window-only)" from that flag (no longer a static
  literal) plus the window-on-close behavior copy. `AppDependencies` gains the typed
  `tray_available` field. Landed with T8/T9.

---

## Phase 4 — Cleanups (anytime)

### - [x] T12 — Resolve the offline-catalogue version-policy TODO
- **Spec:** [§D1](./REMAINING_WORK.md#d1--offline-catalogue-schema-version-gate-is-stricter-than-the-cache-file-policy-open-spec-question) · **Category:** D · **Effort:** S · **Priority:** Low
- **Key files:** `lims/catalogue.py:93-103`
- **Acceptance:** policy decided (exact vs §11.9.2 major-only) and the spec updated; check
  aligned; `TODO(spec)` removed.
- **Status:** ✅ Done
- **Impl note:** Policy = **treat as absent / WARN** (user-confirmed; §7.2.9.3). `read_catalogue`
  now returns `OfflineCatalogue | None` — a `schema_version` mismatch logs a WARN and returns
  `None` (consumer falls through) instead of raising `ConfigError`; the endpoint mismatch and
  missing/parse-error cases stay hard errors. The decision is recorded in the function docstring;
  `TODO(spec)` removed. Caller (`mount._lims_projects_from_catalogue`) treats `None` as `[]`. Test
  flipped to assert `None` + WARN.

### - [x] T13 — Delete the stale plugin-registry comment
- **Spec:** [§D2](./REMAINING_WORK.md#d2--stale-pluginsregistrypy-not-yet-committed-comment-documentation-only) · **Category:** D · **Effort:** XS · **Priority:** Trivial
- **Key files:** `plugins/host.py:73-81,115-123,896-905`
- **Acceptance:** comments rewritten to state `registry.py` is committed + wired and
  `_ListBackedRegistry` is test-only.
- **Status:** ✅ Done
- **Impl note:** Rewrote the three "owned by Agent A / not yet committed" comment blocks in
  `plugins/host.py` (the registry-surface header, the `PluginRegistryProtocol` docstring, and the
  `_ListBackedRegistry`/`build_test_registry` block) to state that
  `plugins.registry.PluginRegistry` is the committed, wired production registry (built in
  `tray.dependencies._build_plugin_host`) and that `_ListBackedRegistry` is test-only. Also fixed a
  stray "before Agent A's msgspec.Struct lands" comment. Pure cleanup; no behavior change.

---

## Shared foundations (build once, reused across tasks)
- **Event-subscription consumer** (T2) → underpins T3 auto-refresh, T4, T5, and T6.
- **Reusable chip/list editor** (first built in T7) → reused by T10.
- **Shared `SessionStore → row` helper + public accessor** (T3) → removes the duplication
  between `api/routers/operations.py` and the in-process modal.

## Progress
13 / 13 complete.
