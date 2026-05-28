#!/bin/sh
# Entrypoint for the password-based ExLab NAS (SFTP) emulator.
#   - sets ${NAS_USER}'s password from ${NAS_PASSWORD} on every boot
#     (so the secret lives only in the env / .env, never in the image)
#   - generates host keys on first boot
#   - execs sshd in the foreground
set -eu

: "${NAS_USER:=testuser}"
: "${NAS_PASSWORD:=}"

if [ -z "${NAS_PASSWORD}" ]; then
    echo "[entrypoint] ERROR: NAS_PASSWORD is empty; set it in tests/docker/.env" >&2
    echo "[entrypoint]        password auth cannot work without it." >&2
    exit 1
fi

# chpasswd reads 'user:password' on stdin; this unlocks the account and
# sets the cleartext password the rclone SFTP backend authenticates with.
echo "${NAS_USER}:${NAS_PASSWORD}" | chpasswd
echo "[entrypoint] set password for ${NAS_USER} (SFTP, password auth)"

# Ensure host keys exist; idempotent.
ssh-keygen -A

exec "$@"
