# Setting up your rclone remote(s)

ExLab-Wizard uses [rclone](https://rclone.org/) as its **default** NAS transport.
Rather than storing NAS credentials inside the app, you configure a named remote
once with `rclone config` and then tell the app which remote name to use. The app
never sees a password — all connection details live in `rclone.conf`.

> **Cluster nodes where SMB/SFTP is blocked by IT:** use the `rsync_ssh`
> transport instead. See [`rsync-ssh-setup.md`](rsync-ssh-setup.md) for the
> key provisioning and `config.yaml` walkthrough.

---

## Why this approach

Earlier versions of ExLab-Wizard required you to enter NAS passwords through the
Settings dialog, where they were stored in the OS keyring per equipment. The
current design replaces that with operator-managed rclone named remotes:

- You configure the remote once with the standard `rclone` tool.
- The app references it by name via `nas.remote` in `config.yaml`.
- Credentials stay in `rclone.conf`, managed entirely by rclone.
- Any backend rclone supports works (SFTP, SMB, S3, WebDAV, …) — not just the
  two the app used to hand-code.

---

## Prerequisites

Install rclone from [rclone.org/downloads](https://rclone.org/downloads/) or
your OS package manager:

```bash
# macOS
brew install rclone

# Linux (Debian/Ubuntu)
sudo apt install rclone

# Windows — download the installer from rclone.org/downloads
```

Verify the install:

```bash
rclone version
```

---

## Creating a remote with `rclone config`

Run `rclone config` — an interactive TUI that walks you through adding a remote.
The prompts vary by backend type. Two common lab setups are shown below.

### SFTP remote

```
$ rclone config
n) New remote
...
name> lab-nas
Type of storage to configure.
Enter a string value. Press Enter for the default ("").
...
Storage> sftp

SSH host to connect to
Enter a string value. Press Enter for the default ("").
host> nas01.lab.example

SSH username
Enter a string value. Press Enter for the default ("").
user> labuser

SSH port number
Enter a signed integer. Press Enter for the default (22).
port>

Choose your SSH connection authentication method.
1 / ...
2 / Use a password
   \ "false"
...
key_pem>

y) Yes this is OK (default)
```

After completing the wizard, `~/.config/rclone/rclone.conf` (Linux/macOS) or
`%APPDATA%\rclone\rclone.conf` (Windows) will contain a stanza like:

```ini
[lab-nas]
type = sftp
host = nas01.lab.example
user = labuser
pass = <rclone-obscured-password>
```

Verify it works:

```bash
rclone lsd lab-nas:
```

### SMB / Samba remote

```
$ rclone config
n) New remote
name> lab-smb
Storage> smb

SMB server hostname to connect to
host> nas01.lab.example

SMB username
user> labuser

SMB port number (default 445)
port>

SMB password
y/g/n> y
password:
```

Resulting stanza:

```ini
[lab-smb]
type = smb
host = nas01.lab.example
user = labuser
pass = <rclone-obscured-password>
```

Verify:

```bash
rclone lsd lab-smb:labshare
```

---

## Wiring the remote to ExLab-Wizard

Add a `nas:` block to your `config.yaml` (see §9 for the full schema):

```yaml
nas:
  remote: "lab-nas"          # the name you gave the remote in rclone config
  base_root: "lab"           # path on the remote under which equipment folders live
                             # equipment runs land at lab-nas:/lab/<EQUIPMENT_ID>/<run-leaf>
```

`base_root` is the path on the remote that acts as the shared root for all
equipment. For example, if your NAS stores data under `/shares/lab/` and the
remote is rooted at the share, set `base_root: "lab"`. An empty `base_root` is
valid — runs then land directly at `<remote>:/<equipment_id>/<run-leaf>`.

The full target path for a run is:

```
<remote>:/<base_root>/<equipment_id>/<run-directory-name>
```

Equipment entries in `config.yaml` carry only `id`, `label`, `local_root`,
`nas_root`, and `sync_mode` — no per-equipment connection block is needed.

---

## Verifying the connection

**From the app:** open **Settings → NAS Remote** and click **Test connection**.
This runs `rclone about <remote>:` and shows free-space info on success or a
classified error reason on failure.

**From a shell:**

```bash
# Confirm the remote answers
rclone about lab-nas:

# List the base root
rclone lsjson lab-nas:lab
```

If `nas.rclone_config_path` is set, pass `--config <path>` to match what the
app sees:

```bash
rclone --config /path/to/rclone.conf about lab-nas:
```

---

## Performance and memory tuning

Two knobs under `nas.perf` control rclone parallelism and, on constrained
machines, peak memory use (rclone buffers `--transfers` × `--buffer-size` in
RAM):

```yaml
nas:
  remote: "lab-nas"
  base_root: "lab"
  perf:
    transfers: 4     # default: 4; maps to rclone --transfers
    checkers: 8      # default: 8; maps to rclone --checkers
```

On space- and RAM-constrained acquisition machines (e.g. a 256 GB instrument PC
with ~100 GB free and limited RAM) lower `transfers` to `2` or `1` to reduce
memory pressure during large syncs.

Upload bandwidth can be capped globally:

```yaml
nas:
  remote: "lab-nas"
  base_root: "lab"
  bandwidth:
    upload_mbps: 50        # null/absent = unlimited
    schedule:              # limit applies only during these windows; outside = unlimited
      - { days: ["mon","tue","wed","thu","fri"], from: "08:00", to: "18:00" }
```

`upload_mbps` translates to `--bwlimit <K>K` (KiB/s) when the app invokes
rclone. The schedule is evaluated in the workstation's local time zone.

---

## Modtime tolerance

SFTP and SMB backends may round modtimes by up to 1–2 seconds. The routine
post-push reconcile credits a file as synced when its remote modtime is within
`mtime_tolerance_s` of local (default 2 s):

```yaml
nas:
  mtime_tolerance_s: 2    # default; increase to 5 for SMB targets that round more
```

---

## Pinning the rclone.conf path

By default rclone discovers its config via standard OS locations
(`~/.config/rclone/rclone.conf` on Linux, `%APPDATA%\rclone\rclone.conf` on
Windows). If the app runs as a different OS user than the one who created the
config — common when running as a tray/service account — set
`nas.rclone_config_path` to pin `--config`:

```yaml
nas:
  remote: "lab-nas"
  base_root: "lab"
  rclone_config_path: "/etc/exlab-wizard/rclone.conf"
```

The NAS remote and the staging remote (see below) must both be defined in the
same `rclone.conf` file; the app passes the same `--config` path to every
rclone invocation.

---

## Encrypted rclone.conf

The app assumes an **unencrypted** `rclone.conf`. Rclone's config encryption
(`RCLONE_CONFIG_PASS`) is outside the app's scope — if you encrypt your config,
`RCLONE_CONFIG_PASS` becomes your single credential to manage in the environment
that runs the tray process. The app will not prompt for it or read it.

---

## Stage-mode devices (orchestrator hop)

If any equipment uses `sync_mode: stage`, those devices push runs to an
intermediate staging area on the orchestrator rather than directly to the NAS.
Configure a second remote (pointing at the staging area) under the `orchestrator:`
block:

```yaml
orchestrator:
  label: "Lab Acquisition Station 01"
  staging_remote: "lab-staging"        # second remote in the same rclone.conf
  staging_base_root: "staging"         # path under which equipment folders land
  staging_perf:
    transfers: 2
    checkers: 4
```

The staging remote uses the same `rclone.conf` file as the NAS remote. Create it
with `rclone config` the same way as the NAS remote, then reference it by name.

Run folders for stage-mode equipment land at:

```
<staging_remote>:/<staging_base_root>/<equipment_id>/<run-directory-name>
```

---

## Minimal `config.yaml` example

```yaml
nas:
  remote: "lab-nas"
  base_root: "lab"
  mtime_tolerance_s: 2
  perf:
    transfers: 4
    checkers: 8
  bandwidth:
    upload_mbps: null

equipment:
  - id: "CONFOCAL_01"
    label: "Confocal Microscope 1"
    local_root: "/data/lab"
    nas_root: "//nas01.lab.example/lab"   # display value; the actual sync uses nas.remote
    sync_mode: nas
```

The `nas_root` field on each equipment entry is a human-readable display value
(shown in the browse view). The actual rclone target is composed from `nas.remote`
and `nas.base_root` at sync time.
