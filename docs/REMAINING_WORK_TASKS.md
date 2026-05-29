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
  block: `created_by` = OS user, `project` = folder name, `run` = run dir / null). Presence is
  already gated by `_validate_inputs` and the GUI submits no typed extra fields yet, so the
  generator's strict validation adds no new failures for current creations (it stays the backstop).
  New end-to-end test `test_real_readme_generator_writes_frontmatter_and_cache` asserts the
  four-layer front matter + `readme_fields.json`. Full unit+integration suite green (2201 passed).

---

## Phase 2 — Session-control epic (build in order; they compound)

### - [ ] T2 — Event-subscription consumer in the creation flow (shared foundation)
- **Spec:** [§B4](./REMAINING_WORK.md#b4--live-per-session-phase-progress-is-static-the-gui-never-subscribes) · **Category:** B · **Effort:** S–M · **Priority:** High
- **Why:** The create flow is fire-and-await-final-status; nothing consumes
  `controller.subscribe()`, so progress is static (`active_phase=None`). This consumer
  underpins T3/T4/T5/T6.
- **Key files:** `ui/mount.py:58,1457,1536` · `controller/creation.py:401,410-419,1104-1128` · `ui/components/session_progress.py:24-31,91` · `ui/pages/wizard_project.py:224` · `ui/pages/wizard_run.py:229`
- **Acceptance:** the Confirm & Create step advances through the live phases (and the §9.3
  per-plugin sub-row when the host emits `progress`); the consumer is cancelled/cleaned up on
  wizard close; emitted phase strings verified to match the component's `PHASES`.
- **Status:** ⬜ Not started
- **Impl note:** _(pending)_

### - [ ] T3 — Render the Operations modal + toolbar/footer hooks
- **Spec:** [§B1](./REMAINING_WORK.md#b1--operations-modal-is-built-exported-and-rendered-nowhere--highest-impact) · **Category:** B · **Effort:** M · **Priority:** High
- **Why:** `operations_modal` is built/exported but rendered nowhere; no `[Operations…]`
  action exists, so sessions can't be inspected or acted on from the GUI.
- **Key files:** `ui/components/operations_modal.py:77` · `ui/pages/main.py:189-209,347-377` · `ui/mount.py` (`_main`) · `api/routers/operations.py:86-121` (extract shared `SessionStore → row` helper + public accessor)
- **Acceptance:** an `[Operations…]` toolbar button (visible when ≥1 operation in flight)
  opens the modal populated from `deps.controller.session_store`; footer Sync segment shows
  the "N need input" state and opens the same modal.
- **Depends on:** T2 (for auto-refresh).
- **Status:** ⬜ Not started
- **Impl note:** _(pending)_

### - [ ] T4 — Wire Resume / Cancel (+ §9.4 confirm, §9.6 disable rule)
- **Spec:** [§B2](./REMAINING_WORK.md#b2--no-gui-path-to-resume-or-cancel-a-session) · **Category:** B · **Effort:** M · **Priority:** High
- **Key files:** `controller/creation.py:327,337-341,351,377-381,1151-1153` · `ui/mount.py:1036` (dispatch pattern) · `ui/pages/main.py:54,198-200`
- **Acceptance:** modal Resume/Cancel call `controller.resume`/`cancel` in-process; Cancel
  routes through a §9.4 Discard/Keep dialog mapped to `discard_files`; creation buttons
  disabled while a session is non-terminal (single-equipment); 409/`ValueError` surfaced.
- **Depends on:** T3.
- **Status:** ⬜ Not started
- **Impl note:** _(pending)_

### - [ ] T5 — Plugin `INPUT_REQUIRED` escalation dialog (new component)
- **Spec:** [§B3](./REMAINING_WORK.md#b3--plugin-input_required-cannot-be-answered-from-the-gui) · **Category:** B · **Effort:** M · **Priority:** High
- **Why:** When a plugin pauses, the wizard hangs indefinitely with no dialog; only the HTTP
  route can answer.
- **Key files:** `controller/creation.py:865-887` · new `ui/components/` escalation dialog · `ui/mount.py`
- **Acceptance:** a §9.1 dialog renders `pending_input["fields"]` with README widgets +
  reason/plugin header; Submit → `controller.resume(sid, values)`, Cancel → §9.4 confirm →
  `cancel`; escalations strictly sequential (§9.2); handles `failed`/timeout force-close.
- **Depends on:** T2 (detect INPUT_REQUIRED) + T3 (surface).
- **Status:** ⬜ Not started
- **Impl note:** _(pending)_

---

## Phase 3 — Visibility & usability

### - [ ] T6 — Live Problems/audit stream + real counts
- **Spec:** [§B5](./REMAINING_WORK.md#b5--audit--problems-live-stream-not-consumed-by-the-in-process-ui) · **Category:** B · **Effort:** M · **Priority:** Med
- **Key files:** `api/app.py:77-133,266,327-356` · `ui/pages/problems.py:279-281` · `ui/pages/main.py:50-51,432,445-450` · `ui/mount.py:699-721,1579-1588`
- **Acceptance:** live findings counts (tab badge + right-pane summary, single source);
  "Last audit: HH:MM:SS · Next refresh in Ns" instead of `--`; override-and-allow-sync
  dialog (§11.5) reachable; `start_audit_task=True` confirmed in the production app build.
- **Depends on:** T2 (subscription pattern, optional).
- **Status:** ⬜ Not started
- **Impl note:** _(pending)_

### - [ ] T7 — Operators allowlist chip editor
- **Spec:** [§A4](./REMAINING_WORK.md#a4--operators-allowlist-has-backend-enforcement-but-no-editor-ui) · **Category:** A · **Effort:** M · **Priority:** Med
- **Key files:** `ui/pages/settings.py:25,30-32,46,464` · `config/models.py:422-427` · `controller/creation.py:540-547`
- **Acceptance:** an Operators section with a chip editor bound to
  `draft.operators.allowlist`, persisting via the existing draft path; **case-sensitive**, no
  trimming/lowercasing; kept **non-gating** (not added to `_missing_setup_sections`).
- **Builds:** the reusable chip/list widget (shared with T10).
- **Status:** ⬜ Not started
- **Impl note:** _(pending)_

### - [ ] T8 — "Start at login" checkbox wiring
- **Spec:** [§A2](./REMAINING_WORK.md#a2--start-at-login-autostart-checkbox-is-not-bound) · **Category:** A · **Effort:** S · **Priority:** Med
- **Key files:** `ui/pages/settings.py:514` · `tray/dependencies.py:130,840` (add `autostart_is_registered`) · `ui/mount.py:687`
- **Acceptance:** checkbox reflects real `is_registered()` on open; `on_change` toggles
  immediately via `deps.autostart_toggle` and reflects the actual post-op result (reverts on
  failure); sandboxed in tests via `EXLAB_AUTOSTART_ROOT`.
- **Status:** ⬜ Not started
- **Impl note:** _(pending)_

### - [ ] T9 — "Quit ExLab-Wizard now" button (+ `deps` quit hook)
- **Spec:** [§A1](./REMAINING_WORK.md#a1--quit-exlab-wizard-now-button-has-no-handler) · **Category:** A · **Effort:** S · **Priority:** Low–Med
- **Key files:** `ui/pages/settings.py:516` · `tray/main.py:77,80,84,171` · `tray/quit_coordinator.py:82` · `ui/mount.py:358`
- **Acceptance:** a `deps.request_quit` hook exists; button triggers graceful shutdown
  **scheduled non-blocking** (response flushes first) behind a confirm; no-op/disabled when
  the hook is absent (headless/tests).
- **Status:** ⬜ Not started
- **Impl note:** _(pending)_

### - [ ] T10 — `content_scan_extensions` chip editor + reset-to-defaults
- **Spec:** [§A5](./REMAINING_WORK.md#a5--content_scan_extensions-renders-read-only-partial) · **Category:** A · **Effort:** S · **Priority:** Low
- **Key files:** `ui/pages/settings.py:482-484` · `config/models.py:435,464-471`
- **Acceptance:** chip editor bound to `draft.validator.content_scan_extensions` with a
  `[Reset to defaults]` action; entries validated to start with `.` on add.
- **Depends on:** T7 (reuse the chip widget).
- **Status:** ⬜ Not started
- **Impl note:** _(pending)_

### - [ ] T11 — Application-section status labels parity (§7.13)
- **Spec:** [§A3](./REMAINING_WORK.md#a3--application-section-status-indicators-are-hardcoded--missing-713-parity) · **Category:** A · **Effort:** S · **Priority:** Low
- **Key files:** `ui/pages/settings.py:515`
- **Acceptance:** tray-availability label reflects real `pystray` init; window-on-close
  behavior text present.
- **Status:** ⬜ Not started
- **Impl note:** _(pending)_

---

## Phase 4 — Cleanups (anytime)

### - [ ] T12 — Resolve the offline-catalogue version-policy TODO
- **Spec:** [§D1](./REMAINING_WORK.md#d1--offline-catalogue-schema-version-gate-is-stricter-than-the-cache-file-policy-open-spec-question) · **Category:** D · **Effort:** S · **Priority:** Low
- **Key files:** `lims/catalogue.py:93-103`
- **Acceptance:** policy decided (exact vs §11.9.2 major-only) and the spec updated; check
  aligned; `TODO(spec)` removed.
- **Status:** ⬜ Not started
- **Impl note:** _(pending)_

### - [ ] T13 — Delete the stale plugin-registry comment
- **Spec:** [§D2](./REMAINING_WORK.md#d2--stale-pluginsregistrypy-not-yet-committed-comment-documentation-only) · **Category:** D · **Effort:** XS · **Priority:** Trivial
- **Key files:** `plugins/host.py:73-81,115-123,896-905`
- **Acceptance:** comments rewritten to state `registry.py` is committed + wired and
  `_ListBackedRegistry` is test-only.
- **Status:** ⬜ Not started
- **Impl note:** _(pending)_

---

## Shared foundations (build once, reused across tasks)
- **Event-subscription consumer** (T2) → underpins T3 auto-refresh, T4, T5, and T6.
- **Reusable chip/list editor** (first built in T7) → reused by T10.
- **Shared `SessionStore → row` helper + public accessor** (T3) → removes the duplication
  between `api/routers/operations.py` and the in-process modal.

## Progress
1 / 13 complete.
