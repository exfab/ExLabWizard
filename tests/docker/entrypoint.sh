#!/bin/sh
# Entrypoint for the password-based ExLab NAS (SFTP) + rsync-over-ssh emulator.
#
# SFTP user (${NAS_USER}):
#   - sets password from ${NAS_PASSWORD} on every boot
#   - password lives only in the env / .env, never in the image
#
# rsync-over-ssh user (${RSYNC_USER}):
#   - key-only auth (no password, no SFTP)
#   - generates an ed25519 keypair into the bind-mounted /keys/ dir on
#     first boot (chmod 600); subsequent boots skip generation if the
#     private key already exists
#   - installs the public key as the user's authorized_keys
#   - mirrors Synology posture: rsync --server over ssh works; sftp does not
set -eu

: "${NAS_USER:=testuser}"
: "${NAS_PASSWORD:=}"
: "${RSYNC_USER:=rsyncuser}"

if [ -z "${NAS_PASSWORD}" ]; then
    echo "[entrypoint] ERROR: NAS_PASSWORD is empty; set it in tests/docker/.env" >&2
    echo "[entrypoint]        password auth cannot work without it." >&2
    exit 1
fi

# chpasswd reads 'user:password' on stdin; this unlocks the account and
# sets the cleartext password the rclone SFTP backend authenticates with.
echo "${NAS_USER}:${NAS_PASSWORD}" | chpasswd
echo "[entrypoint] set password for ${NAS_USER} (SFTP, password auth)"

# rsync-over-ssh user: generate keypair on first boot, install authorized_keys.
KEY_FILE="/keys/id_exlab"
if [ ! -f "${KEY_FILE}" ]; then
    echo "[entrypoint] generating ed25519 keypair for ${RSYNC_USER} -> ${KEY_FILE}"
    mkdir -p /keys
    ssh-keygen -t ed25519 -f "${KEY_FILE}" -N "" -C "${RSYNC_USER}@exlab-docker"
    chmod 600 "${KEY_FILE}"
    chmod 644 "${KEY_FILE}.pub"
else
    echo "[entrypoint] keypair already exists at ${KEY_FILE}, skipping generation"
fi

# Install the public key as authorized_keys for the rsync user.
SSH_DIR="/home/${RSYNC_USER}/.ssh"
mkdir -p "${SSH_DIR}"
cp "${KEY_FILE}.pub" "${SSH_DIR}/authorized_keys"
chmod 700 "${SSH_DIR}"
chmod 600 "${SSH_DIR}/authorized_keys"
chown -R "${RSYNC_USER}:${RSYNC_USER}" "${SSH_DIR}"
echo "[entrypoint] installed authorized_keys for ${RSYNC_USER} (key-only, no SFTP)"

# Ensure host keys exist; idempotent.
ssh-keygen -A

exec "$@"
