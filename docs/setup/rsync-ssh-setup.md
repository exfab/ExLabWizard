# Setting up rsync-over-ssh NAS transport

ExLab-Wizard's `rsync_ssh` transport is for cluster nodes where IT blocks
rclone (SMB/SFTP is disabled on the Synology) but allows rsync-over-ssh. Lab
acquisition PCs keep the default `rclone` transport unchanged; only cluster
instances need this walkthrough.

> **Also see:** [`rclone-remote-setup.md`](rclone-remote-setup.md) for the
> rclone/SMB setup used on lab acquisition PCs.

---

## How it works

Instead of a named rclone remote, the `rsync_ssh` transport drives `rsync -e
ssh` directly. `nas.remote` becomes `user@host` (the Synology service account),
and the app composes the full target as `user@host:/<base_root>/<equipment_id>/<run>`.
All four sync operations (push, manifest listing, content verify, connection
probe) use the rsync protocol channel only — no interactive ssh login, no
SFTP subsystem, no remote command execution. IT allowlists exactly
`rsync --server` over ssh; that is all this transport needs.

Credentials are ssh keys only (`BatchMode=yes`). No password is ever stored or
prompted. Key exchange happens before authentication, so the host-key can be
pre-provisioned without any login permission.

---

## Prerequisites

- **rsync** on the cluster node (`rsync --version` should report 3.x).
- **rsync** on the Synology NAS (installed via DSM Package Center or the
  Synology rsync service; verify with `rsync --version` output recorded in
  your runbook — see the "Recording rsync version" step below).
- **OpenSSH client** (`ssh`, `ssh-keygen`, `ssh-keyscan`) on the cluster node.

---

## Step 1 — Generate a dedicated keypair

Generate a key specifically for ExLab-Wizard NAS sync. Using a dedicated key
lets you revoke or rotate it without affecting your personal ssh access.

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_exlab -N ""
```

- `-N ""` sets an empty passphrase so the sync worker can use the key
  non-interactively (`BatchMode=yes` requires no passphrase prompt).
- The private key is `~/.ssh/id_exlab`; the public key is `~/.ssh/id_exlab.pub`.

Restrict permissions:

```bash
chmod 600 ~/.ssh/id_exlab
```

---

## Step 2 — Install the public key on the Synology service account

Copy the public key to the person who manages your Synology NAS (lab IT or
the NAS admin). They need to append the contents of `~/.ssh/id_exlab.pub` to
the `~/.ssh/authorized_keys` file of the NAS service account
(`svc-sync@nas01.lab.example` in the example below).

If you have temporary password ssh access to the NAS service account:

```bash
ssh-copy-id -i ~/.ssh/id_exlab.pub -p <port> svc-sync@nas01.lab.example
```

Or share the one-line `~/.ssh/id_exlab.pub` content with your NAS admin for
manual installation.

---

## Step 3 — Pre-provision the NAS host key

`BatchMode=yes` means ssh will fail immediately if the host key is unknown
rather than prompting you interactively. Pre-provision the key before the
first sync run.

> **No login permission required.** The host-key exchange (Step 3) happens
> at the TCP/cryptographic layer, before authentication. You do not need
> shell access to the NAS to run `ssh-keyscan`.

```bash
ssh-keyscan -p <port> nas01.lab.example >> ~/.ssh/known_hosts
```

Verify the key fingerprint out-of-band against DSM or your NAS admin to
guard against a man-in-the-middle substitution:

```bash
# Compare this fingerprint against what DSM shows under
# Control Panel → Terminal & SNMP → SSH key fingerprints.
ssh-keygen -lf <(ssh-keyscan -p <port> nas01.lab.example 2>/dev/null)
```

---

## Step 4 — Record the NAS rsync version

Record the Synology's rsync version in your runbook. Differences in rsync
protocol versions between client and server can occasionally cause format
quirks in listing output. Run from the cluster node after keys are installed:

```bash
ssh -p <port> -i ~/.ssh/id_exlab svc-sync@nas01.lab.example rsync --version
# Record the first line (e.g. "rsync version 3.2.3 ...") in your lab runbook.
```

---

## Step 5 — Add the `rsync_ssh` block to `config.yaml`

```yaml
nas:
  transport: "rsync_ssh"
  remote: "svc-sync@nas01.lab.example"   # user@host — doubles as the target prefix
  base_root: "/volume1/lab"              # absolute path on the NAS
  ssh_port: 22                           # optional, default 22
  ssh_identity_file: "~/.ssh/id_exlab"  # optional; blank = ssh default key discovery
  mtime_tolerance_s: 2                   # modtime tolerance for reconcile (seconds)
  bandwidth:
    upload_mbps: null                    # null = unlimited
```

The full target path for a run is:

```
svc-sync@nas01.lab.example:/volume1/lab/<EQUIPMENT_ID>/<run-directory-name>
```

---

## Step 6 — Test the connection

Open **Settings → NAS Remote → Test connection** in ExLab-Wizard.

> **Degraded probe by design:** the rsync transport cannot query free-space
> without remote command execution (which IT blocks). Test-connection reports
> reachable + auth ok or a classified failure — no free-space info is shown.
> This is expected and not a configuration error.

---

## Failure modes (BatchMode=yes)

When ssh runs in `BatchMode=yes` it fails immediately instead of prompting.
The error is classified by the driver and surfaced in the Settings panel and
the log.

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `Permission denied (publickey)` | Key not installed on the NAS service account, or wrong identity file | Verify `ssh_identity_file` path; re-run `ssh-copy-id` |
| `Host key verification failed` | Host key not in `~/.ssh/known_hosts` | Re-run `ssh-keyscan` (Step 3); verify fingerprint |
| `Connection refused` / timeout | Network or IT policy blocking the port | Check firewall rules; confirm with IT that the NAS ssh port is reachable from this cluster node |
| `Too many authentication failures` | SSH agent offering too many keys before the correct one | Set `ssh_identity_file` explicitly in config to skip key negotiation |

---

## Notes on `perf` and rclone-only fields

`nas.perf.transfers` and `nas.perf.checkers` are rclone parallelism dials.
They are **ignored** (not rejected) by the `rsync_ssh` transport — rsync is
single-stream per invocation. Set them to rclone defaults for forward
compatibility; they will be used again if you ever switch the instance back
to rclone.

`nas.rclone_config_path` is also ignored by `rsync_ssh` — there is no
rclone involved.
