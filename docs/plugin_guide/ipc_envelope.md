# IPC Envelope

Plugins run inside short-lived worker subprocesses; the host
communicates with each worker over a JSON envelope on the worker's
stdin and stdout. Both sides serialise via `msgspec.json` against
`msgspec.Struct` envelope types defined in
`exlab_wizard/plugins/_ipc.py`. Schema validation happens during
decode in one pass, so a malformed envelope is caught at the boundary
rather than mid-handler. See design spec section 06 §6.3.2 for the
full contract.

## Stdin (host to worker)

A single JSON object terminated by `\n`:

```json
{
  "context": { /* PluginContext fields, paths as strings */ },
  "files": ["metadata.xlsx", "subdir/calibration.xlsx"],
  "dry_run": false,
  "extra_inputs": null
}
```

`context` is the serialised `PluginContext`: variables, dst_root,
answers_file, template_name, template_version, run_kind, equipment_id,
project, dry_run, and a log-channel handle. Paths are stringified
across the IPC boundary; the worker re-hydrates them as `Path`
instances before handing them to the plugin.

## Stdout (worker to host)

A single JSON object terminated by `\n`:

```json
{
  "result": "success",
  "per_file": [
    {"path": "metadata.xlsx", "status": "modified", "changes": null},
    {"path": "subdir/calibration.xlsx", "status": "modified", "changes": null}
  ],
  "log_records": [],
  "input_required": null
}
```

`result` is one of `"success"`, `"failed"`, `"input_required"`, or
`"timeout"`. When the result is `"input_required"` the worker exits
non-zero and the `input_required` field carries the
`PluginInputRequired` payload (a list of additional fields plus a
short reason); the host surfaces these to the operator and resumes
the worker with the supplied values.

## Stderr (structured log channel)

Each line is a JSON object with `level`, `message`, and optional
`context` keys. The host parses stderr line-by-line and merges into
`wizard.<hostname>.log` for the equipment, so plugin-side logging
ends up in the same audit stream as controller events.

## Exit codes

- `0` -- success.
- `1` -- `PluginError` raised; the host records the failure in
  `creation.json` under `plugins_applied[].status = "failed"` and
  continues with remaining plugins.
- `2` -- `PluginInputRequired` raised; the host suspends, prompts the
  operator, then resumes the worker.
- `3` -- uncaught exception in the worker.
- `124` -- host-side timeout (SIGTERM then SIGKILL after a grace
  period).
