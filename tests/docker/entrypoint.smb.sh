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

# smbpasswd -a needs the password twice on stdin; -s reads it silently.
printf '%s\n%s\n' "${NAS_PASSWORD}" "${NAS_PASSWORD}" | smbpasswd -a -s "${NAS_USER}"
smbpasswd -e "${NAS_USER}"
echo "[entrypoint] set Samba password for ${NAS_USER} (SMB, password auth)"

exec "$@"
