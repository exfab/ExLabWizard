#!/usr/bin/env bash
# Regenerate the exlab v1 contract snapshots in tests/fixtures/lims/exlab_v1/
# by booting a real upstream `mcnaughtonadm/exlab` instance, hitting each
# endpoint with admin credentials, and writing the JSON responses to disk.
#
# Requires: docker (with compose plugin), git, curl, jq.
# Usage:    ./scripts/regen_lims_snapshots.sh
#
# The admin credentials below seed an ephemeral local container that is
# torn down on script exit; do NOT reuse them for a non-throwaway exlab.
# Avoid `bash -x` while running this script -- the shell trace would
# echo the password verbatim.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${REPO_ROOT}/tests/fixtures/lims/exlab_v1"
WORK="$(mktemp -d)"
cleanup() {
  if [[ -d "${WORK}/exlab" ]]; then
    (cd "${WORK}/exlab" && docker compose -f deploy/docker-compose.local.yml down -v >/dev/null 2>&1) || true
  fi
  rm -rf "${WORK}"
}
trap cleanup EXIT

ADMIN_EMAIL="admin@exlab.test"
ADMIN_PASSWORD="ci-test-password"

echo "==> cloning upstream exlab @ HEAD"
git clone --depth=1 https://gitlab.com/mcnaughtonadm/exlab.git "${WORK}/exlab"
cd "${WORK}/exlab"

echo "==> booting exlab + postgres via deploy/docker-compose.local.yml"
DEFAULT_ADMIN_EMAIL="${ADMIN_EMAIL}" \
DEFAULT_ADMIN_PASSWORD="${ADMIN_PASSWORD}" \
AUTO_POPULATE_TEST_DATA=true \
  docker compose -f deploy/docker-compose.local.yml up -d --build

echo "==> waiting for /api/v1/me to respond (401 means server is up)"
for _ in $(seq 1 120); do
  status="$(curl -sS -o /dev/null -w '%{http_code}' http://localhost:8080/api/v1/me || true)"
  if [[ "${status}" == "401" ]]; then
    break
  fi
  sleep 2
done
if [[ "${status:-000}" != "401" ]]; then
  echo "exlab failed to come up; last status=${status}" >&2
  echo "to inspect: cd ${WORK}/exlab && docker compose -f deploy/docker-compose.local.yml logs" >&2
  exit 1
fi

mkdir -p "${OUT}"
COOKIES="${WORK}/cookies.txt"

echo "==> POST /api/v1/login"
curl -fsS -c "${COOKIES}" \
  -H 'Content-Type: application/json' \
  -d "{\"email\":\"${ADMIN_EMAIL}\",\"password\":\"${ADMIN_PASSWORD}\"}" \
  http://localhost:8080/api/v1/login | jq . > "${OUT}/login_response.json"

echo "==> GET /api/v1/me"
curl -fsS -b "${COOKIES}" http://localhost:8080/api/v1/me | jq . > "${OUT}/me.json"

echo "==> GET /api/v1/projects"
curl -fsS -b "${COOKIES}" http://localhost:8080/api/v1/projects | jq . > "${OUT}/projects_list.json"

first_uid="$(jq -r '.data[0].uid // empty' "${OUT}/projects_list.json")"
if [[ -z "${first_uid}" ]]; then
  echo "no project rows returned; AUTO_POPULATE_TEST_DATA may have failed" >&2
  exit 1
fi

echo "==> GET /api/v1/projects/${first_uid}"
curl -fsS -b "${COOKIES}" "http://localhost:8080/api/v1/projects/${first_uid}" | jq . > "${OUT}/project_one.json"

echo "==> wrote snapshots to ${OUT}"
ls -1 "${OUT}"
