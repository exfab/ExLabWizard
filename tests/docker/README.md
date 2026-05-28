# NAS Emulator — Docker Compose Stack

Local NAS stand-in for testing the ExLab-Wizard sync pipeline without
touching the real NAS. Two containers cover both rclone backends, each
authenticated with an **operator-typed password** (the rclone-only
migration, 2026-05-26): an OpenSSH server for `rclone_sftp` and a Samba
server for `rclone_smb`.

## Purpose & Scope

**In scope:** password-authenticated SFTP and SMB endpoints, a
bind-mounted data volume for inspection, teardown/reset between runs.

**Out of scope:** Google Cloud bucket sync, NAS quota / snapshot
behaviour, real host-key trust (the emulator disables host-key checking).

> **Why password auth, not SSH keys?**
> IT policy forbids SSH keys, so the wizard's only NAS transports are
> `rclone_sftp` and `rclone_smb`, both password-based. The wizard injects
> the password through `RCLONE_CONFIG_<remote>_PASS` at push time (see
> `exlab_wizard.sync.transports.rclone.build_rclone_env`); rclone obscures
> it via `rclone obscure -`. This stack mirrors that: `sshd` runs with
> `PasswordAuthentication yes` and `smbd` with `security = user`, and each
> container sets the test user's password from `${NAS_PASSWORD}` at boot.

## Requirements

| Requirement      | Detail                                                       |
|------------------|--------------------------------------------------------------|
| Protocols        | SFTP (`${SFTP_HOST_PORT:-2222}`) + SMB (`${SMB_HOST_PORT:-1445}`) |
| Auth             | Password only (no keys); SMB user-level security             |
| Platform         | macOS, Linux, Windows (Docker Desktop or Engine)             |
| Internet access  | Not required after `docker compose build` succeeds           |
| Tooling          | Docker + Docker Compose v2 (optionally the `rclone` CLI)     |

## Layout

```
tests/docker/
├── Dockerfile              # debian-slim + openssh-server (password auth)
├── Dockerfile.smb          # debian-slim + samba (password auth)
├── entrypoint.sh           # sets the SFTP user password, generates host keys
├── entrypoint.smb.sh       # registers the Samba user + password
├── docker-compose.yml      # nas-sftp + nas-smb services
├── .env.example            # credential / port template (committed)
├── .env                    # actual values incl. NAS_PASSWORD (gitignored)
├── nas-data/               # bind-mounted NAS state (gitignored except .gitkeep)
│   └── .gitkeep
├── rclone.conf.example     # ad-hoc rclone CLI reference (the app uses env vars)
└── README.md
```

## First-Time Setup

From the repo root:

```bash
# 1. credentials / ports — edit NAS_PASSWORD to taste
cp tests/docker/.env.example tests/docker/.env

# 2. build + start both backends
docker compose -f tests/docker/docker-compose.yml up -d --build

# 3. confirm SFTP answers (uses the password from .env)
RCLONE_CONFIG_T_TYPE=sftp RCLONE_CONFIG_T_HOST=localhost \
RCLONE_CONFIG_T_PORT=2222 RCLONE_CONFIG_T_USER=testuser \
RCLONE_CONFIG_T_PASS=$(rclone obscure 'changeme') \
    rclone lsd t:

# 4. confirm SMB answers
RCLONE_CONFIG_T_TYPE=smb RCLONE_CONFIG_T_HOST=localhost \
RCLONE_CONFIG_T_PORT=1445 RCLONE_CONFIG_T_USER=testuser \
RCLONE_CONFIG_T_PASS=$(rclone obscure 'changeme') \
    rclone lsd t:labshare
```

The password lives only in `tests/docker/.env`; the entrypoints set it on
the container user at boot, so rotating it is just an `.env` edit + a
`docker compose up -d` bounce.

## Wiring Into ExLab Config

Register one equipment block per backend, then set its password in the app
under **Settings → NAS Credentials** (the password is *never* written to
`config.yaml` — it goes to the OS keyring).

### `rclone_sftp` transport

```yaml
equipment:
  - id: EQ1
    label: Equipment 1
    local_root: /tmp/exlab-local
    nas_root: /home/testuser/data
    transport:
      type: rclone_sftp
      host: localhost
      port: 2222
      user: testuser
      remote_path: data        # relative to the SFTP user's home
```

### `rclone_smb` transport

```yaml
equipment:
  - id: EQ2
    label: Equipment 2
    local_root: /tmp/exlab-local
    nas_root: /srv/labshare
    transport:
      type: rclone_smb
      host: localhost
      share: labshare
      user: testuser
      remote_path: ""          # subpath within the share
```

Then start the app, open **Settings → NAS Credentials**, and set the
password (`changeme` from your `.env`) for each equipment. The **Test
connection** button runs `rclone about` against the container.

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
            "sftp": {"host": "localhost", "port": 2222, "user": "testuser",
                     "remote_path": "data"},
            "smb": {"host": "localhost", "port": 1445, "user": "testuser",
                    "share": "labshare"},
            "password": "changeme",
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

| Symptom                                                                | Likely cause / fix                                                                                   |
|------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------|
| Container exits immediately with `NAS_PASSWORD is empty`               | Set `NAS_PASSWORD` in `tests/docker/.env`                                                             |
| `Permission denied` / auth fails                                       | The app password (Settings → NAS Credentials) must match `NAS_PASSWORD`; bounce after editing `.env` |
| `Connection refused` on 2222 / 1445                                    | Port already bound; pick free ports via `SFTP_HOST_PORT` / `SMB_HOST_PORT` and update the equipment   |
| rclone reports `auth_error` and the ExLab queue terminates at FAILED   | Working as designed — the transport classifies auth failures as terminal (no retries)                |
| Files appear under `nas-data/` but `sync_state.json` never flips       | Check `sync.quiescence_minutes` — the per-file rollup needs a settled window before crediting files  |
