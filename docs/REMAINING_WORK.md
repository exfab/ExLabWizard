# ExLab-Wizard — Remaining Work Specification

**Verified against:** branch `fix/debug-with-playwright`, HEAD `85de7a3 feat: live config reload + opt-in staging_root`.
All `file:line` references below were read from the current tree. Each item states the
feature **intent** (with design-spec citation), the **current behavior / gap**, **what
implementing it entails**, and the **issues / risks**.

> **Tracked checklist:** [`REMAINING_WORK_TASKS.md`](./REMAINING_WORK_TASKS.md) — tick each
> task and add an implementation note as it is integrated.

---

## 0. Context

### 0.1 Architecture (read first)
The GUI is **NiceGUI mounted in-process on the FastAPI app**. The UI does **not** call the
HTTP API — it calls backend objects directly off a dependency container `deps`
(`deps.controller`, `deps.nas_sync`, `deps.lims_client`, `deps.keyring_store`,
`deps.equipment_probe`, `deps.autostart_toggle`, `deps.audit_channel`, …). The `@router.*`
HTTP routes under `src/exlab_wizard/api/` are a **parallel surface consumed only by the
e2e/Playwright tests**. So "a route has no UI caller" is normal; an item is only a real gap
when the **underlying capability has no path in the in-process UI** either. Every item below
meets that stricter bar.

### 0.2 What the recent edit changed (and didn't)
`85de7a3` introduced **live config reload** (`apply_live_config` in `tray/dependencies.py`)
plus an opt-in `orchestrator.staging_root`. Practical consequence for this backlog: settings
saved from the dialog now apply **without an app restart**, so the editors proposed below
(A2/A4/A5) take effect immediately once wired. The edit did **not** wire any of the inert
Application-section controls, the operators editor, or any of the §B/§C items.

### 0.3 Healthy baseline (not stubs)
No `raise NotImplementedError` exists in the package; no bare `pass`/`...` bodies outside
`typing.Protocol`; the validator's unresolved-placeholder detection is a **shipped feature**.
The HTTP routers, `operations_modal`, and `session_progress` components are all built and
unit-tested — the missing work is mostly **mount-layer wiring**.

---

## A. UI controls that exist but are not functional

### A1 — "Quit ExLab-Wizard now" button has no handler
- **Where:** `src/exlab_wizard/ui/pages/settings.py:516` — `ui.button("Quit ExLab-Wizard now").props("flat")`, no `on_click`.
- **Intent:** Frontend Spec **§7.13** (`design_specs/ExLab-Wizard_Frontend_Spec.md:1052`): an in-window button that initiates the same graceful shutdown as the tray "Quit", for when the operator can't find the tray icon. Graceful-shutdown protocol: Backend **§4.3.2** (`tray/quit_coordinator.py`).
- **Current behavior / gap:** Renders but does nothing. The shutdown machinery exists but is **tray-process-local and not on `deps`**: `QuitCoordinator.quit()` (`tray/quit_coordinator.py:82`) runs the drain/force-quit protocol and `TrayApp.request_quit()` (`tray/main.py:77`) is the sync entry the pystray menu uses (`tray/main.py:109`), but `tray/dependencies.py` never attaches any quit callback to `deps` (it sets `deps.autostart_toggle` at `:130` and nothing quit-related; the `QuitCoordinator` is built later at `tray/main.py:171`).
- **What implementing it entails:**
  1. Expose a quit hook on `deps`: in `tray/main.py` after the coordinator/app are built (~`:171`–`:183`), set e.g. `deps.request_quit = tray_app.request_quit` (already handles running-loop vs no-loop, `tray/main.py:80`/`:84`).
  2. Add an `on_quit` param to `render_settings_page(...)` and thread it to the Application section, mirroring how the LIMS credential handlers are plumbed.
  3. In `ui/mount.py` `_settings()` (~`:358`) pass `on_quit=getattr(deps, "request_quit", None)` and wire the button with a `None`-guard; route the confirm through `QuitCoordinator.on_force_quit_prompt` (a NiceGUI confirm dialog) per §3.4.6.
- **Issues / risks:** Calling `request_quit` **synchronously inside a NiceGUI handler kills the server serving the page** — schedule it (fire-and-forget) so the HTTP response flushes first; avoid deadlocking the event loop the window runs on. Disable/no-op the button in headless/test fixtures where `deps.request_quit` is absent.

### A2 — "Start at login" autostart checkbox is not bound
- **Where:** `src/exlab_wizard/ui/pages/settings.py:514` — `ui.checkbox("Start ExLab-Wizard at login")` with no initial value and no `on_change`. The comment at `:511-513` says wiring it "is a follow-up."
- **Intent:** Frontend Spec **§7.13** (`Frontend_Spec.md:1048`) + Distribution **§15.7** (`design_spec_sections/15_Distribution.md:150,161,167`): a toggle that *immediately* registers/unregisters the platform autostart entry (LaunchAgent / systemd / Run-key), reflecting and reversible from Settings → Application.
- **Current behavior / gap:** Renders inert; doesn't reflect real registration state. The capability is fully present and already used by the welcome card: `deps.autostart_toggle` (`tray/dependencies.py:130`, factory `:840`) calls `AutostartManager.register()/unregister()` and returns `is_registered()`; the welcome flow calls it via `_apply_autostart(deps, enabled)` (`ui/mount.py:687`). Same surface as the e2e-only `POST /setup/autostart` (`api/setup.py:289`).
- **What implementing it entails:**
  1. This section is **exempt from the draft/Save model** (§7.13 — applied immediately), so do not bind to the draft.
  2. Add `deps.autostart_is_registered: bool` at build time (`tray/dependencies.py`, next to `autostart_toggle`) so the page can seed the checkbox without importing `tray/`.
  3. Add params to `render_settings_page` (`autostart_present`, `on_set_autostart`); in `ui/mount.py` `_settings()` seed the value and wire `on_change` to reuse `_apply_autostart` (`mount.py:687`, already `None`-guards and logs). Reflect the **actual post-op `is_registered()`** result, not the optimistic checkbox state.
- **Issues / risks:** `register()/unregister()` touch the real per-user filesystem; sandbox in tests via `EXLAB_AUTOSTART_ROOT` (`tray/autostart.py:80`) or the toggle stub. A failed registration must visibly revert the checkbox. Default-on applies only to the welcome card; here the initial state mirrors reality.

### A3 — Application-section status indicators are hardcoded / missing (§7.13 parity)
- **Where:** `src/exlab_wizard/ui/pages/settings.py:515` — `ui.label("Show in system tray: available")` is a **static literal** that doesn't reflect real `pystray` availability; there is no "Window behavior on close" text and no `[Refresh]` action.
- **Intent:** Frontend Spec **§7.13** specifies the Application section also surfaces real tray availability and window-on-close behavior.
- **Current behavior / gap:** The "available" string is always shown regardless of whether a tray backend loaded (on Linux without a tray, the app runs window-only per §15.7.4). Missing the close-behavior text.
- **What implementing it entails:** seed the tray-availability label from a `deps` flag derived from whether `pystray` initialized; add the close-behavior copy. Low effort; bundle with A1/A2.
- **Issues / risks:** low; cosmetic correctness.

### A4 — Operators allowlist has backend enforcement but **no editor UI**
- **Where:** omitted from `SETTINGS_SECTIONS` at `src/exlab_wizard/ui/pages/settings.py:30-32`; also absent from `_render_section_body`. Setup routing skips it in `ui/mount.py` (operators intentionally omitted).
- **Intent:** Frontend Spec **§7.9** (`Frontend_Spec.md:1003-1007`): an Operators section backing `operators.allowlist` — a **chip input** (one chip per username, `[+ Add]`, per-chip delete) with helper text: *"If empty (default), the operator field accepts any value; if non-empty, the wizard renders a dropdown of these values and rejects free-text."* Config schema **§9** (`design_spec_sections/09_Configuration_File.md:116-123`); resolved Design-Spec Open-Q#11 (`ExLab-Wizard_Design_Spec.md:180`); creation-time gate in User Interaction **§2** (`02_User_Interaction.md:53,162`).
- **Current behavior / gap:** Backend is correct and live: `OperatorsConfig.allowlist` (`config/models.py:422-427`) on `Config.operators` (`:565`); `controller/creation.py:540-547` rejects an operator absent from a **non-empty** allowlist. But with no GUI to populate it, the list stays `[]` and the gate is a permanent no-op for GUI users. The wizard's "dropdown vs free-text" behavior (Open-Q#11 resolution) is also not implemented on the creation wizard.
- **What implementing it entails:**
  1. Add `"operators"` to `SETTINGS_SECTIONS` (`settings.py:25`) and a `SECTION_TITLES` entry (`:46`).
  2. Add an `elif section == "operators":` branch in `_render_section_body` (~`:464`) with a chip editor bound to `draft.operators.allowlist`. Persistence rides the existing draft → `finalize_settings_draft` → `on_save` → `_persist_config` → `apply_live_config` path — **no new plumbing**.
  3. (Optional, separate) implement the wizard dropdown-vs-free-text behavior in the project/run wizard pages.
- **Issues / risks:** needs a real chip widget (shared with A5). `OperatorsConfig` is `extra="forbid"`, `str_strip_whitespace=True`, and the allowlist is **case-sensitive** — the editor must not lowercase. Keep it **optional/non-gating**: do not add to `_missing_setup_sections` (the original reason it was pulled, so it can't block reaching the main GUI).

### A5 — `content_scan_extensions` renders read-only (partial)
- **Where:** `src/exlab_wizard/ui/pages/settings.py:482-484` — `ui.label("Scanned file extensions: " + ", ".join(...))`. Module docstring at `:399-402` notes list editors are "a follow-up."
- **Status:** **Partially fixed** — equipment is now an editable add-form (`_render_equipment_section`, `:526-650`) and NAS-credentials is fully interactive; only `validator.content_scan_extensions` remains display-only (its scalar sibling `content_scan_max_mib` *is* editable at `:478-481`).
- **Intent:** Frontend Spec **§7.10** (`Frontend_Spec.md:1016`): a **chip input** pre-populated with the spec defaults plus a **[Reset to defaults]** action. Configurability is resolved Design-Spec Open-Q#14 (`ExLab-Wizard_Design_Spec.md:183`).
- **What implementing it entails:** replace the label at `:483` with a chip editor bound to `draft.validator.content_scan_extensions`; add `[Reset to defaults]` calling `_default_content_scan_extensions()` (`config/models.py:435`). Persistence is automatic via the draft path.
- **Issues / risks:** `ValidatorConfig._extensions_must_start_with_dot` (`config/models.py:464-471`) rejects entries not starting with `.` — validate on add for good UX (the Save path already surfaces the first `ValidationError` via `notifications.notify_error`, `settings.py:355-361`). Build the chip widget once and reuse for A4.

### Resolved / not-a-defect (previously suspected in A)
- **"Phase 12" nav no-op — RESOLVED.** `on_select_section` is now a real handler: nav rows bind to `_select_section` (`settings.py:315-318`) which toggles section visibility client-side (`:298-302`) then calls `on_select_section` if provided. `ui/mount.py` deliberately leaves it unset (switching sections is client-side so edits survive). The docstring at `:213-216` is historical, not live. Not a bug.
- **`lambda: None` handler defaults — RESOLVED.** They are parameter fallbacks for tests; production injects real handlers (LIMS creds `mount.py:345`; NAS creds `mount.py:367`; `nas_password_present` `mount.py:366`). Minor: `on_discard` is passed as `None` (`mount.py:362`) so **"Discard all" is inert in production** (guarded at `settings.py:373`) — acceptable since closing the draft already discards, but wire it if you want explicit discard.

---

## B. Backend features fully built but **not surfaced in the GUI**

> **Shared root cause.** The creation flow `ui/mount.py:_run_creation → _await_session`
> (~`:1457`,`:1536`) is **fire-and-await-final-status only**: it never consumes
> `controller.subscribe()`, never reads `session.pending_input`, and offers no
> resume/cancel/operations affordances. Implementing the **event-subscription consumer (B4)**
> is the foundation that B1/B2/B3 build on.

### B1 — Operations modal is built, exported, and rendered nowhere  ★ highest impact
- **Where:** component `src/exlab_wizard/ui/components/operations_modal.py:77`, exported at `ui/components/__init__.py:12,29`. **Zero call sites** outside the file/export. The main toolbar (`ui/pages/main.py:189-209`) has New Project / New Run / New Test Run / Add Equipment / Refresh / Settings — **no `[Operations…]`**. The footer (`main.py:347-377`) renders Sync/Validator/LIMS/Staging segments with no click handler. `GET /operations` (`api/routers/operations.py:57`) has no in-process caller.
- **Intent:** Frontend Spec **§9.5** "Disconnect and resume — the in-flight operations panel" (`Frontend_Spec.md:1154`): reachable from (1) an `[Operations…]` toolbar action when ≥1 operation is in flight/suspended (`:1163`) and (2) the footer Sync segment's "⚠ N operations need input" state (`:1162`, §3.5.5 `:586`); columns State/Started/Equipment/Project/Run/Plugin with per-row `[Resume]`/`[Cancel]`/`[View log]` (`:1165-1175`), suspended oldest-first (`:1177`), auto-refresh on WS events (`:1179`). Backed by `GET /api/v1/operations` (Backend **§4.6.1**, `04_Backend_Architecture.md:389`).
- **Consequence of the gap:** a plugin that pauses for input mid-creation **cannot be answered from the GUI**, and an in-flight/suspended session **cannot be cancelled or resumed** in-app.
- **What implementing it entails:**
  1. Add an `[Operations…]` toolbar button in `main.py` (mirror the existing `ui.button(...).props('data-testid=...')` pattern), visible when operation count > 0.
  2. Wire its `on_click` in `mount.py` `_main` to build rows from `deps.controller.session_store` — **extract a shared helper** from the route's `_iter_sessions`/`_session_to_entry` (`operations.py:86-121`) rather than duplicating; add a public accessor on `SessionStore` (the route reaches into private `store._sessions`, `operations.py:94`).
  3. Wire row actions to `controller.resume`/`controller.cancel` (B2) and reuse `_open_log_dialog` (`mount.py:1249`) for View-log.
  4. Make the footer Sync segment count suspended sessions (`tray/dependencies.py:861-865` already computes `input_required`) and open the same modal (§3.5.5).
- **Issues / risks:** modal is a static snapshot — true auto-refresh needs B4; otherwise it refreshes only on manual re-render. Concurrency: the list mutates while open.

### B2 — No GUI path to resume or cancel a session
- **Where:** `CreationController.resume` (`controller/creation.py:327`), `.cancel` (`creation.py:351`); HTTP `POST /sessions/{id}/resume` (`api/routers/sessions.py:196`), `/cancel` (`:227`). Grep of `ui/`+`tray/` for `controller.resume(`/`controller.cancel(` → **none**; the GUI only calls `create_project`/`create_run` (`mount.py:1502,1533`) and `status` (`mount.py:1471`).
- **Intent:** Frontend **§9.5** row actions and **§9.6** concurrent-session policy; Cancel routes through the **§9.4** confirmation dialog (`Frontend_Spec.md:1139`) carrying `discard_files` (Backend §4.6.1 `:388`). Resume contract: Backend **§4.7 / §6.4.1** (`06_Plugin_System.md:494-501`); `session_already_completed` → 409 + toast.
- **Current behavior / gap:** resume/cancel reachable only via the e2e HTTP routes. The §9.6 "disable creation buttons while a session is in flight" rule is also unimplemented — `MainPageState.orchestrator_enabled` is hardcoded `True` (`main.py:54`) and button-disable only keys off `selected_node_is_received` (`main.py:198-200`).
- **What implementing it entails:** wire the modal's `on_resume`/`on_cancel` (B1) to `deps.controller.resume(id, extra_inputs)` / `.cancel(id, discard_files=…)` (same dispatch pattern as `_run_staging_action`, `mount.py:1036`). `on_resume` should open the INPUT_REQUIRED form (B3) since `resume()` requires non-empty `extra_inputs` and raises if the session isn't INPUT_REQUIRED (`creation.py:337-341`). `on_cancel` opens the §9.4 Discard/Keep dialog. Replace the hardcoded `orchestrator_enabled` with the real config flag for §9.6.
- **Issues / risks:** `cancel()` cancels and awaits the background task (`creation.py:377-381`); surface 409/`ValueError` if already finished. `discard_files=True` triggers `shutil.rmtree` of the partial dir (`creation.py:1151-1153`) — map the Discard/Keep choice correctly.

### B3 — Plugin `INPUT_REQUIRED` cannot be answered from the GUI
- **Where:** controller wiring `on_input_required` + `_resume_queues` (`controller/creation.py:865-887`); INPUT_REQUIRED transition + `session.pending_input` (`:866-871`); `input_required` event published (`:872-880`). No escalation dialog exists anywhere in `ui/`.
- **Intent:** Frontend **§9.1** escalation dialog (`Frontend_Spec.md:1095-1106`): title "Additional input required", plugin-identity pill, the `reason` line, fields rendered with the README form widgets, `[Submit]`/`[Cancel]`; Submit → resume. Escalations are strictly **sequential** (§9.2 `:1112`). Backend **§6.4.1** suspend/resume; OS-notification fast path §9.5 `:1158` / Backend §15.7.3.
- **Current behavior / gap:** the controller correctly suspends and parks on `await queue.get()` (`creation.py:881-887`) setting `session.pending_input`, but `_await_session` (`mount.py:1457`) just awaits the task — which never completes while suspended — so the wizard **hangs indefinitely with no dialog**. Only the HTTP `/resume` route can answer.
- **What implementing it entails:** depends on **B4** (to learn a session entered INPUT_REQUIRED) and **B1** (the surface). Build a new §9.1 dialog rendering `pending_input["fields"]` with README field widgets + reason/plugin header; Submit → `controller.resume(sid, values)`, Cancel → §9.4 confirm → `controller.cancel`. Reuse from the Operations modal's `on_resume`.
- **Issues / risks:** preserve the §9.2 sequential invariant (one dialog fully resolves before the next frame). The plugin's `isolation.timeout_seconds` keeps counting while suspended (§9.1 `:1110`) — handle a `failed` frame force-closing the dialog. `resume()` rejects empty/invalid input (`ValueError`; backend maps plugin-side rejection to `plugin_variable_validation_failed` 422) — surface inline.

### B4 — Live per-session phase progress is static; the GUI never subscribes
- **Where:** `CreationController.subscribe` (`controller/creation.py:401`); phase frames published in `_transition`/`_publish` (`creation.py:1104-1128`). Component `session_progress(...)` (`ui/components/session_progress.py:91`) is rendered **statically with `active_phase=None`** at `ui/pages/wizard_project.py:224` and `ui/pages/wizard_run.py:229`. Grep of `ui/`+`tray/` for `subscribe` → **none**.
- **Intent:** Frontend **§10.1** (`Frontend_Spec.md:1196`): the Confirm & Create step shows a phase bar advancing Validating inputs → Rendering template → Running plugins → Writing cache → Validating post-creation → Queueing NAS sync; **§9.3** adds a per-plugin sub-row from `{kind:"progress", phase:"running_plugins", current, total}` (`:1120,1135`). Backend **§4.6.2** WS frames (`04_Backend_Architecture.md:406-413`), **§4.7.1** SessionState→Phase mapping (`:573`).
- **Current behavior / gap:** every phase row sits at fraction 0.0 forever; `_run_creation` reads only final status. The frames the controller publishes (`creation.py:1116-1123`) are drained only by the e2e WebSocket.
- **What implementing it entails:** in `mount.py`, spawn a background consumer (via `_spawn_background`, `mount.py:58`) iterating `controller.subscribe(session_id)`; on each `phase`/`progress` frame re-render `session_progress` with live `active_phase`/`completed`/`plugin_current/total`; pass real args at the two call sites instead of `None`. **Verify** the controller's emitted phase strings match the component's hardcoded `PHASES` tuple (`session_progress.py:24-31`) — a mismatch silently no-ops.
- **Issues / risks:** the iterator ends on `done`/`failed` (`creation.py:416-419`) — cancel/clean up the consumer on wizard close (mind the `_BACKGROUND_TASKS` strong-ref set). The queue is lazily created (`creation.py:410-411`) — subscribe at/before create time or miss early phases. Re-render from a background task needs the right NiceGUI client context. The §9.3 sub-row depends on the plugin host actually emitting `progress` frames — confirm it does.

### B5 — Audit / Problems live stream not consumed by the in-process UI
- **Where:** `AuditChannel` + 30 s `_audit_loop` (`api/app.py:77-133`, `:327-356`); WS `/problems/events` (`api/routers/problems.py:252-282`). Grep of `ui/` for `audit_channel`/`AuditChannel`/`.subscribe()`/`problems/events` → **zero**.
- **Intent:** Backend **§4.6.2** (`04_Backend_Architecture.md:406-426`; the 30 s task at `:366`): a background `Validator.audit("all")` every 30 s publishes a diff to a pub-sub channel the always-on Problems tab reads (snapshot on connect, then deltas). Frontend **§11 / §3.3**: the Problems tab carries a live count badge (`Frontend_Spec.md:429`) and the footer Validator segment shows "Last audit: HH:MM:SS" (`:587`); page footer copy is "Showing N of M findings · Last audit: HH:MM:SS · Next refresh in 23s".
- **Current behavior / gap (all static):**
  - `/problems` page (`ui/mount.py:371-375`) runs `_safe_audit(deps)` **once per render** (`mount.py:1579-1588`); footer is the literal `"Showing {n} of {m} findings  ·  Last audit: --"` (`ui/pages/problems.py:279-281`) — **"Last audit: --" is hardcoded**, no countdown.
  - Embedded right-pane Problems (`ui/pages/main.py:445-450`) shows `f"Showing 0 of {...} findings"` — **shown-count hardcoded to 0**; `problems_count_hard/soft` default 0 (`main.py:50-51`) and `_build_main_state` (`mount.py:699-721`) never sets them, so the tab badge `f"Problems ({...})"` (`main.py:432`) is always 0.
  - Nothing in the GUI reads `deps.last_audit_at` or the channel; only the HTTP/WS surface does.
- **What implementing it entails:**
  1. Either (a) a `ui.timer` that polls `deps.last_audit_at` and re-runs `validator.audit`, or (b) consume `deps.audit_channel.subscribe()` and push deltas into reactive page state.
  2. Populate `MainPageState.problems_count_hard/soft` in `_build_main_state` from the audit summary; replace the hardcoded `0` at `main.py:447`.
  3. Replace `"Last audit: --"` (`problems.py:280`) with `deps.last_audit_at` and add the "Next refresh in Ns" countdown.
  4. Confirm production `create_app(...)` passes `start_audit_task=True` (`app.py:266`).
  5. Verify the override-and-allow-sync dialog (Frontend §11.5, already present in `ui/pages/problems.py`) is reachable from wherever the tab is mounted.
- **Issues / risks:** NiceGUI re-renders per navigation, so a per-render full `validator.audit` (current `_safe_audit`) is O(tree) each time — prefer the 30 s channel. Keep the three surfaces (tab badge, right-pane summary, `/problems` page) reading the same source to avoid drift; debounce delta storms; preserve filter-chip state across refreshes.

---

## C. No-op default collaborators

### C1 — Production runs on the **stub** README generator  ★ real defect
- **Where:** `NoOpReadmeGenerator` (`controller/creation.py:210-221`), wired as the constructor default at `:271-273`. The real `ReadmeGenerator` (`readme/generator.py:176-220`) is **never imported or constructed** in `tray/` or `api/` — `tray/dependencies._build_controller` (`:519-542`) and `apply_live_config` (`:226-238`) call `CreationController(...)` **without** `readme_generator=`.
- **Intent:** Backend Spec **§10** (`10_README_Generation.md`): every project/run creation must generate a `README.md` (YAML front matter + Markdown) **and** the `readme_fields.json` cache via the four-layer field merge (template/config/custom/system) with full validation (§10.2, §10.3, §10.7; cache file §11.4). README is non-optional for project/run scope.
- **Current behavior / gap:** `NoOpReadmeGenerator.generate()` writes a 3-line stub
  (`# {label}` / `Operator:` / `{objective}`) with **no front matter, no field blocks, and
  no `readme_fields.json`**. So GUI-created runs ship a placeholder README and are missing the
  fields cache that downstream consumers (LIMS ingest, validator) expect.
- **What implementing it entails:**
  1. Inject the real generator: `readme_generator=ReadmeGenerator()` in `_build_controller` and the `apply_live_config` fresh-build branch.
  2. **Reconcile the type mismatch first** — there are two same-named `ReadmeContext` classes: the controller's flat one (`creation.py:188-201`) vs the generator's layered one (`generator.py:152-168`); and the controller's Protocol expects `-> Path` (`creation.py:204-207`) while `ReadmeGenerator.generate()` returns `tuple[Path, Path]`. Add an adapter in `_write_cache` (`creation.py:908-919`) that assembles the layered context (core fields, template/config decls from the resolved template + `config.readme.defaults`, `SystemFields`, custom fields) and consumes both returned paths.
- **Issues / risks:** the real generator **raises** on validation failures (missing required template/config fields, type mismatches, core redeclaration); `_validate_inputs` (`creation.py:578-599`) currently checks required-field presence but not types, so newly enforced gates could fail creations that previously "passed" against the stub. Generator does sync work via `asyncio.to_thread` — fine for the pipeline. **This should be validated end-to-end with a real template before release.**

### C2 — `NoOpNASSync` controller default (informational — not a production defect)
- **Where:** `NoOpNASSync` (`controller/creation.py:230-234`), default at `:274`; real `NASSyncClient` (`sync/nas_client.py:257-1089`).
- **Why it's not a defect:** the controller is **not** the production enqueue path — its inline enqueue is best-effort and `contextlib.suppress`-wrapped (`creation.py:686-688`). Auto-sync is owned by the **quiescence poller**, which gets the real `deps.nas_sync` (built `tray/dependencies.py:110-119`, `init()`'d in lifespan `api/app.py:277-279`, polled `:807-837`); force-sync / clear-verified call `deps.nas_sync` directly (`ui/mount.py:1056-1070`). `NASSyncClient` is complete (push/check/verify/cleanup/reconcile, rclone-only).
- **Optional cleanup:** either inject `nas_sync=deps.nas_sync` into `_build_controller` if the inline enqueue is meant to be live, or add a one-line comment that the NoOp is intentional because the poller owns auto-sync. No functional change needed.

### C3 — Runtime guard toasts (where the `None`/NoOp path surfaces in the live UI)
All in `src/exlab_wizard/ui/mount.py` — useful as detectors of mis-wired deps:
- `:1058` `"Force-sync unavailable: NAS sync not wired"` (`deps.nas_sync is None`)
- `:1193` `"Keep-local unavailable: sync-state writer not wired"` (`deps.sync_state_writer is None`)
- `:531` `"equipment probe is not available"` (`deps.equipment_probe is None`)
- related: `:1052` no-config staging, `:1478`/`:1509` controller-not-initialized.

---

## D. TODO / stale markers

### D1 — Offline-catalogue schema-version gate is stricter than the cache-file policy (open spec question)
- **Where:** `src/exlab_wizard/lims/catalogue.py:93-103` (comment `:93-96`, check `:97-103`).
- **Intent:** Backend **§7.2.9.3** (`07_Sync_and_Database_Integration.md:360`): a `schema_version` mismatch should be treated as **catalogue absent** (WARN + fall through). The cache-schema reader policy **§11.9.2** (`11_Cache_Folders.md:462-468`) is **major-only** (accept newer/older minor).
- **Gap:** `read_catalogue` does an **exact** match (`catalogue.py:98`) and **raises `ConfigError`**, which conflicts with both the major-only cache policy and the "treat as absent / WARN" wording. Net UI behavior already matches "absent" because the only caller wraps it in try/except → WARN (`ui/mount.py:1401-1416`); the inconsistency is internal-contract, not user-visible. The catalogue isn't in §11.9.4's enumerated schema list, hence the open question.
- **What resolving it entails:** decide the policy (add the catalogue to §11.9.4 / §7.2.9, choose exact vs major-only); if major-only, swap the check to a major-compare and parse known-minor fields; if strict, consider a typed "treat-as-absent" signal instead of `ConfigError` to match §7.2.9.3. Drop the TODO once decided.
- **Issues / risks:** low. With exact-match, a future *minor* bump of `OFFLINE_CATALOGUE_VERSION` on the producer makes every consumer silently ignore the catalogue — the hazard the TODO flags.

### D2 — Stale "plugins/registry.py not yet committed" comment (documentation only)
- **Where:** `src/exlab_wizard/plugins/host.py:73-81` (plus `:115-123`, `:896-905`).
- **Reality:** `plugins/registry.py:128-378` is a **real, complete** `PluginRegistry` (manifest scan over bundled+lab roots, `api_version` gate, network-opt-in gate, lab-wins merge, `get`/`list_all`/`candidates_for`, no plugin imported at scan time) and is **wired in production** (`tray/dependencies._build_plugin_host:497-516` constructs it, `.reload()`s, adapts it to the host). The host's `_ListBackedRegistry`/`build_test_registry` (`host.py:896-918`) is test-only.
- **Intent reference:** Backend **§6.2.1** (`06_Plugin_System.md:289-306`).
- **What resolving it entails:** delete/rewrite the three stale comment blocks to say `registry.py` is committed and wired. Pure cleanup. (Minor spec note: §6.2.1 describes the registry as `extension -> list[PluginRecord]`; the impl keys `name -> PluginRecord` with `candidates_for` computing extension matches — functionally equivalent; flag only if the spec wants the literal structure.)

---

## Prioritized backlog

| # | Item | § | Impact | Effort |
|---|------|---|--------|--------|
| 1 | **Inject real `ReadmeGenerator`** (+ reconcile `ReadmeContext`/return type) | C1 | **High — GUI-created runs ship stub READMEs, no `readme_fields.json`** | M |
| 2 | Event-subscription consumer in the creation flow (foundation) | B4 | High — unblocks 3–5; live progress | S–M |
| 3 | Render Operations modal + toolbar/footer hooks | B1 | High — session control surface | M |
| 4 | Resume/Cancel wiring (+ §9.4 confirm, §9.6 disable rule) | B2 | High | M |
| 5 | INPUT_REQUIRED escalation dialog (new component) | B3 | High — creations can hang silently | M |
| 6 | Live Problems/audit stream + real counts | B5 | Med | M |
| 7 | Operators allowlist chip editor | A4 | Med — security gate has no populator | M |
| 8 | "Start at login" checkbox wiring | A2 | Med | S |
| 9 | "Quit ExLab-Wizard now" button (+ `deps` quit hook) | A1 | Low–Med | S |
| 10 | `content_scan_extensions` chip editor + reset | A5 | Low | S (reuse #7) |
| 11 | Application-section status labels parity (§7.13) | A3 | Low | S |
| 12 | Resolve offline-catalogue version-policy TODO | D1 | Low | S |
| 13 | Delete stale registry comment | D2 | Trivial | XS |

### Shared foundations to build once
- **Event-subscription consumer** (B4) underpins B1 auto-refresh, B2 in-flight cancel, B3 escalation, and B5.
- **Reusable chip/list editor** serves A4 and A5 (and the equipment-section follow-up).
- **Shared `SessionStore → row` helper / public accessor** removes the duplication between
  `api/routers/operations.py` and the new in-process modal.

### Suggested sequencing
**Release-blocking correctness first:** #1 (README) — validate end-to-end with a real
template. **Then the session-control epic:** #2 → #3 → #4 → #5 (they compound). **Then**
the visibility/usability items #6–#11, and the cleanups #12–#13 anytime.
