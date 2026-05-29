#!/bin/sh
# Entrypoint for the password-based ExLab NAS (SMB) emulator.
#   - registers ${NAS_USER} with Samba and sets its password from
#     ${NAS_PASSWORD} on every boot (secret stays in env / .env)
#   - execs smbd in the foreground
set -eu

: "${NAS_USER:=testuser}"
: "${NAS_PASSWORD:=}"

if [ -z "${NAS_PASSWORD}" ]; then
    echo "[entrypoint] ERROR: NAS_PASSWORD is empty; set it in tests/docker/.env" >&2
    exit 1
fi

# smbpasswd needs the password twice on stdin; -s reads it silently. The
# Samba TDB lives in the container's writable layer and survives a
# `docker compose restart`, so `-a` (add) fails on the second boot and
# would crash-loop under `restart: unless-stopped`. Add on first boot,
# fall back to a plain password change on later boots -- both idempotent.
printf '%s\n%s\n' "${NAS_PASSWORD}" "${NAS_PASSWORD}" | smbpasswd -a -s "${NAS_USER}" 2>/dev/null \
    || printf '%s\n%s\n' "${NAS_PASSWORD}" "${NAS_PASSWORD}" | smbpasswd -s "${NAS_USER}"
smbpasswd -e "${NAS_USER}"
echo "[entrypoint] set Samba password for ${NAS_USER} (SMB, password auth)"

exec "$@"
