# 9. Configuration File

Parent: [[ExLab-Wizard_Design_Spec]]

---

App-level config lives in a single `config.yaml` in the user's app data directory:

| OS | Default path |
|---|---|
| macOS | `~/Library/Application Support/exlab-wizard/config.yaml` |
| Windows | `%APPDATA%\exlab-wizard\config.yaml` |
| Linux | `$XDG_CONFIG_HOME/exlab-wizard/config.yaml` (falls back to `~/.config/exlab-wizard/config.yaml`) |

`config.yaml` contains **no secrets**. NAS connection details and credentials live entirely in the operator's `rclone.conf` (set up separately with `rclone config`; see `docs/setup/rclone-remote-setup.md`). The only secret the app manages is the LIMS password, which is stored in the OS keyring (§7.4); `config.yaml` references it by keyring service+username pair only.

**YAML library.** `config.yaml` is read AND written by the app (the Settings UI's Save action; Frontend §7.3). To preserve operator-readable comments and key order across save/reload cycles, the loader uses [`ruamel.yaml`](https://yaml.dev/doc/ruamel.yaml/) (round-trip mode). Read-only YAML files elsewhere in the codebase (`copier.yml`, `manifest.yml`, README front matter) use PyYAML's `yaml.safe_load` because round-trip preservation isn't needed and PyYAML is faster. Both libraries are committed in `pyproject.toml`.

```yaml
paths:
  templates_dir: "..."      # directory containing Copier template subdirectories (app files)
  plugin_dir: "..."
  local_root: "..."         # equipment-first root; the app writes into <local_root>/<equipment>/<project>/Runs/Run_<DATE>/ for experimental runs and <local_root>/<equipment>/<project>/TestRuns/TestRun_<DATE>/ for test runs

lims:
  endpoint: "https://lims.lab.example/api/v1"   # Optional if offline_catalogue_path is set (offline-only workstation).
  email: "alex.nguyen@lab.example"              # Operator email; not a secret. Used for cookie-session login. Optional if offline_catalogue_path is set.
  cache_ttl_hours: 24                           # How long the local LIMS-project cache is considered fresh (§7.2.4).
  offline_catalogue_path: ""                    # Optional absolute path to a shared JSON file written by another connected workstation; used as a fallback project source when this workstation can't reach the LIMS directly. Empty/absent disables the feature. See §7.2.9.
  # No password field. The LIMS password is stored in the OS keyring under
  # (service="exlab-wizard", username="lims"). See §7.4.
  # The Settings dialog manages the keyring entry; nothing about the credential
  # is written here.
  # ExLab-Wizard is read-only against the LIMS in v1 (Mapping B; §7.2). No write
  # endpoints are exercised; project creation flows through the LIMS web UI.
  # Setup completeness: the LIMS slot is satisfied by EITHER (endpoint + email +
  # keyring password) OR a configured offline_catalogue_path. See §7.2.9 and
  # Frontend Spec §3.1 (Application Lifecycle).

readme:
  # The core mandatory fields (label, operator, objective) are hard-coded in
  # the backend and always applied to project and run creations. They cannot
  # be disabled here. The defaults block below only *extends* the required set
  # with lab-policy fields.
  defaults:                 # global README fields applied to all templates
    # Example of extending the required set with a lab-policy field.
    # Set required: true to force the user to fill it in on every creation.
    - id: irb_protocol
      label: "IRB Protocol Number"
      type: string
      required: false
      default: ""

# rclone.conf NAS-sync migration (Phase 8): per-equipment transport: blocks and
# NAS keyring entries are GONE. The NAS connection is defined once by the nas:
# block (a single rclone named remote). Equipment entries carry only the fields
# below. See docs/setup/rclone-remote-setup.md for the rclone config walkthrough.
equipment:
  # local_root and nas_root below are equipment-scoped anchor paths. The full
  # destination is composed at creation time as
  #   <*_root>/<equipment>/<lims_short_id>/Runs/Run_<DATE>/   (experimental)
  #   <*_root>/<equipment>/<lims_short_id>/TestRuns/TestRun_<DATE>/ (test)
  # The actual NAS target is composed from nas.remote + nas.base_root + equipment_id
  # + run leaf at sync time (not from nas_root). nas_root is a human-readable
  # display value only (shown in the browse view).
  - id: "CONFOCAL_01"
    label: "Confocal Microscope 1"
    local_root: "/data/lab"             # equipment-first root on this workstation
    nas_root: "//nas01/lab"             # display value only; the rclone target uses nas.remote
    sync_mode: "nas"                    # "nas" = sync directly to NAS; "stage" = push to orchestrator staging
  - id: "FLOW_01"
    label: "Flow Cytometer 1"
    local_root: "/data/lab"
    nas_root: "/mnt/nas/lab"
    sync_mode: "nas"

# Top-level NAS sync configuration. A single named rclone remote covers all
# nas-mode equipment. Credentials live entirely in rclone.conf.
nas:
  remote: "lab-nas"                     # remote name from rclone.conf (set up with rclone config)
  base_root: "lab"                      # path on the remote under which equipment folders live
                                        # run target: lab-nas:/lab/<EQUIPMENT_ID>/<run-leaf>
  rclone_config_path: ""                # optional: pin --config <path> (blank = rclone default discovery)
  mtime_tolerance_s: 2                  # reconcile tolerance (s); absorbs SFTP/SMB modtime rounding
  perf:
    transfers: 4                        # rclone --transfers; also the RAM dial on constrained machines
    checkers: 8                         # rclone --checkers
  bandwidth:
    upload_mbps: null                   # null/absent = unlimited
    schedule:                           # optional time windows; outside windows = unlimited
      - { days: ["mon","tue","wed","thu","fri"], from: "08:00", to: "18:00" }

nas_cleanup:
  enabled: true                         # disable to keep all local copies; operator deletes manually
  min_verify_passes: 2                  # successful hash verifications required before deletion (§7.1.6)
  min_age_hours: 24                     # grace period after the most recent VERIFIED transition
  retain_cache: true                    # metadata-only retention: keep .exlab-wizard/ after data files deleted (default true; §7.1.10)

logging:
  level: "INFO"                         # DEBUG | INFO | WARN | ERROR (case-insensitive)
  central_log_max_mb: 10                # rotate the central app log when it exceeds this size
  central_log_keep: 5                   # keep this many rotated central-log files
  # Per-equipment / per-run logs are unbounded by spec and not rotated; they are
  # bounded in practice by the lab's run cadence. See §11.5.1.

operators:
  # Optional allowlist for the mandatory `operator` core field. If absent or empty,
  # the operator field accepts any non-empty string (free-text default). If
  # non-empty, the operator field must match an entry in this list (case-sensitive)
  # at creation time; the wizard renders a dropdown of these values instead of a
  # free-text entry, and the OS-username pre-fill is applied only when the username
  # appears in the allowlist. Resolves Open Question #11.
  allowlist: []
    # - "asmith"
    # - "jlee"
    # - "alex.nguyen"

validator:
  # Content-scan parameters for the unresolved-placeholder rule (§8.1.1) and the
  # audit-mode walk (§11.8). Configurability lets labs with unusual text formats
  # (large rendered configs, custom file extensions) extend coverage; the trade-off
  # is that validator determinism (§11.8) holds only across identical configs.
  content_scan_max_mib: 5               # max file size to scan for placeholder tokens; larger files are skipped
  content_scan_extensions:              # text extensions eligible for content scanning; binary extensions are always skipped
    - ".txt"
    - ".md"                             # only YAML front matter is scanned for .md files; see §8.1.1
    - ".csv"
    - ".tsv"
    - ".json"
    - ".yaml"
    - ".yml"
    - ".toml"
    - ".ini"
    - ".cfg"
    - ".conf"
    - ".xml"
    - ".sh"
    - ".py"

plugins:
  # Master opt-in for plugins that declare `isolation.network: true` in their
  # manifest. v0.7 semantics: this is an INSTALLATION GATE, not a runtime
  # block. When false (default), plugins declaring network: true are REFUSED
  # at the plugin-load step (the host does not register them, and they do not
  # appear in any wizard's plugin list). When true, those plugins load
  # normally. The host does NOT install firewall rules at the OS level —
  # plugin trust comes from filesystem permissions on paths.plugin_dir and
  # operator review, not from runtime network blocking. See §6.3.3 / §6.3.5.
  allow_network: false

sync:
  enabled: true
  retry_attempts: 3
  quiescence_minutes: 10              # files must be stable for this long before sync eligibility
  poll_interval_seconds: 120          # how often the sync worker polls for newly eligible runs
  ignore_globs:                       # patterns skipped by the sync worker
    - "*.partial"
    - "*.tmp"

orchestrator:
  label: "Lab Acquisition Station 01"  # required; stamped into every run's creation.json
  staging_root: ""                     # opt-in: blank = this device is not a staging PC; no directory created
                                       # when non-empty, the suggested path is OS-conditional (see paths.py)
  # Stage-mode hop (rclone.conf NAS-sync migration): stage-mode equipment push
  # runs to <staging_remote>:<staging_base_root>/<equipment_id>/<run-leaf> using
  # the same RcloneDriver as the NAS leg. Both remotes live in the same rclone.conf.
  staging_remote: "lab-staging"        # second remote in rclone.conf for the staging hop (blank if unused)
  staging_base_root: "staging"         # path on the staging remote under which equipment folders land
  staging_perf:
    transfers: 4
    checkers: 8
  staging_cleanup:
    mode: "manual"          # or "scheduled"
    retain_hours: 24        # only used if mode is "scheduled"
```

Single-equipment workstations use a one-entry `equipment` list with `sync_mode: nas`. No migration is required between modes.
