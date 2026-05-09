# exlab v1 LIMS contract snapshots

JSON captures of the four upstream `/api/v1` endpoints ExLab-Wizard's
read-only LIMS client (`exlab_wizard.lims.client.LIMSClient`) consumes.
Loaded by `tests/integration/test_lims_contract.py` to exercise the
client's decoders against the real wire format without booting a live
service in PR CI.

## Files

| File | Endpoint | Notes |
|---|---|---|
| `login_response.json` | `POST /api/v1/login` | Real upstream returns the `safe_user`; the client only inspects status. |
| `me.json` | `GET /api/v1/me` | Same `safe_user` shape: `{id, uid, email, role, created_at, updated_at}`. |
| `projects_list.json` | `GET /api/v1/projects` | Wrapped envelope `{"data": [...], "count": N}`. |
| `project_one.json` | `GET /api/v1/projects/{id}` | Bare project object. |

## Provenance

Initial snapshots were authored from the upstream OCaml type definitions
in `gitlab.com/mcnaughtonadm/exlab` at `src/server/api_types.ml` and
`src/server/auth_routes.ml`. They are **not** captured from a running
container; the weekly `lims-live` workflow is what catches drift between
these snapshots and what a real exlab actually serves.

## Regenerating

When upstream changes shape, re-capture against a real container:

```bash
./scripts/regen_lims_snapshots.sh
```

The script clones upstream HEAD, brings the `deploy/docker-compose.local.yml`
stack up with `AUTO_POPULATE_TEST_DATA=true`, calls each endpoint with
the seeded admin credentials, and writes the responses here.
