# ExLabWizard — project notes for Claude

## Orchestrator / staging is intentionally hidden (not removed)

The per-equipment `stage` sync mode and the staging-PC relay
(orchestrator) are **hidden at the UI layer only**. The operator sees a
single model: one instance hosts multiple equipment folders, each syncing
**directly to the NAS** (`sync_mode = "nas"`).

- The staging **backend is still present and still tested** — `orchestrator/`,
  `api/routers/staging.py`, `ui/pages/staging.py`, the `nas_client` stage
  branch, `SyncMode.STAGE`, and the `orchestrator.staging_*` config fields all
  remain. They are dormant (no equipment is `stage` mode; `staging_root` is
  never surfaced).
- **Do not** "fix" the missing staging dock, the absent sync-mode wizard step,
  or the missing staging-root setting — their removal from the GUI is
  deliberate (see `docs/superpowers/specs/2026-05-29-hide-orchestrator-staging-design.md`).
- **Do not** assume the feature is gone: `orchestrator/quiescence_poller.py` is
  the auto-sync engine for **both** nas- and stage-mode runs — never delete it.
- `orchestrator.label` is still **required** (workstation identity in every
  `creation.json`); it is unrelated to staging. The Settings section that
  collects it is titled "Workstation" (its section id stays `"orchestrator"`).
- To re-enable staging, restore the four UI surfaces listed in the spec's §3.6.
  A full backend removal, if ever wanted, is a separate spec.
