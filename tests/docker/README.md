# NAS Emulator — Docker Compose Stack

Local NAS stand-in for testing the ExLab-Wizard sync pipeline without
touching the real NAS. Two containers cover both rclone backends: an
OpenSSH server for SFTP and a Samba server for SMB.

## Purpose & Scope

**In scope:** password-authenticated SFTP and SMB endpoints, key-authenticated
rsync-over-ssh endpoint, a bind-mounted data volume for inspection,
teardown/reset between runs.

**Out of scope:** Google Cloud bucket sync, NAS quota / snapshot
behaviour.

> **rsync-over-ssh:** The `nas-sftp` container also hosts a second user
> (`rsyncuser`) with key-only auth and no SFTP subsystem, mirroring the
> Synology posture. The container's `TZ` is set to `America/New_York` so
> it differs from typical client timezones — this exercises the timezone
> characterization in the integration test suite (see §rsync-over-ssh below).

> **How the app connects:** ExLab-Wizard uses operator-managed rclone named
> remotes. You configure the remote in `rclone.conf` once with `rclone config`
> (or by editing the file directly), point `nas.remote` in `config.yaml` at
> the remote name, and the app calls `rclone copy`/`rclone lsjson`/`rclone check`
> with that named remote. The app injects **no credentials** at subprocess time.
> See `docs/setup/rclone-remote-setup.md` for the full walkthrough.

## Requirements

| Requirement      | Detail                                                       |
|------------------|--------------------------------------------------------------|
| Protocols        | SFTP (`${SFTP_HOST_PORT:-2222}`) + SMB (`${SMB_HOST_PORT:-1445}`) + rsync-over-ssh (same port as SFTP) |
| Auth             | SFTP/SMB: password only; rsync-over-ssh: key-only (no password, no SFTP subsystem) |
| Platform         | macOS, Linux, Windows (Docker Desktop or Engine)             |
| Internet access  | Not required after `docker compose build` succeeds           |
| Tooling          | Docker + Docker Compose v2 (optionally the `rclone` CLI)     |

## Layout

```
tests/docker/
├── Dockerfile              # debian-slim + openssh-server + rsync (SFTP + rsync-over-ssh)
├── Dockerfile.smb          # debian-slim + samba (password auth)
├── entrypoint.sh           # sets SFTP user password; generates ed25519 keypair for rsync user
├── entrypoint.smb.sh       # registers the Samba user + password
├── docker-compose.yml      # nas-sftp (TZ=America/New_York) + nas-smb services
├── .env.example            # credential / port template (committed)
├── .env                    # actual values incl. NAS_PASSWORD (gitignored)
├── nas-data/               # bind-mounted NAS state (gitignored except .gitkeep)
│   └── .gitkeep
├── keys/                   # generated keypair for rsync-over-ssh user (gitignored except .gitkeep)
│   └── .gitkeep            # id_exlab + id_exlab.pub appear here after first `docker compose up`
├── rclone.conf             # test rclone.conf pointing at the compose containers
├── rclone.conf.example     # annotated example for ad-hoc shell use
└── README.md
```

## First-Time Setup

From the repo root:

```bash
# 1. credentials / ports — edit NAS_PASSWORD to taste
cp tests/docker/.env.example tests/docker/.env

# 2. build + start both backends
docker compose -f tests/docker/docker-compose.yml up -d --build

# 3. fill in the password in tests/docker/rclone.conf (see that file's comments)
#    or run rclone config to add the remotes interactively:
rclone --config tests/docker/rclone.conf config

# 4. confirm SFTP answers
rclone --config tests/docker/rclone.conf lsd nas01-sftp:

# 5. confirm SMB answers
rclone --config tests/docker/rclone.conf lsd nas01-smb:labshare
```

The password lives only in `tests/docker/.env`; the entrypoints set it on
the container user at boot, so rotating it is just an `.env` edit + a
`docker compose up -d` bounce.

## Wiring Into ExLab Config

1. Add the test remotes to your `rclone.conf` (edit `tests/docker/rclone.conf`
   and uncomment/fill the `pass =` lines, or run `rclone config` as above).

2. Set `nas.remote` and optionally `nas.rclone_config_path` in your `config.yaml`:

```yaml
nas:
  remote: "nas01-sftp"                          # or nas01-smb
  base_root: ""                                 # runs land at nas01-sftp:/<equipment_id>/<run>
  rclone_config_path: "/path/to/tests/docker/rclone.conf"
```

3. Add equipment entries (no transport block needed):

```yaml
equipment:
  - id: EQ1
    label: Equipment 1
    local_root: /tmp/exlab-local
    nas_root: /home/testuser/data    # display value only
    sync_mode: nas
```

4. Open Settings → **NAS Remote** → **Test connection** to verify the remote
   is reachable before starting a sync.

### TEST_-prefix mode

Export `EXLAB_WIZARD_TEST_MODE=1` before launching the app to namespace
every `equipment[i].id` as `TEST_<id>` (see
`apply_test_mode_prefix` in `src/exlab_wizard/config/loader.py`), so
writes land under `…/TEST_EQ1/…` and you can `rm -rf nas-data/TEST_*`
between runs without touching anything else.

### Shorter quiescence window

For interactive testing, shrink the settle window in your test
`config.yaml`:

```yaml
sync:
  quiescence_minutes: 0          # eligible immediately on next poll
  poll_interval_seconds: 5
```

## Lifecycle

```bash
# from tests/docker/
docker compose up -d                         # start (preserves nas-data/)
docker compose ps                            # confirm containers are healthy
docker compose logs -f nas-sftp              # tail sshd logs
docker compose logs -f nas-smb               # tail smbd logs

docker compose down                          # stop, preserve data
docker compose down -v && rm -rf nas-data/*  # full reset to empty NAS
```

## rsync-over-ssh (key-auth, no SFTP)

The `nas-sftp` container runs a second user (`rsyncuser`, UID 1001) that mirrors
the Synology rsync-over-ssh posture: key-only auth, no SFTP subsystem, plain
`rsync --server` over ssh. This is the backend for the integration characterization
suite (`tests/integration/test_rsync_ssh_characterization.py`).

**Users and keys:**

| Field | Value |
|-------|-------|
| User | `rsyncuser` (configured via `RSYNC_USER` in `.env`) |
| UID/GID | 1001 / 1001 (configured via `RSYNC_UID` / `RSYNC_GID`) |
| Auth | ed25519 keypair; private key at `tests/docker/keys/id_exlab` (generated on first boot) |
| Port | Same as SFTP: `${SFTP_HOST_PORT:-2222}` |
| SFTP | Disabled for this user (Match User block without ForceCommand `internal-sftp`) |
| Data root | `/home/rsyncuser/data` (bind-mounted to `tests/docker/nas-data/`) |

**Keypair generation:** the entrypoint generates `tests/docker/keys/id_exlab` and
`tests/docker/keys/id_exlab.pub` on first boot (skips if already present). The
`keys/` directory is bind-mounted into the container at `/keys/`. The integration
test reads the private key directly from `tests/docker/keys/id_exlab`.

**Timezone:** the container's `TZ` is set to `America/New_York` in
`docker-compose.yml`. This causes `--list-only` rsync timestamps to be formatted
in the client's local timezone, NOT the server's — confirmed by
`test_push_list_reconcile_roundtrip`. If a future rsync version changes this
behaviour, the test will fail by a whole-hour mtime offset, triggering the
`nas.remote_tz` contingency in the spec.

**Resetting the keypair:** delete `tests/docker/keys/id_exlab*` and restart the
container (`docker compose up -d`). The entrypoint regenerates the keypair.

## Pytest Fixture (optional)

`tests/integration/test_nas_sync.py` exercises the queue / verifier state
machine via the **stub binary** (`tests/fixtures/stub_rclone.py`), which is
the right tool for fast unit-style coverage and runs in CI. This emulator
is for *higher-fidelity* manual runs that need the real `rclone` binary and
an actual SFTP/SMB handshake — it is **not** wired into CI.

A minimal fixture pattern (add when the first such test needs it):

```python
import shutil, subprocess
from pathlib import Path
import pytest

EMULATOR = Path(__file__).resolve().parents[2] / "tests" / "docker"

@pytest.fixture(scope="session")
def nas_emulator():
    if not shutil.which("docker"):
        pytest.skip("docker not available")
    subprocess.run(
        ["docker", "compose", "up", "-d", "--build"],
        cwd=EMULATOR, check=True,
    )
    try:
        yield {
            "sftp": {"remote": "nas01-sftp", "rclone_conf": str(EMULATOR / "rclone.conf")},
            "smb":  {"remote": "nas01-smb",  "rclone_conf": str(EMULATOR / "rclone.conf")},
        }
    finally:
        subprocess.run(["docker", "compose", "down", "-v"], cwd=EMULATOR, check=False)
```

## Platform Notes

**macOS** — Port 22 is held by the system SSH daemon and 445 by `smbd`. We
default to `2222` / `1445`; only change the host ports if they conflict.

**Linux** — Ports `>=1024` work without root. If the docker socket is
restricted, add yourself to the `docker` group rather than `sudo` (the
`nas-data/` bind mount would otherwise end up root-owned).

**Windows** — Run docker via Docker Desktop or WSL2. Connect via
`localhost:2222` / `localhost:1445`.

## Offline / Air-Gapped Use

```bash
# online machine
docker compose -f tests/docker/docker-compose.yml build
docker save exlab/nas-emulator-sftp:local exlab/nas-emulator-smb:local \
    | gzip > nas-emulator.tar.gz

# target machine
docker load < nas-emulator.tar.gz
```

## Troubleshooting

| Symptom                                                                | Likely cause / fix                                                                                         |
|------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------|
| Container exits immediately with `NAS_PASSWORD is empty`               | Set `NAS_PASSWORD` in `tests/docker/.env`                                                                   |
| `Permission denied` / auth fails from rclone                           | The `pass =` line in `rclone.conf` must be the `rclone obscure`-encoded form of `NAS_PASSWORD`             |
| `Connection refused` on 2222 / 1445                                    | Port already bound; pick free ports via `SFTP_HOST_PORT` / `SMB_HOST_PORT` and update the rclone.conf host/port |
| rclone reports `auth_error` and the ExLab queue terminates at FAILED   | Working as designed — the transport classifies auth failures as terminal (no retries)                      |
| Files appear under `nas-data/` but `sync_state.json` never flips       | Check `sync.quiescence_minutes` — the per-file rollup needs a settled window before crediting files        |
| `nas.remote` shows as `not_found_in_rclone_conf` in Settings           | The remote name in `config.yaml` must match the stanza name in `rclone.conf` exactly                      |
